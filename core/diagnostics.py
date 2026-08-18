"""User-facing diagnostics report.

One click turns "sync doesn't work" into a copyable report that carries the
app version, environment, per-backend sync state and the stderr.log tail, so
a maintainer can diagnose without a back-and-forth.

Design rules:

- Pure standard library; importable anywhere.
- Safe to call from the UI thread. It NEVER triggers a blocking connect:
  Photoshop reports via ``status_lite()`` (explicitly non-connecting), the
  memory-scan backends (CSP / SAI2 / UDM) report their cached version /
  process attributes instead of calling ``status()`` (which may connect and
  would race the sync thread), and the live "connected" verdict comes from
  the already-cached ``_sync_status`` / ``_sync_error`` / ``_last_error_text``
  state.
- Never raises: every section is guarded, a failure degrades to a note in
  the report instead of an exception.
"""

import os
import platform
import sys
import time

STDERR_LOG_NAME = "stderr.log"

# Per-backend attributes worth reporting. Reading these is cheap and never
# connects; ``status()`` on the memory backends is deliberately avoided.
_BACKEND_ATTRS = {
    "csp": ("process_name", "current_version"),
    "sai": ("process_name", "version"),
    "udm": ("process_name", "current_version"),
}

_MODE_NAMES = {
    "csp": "CLIP Studio Paint (内存)",
    "sai": "SAI2",
    "udm": "UDM Paint",
    "ps": "Photoshop",
    "companion": "CSP 智能手机 (R)",
}


def _tail(path: str, max_lines: int = 40, max_bytes: int = 8192) -> list[str] | None:
    """Read the tail of a UTF-8 text file; never raises."""
    if not path or not os.path.exists(path):
        return None
    try:
        size = os.path.getsize(path)
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(max(0, size - max_bytes))
            text = f.read()
        lines = [ln.rstrip() for ln in text.splitlines()]
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
        return lines
    except Exception:
        return None


def _fmt_dict(d) -> str:
    """Compact ``k=v`` rendering that skips empty/None/False values."""
    if not isinstance(d, dict):
        return repr(d)
    parts = [f"{k}={v}" for k, v in d.items() if v not in (None, "", False, [], {})]
    return "; ".join(parts) if parts else "(空)"


def _safe(fn, label: str, lines: list[str]) -> None:
    """Run *fn* and append its result; degrade to a note on failure."""
    try:
        value = fn()
        lines.append(f"{label}: {value}")
    except Exception as exc:  # noqa: BLE001 — diagnostics must never raise
        lines.append(f"{label}: <获取失败: {exc}>")


def collect_diagnostics(sync_thread=None, cfg=None, mixin=None,
                        include_log: bool = True) -> str:
    """Build a plain-text diagnostics report.

    ``sync_thread`` 是 ``MemorySyncThread``（或 None），``mixin`` 是
    ``MainWindow``（提供缓存的同步状态），``cfg`` 是当前配置 dict。
    """
    lines: list[str] = []
    add = lines.append

    add("=" * 60)
    add("Colorink 诊断信息 / Diagnostics")
    add("=" * 60)
    add(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    try:
        from core import updater
        add(f"版本: v{updater.APP_VERSION}")
    except Exception:
        add("版本: <未知>")
    add("")

    # ── 环境 ────────────────────────────────────────────────────────────
    add("[环境 Environment]")
    add(f"系统: {platform.platform()}")
    add(f"Python: {sys.version.split()[0]}  打包: {'是 (onefile/onedir)' if getattr(sys, 'frozen', False) else '否 (源码)'}")
    if cfg:
        add(f"界面语言: {cfg.get('language', 'auto')}")
        add(f"同步目标: {_MODE_NAMES.get(cfg.get('syncSoftware', ''), cfg.get('syncSoftware', '<未设置>'))}")
    if mixin is not None:
        dpr = getattr(mixin, "devicePixelRatio", None)
        if callable(dpr):
            add(f"DPI 缩放: {dpr():g}")
    try:
        from core import config as _config
        add(f"用户数据目录: {_config.get_user_data_dir()}")
    except Exception:
        pass
    add("")

    # ── 同步 ─────────────────────────────────────────────────────────────
    add("[同步 Sync]")
    if sync_thread is None:
        add("同步线程: <未启动>")
    else:
        mode = getattr(sync_thread, "software_mode", "?")
        add(f"当前软件: {_MODE_NAMES.get(mode, mode)}")
        add(f"CSP 版本: {getattr(sync_thread, 'csp_version', '?')}"
            f" | SAI2: {getattr(sync_thread, 'sai2_version', '?')}"
            f" | UDM: {getattr(sync_thread, 'udm_version', '?')}"
            f" | PS: {getattr(sync_thread, 'ps_version', '?')}")

        # 缓存的连接状态（UI 侧）。
        status = getattr(mixin, "_sync_status", None)
        if status and len(status) == 2:
            connected = "已连接 ✓" if status[1] else "未连接 ✗"
            add(f"连接状态: {_MODE_NAMES.get(status[0], status[0])} {connected}")
        else:
            add("连接状态: (尚未上报)")

        err = getattr(mixin, "_sync_error", None)
        if err and len(err) >= 3 and err[1]:
            add(f"错误: {err[1]}  权限问题: {'是' if err[2] else '否'}")

        # 各后端非阻塞快照。
        ps = getattr(sync_thread, "ps_sync", None)
        if ps is not None:
            _safe(lambda: _fmt_dict(ps.status_lite()), "Photoshop", lines)
        for key, attrs in _BACKEND_ATTRS.items():
            backend = getattr(sync_thread, f"{key}_sync", None)
            if backend is None:
                continue
            parts = []
            for attr in attrs:
                value = getattr(backend, attr, None)
                if value not in (None, ""):
                    parts.append(f"{attr}={value}")
            add(f"{key.upper()}: {('; '.join(parts)) if parts else '(静态信息不可用)'}")
        companion = getattr(sync_thread, "companion_sync", None)
        if companion is not None:
            _safe(lambda: "session=" + ("有" if companion._has_session() else "无"),
                  "Companion", lines)
    add("")

    # ── 崩溃标记 ─────────────────────────────────────────────────────────
    try:
        from core import crash_report
        if os.path.exists(crash_report.marker_path()):
            add("[上次崩溃] 存在崩溃标记（crash-marker.json）")
    except Exception:
        pass

    # ── 最近日志 ─────────────────────────────────────────────────────────
    if include_log:
        log_path = None
        try:
            from core import config as _config
            log_path = os.path.join(_config.get_user_data_dir(), STDERR_LOG_NAME)
        except Exception:
            pass
        tail = _tail(log_path) if log_path else None
        if tail:
            add("")
            add(f"[最近日志 {STDERR_LOG_NAME} tail]")
            add(f"日志路径: {log_path}")
            add("")
            add("\n".join(tail))
        else:
            add("")
            add("[最近日志] （无 stderr.log 或不可读）")

    return "\n".join(lines)


def write_diagnostics_report(path: str, sync_thread=None, cfg=None,
                             mixin=None) -> bool:
    """Write the diagnostics report to *path*; True on success."""
    try:
        report = collect_diagnostics(sync_thread=sync_thread, cfg=cfg, mixin=mixin)
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)
        return True
    except Exception:
        return False
