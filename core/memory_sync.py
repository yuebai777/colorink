import sys
import time
from typing import Any
from collections.abc import Mapping

from PyQt6.QtCore import QObject, QThread, pyqtSignal

# Import native modules
from core import (
    csp_brush_link,
    csp_companion_sync,
    photoshop_color_sync,
    sai2_brush_link,
    udm_brush_link,
)


def _rgb_close(a: tuple | None, b: tuple | None, tolerance: int = 2) -> bool:
    """Return True when two RGB triples match within *tolerance* per channel."""
    if a is None or b is None:
        return False
    return all(abs(x - y) <= tolerance for x, y in zip(a, b))


class MemorySyncSignals(QObject):
    # Emitted when the drawing software color changes:
    # (r, g, b, color_index) where color_index is 0 = main/fg, 1 = sub/bg.
    color_changed = pyqtSignal(int, int, int, int)
    # Emitted when the drawing software reports a slot's transparent state
    # changed: (color_index, transparent).
    transparent_changed = pyqtSignal(int, bool)
    # Emitted when the drawing software's ACTIVE slot changes:
    # (color_index) 0 = main, 1 = sub. Only emitted while not transparent.
    active_slot_changed = pyqtSignal(int)
    # Emitted when the connection status changes: (software_mode, connected_bool)
    status_changed = pyqtSignal(str, bool)
    # Emitted when the connection failure reason changes:
    # (software_mode, error_text, permission_issue)
    # error_text is empty when the backend is connected / healthy.
    # permission_issue hints the failure is a UAC privilege mismatch
    # (e.g. Photoshop elevated, Colorink not) — the UI can offer a
    # one-click "relaunch as administrator".
    error_changed = pyqtSignal(str, str, bool)

class MemorySyncThread(QThread):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.signals = MemorySyncSignals()
        self.running = True
        
        # State variables
        self.software_mode = "csp"  # "csp" | "sai" | "udm" | "ps" | "companion"
        self.sync_enabled = True
        self.paused = False
        
        # Versions for memory syncing
        self.csp_version = "auto"
        self.sai2_version = "auto"
        self.udm_version = "auto"
        # How hard to nudge SAI into repainting its own colour widgets after a
        # memory write: "off" | "repaint" | "full" (see core.sai2_ui_refresh).
        self.sai_ui_refresh = "full"
        
        # Cache to prevent loops
        self._last_synced_color: dict[int, tuple[int, int, int]] = {}  # per slot
        # Timestamp of the last local write per slot. The PS script/CEP
        # bridge applies writes asynchronously (up to ~100 ms poll
        # latency); read-back echoes of the previous value during that
        # window must not yank the UI back (乱跳).
        self._last_write_ts: dict[int, float] = {}
        # Pre-write RGB value per slot, so echo suppression can reject only
        # the stale read-back instead of every PS change for 1.5 seconds.
        self._last_write_old_color: dict[int, tuple[int, int, int] | None] = {}
        # Pending writes are kept per slot. A single shared pending value
        # would let a quick fg write be overwritten by a bg write (and vice
        # versa) before the worker thread had a chance to apply it.
        self._pending_writes: dict[int, dict[str, Any]] = {}
        self._last_read_transparent: dict[int, bool] = {}  # read-back dedup per slot
        self._last_active_slot: int | None = None          # 0/1/None dedup
        self.companion_hsv = None
        self._last_companion_hsv = (0.0, 0.0, 0.0)
        self.last_write_time = 0.0
        self._last_error_text = ("", False)
        # PS read-back throttle: get_color/get_bg_color on the COM backend
        # are real COM round-trips into Photoshop's UI thread (~6-8 calls
        # per read); polling them every 100 ms (~60-80 COM calls/s) keeps a
        # genuine Photoshop's engine permanently busy and can starve its
        # CEP plugins (e.g. Coolorus) — the same failure mode as the old
        # bridge panel. The script-bridge panel only rewrites state.txt
        # every 0.5 s anyway, so a 0.5 s read cadence serves both backends.
        self._last_ps_read = 0.0
        
        # Instantiate per-software sync backends
        self.csp_sync = csp_brush_link.CSPSync()
        self.sai2_sync = sai2_brush_link.SAI2Sync()
        self.udm_sync = udm_brush_link.UDMSync()
        self.ps_sync = photoshop_color_sync.PhotoshopSync()
        self.companion_sync = csp_companion_sync.CSPCompanionSync()
        
        self.update_versions()
        
    def update_versions(self):
        # Set versions in backend scripts/instances
        self.csp_sync.set_version(self.csp_version)
        self.sai2_sync.set_version(self.sai2_version)
        self.sai2_sync.set_ui_refresh(self.sai_ui_refresh)
        self.udm_sync.set_version(self.udm_version)
        self.ps_sync.set_version(getattr(self, 'ps_version', 'auto'))
        
    def set_software_mode(self, mode):
        self.software_mode = mode
        self._last_synced_color = {}
        self._last_write_ts = {}
        self._last_write_old_color = {}
        self._pending_writes = {}
        self._last_read_transparent = {}
        self._last_active_slot = None
        # 切走 / 切回同步目标时清掉 CSP 5.1 的不可用标记，避免切回 csp 后
        # 一直停在"5.1 内存同步已移除"而不再重新检测（比如换成了 5.0）。
        self.csp_sync.unsupported_reason = ""
        self._last_error_text = ("", False)
        
    def set_sync_enabled(self, enabled):
        self.sync_enabled = enabled
        if not enabled:
            self._last_synced_color = {}
            self._last_write_ts = {}
            self._last_write_old_color = {}
            self._pending_writes = {}

    def write_color(self, r, g, b, hsv_u32=None, source_space=None, source_values=None,
                    transparent=False, color_index=0):
        color_index = int(color_index)
        pending = self._pending_writes.get(color_index)
        if pending is not None:
            old_color = pending.get("old_color")
        elif (time.time() - self._last_write_ts.get(color_index, 0.0)
                < 1.5):
            # A previous write to this slot is still in flight; keep the
            # original pre-write color so a stale read-back of it stays
            # suppressed even while the user keeps dragging.
            old_color = self._last_write_old_color.get(
                color_index, self._last_synced_color.get(color_index))
        else:
            old_color = self._last_synced_color.get(color_index)
        self._pending_writes[color_index] = {
            "rgb": (r, g, b),
            "hsv_u32": hsv_u32,
            "source_space": source_space,
            "source_values": source_values,
            "transparent": bool(transparent),
            "old_color": old_color,
        }
        self.last_write_time = time.time()

    def get_active_pid(self):
        if not self.sync_enabled or self.paused:
            return None
        if self.software_mode == 'csp':
            return self.csp_sync.pid if self.csp_sync.pm else None
        elif self.software_mode == 'sai':
            status = self.sai2_sync.status()
            return status.get('pid')
        elif self.software_mode == 'udm':
            return self.udm_sync.pid if self.udm_sync.pm else None
        elif self.software_mode == 'ps':
            status_ = self.ps_sync.status()
            return status_.get('pid')
        elif self.software_mode == 'companion':
            return None
        return None
        
    def stop(self, timeout_ms: int = 3000):
        """Signal the thread to stop, then wait with a timeout.

        If the thread is blocked on a hung COM call (e.g. Photoshop died),
        waiting forever would freeze the main thread.  After *timeout_ms*
        we terminate the thread to unblock the caller.
        """
        self.running = False
        if not self.wait(timeout_ms):
            # Thread is stuck — likely in a hung COM RPC call
            self.terminate()
            self.wait(500)
        
    def run(self):
        last_status = None
        
        while self.running:
            # Sleep 100ms
            time.sleep(0.1)
            
            if not self.sync_enabled or self.paused:
                continue
                
            try:
                # 1) Handle write request
                if self._pending_writes:
                    # GUI 线程可能同时整体替换 _pending_writes（
                    # set_sync_enabled/set_software_mode），判空与取键
                    # 之间被替换会抛 StopIteration/KeyError——必须捕获，
                    # 否则写入被裸 except 静默吞掉。
                    try:
                        color_index = next(iter(self._pending_writes))
                    except StopIteration:
                        continue
                    try:
                        pending = self._pending_writes.pop(color_index)
                    except KeyError:
                        continue
                    r, g, b = pending["rgb"]
                    hsv_override = pending["hsv_u32"]
                    src_space = pending["source_space"]
                    src_vals = pending["source_values"]
                    transparent = pending["transparent"]

                    old_color = pending.get("old_color")
                    if old_color is None:
                        old_color = self._last_write_old_color.get(
                            color_index, self._last_synced_color.get(color_index))
                    if transparent and self.software_mode not in ("companion", "csp"):
                        print(f"[Sync] transparent write unsupported in mode '{self.software_mode}' — skipped")
                        continue
                    self._last_write_old_color[color_index] = old_color
                    self._last_synced_color[color_index] = (r, g, b)
                    self._last_write_ts[color_index] = time.time()

                    if transparent:
                        # Transparent is a flag on the ACTIVE slot
                        # (companion: IsColorTransparent; CSP 5.1 memory:
                        # +0x08 = 0xFFFFFFFF). Both main and sub slots
                        # support it — the backends activate the target
                        # slot before setting the flag.
                        if self.software_mode == 'companion':
                            self.companion_sync.set_color(
                                r, g, b, hsv_u32=hsv_override, transparent=True,
                                color_index=color_index,
                            )
                            if hsv_override:
                                _U32 = 4294967295
                                self.companion_hsv = (
                                    hsv_override[0] / _U32 * 360.0,
                                    hsv_override[1] / _U32 * 100.0,
                                    hsv_override[2] / _U32 * 100.0,
                                )
                                self._last_companion_hsv = self.companion_hsv
                            self._last_read_transparent[color_index] = True
                        elif self.software_mode == 'csp':
                            self.csp_sync.set_color(
                                r, g, b, source_space=src_space,
                                source_values=src_vals, transparent=True,
                                color_index=color_index,
                            )
                            # CSP 内存模式的透明标志属于激活槽，读回总是以
                            # index 0 报告（get_color），所以种子必须落在 0
                            # 上——否则下一轮 get_sub_color (transparent=0)
                            # 会把它误判为"槽 1 已清除"并发
                            # 出虚假的 transparent_changed(1, False)。
                            self._last_read_transparent[0] = True
                        continue

                    if self.software_mode == 'csp':
                        # 纯内存写副色：`_write_sub_color` 会把新模式写入
                        # 进程内搜索到的全部副本（含权威笔刷副本），不依赖
                        # companion —— csp 内存模式与 companion 完全独立。
                        self.csp_sync.set_color(
                            r, g, b, source_space=src_space,
                            source_values=src_vals, color_index=color_index,
                        )
                        self._last_synced_color[color_index] = (r, g, b)
                        self._last_read_transparent[color_index] = False
                    elif self.software_mode == 'sai':
                        self.sai2_sync.set_color(r, g, b)
                    elif self.software_mode == 'udm':
                        self.udm_sync.set_color(r, g, b)
                    elif self.software_mode == 'ps':
                        self.ps_sync.set_color(r, g, b, color_index=color_index)
                    elif self.software_mode == 'companion':
                        self.companion_sync.set_color(
                            r, g, b, hsv_u32=hsv_override, color_index=color_index,
                        )
                        # Seed dedup with what we just wrote so the read-back
                        # echo is suppressed (HSV dedup below catches it).
                        if hsv_override:
                            _U32 = 4294967295
                            self.companion_hsv = (
                                hsv_override[0] / _U32 * 360.0,
                                hsv_override[1] / _U32 * 100.0,
                                hsv_override[2] / _U32 * 100.0,
                            )
                            self._last_companion_hsv = self.companion_hsv
                        self._last_read_transparent[color_index] = False
                    continue
                
                # 2) Handle read request (polling)
                # Each entry is a color dict with r/g/b plus optional
                # "index" (0 = main/fg, 1 = sub/bg) and "transparent".
                colors: list[Mapping[str, Any]] = []
                connected = False

                if self.software_mode == 'csp':
                    # 纯内存模式：主色/副色全部从 CSP 进程内存读取（主槽
                    # +0x3C.. / 副槽 +0x9C..，均为 HSV u32 比例编码），
                    # 与 companion 完全独立。
                    main_color = self.csp_sync.get_color()
                    sub_color = self.csp_sync.get_sub_color()
                    if main_color is not None:
                        colors.append(main_color)
                    if sub_color is not None:
                        colors.append(sub_color)
                    active = self.csp_sync.get_active_slot_index()
                    if active is not None and active != self._last_active_slot:
                        self._last_active_slot = active
                        self.signals.active_slot_changed.emit(active)
                    status = self.csp_sync.status()
                    connected = status.get('connected', False)
                    # CSP 5.1 内存同步已移除：把原因抛给 UI（显示在同步页），
                    # 引导改用手机（Companion）模式；恢复连接时同样把错误清掉。
                    csp_unsupported = status.get('unsupported_reason') or ""
                    err_pair = (csp_unsupported, False)
                    if err_pair != self._last_error_text:
                        self._last_error_text = err_pair
                        self.signals.error_changed.emit(
                            self.software_mode, csp_unsupported, False)
                    # NOTE: this used to proactively warm CSP's copy-address
                    # caches here. A locate scan reads every committed page of
                    # the CSP process (2.4 GB working set observed, ~1.1s per
                    # scan, two scans per prime), and paying that at connect
                    # made the app feel worse right when the user starts
                    # working. The copy set is now established for free instead:
                    # whichever slot the user changes first performs a locate
                    # anyway, and that locate also remembers the OTHER slot's
                    # copies via _remember_peer_siblings(), so no extra scan is
                    # ever run on Colorink's behalf.
                elif self.software_mode == 'sai':
                    color = self.sai2_sync.get_color()
                    if color is not None:
                        colors = [color]
                    status = self.sai2_sync.status()
                    connected = status.get('connected', False)
                    # Land a UI refresh that the write path had to throttle or
                    # defer (drag bursts, or SAI mid-interaction), so the last
                    # colour of a drag still reaches SAI's widgets.
                    self.sai2_sync.tick_ui_refresh()
                elif self.software_mode == 'udm':
                    color = self.udm_sync.get_color()
                    if color is not None:
                        colors = [color]
                    status = self.udm_sync.status()
                    connected = status.get('connected', False)
                elif self.software_mode == 'ps':
                    # Read-back cadence depends on the backend:
                    #  - COM backend: get_color/get_bg_color are real COM
                    #    round-trips into Photoshop's UI thread, so they are
                    #    throttled to 0.5 s (see _last_ps_read in __init__)
                    #    to avoid keeping a genuine PS engine busy.
                    #  - script-bridge backend: state.txt is a plain local
                    #    file, so reads are immediate — the only latency is
                    #    the panel's own write cadence. Throttling here too
                    #    would stack two 0.5 s delays (~1 s perceived).
                    # Writes (pending_writes above) are never throttled.
                    backend = getattr(self.ps_sync, "backend", "")
                    read_now = True
                    if backend == "com":
                        read_now = (time.time() - self._last_ps_read >= 0.5)
                        if read_now:
                            self._last_ps_read = time.time()
                    if read_now:
                        fg = self.ps_sync.get_color()
                        bg = self.ps_sync.get_bg_color()
                        if fg is not None:
                            colors.append(fg)
                        if bg is not None:
                            colors.append(bg)
                        # External X-swap detection: the user pressed X in
                        # Photoshop, so each slot now holds the OTHER slot's
                        # previous value. Without clearing the write-echo
                        # suppression here, the slot whose write timestamp is
                        # still fresh keeps its stale swatch while the other
                        # slot updates — both swatches then end up showing the
                        # same color ("把前景色背景色同步成一样的").
                        if fg is not None and bg is not None:
                            prev_fg = self._last_synced_color.get(0)
                            prev_bg = self._last_synced_color.get(1)
                            if prev_fg is not None and prev_bg is not None:
                                fg_rgb = (fg.get("r"), fg.get("g"), fg.get("b"))
                                bg_rgb = (bg.get("r"), bg.get("g"), bg.get("b"))
                                fg_is_old_bg = _rgb_close(fg_rgb, prev_bg)
                                bg_is_old_fg = _rgb_close(bg_rgb, prev_fg)
                                if fg_is_old_bg and bg_is_old_fg:
                                    # Genuine external swap, not a write echo:
                                    # let both slots refresh immediately.
                                    self._last_write_ts.pop(0, None)
                                    self._last_write_ts.pop(1, None)
                    # status() is cheap on both backends (COM: no calls;
                    # bridge: file/process checks) — keep the connection
                    # flag fresh every loop.
                    status = self.ps_sync.status()
                    connected = status.get('connected', False)
                elif self.software_mode == 'companion':
                    color = self.companion_sync.get_color_hsv()
                    if color:
                        colors = [color]
                        self.companion_hsv = (color["h"], color["s"], color["v"])
                    else:
                        self.companion_hsv = None
                    status = self.companion_sync.status()
                    connected = status.get('connected', False)
                    
                # Notify status change
                status_key = (self.software_mode, connected)
                if status_key != last_status:
                    self.signals.status_changed.emit(self.software_mode, connected)
                    last_status = status_key

                # Notify failure reason changes (Photoshop COM etc.) so the
                # UI can show *why* the backend is not connected.
                err_text = ""
                perm_issue = False
                if not connected and self.software_mode == 'ps':
                    err_text = getattr(self.ps_sync, "last_error", "") or ""
                    perm_issue = bool(getattr(self.ps_sync, "permission_issue", False))
                if (err_text, perm_issue) != self._last_error_text:
                    self._last_error_text = (err_text, perm_issue)
                    self.signals.error_changed.emit(self.software_mode, err_text, perm_issue)

                if not connected or not colors:
                    continue

                # Active-slot tracking (companion mode): CSP reports
                # CurrentColorIndex in every read-back. The write applies
                # asynchronously over TCP, so its read-back echo lags behind
                # local clicks; following it would yank the highlight back to
                # a stale slot during rapid fg/bg switching (抽搐). Suppress
                # the echo within the write window — the same 1.5 s guard as
                # the PS colour echo — so local slot selection stays
                # authoritative while a write settles.
                if self.software_mode == 'companion':
                    for color in colors:
                        ai = color.get("index")
                        if isinstance(ai, int) and ai in (0, 1) and ai != self._last_active_slot:
                            self._last_active_slot = ai
                            if time.time() - self._last_write_ts.get(ai, 0.0) < 1.5:
                                continue
                            self.signals.active_slot_changed.emit(ai)

                # Process each reported slot (main index 0, sub index 1).
                # Transparent read-back: CSP reports the drawing color is
                # transparent via IsCurrentColorTransparent (companion) or
                # the memory flag (CSP 5.1 main slot, get_color returns
                # "transparent": 0/1). Mirror onto the UI and skip the RGB
                # update — the channel values are meaningless then.
                # Entries WITHOUT an explicit "transparent" key are pure RGB
                # observations (companion UI main/sub) — they must not emit
                # transparent signals, or a routine RGB change would clear a
                # slot's transparent state that the dedicated flag observer
                # (ui["transparent"]) is still reporting as set.
                for color in colors:
                    color_index = int(color.get("index", 0))
                    if "transparent" in color:
                        is_transparent = bool(color["transparent"])
                        if is_transparent != self._last_read_transparent.get(color_index, False):
                            self._last_read_transparent[color_index] = is_transparent
                            self.signals.transparent_changed.emit(color_index, is_transparent)
                        if is_transparent:
                            continue

                    r = color.get('r')
                    g = color.get('g')
                    b = color.get('b')
                    if r is None or g is None or b is None:
                        continue

                    if self.software_mode == 'companion' and color_index == 0 and self.companion_hsv is not None:
                        lh, ls, lv = self._last_companion_hsv
                        ch, cs, cv = self.companion_hsv
                        if abs(ch - lh) <= 0.1 and abs(cs - ls) <= 0.5 and abs(cv - lv) <= 0.5:
                            continue
                        self._last_companion_hsv = self.companion_hsv
                    else:
                        prev = self._last_synced_color.get(color_index)
                        if prev is not None:
                            lr, lg, lb = prev
                            if abs(r - lr) <= 2 and abs(g - lg) <= 2 and abs(b - lb) <= 2:
                                if self.software_mode == 'ps':
                                    # The target value has been observed, so
                                    # the asynchronous write is applied and
                                    # the echo window can close immediately.
                                    self._last_write_ts.pop(color_index, None)
                                    self._last_write_old_color.pop(color_index, None)
                                continue

                    # PS script/CEP bridge applies writes asynchronously
                    # (up to ~100 ms poll latency): the read-back echoes
                    # the previous value while our write is still in
                    # flight. Accepting it would yank the UI back to the
                    # stale color (乱跳). Suppress only that stale echo;
                    # a genuinely new PS-side change still propagates.
                    if self.software_mode == 'ps':
                        age = time.time() - self._last_write_ts.get(color_index, 0.0)
                        if age < 1.5:
                            old = self._last_write_old_color.get(color_index)
                            if old is None or _rgb_close((r, g, b), old):
                                continue

                    color_tuple = (r, g, b)
                    if self._last_synced_color.get(color_index) != color_tuple:
                        self._last_synced_color[color_index] = color_tuple
                        self.signals.color_changed.emit(r, g, b, color_index)
                    
            except Exception as e:
                # 轮询循环兜底：限频记录（每 5 秒最多一条），绝不静默——
                # 之前裸 except 吞掉一切，任何后端/竞态故障都不可见。
                now = time.time()
                if now - getattr(self, "_last_loop_error_ts", 0.0) > 5.0:
                    self._last_loop_error_ts = now
                    print(f"[Sync] poll loop error: {e!r}")
