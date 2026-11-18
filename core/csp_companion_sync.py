#!/usr/bin/env python3

"""CSP Companion Mode (TCP-based) color sync backend.

Connects to CLIP STUDIO PAINT's built-in Companion Mode server — the same
protocol CSP's official mobile app uses.  No memory scanning, no pymem,
no version-specific offsets: pure TCP with XOR-authenticated JSON framing.

Usage::

    cspc = CSPCompanionSync()
    cspc.connect(host="127.0.0.1", port=54321, password="abc123")
    rgb = cspc.get_color()          # -> {"r": 128, "g": 64, "b": 32} or None
    cspc.set_color(255, 0, 0)       # -> True

QR-code auto-discovery (optional — needs pyzbar)::

    info = CSPCompanionSync.scan_qr_code()
    cspc.connect(**info)

Matches the CSPSync / PhotoshopSync interface for drop-in compatibility
with MemorySyncThread.
"""

from __future__ import annotations

import json
import os
import select
import socket
import sys
import time
from typing import TYPE_CHECKING, Callable, TypedDict

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QDialog, QWidget

# ---------------------------------------------------------------------------
# Protocol constants — compatible with CSP's companion protocol
# (G#1:2022.12).  These are identical across all CSP builds.
# ---------------------------------------------------------------------------

# XOR keys (7-byte cycling)
_REMOTE_KEY = bytes([0x74, 0xB2, 0x92, 0x5B, 0x4A, 0x21, 0xDA])
_AUTH_KEY   = bytes([0xB6, 0xD5, 0x92, 0xC4, 0xA7, 0x83, 0xE1])

# TCP message framing
_TYPE_CLIENT  = 0x01
_TYPE_SUCCESS = 0x06
_TYPE_ERROR   = 0x15
_RS  = b"\x1E"   # Record Separator
_TERM = b"\x00"  # Terminator

# Header preamble
_HEADER_PREAMBLE = b"$tcp_remote_command_protocol_version=1.0"

# Reconnection
_RECONNECT_MARKER = b"{{(([[reconnection request marker]]))}}\r\n"

# U32 scaling
_MAX_U32 = 4294967295

# Heartbeat interval (seconds)
_HEARTBEAT_INTERVAL = 3.0

# Debug logging
_DEBUG = False


# ---------------------------------------------------------------------------
# TypedDicts for CSP protocol data
# ---------------------------------------------------------------------------

class QRInfo(TypedDict):
    """QR code connection parameters."""
    host: str
    port: int
    password: str
    generation: str


class ColorHSV(TypedDict):
    """Normalized HSV + RGB color from CSP."""
    h: float
    s: float
    v: float
    r: int
    g: int
    b: int
    # True when CSP reports the current drawing color is transparent
    # (IsCurrentColorTransparent). Channel values are meaningless then.
    transparent: bool
    # CSP's active color slot: 0 = main color, 1 = sub color
    # (CurrentColorIndex).
    index: int


class _CSPColorState(TypedDict, total=False):
    """Fields we extract from CSP's SyncColorCircleUIState JSON response.

    Using ``total=False`` so ``.get()`` returns ``ValueType | None`` for
    optional keys — matching the actual JSON shape where only the active
    colour model's fields are present.
    """
    CurrentColorIndex: int
    ColorSelectionModel: str
    HSVColorMainH: int
    HSVColorMainS: int
    HSVColorMainV: int
    HSVColorSubH: int
    HSVColorSubS: int
    HSVColorSubV: int
    HSVColorH: int
    HSVColorS: int
    HSVColorV: int
    HLSColorMainH: int
    HLSColorMainS: int
    HLSColorMainL: int
    HLSColorSubH: int
    HLSColorSubS: int
    HLSColorSubL: int
    IsCurrentColorTransparent: bool


class _SessionData(TypedDict, total=False):
    """Fields persisted in csp_companion_session.json."""
    host: str
    port: int
    password: str
    generation: str


def _log(msg: str) -> None:
    if _DEBUG:
        print(f"[CSPCompanion] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# XOR helpers
# ---------------------------------------------------------------------------

def _xor_cycle(data: bytes, key: bytes) -> bytes:
    """XOR *data* with a cycling *key*."""
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def _xor_hex(text: str, key: bytes) -> str:
    """XOR *text* (UTF-8 bytes) with cycling *key*, return hex string."""
    return _xor_cycle(text.encode("utf-8"), key).hex()


# ---------------------------------------------------------------------------
# QR code URL decoding
# ---------------------------------------------------------------------------

def _decode_qr_url(url: str) -> QRInfo | None:
    """Decode a CSP Companion QR URL into connection parameters.

    URL format: ``https://companion.clip-studio.com/rc/zh-tw?s=<hex>``
    The ``s=`` param is hex-encoded ciphertext XOR'd with REMOTE_KEY.
    Decrypted plaintext is tab-separated: ``ips\tport\tpassword\tgeneration``.
    """
    if "?s=" not in url:
        return None
    try:
        hex_part = url.split("?s=", 1)[1].split("&")[0]
        raw = bytes.fromhex(hex_part)
        plain = _xor_cycle(raw, _REMOTE_KEY).decode("utf-8", errors="replace")
        parts = plain.split("\t")
        if len(parts) < 4:
            _log(f"QR decode: expected 4 fields, got {len(parts)}: {plain!r}")
            return None
        ips_str, port_str, password, generation = parts[0], parts[1], parts[2], parts[3]
        # Take first IP
        first_ip = ips_str.split(",")[0].strip()
        return {
            "host": first_ip,
            "port": int(port_str),
            "password": password,
            "generation": generation,
        }
    except Exception as exc:
        _log(f"QR decode failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# TCP message framing
# ---------------------------------------------------------------------------

def _build_message(command: str, detail: object, serial: int = 0) -> bytes:
    """Build a single TCP message following CSP's companion wire format.

    Wire format::

        <TYPE:1> <HEADER_PREAMBLE> <RS> $command=<NAME> <RS> $serial=<N> <RS> $detail=<JSON> <RS> <TERM>
    """
    detail_str = json.dumps(detail, ensure_ascii=False, separators=(",", ":"))
    body = (
        _HEADER_PREAMBLE
        + _RS
        + f"$command={command}".encode("utf-8")
        + _RS
        + f"$serial={serial}".encode("utf-8")
        + _RS
        + f"$detail={detail_str}".encode("utf-8")
        + _RS
    )
    return bytes([_TYPE_CLIENT]) + body + _TERM


def _parse_messages(data: bytes) -> list[tuple[int, str, str]]:
    """Parse one or more CSP companion messages from raw TCP bytes.

    Returns list of ``(type_byte, command_name, detail_json_str)`` tuples.
    Coalesced records (multiple messages in one recv() chunk) are handled.
    """
    if not data:
        return []
    # Split on TERM byte
    raw_msgs = data.split(_TERM)
    results: list[tuple[int, str, str]] = []
    for raw in raw_msgs:
        if not raw or len(raw) < 2:
            continue
        type_byte = raw[0]
        body = raw[1:]
        # Strip any leading RS from the body end (detail field ends with RS)
        body = body.rstrip(_RS)

        command = ""
        detail = ""
        for part in body.split(_RS):
            part_str = part.decode("utf-8", errors="replace")
            if part_str.startswith("$command="):
                command = part_str[len("$command="):]
            elif part_str.startswith("$serial="):
                pass  # Ignored
            elif part_str.startswith("$detail="):
                detail = part_str[len("$detail="):]
            # Skip preamble and unknown fields
        results.append((type_byte, command, detail))
    return results


# ---------------------------------------------------------------------------
# HSV <-> RGB conversion with CSP uint32 scaling
# ---------------------------------------------------------------------------

def _hsv_to_rgb_u32(h_u32: int, s_u32: int, v_u32: int) -> tuple[int, int, int]:
    """Convert CSP's uint32-scaled HSV to 8-bit RGB."""
    h = (h_u32 / _MAX_U32) * 360.0
    s = s_u32 / _MAX_U32
    v = v_u32 / _MAX_U32

    h = h % 360.0
    c = v * s
    x = c * (1.0 - abs((h / 60.0) % 2.0 - 1.0))
    m = v - c

    if h < 60:
        r1, g1, b1 = c, x, 0.0
    elif h < 120:
        r1, g1, b1 = x, c, 0.0
    elif h < 180:
        r1, g1, b1 = 0.0, c, x
    elif h < 240:
        r1, g1, b1 = 0.0, x, c
    elif h < 300:
        r1, g1, b1 = x, 0.0, c
    else:
        r1, g1, b1 = c, 0.0, x

    return (
        max(0, min(255, round((r1 + m) * 255.0))),
        max(0, min(255, round((g1 + m) * 255.0))),
        max(0, min(255, round((b1 + m) * 255.0))),
    )


def _rgb_to_hsv_u32(r: int, g: int, b: int) -> tuple[int, int, int]:
    """Convert 8-bit RGB to CSP's uint32-scaled HSV."""
    rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
    cmax = max(rf, gf, bf)
    cmin = min(rf, gf, bf)
    delta = cmax - cmin

    if delta < 1e-9:
        h_f = 0.0
    elif cmax == rf:
        h_f = 60.0 * (((gf - bf) / delta) % 6.0)
    elif cmax == gf:
        h_f = 60.0 * (((bf - rf) / delta) + 2.0)
    else:
        h_f = 60.0 * (((rf - gf) / delta) + 4.0)

    if h_f < 0:
        h_f += 360.0

    s_f = 0.0 if cmax < 1e-9 else delta / cmax
    v_f = cmax

    return (
        max(0, min(_MAX_U32, round((h_f / 360.0) * _MAX_U32))),
        max(0, min(_MAX_U32, round(s_f * _MAX_U32))),
        max(0, min(_MAX_U32, round(v_f * _MAX_U32))),
    )


# ---------------------------------------------------------------------------
# CSPCompanionSync — the main backend class
# ---------------------------------------------------------------------------

class CSPCompanionSync:
    """TCP client for CSP's built-in Companion Mode color sync.

    Implements the same contract as CSPSync / PhotoshopSync / SAI2Sync /
    UDMSync so it can be dropped into MemorySyncThread unchanged:
    ``connect()``, ``get_color()``, ``set_color()``, ``status()``,
    ``set_version()``, ``dump()``.
    """

    def __init__(self) -> None:
        self._sock: socket.socket | None = None
        self._recv_buf: bytes = b""
        self._serial: int = 0
        self._connected: bool = False
        self._aborting: bool = False
        self._last_heartbeat: float = 0.0
        self._host: str = ""
        self._port: int = 0
        self._password: str = ""
        self._generation: str = "G#1:2022.12"
        # 最近写入颜色去重缓存——按槽位（color_index）独立存储：主槽写过的
        # 颜色再次写副槽必须照常发送 SetCurrentColor，否则 CSP 副槽不更新。
        self._current_color: dict[int, dict[str, int]] = {}
        self._last_status: dict[str, bool | str | int | None] = {"connected": False}
        self._last_hue_u32: int = 0     # survives grayscale writes
        self._last_sat_u32: int = 0     # survives black writes

        # Backend contract fields
        self.current_version: str = "auto"
        self.process_name: str = "CLIPStudioPaint.exe"
        self.pid: int | None = None
        self.pm: None = None  # Compat with CSPSync attribute checks
        self.last_error: str = ""
        self.consecutive_failures: int = 0
        self.ever_connected: bool = False

        # Load saved session for fast reconnect
        self._load_session()

    # ------------------------------------------------------------------
    # Session persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _session_path() -> str:
        """Path to session.json in user data directory."""
        appdata = os.environ.get("APPDATA", "")
        if not appdata:
            appdata = os.path.expanduser("~")
        d = os.path.join(appdata, "Colorink")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "csp_companion_session.json")

    def _load_session(self) -> None:
        path = self._session_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data: _SessionData = json.load(f)
                self._host = data.get("host", "")
                self._port = int(data.get("port", 0))
                self._password = data.get("password", "")
                self._generation = data.get("generation", "G#1:2022.12")
                _log(f"Session loaded: {self._host}:{self._port}")
            except Exception as exc:
                _log(f"Session load failed: {exc}")

    def _save_session(self) -> None:
        path = self._session_path()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "host": self._host,
                    "port": self._port,
                    "password": self._password,
                    "generation": self._generation,
                }, f, ensure_ascii=False, indent=2)
            _log("Session saved.")
        except Exception as exc:
            _log(f"Session save failed: {exc}")

    # ------------------------------------------------------------------
    # QR code scanning (optional — requires pyzbar + Pillow)
    # ------------------------------------------------------------------

    @staticmethod
    def is_qr_scan_available() -> bool:
        """Return True when mss, Pillow and pyzbar can be imported."""
        try:
            import mss  # noqa: F401
            from PIL import Image  # noqa: F401
            from pyzbar.pyzbar import decode  # noqa: F401
            return True
        except ImportError:
            return False

    @staticmethod
    def _decode_qr_from_image(img, zbar_decode) -> QRInfo | None:
        codes = zbar_decode(img)
        for code in codes:
            url = code.data.decode("utf-8", errors="replace")
            _log(f"QR found: {url[:80]}...")
            info = _decode_qr_url(url)
            if info:
                return info
        return None

    @staticmethod
    def scan_qr_code(
        target_rect: tuple[int, int, int, int] | None = None,
    ) -> QRInfo | None:
        """Capture the screen and scan for CSP's companion QR code.

        When *target_rect* ``(left, top, right, bottom)`` is provided, that
        region is scanned first before falling back to all attached monitors.

        Returns ``{"host": ..., "port": ..., "password": ..., "generation": ...}``
        on success, ``None`` if no QR found or dependencies missing.
        """
        try:
            import mss
            from PIL import Image
            from pyzbar.pyzbar import decode as zbar_decode
        except ImportError:
            _log("QR scan skipped: install mss + pyzbar + Pillow")
            return None

        try:
            with mss.mss() as sct:
                if target_rect is not None:
                    tl, tt, tr, tb = target_rect
                    if tr > tl and tb > tt:
                        try:
                            pad = 8
                            region = {
                                "left": int(tl - pad),
                                "top": int(tt - pad),
                                "width": int((tr - tl) + pad * 2),
                                "height": int((tb - tt) + pad * 2),
                            }
                            shot = sct.grab(region)
                            img = Image.frombytes(
                                "RGB", shot.size, shot.bgra, "raw", "BGRX"
                            )
                            info = CSPCompanionSync._decode_qr_from_image(img, zbar_decode)
                            if info:
                                return info
                        except Exception as exc:
                            _log(f"QR target-rect grab skipped: {exc}")

                monitors = sct.monitors[1:] if len(sct.monitors) > 1 else sct.monitors
                for monitor in monitors:
                    screenshot = sct.grab(monitor)
                    img = Image.frombytes(
                        "RGB", screenshot.size, screenshot.bgra, "raw", "BGRX"
                    )
                    info = CSPCompanionSync._decode_qr_from_image(img, zbar_decode)
                    if info:
                        return info
        except Exception as exc:
            _log(f"QR scan error: {exc}")
        return None

    @staticmethod
    def decode_qr_text(url: str) -> QRInfo | None:
        """Decode a CSP companion QR URL string directly (no camera needed).

        If you can read CSP's QR text (shown alongside the QR code in CSP's
        dialog), pass it here.
        """
        return _decode_qr_url(url)

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self, host: str = "", port: int = 0, password: str = "",
                generation: str = "") -> bool:
        """Connect to CSP's Companion Mode TCP server.

        If *host*/*port*/*password* are provided, use them directly.
        Otherwise, attempt reconnection with the saved session.
        """
        if getattr(self, "_aborting", False):
            return False

        if host:
            self._host = host
            self._port = int(port)
            self._password = password
            if generation:
                self._generation = generation

        if not self._host or not self._port or not self._password:
            _log("connect: no session info — scan QR or provide manual params")
            self.last_error = "no_session"
            return False

        # If already connected, check if still alive
        if self._connected and self._sock is not None:
            try:
                self._send_heartbeat()
                return True
            except Exception:
                self._disconnect()

        try:
            _log(f"Connecting to {self._host}:{self._port}...")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock = sock
            sock.settimeout(1.2)
            sock.connect((self._host, self._port))
            if getattr(self, "_aborting", False):
                self._disconnect()
                return False
            sock.settimeout(0.5)  # Non-blocking-ish for polling
            self._recv_buf = b""
            self._serial = 0

            # Try reconnection marker first; if it fails, do fresh auth
            if not self._try_reconnect():
                if getattr(self, "_aborting", False) or not self._authenticate():
                    _log("connect: auth failed")
                    self.last_error = "auth_failed"
                    self.consecutive_failures = getattr(self, "consecutive_failures", 0) + 1
                    self._disconnect()
                    return False

            if getattr(self, "_aborting", False):
                self._disconnect()
                return False

            # Activation ritual
            self._activate()

            self._connected = True
            self._last_heartbeat = time.time()
            self.last_error = ""
            self.consecutive_failures = 0
            self.ever_connected = True
            self._save_session()
            _log("connect: OK")
            return True

        except Exception as exc:
            _log(f"connect failed: {exc}")
            self.last_error = "timeout" if isinstance(exc, socket.timeout) else "connect_failed"
            self.consecutive_failures = getattr(self, "consecutive_failures", 0) + 1
            self._disconnect()
            return False

    def abort(self) -> None:
        """Abort any in-flight blocking socket operation immediately (thread-safe)."""
        self._aborting = True
        self._disconnect()

    def reset_abort(self) -> None:
        """Clear the abort flag so future connections can proceed."""
        self._aborting = False

    def _disconnect(self) -> None:
        self._connected = False
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass
        self._recv_buf = b""
        self._current_color = {}

    def _try_reconnect(self) -> bool:
        """Attempt reconnection with the saved session marker.

        CSP accepts ``reconnect_marker`` as currentAuthToken and the
        existing password as newAuthToken for session resumption.
        """
        if not self._password or getattr(self, "_aborting", False):
            return False
        try:
            curr_token = _xor_hex(
                _RECONNECT_MARKER.decode("utf-8", errors="replace"), _AUTH_KEY
            )
            new_token = _xor_hex(self._password, _AUTH_KEY)
            self._send_raw(_build_message("Authenticate", [
                self._generation,
                curr_token,
                new_token,
            ]))
            msgs = self._recv_messages(timeout=1.5)
            for _type, _cmd, _detail in msgs:
                if _type == _TYPE_SUCCESS and _cmd == "Authenticate":
                    _log("Reconnect: OK")
                    return True
            if getattr(self, "_aborting", False):
                self._disconnect()
                return False
            _log("Reconnect: server rejected marker, falling back to fresh auth")
            # Reconnect failed — close and reopen for fresh auth
            self._disconnect()
            if getattr(self, "_aborting", False):
                return False
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock = sock
            sock.settimeout(2.0)
            sock.connect((self._host, self._port))
            if getattr(self, "_aborting", False):
                self._disconnect()
                return False
            sock.settimeout(0.5)
            self._recv_buf = b""
        except Exception as exc:
            _log(f"Reconnect attempt failed: {exc}")
            self._disconnect()
        return False

    def _authenticate(self) -> bool:
        """Perform fresh authentication with CSP."""
        if getattr(self, "_aborting", False):
            return False
        try:
            curr_token = _xor_hex(self._password, _AUTH_KEY)
            new_token = _xor_hex(self._password, _AUTH_KEY)
            self._send_raw(_build_message("Authenticate", [
                self._generation,
                curr_token,
                new_token,
            ]))
            msgs = self._recv_messages(timeout=2.0)
            for _type, _cmd, _detail in msgs:
                if _type == _TYPE_SUCCESS and _cmd == "Authenticate":
                    _log("Auth: OK")
                    return True
                elif _type == _TYPE_ERROR:
                    try:
                        err = json.loads(_detail)
                        _log(f"Auth error: {err}")
                    except Exception:
                        _log(f"Auth error (raw): {_detail}")
            _log("Auth: no success response")
        except Exception as exc:
            _log(f"Auth exception: {exc}")
        return False

    def _activate(self) -> None:
        """Send activation ritual after successful auth.

        CSP requires a specific sequence of commands to enable full
        bidirectional sync.
        """
        self._send_heartbeat()
        self._send_raw(_build_message("GetModifyKeyString", {
            "CtrlPushed": False,
            "AltPushed": False,
            "ShiftPushed": False,
        }))
        self._send_raw(_build_message("GetServerSelectedTabKind", {}))
        self._send_raw(_build_message("SetServerSelectedTabKind", {}))
        self._send_heartbeat()
        _log("Activation ritual done")

    def _send_heartbeat(self) -> None:
        """Send TellHeartbeat with idle-reset flag.

        MUST include ``IdleTimerResetRequested: true`` or CSP returns
        empty ``{}`` for all Sync* responses after ~15 seconds.
        """
        self._send_raw(_build_message("TellHeartbeat", {
            "IdleTimerResetRequested": True,
        }))

    def _ensure_heartbeat(self) -> None:
        """Send heartbeat if interval has elapsed."""
        if self._connected and time.time() - self._last_heartbeat > _HEARTBEAT_INTERVAL:
            try:
                self._send_heartbeat()
                self._last_heartbeat = time.time()
            except Exception:
                self.last_error = "disconnected"
                self._disconnect()

    # ------------------------------------------------------------------
    # Raw I/O
    # ------------------------------------------------------------------

    def _send_raw(self, data: bytes) -> None:
        sock = self._sock
        if sock is None or getattr(self, "_aborting", False):
            raise ConnectionError("Not connected")
        sock.sendall(data)

    def _recv_messages(self, timeout: float = 0.5) -> list[tuple[int, str, str]]:
        """Read and parse available messages from the socket.

        Non-blocking within *timeout*; returns all parsed messages.
        Always parses any buffered data (even on timeout / no new data).
        """
        sock = self._sock
        if sock is None or getattr(self, "_aborting", False):
            return []
        try:
            sock.settimeout(timeout)
            chunk = sock.recv(65536)
        except socket.timeout:
            chunk = None
        except Exception as exc:
            _log(f"recv error: {exc}")
            self._connected = False
            return []
        if getattr(self, "_aborting", False):
            return []
        # A graceful close by the peer (user closed CSP's "connect smartphone"
        # dialog, CSP exited) arrives as an empty recv, NOT as an exception.
        # Falling through as "no data" left _connected True, so status() kept
        # reporting a healthy link and every read silently returned None until
        # a later heartbeat sendall finally raised — and the dead socket was
        # never closed. Treat EOF as the disconnect it is.
        if chunk == b"":
            _log("recv: peer closed the connection (EOF)")
            self._disconnect()
            return []
        # Always drain any additional fragments, then parse whatever we have.
        chunk = chunk or b""
        try:
            if chunk:
                self._recv_buf += chunk
            # Collect any remaining fragmented TCP data non-blockingly without stalling
            while True:
                sock = self._sock
                if sock is None or getattr(self, "_aborting", False):
                    break
                rlist, _, _ = select.select([sock], [], [], 0)
                if not rlist:
                    break
                more = sock.recv(65536)
                if not more:
                    break
                self._recv_buf += more
        except Exception:
            pass  # Best-effort drain; parse what we already have

        # Always parse buffered data — even if recv() timed out.
        msgs = _parse_messages(self._recv_buf)
        # Remove parsed bytes from buffer (up to last TERM)
        if msgs:
            last_term_idx = 0
            for _ in msgs:
                idx = self._recv_buf.find(_TERM, last_term_idx)
                if idx >= 0:
                    last_term_idx = idx + 1
            if last_term_idx > 0 and last_term_idx < len(self._recv_buf):
                self._recv_buf = self._recv_buf[last_term_idx:]
            elif last_term_idx > 0:
                self._recv_buf = b""
        return msgs

    # ------------------------------------------------------------------
    # Color I/O — the public API
    # ------------------------------------------------------------------

    def get_color(self) -> dict[str, int] | None:
        """Read CSP's current brush color, returning 8-bit RGB.

        For native HSV (no precision loss), use :meth:`get_color_hsv`.
        """
        hsv = self.get_color_hsv()
        if hsv is None:
            return None
        return {"r": hsv["r"], "g": hsv["g"], "b": hsv["b"]}

    def get_colors_hsv(self) -> dict | None:
        """Read BOTH main and sub UI colors plus the active index.

        Returns ``{"main": ColorHSV, "sub": ColorHSV, "active_index": int,
        "transparent": bool}``. Used by the memory-mode observer so main
        and sub color changes are tracked independently of the active
        slot (a slot switch alone must not overwrite Colorink's values).
        """
        if not self._connected:
            if not self.connect():
                return None
        stale_msgs = self._recv_messages(timeout=0.05)
        self._ensure_heartbeat()
        try:
            self._send_raw(_build_message("SyncColorCircleUIState", {
                "IsManipulating": False,
                "HSVColorMainH": 0,
                "HSVColorMainS": 0,
                "HSVColorMainV": 0,
                "CurrentColorIndex": 0,
                "ColorSelectionModel": "HSV",
            }))
            for attempt in range(3):
                msgs = self._recv_messages(timeout=0.3)
                for _type, _cmd, detail_json in msgs:
                    if _cmd not in ("SyncColorCircleUIState", "SetCurrentColor"):
                        continue
                    try:
                        d: _CSPColorState = json.loads(detail_json)
                        if not d or d == {}:
                            continue
                    except Exception:
                        continue
                    idx = int(d.get("CurrentColorIndex", 0))
                    transp = bool(d.get("IsCurrentColorTransparent", False))

                    def parse_ch(prefix: str) -> dict | None:
                        h = d.get(f"{prefix}H")
                        s = d.get(f"{prefix}S")
                        v = d.get(f"{prefix}V")
                        if h is None or s is None or v is None:
                            return None
                        r, g, b = _hsv_to_rgb_u32(int(h), int(s), int(v))
                        return {"h": int(h) / _MAX_U32 * 360.0,
                                "s": int(s) / _MAX_U32 * 100.0,
                                "v": int(v) / _MAX_U32 * 100.0,
                                "r": r, "g": g, "b": b,
                                "transparent": transp, "index": 0}

                    main = parse_ch("HSVColorMain")
                    sub = parse_ch("HSVColorSub")
                    if main is None:
                        main = parse_ch("HSVColor")
                    if main is None and sub is None:
                        continue
                    return {"main": main, "sub": sub,
                            "active_index": idx, "transparent": transp}
            return None
        except Exception as exc:
            _log(f"get_colors_hsv error: {exc}")
            self._disconnect()
            return None

    def get_color_hsv(self) -> ColorHSV | None:
        """Read CSP's brush color, returning native HSV + RGB.

        Uses ``SyncColorCircleUIState`` — the canonical read command per
        the CSP Companion Protocol (PROTOCOL.md).  A deliberately stale
        (all-zero) state is sent in the request detail so that CSP always
        detects a diff and returns the **full** current color payload.
        """
        if not self._connected:
            if not self.connect():
                return None

        # Drain any lingering activation/heartbeat responses before sending our
        # color request. (LumaPaletteCSP does this inside activate() itself;
        # we defer it here to keep the activation fast.)
        stale_msgs = self._recv_messages(timeout=0.05)
        if stale_msgs:
            _log(f"get_color_hsv: drained {len(stale_msgs)} stale msgs before request")

        self._ensure_heartbeat()

        try:
            # SyncColorCircleUIState is *the* read command (not GetCurrentColor).
            # Send zero stale-state to force CSP to return full current color.
            # Ref: LumaPaletteCSP PROTOCOL.md § "Stale-state request trick"
            self._send_raw(_build_message("SyncColorCircleUIState", {
                "IsManipulating": False,
                "HSVColorMainH": 0,
                "HSVColorMainS": 0,
                "HSVColorMainV": 0,
                "CurrentColorIndex": 0,
                "ColorSelectionModel": "HSV",
            }))

            # Read with retries: CSP may take a moment to process the request.
            hsv = None
            for attempt in range(3):
                msgs = self._recv_messages(timeout=0.3)
                if msgs and _DEBUG:
                    cmds = [m[1] for m in msgs]
                    _log(f"get_color_hsv attempt {attempt}: recv {len(msgs)} msgs: {cmds}")
                hsv = self._parse_color_response_hsv(msgs)
                if hsv is not None:
                    break

            if hsv:
                _log(f"get_color_hsv: H={hsv['h']:.1f} S={hsv['s']:.1f} V={hsv['v']:.1f} → RGB=[{hsv['r']}, {hsv['g']}, {hsv['b']}]")
                # Update hue/sat memory so zero-saturation writes preserve the
                # current hue (set_color falls back to _last_hue_u32 for gray).
                if hsv["s"] > 0.5:
                    self._last_hue_u32 = int(hsv["h"] / 360.0 * _MAX_U32)
                    self._last_sat_u32 = int(hsv["s"] / 100.0 * _MAX_U32)
            elif _DEBUG:
                _log("get_color_hsv: no color in response after 3 attempts")
            return hsv
        except Exception as exc:
            _log(f"get_color error: {exc}")
            self._disconnect()
            return None

    def _parse_color_response_hsv(self, msgs: list[tuple[int, str, str]]) -> ColorHSV | None:
        """Extract native HSV + RGB from SyncColorCircleUIState response.

        CSP can be in either HSV or HLS colour model — the response fields
        and their prefixes differ.  We normalise everything to HSV + 8-bit
        RGB for the rest of the app.
        """
        for _type, _cmd, detail_json in msgs:
            if _cmd in ("SyncColorCircleUIState", "SetCurrentColor"):
                try:
                    d: _CSPColorState = json.loads(detail_json)
                    if not d or d == {}:
                        continue
                    idx = d.get("CurrentColorIndex", 0)
                    model = d.get("ColorSelectionModel", "HSV")

                    if model == "HLS":
                        return self._parse_hls_response(d, idx)
                    else:
                        return self._parse_hsv_response(d, idx)
                except Exception as exc:
                    _log(f"Parse color response error: {exc}")
        return None

    def _parse_hsv_response(self, d: _CSPColorState, idx: int) -> ColorHSV | None:
        """Parse HSV-color-model response from CSP."""
        if idx == 0:
            h_u32 = d.get("HSVColorMainH", d.get("HSVColorH", 0))
            s_u32 = d.get("HSVColorMainS", d.get("HSVColorS", 0))
            v_u32 = d.get("HSVColorMainV", d.get("HSVColorV", 0))
        else:
            h_u32 = d.get("HSVColorSubH", d.get("HSVColorH", 0))
            s_u32 = d.get("HSVColorSubS", d.get("HSVColorS", 0))
            v_u32 = d.get("HSVColorSubV", d.get("HSVColorV", 0))
        if h_u32 == 0 and s_u32 == 0 and v_u32 == 0:
            h_u32 = d.get("HSVColorH", 0)
            s_u32 = d.get("HSVColorS", 0)
            v_u32 = d.get("HSVColorV", 0)
        # Skip truly empty responses (not pure black)
        if h_u32 == 0 and s_u32 == 0 and v_u32 == 0 and d in ({}, None, {"CurrentColorIndex": 0}):
            return None
        h_deg = (h_u32 / _MAX_U32) * 360.0
        s_pct = (s_u32 / _MAX_U32) * 100.0
        v_pct = (v_u32 / _MAX_U32) * 100.0
        r, g, b = _hsv_to_rgb_u32(h_u32, s_u32, v_u32)
        return {"h": h_deg, "s": s_pct, "v": v_pct,
                "r": r, "g": g, "b": b,
                "transparent": bool(d.get("IsCurrentColorTransparent", False)),
                "index": idx}

    def _parse_hls_response(self, d: _CSPColorState, idx: int) -> ColorHSV | None:
        """Parse HLS-color-model response from CSP, convert to HSV+RGB.

        Uses the direct HLS→HSV formula (no RGB intermediate):
          V = L + S_hls × min(L, 1−L)
          S_hsv = 2 × (1 − L/V)   (V > 0)
          H is identical in both models.
        """
        if idx == 0:
            keys = ("HLSColorMainH", "HLSColorMainL", "HLSColorMainS")
        else:
            keys = ("HLSColorSubH", "HLSColorSubL", "HLSColorSubS")
        # Skip responses that carry no HLS fields at all (CSP reported the HLS
        # model but sent an unrelated payload). Testing the VALUES for all-zero
        # instead would discard pure black, which in HLS is exactly
        # H=0, L=0, S=0 — black then never reached the UI while CSP's colour
        # wheel was in HLS mode. The HSV parser makes the same distinction.
        if not any(k in d for k in keys):
            return None
        h_u32 = d.get(keys[0], 0)
        l_u32 = d.get(keys[1], 0)
        s_u32 = d.get(keys[2], 0)
        # CSP uint32 → 0..1
        h_norm = h_u32 / _MAX_U32
        l_norm = l_u32 / _MAX_U32
        s_hls  = s_u32 / _MAX_U32
        # Direct HLS→HSV
        v_norm = l_norm + s_hls * min(l_norm, 1.0 - l_norm)
        if v_norm > 0.0:
            s_hsv = 2.0 * (1.0 - l_norm / v_norm)
        else:
            s_hsv = 0.0
        # HSV → RGB via existing uint32 pipeline
        h_u32_out = int(h_norm * _MAX_U32)
        s_u32_out = int(s_hsv  * _MAX_U32)
        v_u32_out = int(v_norm * _MAX_U32)
        r, g, b = _hsv_to_rgb_u32(h_u32_out, s_u32_out, v_u32_out)
        _log(f"parse_hls: HLS=({h_norm*360:.1f}°,{l_norm*100:.1f}%,{s_hls*100:.1f}%) → HSV=({h_norm*360:.1f}°,{s_hsv*100:.1f}%,{v_norm*100:.1f}%) → RGB=[{r},{g},{b}]")
        return {"h": h_norm * 360.0,
                "s": s_hsv  * 100.0,
                "v": v_norm * 100.0,
                "r": r, "g": g, "b": b,
                "transparent": bool(d.get("IsCurrentColorTransparent", False)),
                "index": idx}

    def _parse_color_response(self, msgs: list[tuple[int, str, str]]) -> dict[str, int] | None:
        """Extract RGB from SyncColorCircleUIState response messages.

        CSP responds with the current color state; we extract the active
        slot (Main/Sub based on CurrentColorIndex), decode uint32 HSV,
        and convert to 8-bit RGB.
        """
        for _type, _cmd, detail_json in msgs:
            # Server can push SyncColorCircleUIState as notification or
            # respond to SetCurrentColor with success.
            if _cmd in ("SyncColorCircleUIState", "SetCurrentColor"):
                try:
                    d: _CSPColorState = json.loads(detail_json)
                    if not d or d == {}:
                        continue
                    idx = d.get("CurrentColorIndex", 0)
                    if idx == 0:
                        h = d.get("HSVColorMainH", d.get("HSVColorH", 0))
                        s = d.get("HSVColorMainS", d.get("HSVColorS", 0))
                        v = d.get("HSVColorMainV", d.get("HSVColorV", 0))
                    else:
                        h = d.get("HSVColorSubH", d.get("HSVColorH", 0))
                        s = d.get("HSVColorSubS", d.get("HSVColorS", 0))
                        v = d.get("HSVColorSubV", d.get("HSVColorV", 0))
                    if h == 0 and s == 0 and v == 0:
                        # Could be unset — try legacy fields
                        h = d.get("HSVColorH", 0)
                        s = d.get("HSVColorS", 0)
                        v = d.get("HSVColorV", 0)
                    if h == 0 and s == 0 and v == 0:
                        continue
                    r, g, b = _hsv_to_rgb_u32(h, s, v)
                    return {"r": r, "g": g, "b": b}
                except Exception as exc:
                    _log(f"Parse color response error: {exc}")
        return None

    def set_color(self, r: int, g: int, b: int, hsv_u32: tuple[int, int, int] | None = None,
                  transparent: bool = False, color_index: int = 0) -> bool:
        """Write a color to CSP's brush via Companion protocol.

        If *hsv_u32* ``(h, s, v)`` is provided (all uint32-scaled), those
        values are used directly instead of converting from RGB.  This
        preserves saturation when V=0 (black RGB carries no S info).

        When *transparent* is ``True`` the ``IsColorTransparent`` flag is
        set on the wire, switching CSP's current drawing color to
        transparent (the channel values are then ignored by CSP).

        *color_index* selects the target slot: 0 = main color, 1 = sub
        color.
        """
        if not self._connected:
            if not self.connect():
                return False

        self._ensure_heartbeat()

        # Skip if same as last set color for THIS slot (dedup is per-slot:
        # a single shared cache would wrongly suppress the sub-slot write
        # when both slots are set to the same RGB).
        # But always send when explicit HSV is provided — the RGB may
        # be identical (e.g., black) while the HSV values changed.
        # Transparent requests must always go through: the flag changed
        # even when the RGB payload did not. That cuts BOTH ways — the
        # cache therefore also remembers the transparent flag, because
        # toggling transparency never changes the slot's RGB. Without it a
        # transparent→opaque clear (transparent tile toggled off, or an
        # fg/bg swatch click restoring an opaque slot) matched the cached
        # RGB and returned early, so CSP kept IsColorTransparent=ON and the
        # next poll snapped the UI tile straight back to transparent.
        if not transparent and hsv_u32 is None:
            cr = self._current_color.get(color_index)
            if (cr is not None and cr["r"] == r and cr["g"] == g and cr["b"] == b
                    and not cr.get("transparent")):
                return True

        try:
            if hsv_u32 is not None:
                h_u32, s_u32, v_u32 = hsv_u32
            else:
                h_u32, s_u32, v_u32 = _rgb_to_hsv_u32(r, g, b)

            # Preserve hue when saturation drops to ~0 (grayscale).
            # Only needed when hue was derived from RGB (h=0 for gray).
            # If the caller passed explicit hsv_u32, trust their hue.
            if s_u32 >= _MAX_U32 * 0.005:
                self._last_hue_u32 = h_u32
            elif hsv_u32 is None:
                h_u32 = self._last_hue_u32

            # Preserve saturation when value drops to ~0 (black).
            # rgb_to_hsv returns s=0 for black — unless the caller
            # passed explicit HSV values (user actively changed S).
            if v_u32 >= _MAX_U32 * 0.005:
                self._last_sat_u32 = s_u32
            elif hsv_u32 is None:
                s_u32 = self._last_sat_u32
            elif hsv_u32 is not None:
                # CSP reads explicit hue and saturation directly at V=0.
                self._last_sat_u32 = s_u32

            self._send_raw(_build_message("SetCurrentColor", {
                "ColorSpaceKind": "HSV",
                "IsColorTransparent": bool(transparent),
                "HSVColorH": h_u32,
                "HSVColorS": s_u32,
                "HSVColorV": v_u32,
                "ColorIndex": int(color_index),
            }))
            _ = self._recv_messages(timeout=0.2)
            self._current_color[color_index] = {
                "r": r, "g": g, "b": b, "transparent": int(bool(transparent)),
            }
            _log(f"set_color: RGB=[{r}, {g}, {b}] transparent={transparent} index={color_index} -> H={h_u32} S={s_u32} V={v_u32}")
            return True
        except Exception as exc:
            _log(f"set_color error: {exc}")
            self._disconnect()
            return False

    # ------------------------------------------------------------------
    # Status / introspection
    # ------------------------------------------------------------------

    def status(self) -> dict[str, bool | str | int | None]:
        if not self._connected:
            # Try reconnecting once
            _ = self.connect()
        return self.status_lite()

    def status_lite(self) -> dict[str, bool | str | int | None]:
        """Return connection status without triggering a blocking connect()."""
        return {
            "connected": self._connected,
            "host": self._host if self._connected else None,
            "port": self._port if self._connected else None,
            "version": self.current_version,
            "processName": self.process_name,
            "hasSession": self._has_session(),
            "consecutiveFailures": getattr(self, "consecutive_failures", 0),
            "everConnected": getattr(self, "ever_connected", False),
            "lastError": getattr(self, "last_error", ""),
        }

    def needs_reconnect(self) -> bool:
        """True when a saved session failed to connect or dropped unexpectedly."""
        if self._connected or not self._has_session():
            return False
        return bool(
            getattr(self, "ever_connected", False)
            or getattr(self, "last_error", "") == "auth_failed"
            or getattr(self, "consecutive_failures", 0) >= 2
        )

    def reset_reconnect_tracking(self) -> None:
        """Clear accumulated failure counters before a fresh reconnect cycle."""
        self.consecutive_failures = 0
        self.last_error = ""

    def set_version(self, version: str) -> bool:
        version = str(version or "auto").strip()
        if version == self.current_version:
            return False
        self.current_version = version
        self._disconnect()
        return True

    def dump(self) -> dict[str, str | int | bool | None | dict[str, int]]:
        color = self.get_color()
        if color is None:
            return {"error": "not connected", "session": self._has_session()}
        return {
            "host": self._host,
            "port": self._port,
            "connected": self._connected,
            "version": self.current_version,
            "color": color,
        }

    def has_session(self) -> bool:
        """True if host, port, and password session parameters are set."""
        return bool(self._host and self._port and self._password)

    def _has_session(self) -> bool:
        return self.has_session()

    # ------------------------------------------------------------------
    # Connection setup / reconnect dialog
    # ------------------------------------------------------------------

    @staticmethod
    def _position_dialog_clear_of_qr(
        dlg: QDialog,
        qr_rect: tuple[int, int, int, int] | None = None,
    ) -> None:
        """Place *dlg* away from the upper-middle area where CSP pops up its QR dialog."""
        from PyQt6.QtCore import QPoint
        from PyQt6.QtGui import QCursor
        from PyQt6.QtWidgets import QApplication

        from core.foreground import get_widget_screen_rect, rects_overlap

        dlg.adjustSize()
        w = max(dlg.width(), dlg.sizeHint().width(), 440)
        h = max(dlg.height(), dlg.sizeHint().height(), 220)

        screen = None
        if qr_rect is not None:
            cx_p = (qr_rect[0] + qr_rect[2]) // 2
            cy_p = (qr_rect[1] + qr_rect[3]) // 2
            for s in QApplication.screens():
                dpr = max(0.5, float(s.devicePixelRatio() or 1.0))
                geo = s.geometry()
                sl = round(geo.left() * dpr)
                st = round(geo.top() * dpr)
                sr = sl + round(geo.width() * dpr)
                sb = st + round(geo.height() * dpr)
                if sl <= cx_p <= sr and st <= cy_p <= sb:
                    screen = s
                    break
        if screen is None:
            try:
                screen = QApplication.screenAt(QCursor.pos())
            except Exception:
                screen = None
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return

        avail = screen.availableGeometry()
        dpr = max(0.5, float(screen.devicePixelRatio() or 1.0))

        def _clamp(x: int, y: int) -> tuple[int, int]:
            cx = max(avail.left() + 12, min(x, avail.right() - w - 12))
            cy = max(avail.top() + 12, min(y, avail.bottom() - h - 12))
            return cx, cy

        # Default placement: lower-center of available geometry, clear of
        # CSP's upper-middle QR dialog (Fig. 2) and top-left File menu (Fig. 1).
        default_x, default_y = _clamp(
            avail.center().x() - w // 2,
            avail.bottom() - h - 32,
        )

        if qr_rect is None:
            dlg.move(default_x, default_y)
            return

        # Convert physical qr_rect to logical coords relative to this screen's DPR.
        ql = round(qr_rect[0] / dpr)
        qt = round(qr_rect[1] / dpr)
        qr = round(qr_rect[2] / dpr)
        qb = round(qr_rect[3] / dpr)
        qr_logical = (ql, qt, qr, qb)

        candidates = [
            _clamp(default_x, default_y),
            _clamp((ql + qr) // 2 - w // 2, qb + 20),
            _clamp(avail.right() - w - 24, avail.bottom() - h - 24),
            _clamp(avail.left() + 24, avail.bottom() - h - 24),
            _clamp(qr + 20, qt),
            _clamp(ql - w - 20, qt),
            _clamp((ql + qr) // 2 - w // 2, qt - h - 20),
        ]
        for cx, cy in candidates:
            cand_phys = (
                round(cx * dpr),
                round(cy * dpr),
                round((cx + w) * dpr),
                round((cy + h) * dpr),
            )
            if not rects_overlap(cand_phys, qr_rect, margin=16):
                dlg.move(cx, cy)
                return

        # Fallback: check current rect and at least keep it lower on screen
        cur_rect = get_widget_screen_rect(dlg)
        if cur_rect is None or rects_overlap(cur_rect, qr_rect, margin=12):
            dlg.move(*_clamp((qr_logical[0] + qr_logical[2]) // 2 - w // 2, qb + 20))

    @staticmethod
    def create_companion_dialog(
        parent: QWidget | None = None,
        *,
        reconnect_mode: bool = False,
        auto_show_without_activating: bool = False,
        hide_for_scan_cb: Callable[[QDialog], object] | None = None,
        restore_after_scan_cb: Callable[[object, QDialog], None] | None = None,
        find_qr_window_cb: Callable[[], tuple[int, tuple[int, int, int, int]] | None] | None = None,
        sync_overlap_cb: Callable[[tuple[int, int, int, int] | None], None] | None = None,
        on_connected_cb: Callable[[], None] | None = None,
        on_dismissed_cb: Callable[[bool], None] | None = None,
    ) -> QDialog:
        """Build the CSP Companion setup / reconnect dialog with QR auto-watch
        and temporary window hiding during QR capture."""
        from PyQt6.QtCore import Qt, QTimer
        from PyQt6.QtWidgets import (
            QApplication,
            QDialog,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QPushButton,
            QVBoxLayout,
        )

        from core import i18n
        from core.foreground import (
            find_csp_companion_qr_window,
            get_widget_screen_rect,
            rects_overlap,
        )

        dlg = QDialog(parent)
        dlg.setWindowTitle(
            i18n.tr("CSP 智能手机需要重新连接")
            if reconnect_mode
            else i18n.tr("CSP 智能手机连接")
        )
        dlg.setMinimumWidth(460)
        dlg.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        if auto_show_without_activating:
            dlg.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        dlg.setStyleSheet("""
            QDialog {
                background-color: #2e2e2e;
            }
            QLabel {
                color: #d5d5d5;
                background: transparent;
                font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei";
                font-size: 12px;
            }
            QLineEdit {
                background-color: #1e1e1e;
                border: 1px solid #555;
                color: #e0e0e0;
                border-radius: 3px;
                padding: 5px 8px;
                font-size: 12px;
            }
            QLineEdit:focus {
                border-color: #5a94e2;
            }
            QPushButton {
                background-color: #3e3e3e;
                border: 1px solid #555;
                color: #e0e0e0;
                border-radius: 3px;
                padding: 5px 14px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #4e4e4e;
                border-color: #666;
            }
            QPushButton:default {
                background-color: #5a94e2;
                border-color: #5a94e2;
                color: #fff;
            }
            QPushButton:default:hover {
                background-color: #6aa4f2;
            }
            QPushButton:disabled {
                background-color: #2e2e2e;
                color: #666;
                border-color: #444;
            }
        """)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(10)

        # ── Step-by-step guide ──
        if reconnect_mode:
            guide_text = i18n.tr(
                "<b style='font-size:13px;'>🔗 CSP 智能手机连接已断开，请重新连接</b>"
            )
            step1_html = i18n.tr(
                "在 <b>CLIP STUDIO PAINT</b> 顶部点击菜单 <b>文件 → 连接智能手机</b>"
            )
            step1_sub = i18n.tr(
                "（弹出二维码窗口时，Colorink 若有遮挡会自动临时隐藏）"
            )
            step2_html = i18n.tr(
                "二维码窗口弹出后将自动扫描重连，或点击下方 <b>自动扫描二维码</b>"
            )
            step2_sub = i18n.tr("（也可手动粘贴二维码链接 URL）")
            steps_html = (
                "<table>"
                + "<tr><td style='padding:4px 8px; vertical-align:top; color:#5a94e2;'><b>①</b></td>"
                + f"<td style='padding:4px 0;'>{step1_html}<br>"
                + f"<span style='color:#999; font-size:10px;'>{step1_sub}</span></td></tr>"
                + "<tr><td style='padding:4px 8px; vertical-align:top; color:#5a94e2;'><b>②</b></td>"
                + f"<td style='padding:4px 0;'>{step2_html}<br>"
                + f"<span style='color:#999; font-size:10px;'>{step2_sub}</span></td></tr>"
                + "</table>"
            )
        else:
            guide_text = i18n.tr(
                "<b style='font-size:13px;'>🔗 CSP 智能手机连接 — 操作步骤</b>"
            )
            s1 = i18n.tr("打开 <b>CLIP STUDIO PAINT</b>")
            s2 = i18n.tr("点击菜单 <b>文件 → 连接智能手机</b>")
            s2_sub = i18n.tr("（CSP 会弹出二维码窗口；扫描时 Colorink 会自动临时隐藏以避免遮挡）")
            s3 = i18n.tr("点击下方 <b>自动扫描二维码</b>，")
            s3_sub = i18n.tr("或手动粘贴二维码旁边的 URL")
            steps_html = (
                "<table>"
                + "<tr><td style='padding:4px 8px; vertical-align:top; color:#5a94e2;'><b>①</b></td>"
                + f"<td style='padding:4px 0;'>{s1}</td></tr>"
                + "<tr><td style='padding:4px 8px; vertical-align:top; color:#5a94e2;'><b>②</b></td>"
                + f"<td style='padding:4px 0;'>{s2}<br>"
                + f"<span style='color:#999; font-size:10px;'>{s2_sub}</span></td></tr>"
                + "<tr><td style='padding:4px 8px; vertical-align:top; color:#5a94e2;'><b>③</b></td>"
                + f"<td style='padding:4px 0;'>{s3}<br>"
                + f"<span style='color:#999; font-size:10px;'>{s3_sub}</span></td></tr>"
                + "</table>"
            )

        guide = QLabel(guide_text)
        guide.setWordWrap(True)
        layout.addWidget(guide)

        steps = QLabel(steps_html)
        steps.setWordWrap(True)
        steps.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(steps)

        sep = QLabel("")
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: #555;")
        layout.addWidget(sep)

        url_label = QLabel(i18n.tr("<b>连接 URL</b>（可选，手动粘贴链接）："))
        layout.addWidget(url_label)
        url_input = QLineEdit()
        url_input.setPlaceholderText(i18n.tr("粘贴以 https://companion.clip-studio.com 开头的 URL"))
        layout.addWidget(url_input)

        status_lbl = QLabel(
            i18n.tr("等待在 CSP 中打开「文件 → 连接智能手机」...")
            if reconnect_mode
            else ""
        )
        status_lbl.setWordWrap(True)
        status_lbl.setStyleSheet("color: #888;")
        layout.addWidget(status_lbl)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        scan_btn = QPushButton(i18n.tr("📷 自动扫描二维码"))
        scan_btn.setToolTip(i18n.tr("临时隐藏软件并截屏搜索 CSP 的二维码"))

        connect_btn = QPushButton(i18n.tr("连接"))
        connect_btn.setDefault(True)

        cancel_btn = QPushButton(i18n.tr("稍后") if reconnect_mode else i18n.tr("取消"))

        btn_layout.addWidget(scan_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(connect_btn)
        layout.addLayout(btn_layout)

        state: dict[str, object] = {
            "ok": False,
            "closed": False,
            "scanning": False,
            "notified_connected": False,
            "qr_attempts": {},  # hwnd -> int
            "pending_auto_hwnd": 0,
            "last_qr_rect": None,
        }
        instance = CSPCompanionSync()
        qr_finder = find_qr_window_cb or find_csp_companion_qr_window

        setattr(dlg, "result_ok", False)
        setattr(dlg, "_reconnect_mode", reconnect_mode)
        setattr(dlg, "_auto_prompt", auto_show_without_activating)

        def _is_alive() -> bool:
            return not bool(state["closed"])

        def _default_hide(d: QDialog) -> bool:
            was_vis = d.isVisible()
            if was_vis:
                d.hide()
            return was_vis

        def _default_restore(snap: object, d: QDialog) -> None:
            if snap and _is_alive():
                d.show()
                d.raise_()

        hide_fn = hide_for_scan_cb or _default_hide
        restore_fn = restore_after_scan_cb or _default_restore

        watch_timer = QTimer(dlg)
        watch_timer.setInterval(300)

        def _finish_connected():
            if state["notified_connected"]:
                return
            state["notified_connected"] = True
            state["ok"] = True
            setattr(dlg, "result_ok", True)
            watch_timer.stop()
            instance._disconnect()
            if callable(on_connected_cb):
                try:
                    on_connected_cb()
                except Exception:
                    pass
            status_lbl.setText(
                i18n.tr("✓ 重新连接成功！颜色同步已恢复")
                if reconnect_mode
                else i18n.tr("✓ 连接成功！颜色同步已启动")
            )
            status_lbl.setStyleSheet("color: #4a4; font-weight: bold;")
            QTimer.singleShot(1000, lambda: dlg.accept() if _is_alive() else None)

        def _complete_connect(auto_triggered: bool = False, qr_hwnd: int = 0) -> None:
            if not _is_alive():
                return
            connect_btn.setEnabled(False)
            scan_btn.setEnabled(False)
            url_text = url_input.text().strip()

            if url_text:
                info = CSPCompanionSync.decode_qr_text(url_text)
                if info:
                    instance._host = info["host"]
                    instance._port = info["port"]
                    instance._password = info["password"]
                    if info.get("generation"):
                        instance._generation = info["generation"]
                else:
                    status_lbl.setText(i18n.tr("URL 格式不正确，请检查后重试"))
                    status_lbl.setStyleSheet("color: #c44;")
                    connect_btn.setEnabled(True)
                    scan_btn.setEnabled(True)
                    return

            if not instance._host:
                status_lbl.setText(i18n.tr("请先扫描二维码或粘贴 URL"))
                status_lbl.setStyleSheet("color: #c44;")
                connect_btn.setEnabled(True)
                scan_btn.setEnabled(True)
                return

            status_lbl.setText(
                i18n.tr("正在连接 {host}:{port}...", host=instance._host, port=instance._port)
            )
            status_lbl.setStyleSheet("color: #5a94e2;")
            QApplication.processEvents()

            ok = instance.connect(
                host=instance._host,
                port=instance._port,
                password=instance._password,
                generation=instance._generation,
            )

            if ok:
                _finish_connected()
            else:
                if auto_triggered and qr_hwnd:
                    attempts_map = state["qr_attempts"]
                    assert isinstance(attempts_map, dict)
                    attempts = int(attempts_map.get(qr_hwnd, 0))
                    if attempts < 2:
                        status_lbl.setText(i18n.tr("连接重试中，请保持 CSP 二维码窗口打开..."))
                        status_lbl.setStyleSheet("color: #c84;")
                        connect_btn.setEnabled(True)
                        scan_btn.setEnabled(True)
                        QTimer.singleShot(600, lambda h=qr_hwnd: _auto_scan_for_hwnd(h))
                        return
                status_lbl.setText(
                    i18n.tr(
                        "连接失败 — 请确认：\n"
                        "1. CSP 正在运行且\"连接智能手机\"窗口已打开\n"
                        "2. URL 完整且未过期（每次打开都会生成新的）\n"
                        "3. 防火墙未阻止 TCP 连接"
                    )
                )
                status_lbl.setStyleSheet("color: #c44;")
                connect_btn.setEnabled(True)
                scan_btn.setEnabled(True)
                scan_btn.setText(i18n.tr("重新扫描"))

        def do_scan(auto_triggered: bool = False, target_rect: tuple[int, int, int, int] | None = None, qr_hwnd: int = 0) -> None:
            if not _is_alive() or state["scanning"] or state["ok"]:
                return
            state["scanning"] = True
            scan_btn.setEnabled(False)
            connect_btn.setEnabled(False)
            scan_btn.setText(i18n.tr("扫描中..."))
            status_lbl.setText(i18n.tr("正在临时隐藏软件并扫描屏幕上的二维码..."))
            status_lbl.setStyleSheet("color: #5a94e2;")
            QApplication.processEvents()

            snap = hide_fn(dlg)
            QApplication.processEvents()

            def _scan_tick():
                info = None
                try:
                    if _is_alive():
                        rect = target_rect if target_rect is not None else state["last_qr_rect"]
                        info = CSPCompanionSync.scan_qr_code(target_rect=rect)  # type: ignore[arg-type]
                finally:
                    try:
                        restore_fn(snap, dlg)
                    except Exception:
                        pass
                    state["scanning"] = False

                if not _is_alive():
                    return

                if info:
                    status_lbl.setText(
                        i18n.tr("✓ 发现 CSP 服务器: {host}:{port}", host=info["host"], port=info["port"])
                    )
                    status_lbl.setStyleSheet("color: #4a4;")
                    instance._host = info["host"]
                    instance._port = info["port"]
                    instance._password = info["password"]
                    if info.get("generation"):
                        instance._generation = info["generation"]
                    url_input.clear()
                    _complete_connect(auto_triggered=auto_triggered, qr_hwnd=qr_hwnd)
                else:
                    if auto_triggered and qr_hwnd:
                        attempts_map = state["qr_attempts"]
                        assert isinstance(attempts_map, dict)
                        attempts = int(attempts_map.get(qr_hwnd, 0))
                        if attempts < 2:
                            status_lbl.setText(i18n.tr("已检测到「连接智能手机」窗口，等待二维码显示后重试..."))
                            status_lbl.setStyleSheet("color: #5a94e2;")
                            connect_btn.setEnabled(True)
                            scan_btn.setEnabled(True)
                            scan_btn.setText(i18n.tr("重新扫描"))
                            QTimer.singleShot(600, lambda h=qr_hwnd: _auto_scan_for_hwnd(h))
                            return
                        status_lbl.setText(
                            i18n.tr("已检测到「连接智能手机」窗口，但未识别到二维码，请点击「重新扫描」")
                        )
                    else:
                        status_lbl.setText(
                            i18n.tr(
                                "未找到二维码 — 请确认 CSP 的\"连接智能手机\"窗口可见，\n"
                                "或手动粘贴 URL 后点击\"连接\""
                            )
                        )
                    status_lbl.setStyleSheet("color: #c84;")
                    connect_btn.setEnabled(True)
                    scan_btn.setEnabled(True)
                    scan_btn.setText(i18n.tr("重新扫描"))

            QTimer.singleShot(180, _scan_tick)

        def _auto_scan_for_hwnd(hwnd: int) -> None:
            if not _is_alive() or state["scanning"] or state["ok"]:
                return
            if state["pending_auto_hwnd"] == hwnd:
                state["pending_auto_hwnd"] = 0
            hit = None
            try:
                hit = qr_finder()
            except Exception:
                hit = None
            if hit is None:
                return
            cur_hwnd, cur_rect = hit
            if cur_hwnd != hwnd:
                return
            attempts_map = state["qr_attempts"]
            assert isinstance(attempts_map, dict)
            attempts = int(attempts_map.get(cur_hwnd, 0))
            if attempts >= 2:
                return
            attempts_map[cur_hwnd] = attempts + 1
            state["last_qr_rect"] = cur_rect
            do_scan(auto_triggered=True, target_rect=cur_rect, qr_hwnd=cur_hwnd)

        def _watch_tick() -> None:
            if not _is_alive() or state["scanning"] or state["ok"]:
                return
            try:
                hit = qr_finder()
            except Exception:
                hit = None
            qr_rect = hit[1] if hit is not None else None
            state["last_qr_rect"] = qr_rect
            if callable(sync_overlap_cb):
                try:
                    sync_overlap_cb(qr_rect)
                except Exception:
                    pass
            if hit is None:
                state["pending_auto_hwnd"] = 0
                attempts_map = state["qr_attempts"]
                assert isinstance(attempts_map, dict)
                attempts_map.clear()
                return

            qr_hwnd, qr_rect = hit
            if dlg.isVisible():
                dlg_rect = get_widget_screen_rect(dlg)
                if dlg_rect is not None and rects_overlap(dlg_rect, qr_rect, margin=16):
                    CSPCompanionSync._position_dialog_clear_of_qr(dlg, qr_rect=qr_rect)

            attempts_map = state["qr_attempts"]
            assert isinstance(attempts_map, dict)
            if int(attempts_map.get(qr_hwnd, 0)) == 0 and state["pending_auto_hwnd"] != qr_hwnd:
                state["pending_auto_hwnd"] = qr_hwnd
                status_lbl.setText(i18n.tr("已检测到 CSP「连接智能手机」窗口，准备自动扫描..."))
                status_lbl.setStyleSheet("color: #5a94e2;")
                QTimer.singleShot(300, lambda h=qr_hwnd: _auto_scan_for_hwnd(h))

        def _mark_connected_externally() -> None:
            if not _is_alive() or state["ok"]:
                return
            state["ok"] = True
            state["notified_connected"] = True
            setattr(dlg, "result_ok", True)
            watch_timer.stop()
            status_lbl.setText(i18n.tr("✓ CSP 智能手机已连接"))
            status_lbl.setStyleSheet("color: #4a4; font-weight: bold;")
            QTimer.singleShot(800, lambda: dlg.accept() if _is_alive() else None)

        def _on_finished(res: int) -> None:
            state["closed"] = True
            watch_timer.stop()
            if callable(sync_overlap_cb):
                try:
                    sync_overlap_cb(None)
                except Exception:
                    pass
            if callable(on_dismissed_cb):
                try:
                    on_dismissed_cb(bool(state["ok"]))
                except Exception:
                    pass

        setattr(dlg, "is_scanning", lambda: bool(state["scanning"]))
        setattr(dlg, "trigger_scan", lambda: do_scan(auto_triggered=False))
        setattr(dlg, "watch_tick", _watch_tick)
        setattr(dlg, "mark_connected_externally", _mark_connected_externally)

        _ = scan_btn.clicked.connect(lambda: do_scan(auto_triggered=False))
        _ = connect_btn.clicked.connect(lambda: _complete_connect(auto_triggered=False))
        _ = cancel_btn.clicked.connect(dlg.reject)
        _ = dlg.finished.connect(_on_finished)

        watch_timer.timeout.connect(_watch_tick)
        watch_timer.start()

        # Position clear of any currently open QR window (or upper-middle area)
        initial_hit = None
        try:
            initial_hit = qr_finder()
        except Exception:
            initial_hit = None
        CSPCompanionSync._position_dialog_clear_of_qr(
            dlg, qr_rect=(initial_hit[1] if initial_hit is not None else None)
        )
        return dlg

    @staticmethod
    def show_setup_dialog(
        parent: QWidget | None = None,
        *,
        reconnect_mode: bool = False,
    ) -> bool:
        """Show a modal setup dialog to acquire CSP companion connection info.

        Auto-scans QR code (if pyzbar available), falls back to manual
        URL paste.  Saves session on success.
        """
        dlg = CSPCompanionSync.create_companion_dialog(
            parent, reconnect_mode=reconnect_mode
        )
        _ = dlg.exec()
        return bool(getattr(dlg, "result_ok", False))


# ---------------------------------------------------------------------------
# Quick test entry point (run directly: python -m core.csp_companion_sync)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cspc = CSPCompanionSync()
    print("Session loaded:", cspc._has_session())
    print("Status:", cspc.status())

    # Try auto-scan QR
    print("Scanning for QR code...")
    info = CSPCompanionSync.scan_qr_code()
    if info:
        print(f"QR found: {info['host']}:{info['port']}")
        _ = cspc.connect(**info)
        color = cspc.get_color()
        if color:
            print(f"Current brush color: RGB({color['r']}, {color['g']}, {color['b']})")
    else:
        print("No QR found. Provide host/port/password manually:")
        print("  cspc.connect(host='127.0.0.1', port=54321, password='...')")
