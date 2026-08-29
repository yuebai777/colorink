"""CEP-extension bridge for green/portable Photoshop installs.

Green editions register no COM automation interface, so COM-based sync
cannot attach to them. Instead Colorink deploys a **hidden CEP background
extension** into ``<ps_dir>/Required/CEP/extensions/ColorinkBridge/``.

The extension manifest mirrors Adobe's own ``com.adobe.Butler.backend``
structure (``Version 7.0``, ``AutoVisible false`` + ``StartOn
applicationActivate``, ``Type Custom``) — the only manifest pattern
proven to auto-load at startup on portable builds. The panel's JS runs
in a persistent CEF runtime with Node.js enabled, so it polls the bridge
files itself every 0.1 s and applies colors via ``evalScript``.

File protocol (ASCII, one line, ``|``-separated), all inside the
extension folder:

- ``cmd.txt``       ``<token>|<index>|<r>|<g>|<b>``   — write target
- ``state.txt``     ``<fg_r>|<fg_g>|<fg_b>|<bg_r>|<bg_g>|<bg_b>`` — live colors
- ``heartbeat.txt`` millisecond epoch written on every poll
- ``panel_version.txt`` panel protocol version, rewritten on every poll by
  the panel itself — a missing/older value means the *running* panel
  predates a newer deploy (Photoshop not restarted yet), which callers
  surface as a "restart Photoshop" hint.

Deploying only takes effect after Photoshop restarts; ``is_alive()``
reports whether the loaded panel is currently polling (heartbeat stays
fresh while PS runs, since the panel polls every 0.1 s).
"""

from __future__ import annotations

import os
import shutil
import time
from typing import Final

INDEX_FILENAME: Final = "index.html"
MANIFEST_FILENAME: Final = "manifest.xml"
CMD_FILENAME: Final = "cmd.txt"
STATE_FILENAME: Final = "state.txt"
HEARTBEAT_FILENAME: Final = "heartbeat.txt"
PANEL_VERSION_FILENAME: Final = "panel_version.txt"
# Bump when the panel's behaviour changes in a way Colorink needs to
# detect (e.g. poll interval). The panel rewrites panel_version.txt on
# every poll; a running panel with an older protocol keeps writing the
# old value (or nothing), so Colorink can tell "deployed file is new"
# from "running panel is new" — only the latter clears the hint.
PANEL_VERSION: Final = 4

EXTENSION_ID: Final = "com.colorink.bridge"
EXTENSION_DIR_NAME: Final = "ColorinkBridge"

_CEP_RELATIVE_DIR: Final = os.path.join(
    "Required", "CEP", "extensions", EXTENSION_DIR_NAME)

# Manifest mirroring com.adobe.Butler.backend (the one structure proven
# to auto-load at startup on green builds) — only IDs changed.
_MANIFEST_TEMPLATE: Final = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<ExtensionManifest xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" ExtensionBundleId="{ext_id}" ExtensionBundleName="ColorinkBridge" ExtensionBundleVersion="1.0.0" Version="7.0">
  <ExtensionList>
    <Extension Id="{ext_id}" Version="1.0.0"/>
  </ExtensionList>
  <ExecutionEnvironment>
    <HostList>
      <Host Name="ILST" Version="[21.0,99.9]"/>
      <Host Name="IDSN" Version="[11.4,99.9]"/>
      <Host Name="PHXS" Version="[16.0,99.9]"/>
    </HostList>
    <LocaleList>
      <Locale Code="All"/>
    </LocaleList>
    <RequiredRuntimeList>
      <RequiredRuntime Name="CSXS" Version="6.0"/>
    </RequiredRuntimeList>
  </ExecutionEnvironment>
  <DispatchInfoList>
    <Extension Id="{ext_id}">
      <DispatchInfo>
        <Resources>
          <MainPath>./index.html</MainPath>
          <CEFCommandLine>
            <Parameter>--enable-nodejs</Parameter>
          </CEFCommandLine>
        </Resources>
        <Lifecycle>
          <AutoVisible>false</AutoVisible>
          <StartOn>
            <Event>applicationActivate</Event>
          </StartOn>
        </Lifecycle>
        <UI>
          <Type>Custom</Type>
          <Geometry>
            <Size>
              <Height>1</Height>
              <Width>1</Width>
            </Size>
          </Geometry>
        </UI>
      </DispatchInfo>
    </Extension>
  </DispatchInfoList>
</ExtensionManifest>
"""

# The panel uses a two-path design. All work runs through evalScript
# into Photoshop's ExtendScript engine (File objects — no Node fs
# dependency in the *script* itself), but the command-mailbox *check*
# prefers Node fs (the manifest enables --enable-nodejs), so the panel
# does not touch Photoshop's single-threaded script engine just to look
# for work:
#
#   * command mailbox checked every 100 ms via Node fs.statSync (zero
#     ExtendScript traffic while idle); a new command is applied on the
#     next tick — the same perceived latency as the old 0.1 s design,
#   * state read-back every 10 ticks (1 s) and immediately after a
#     command (Photoshop foreground/background colours),
#   * heartbeat + panel_version every 40 ticks (4 s) — is_alive() in
#     Colorink tolerates an 8 s gap, so 4 s is ample,
#   * fallback: CEP builds without a working Node fs probe the mailbox
#     with a lightweight evalScript (reads cmd.txt only, no writes)
#     instead of the full five-file-write-every-tick script that starved
#     TourBox / Coolorus.
_INDEX_TEMPLATE: Final = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>body { margin: 0; }</style>
</head>
<body>
<script>
var dir = "";
try {
    if (window.__adobe_cep__) {
        dir = window.__adobe_cep__.getSystemPath("extension");
        // This CEP build returns a file:/// URL, not a plain path.
        if (String(dir).indexOf("file:///") === 0) {
            dir = String(dir).slice(8);
        }
        try { dir = decodeURIComponent(dir); } catch (e) {}
    }
} catch (e) {}
// Prefer Node fs for the command-mailbox check so the panel never
// enters Photoshop's ExtendScript engine just to look for work.
// Some CEP builds lack a working Node fs — probe it and fall back.
var useNodeFs = false;
try {
    var fs = require('fs');
    useNodeFs = (typeof fs !== 'undefined' && typeof fs.statSync === 'function');
} catch (e) { useNodeFs = false; }
function evalScript(script, cb) {
    if (window.__adobe_cep__) {
        window.__adobe_cep__.evalScript(script, function (code, result) {
            if (cb) cb(code, result);
        });
    } else if (cb) { cb(-1, null); }
}
// On load, DELETE the leftover command mailbox entry without applying
// it: any command present at panel load predates this Photoshop session
// (written while PS was closed or before the restart) and re-applying it
// would overwrite the user's colors. Polling is gated on this claim
// finishing so no race can re-apply the stale command.
var claimed = false;
function claimStaleCmd() {
    var s = "var f=new File('" + dir + "/cmd.txt');" +
        "if(f.exists){f.remove();}true;";
    evalScript(s, function (code, result) { claimed = true; });
}
claimStaleCmd();
// Diagnostics: record whether Node fs is usable in this CEP runtime so
// Colorink can tell which polling path is active.
(function () {
    var d0 = String(dir).replace(/\\/g, "/");
    try {
        if (useNodeFs) {
            fs.writeFileSync(d0 + '/nodefs.txt', '1');
        } else {
            evalScript("var f=new File('" + d0 + "/nodefs.txt');f.open('w');f.write('0');f.close();", function () {});
        }
    } catch (e) {}
})();
var tick = 0;
var FAST_MS = 100;     // command mailbox check interval
var STATE_EVERY = 10;  // state read-back every 10 ticks (1 s)
var BEAT_EVERY = 40;   // heartbeat + panel_version every 40 ticks (4 s)
var cmdMtime = 0;
function poll() {
    try {
        tick++;
        var d = String(dir).replace(/\\/g, "/");
        var doState = (tick % STATE_EVERY === 0);
        var doBeat  = (tick % BEAT_EVERY === 0);
        var changed = false;
        if (useNodeFs) {
            try {
                var st = fs.existsSync(d + '/cmd.txt') ? fs.statSync(d + '/cmd.txt') : null;
                var m = st ? (st.mtimeMs || st.mtime.getTime()) : 0;
                changed = (m !== cmdMtime);
                cmdMtime = m;
            } catch (e) { changed = false; }
        }
        if (useNodeFs && !changed && !doState && !doBeat) {
            return;  // zero ExtendScript-engine traffic while idle
        }
        // Dedup state lives in applied.txt, read and written by the
        // script itself: the evalScript return value is unreliable in
        // some CEP builds, so the panel must NOT depend on it to
        // remember which command was already applied.
        var script =
            "var d='" + d + "';" +
            "var ap=new File(d+'/applied.txt');var last='';" +
            "if(ap.exists){ap.open('r');last=ap.read();ap.close();}" +
            "var line='';var cf=new File(d+'/cmd.txt');" +
            "if(cf.exists){cf.open('r');line=cf.read();cf.close();}" +
            "var parts=String(line).split('|');" +
            "var applied=false;" +
            "if(parts.length>=2&&parts[0]!=last){" +
            "if(parts[1]=='swap'){" +
            "var t=app.foregroundColor;" +
            "app.foregroundColor=app.backgroundColor;" +
            "app.backgroundColor=t;" +
            "}else if(parts.length>=5){" +
            "var c=new SolidColor();" +
            "c.rgb.red=parseInt(parts[2],10);" +
            "c.rgb.green=parseInt(parts[3],10);" +
            "c.rgb.blue=parseInt(parts[4],10);" +
            "if(parts[1]=='1'){app.backgroundColor=c;}" +
            "else{app.foregroundColor=c;}" +
            "}" +
            "var a=new File(d+'/applied.txt');a.open('w');" +
            "a.write(parts[0]);a.close();" +
            "applied=true;" +
            "}" +
            (changed || doState ? "if(applied||true){" : "if(applied){") +
            "var fg=app.foregroundColor;var bg=app.backgroundColor;" +
            "var s=new File(d+'/state.txt');s.open('w');" +
            "s.write(Math.round(fg.rgb.red)+'|'+Math.round(fg.rgb.green)+'|'+Math.round(fg.rgb.blue)+'|'+Math.round(bg.rgb.red)+'|'+Math.round(bg.rgb.green)+'|'+Math.round(bg.rgb.blue));" +
            "s.close();" +
            "}";
        if (doBeat) {
            // Keep PANEL_VERSION in sync with PANEL_VERSION_FILENAME below
            script +=
            "var pv=new File(d+'/panel_version.txt');pv.open('w');" +
            "pv.write('4');pv.close();" +
            "var h=new File(d+'/heartbeat.txt');h.open('w');" +
            "h.write(String(new Date().getTime()));h.close();";
        }
        evalScript(script, function (code, result) {});
    } catch (e) {}
}
setInterval(function () {
    if (!claimed) { return; }  // wait for the stale-command claim
    poll();
}, FAST_MS);
</script>
</body>
</html>
"""


class PhotoshopScriptBridge:
    """Hidden-CEP-panel bridge controlling one green/portable Photoshop."""

    def __init__(self, ps_dir: str) -> None:
        self.ps_dir: str = ps_dir
        self.dir: str = os.path.join(ps_dir, *_CEP_RELATIVE_DIR.split(os.sep))
        self._panel_version_cache: tuple[float, int | None] | None = None

    # -- deployment ---------------------------------------------------------

    def is_deployed(self) -> bool:
        return os.path.isfile(self._manifest_path())

    def _manifest_path(self) -> str:
        # CEP requires the manifest inside a CSXS subfolder of the
        # extension root.
        return os.path.join(self.dir, "CSXS", MANIFEST_FILENAME)

    def deploy(self) -> bool:
        """Write the hidden CEP extension (manifest + panel).

        The manifest and panel HTML are always rewritten so updates
        propagate to already-deployed installs.
        """
        try:
            os.makedirs(self.dir, exist_ok=True)
            os.makedirs(os.path.join(self.dir, "CSXS"), exist_ok=True)
            with open(self._manifest_path(), "w",
                      encoding="utf-8", newline="\n") as f:
                f.write(_MANIFEST_TEMPLATE.format(ext_id=EXTENSION_ID))
            with open(os.path.join(self.dir, INDEX_FILENAME), "w",
                      encoding="utf-8", newline="\n") as f:
                f.write(_INDEX_TEMPLATE)
            self._suppress_script_warning()
            return True
        except OSError:
            return False

    @staticmethod
    def cleanup_other_installs(keep_ps_dir: str) -> list[str]:
        """Remove ColorinkBridge from every *other* running Photoshop
        install so a stale copy cannot auto-load there.

        A deployed extension auto-loads whenever that Photoshop starts —
        independent of which instance Colorink is currently syncing with.
        With a fixed extension ID (``com.colorink.bridge``) and multiple
        installs, both panels fight over the shared CEP runtime and the
        single-threaded ExtendScript engine, which shows up as
        load-order-dependent breakage (the "open genuine PS first, then
        green PS" workaround).  Keeping only the target install's copy
        avoids the fight entirely.

        Returns the list of install dirs whose stale copy was removed.
        """
        removed: list[str] = []
        try:
            from core.photoshop_instances import detect_instances
            keep = os.path.normcase(os.path.abspath(keep_ps_dir))
            for inst in detect_instances():
                other = os.path.dirname(os.path.abspath(inst.exe_path))
                if os.path.normcase(other) == keep:
                    continue
                ext = os.path.join(other, *_CEP_RELATIVE_DIR.split(os.sep))
                if (os.path.isdir(ext)
                        and os.path.isfile(os.path.join(
                            ext, "CSXS", MANIFEST_FILENAME))):
                    shutil.rmtree(ext, ignore_errors=True)
                    removed.append(other)
        except Exception:
            pass
        return removed

    def remove(self) -> bool:
        """Delete the deployed extension directory for this install.

        The running Photoshop keeps the already-loaded panel until it is
        restarted, so callers should surface a "restart Photoshop" hint
        after a successful removal.
        """
        try:
            if os.path.isdir(self.dir) and os.path.isfile(self._manifest_path()):
                shutil.rmtree(self.dir, ignore_errors=True)
            return not (os.path.isdir(self.dir)
                        and os.path.isfile(self._manifest_path()))
        except OSError:
            return False

    def _write_if_missing(self, filename: str, content: str) -> None:
        path = os.path.join(self.dir, filename)
        if not os.path.isfile(path):
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)

    def _suppress_script_warning(self) -> None:
        """PSUserConfig.txt with WarnRunningScripts 0 so Photoshop never
        asks before running scripts (harmless for CEP; covers fallbacks)."""
        try:
            appdata = os.environ.get("APPDATA", "")
            for name in _settings_names(self.ps_dir):
                settings_dir = os.path.join(
                    appdata, "Adobe", name, name + " Settings")
                if not os.path.isdir(settings_dir):
                    continue
                cfg = os.path.join(settings_dir, "PSUserConfig.txt")
                if os.path.isfile(cfg):
                    with open(cfg, encoding="utf-8", errors="ignore") as f:
                        if "WarnRunningScripts" in f.read():
                            continue
                    with open(cfg, "a", encoding="ascii") as f:
                        f.write("\nWarnRunningScripts 0\n")
                else:
                    with open(cfg, "w", encoding="ascii") as f:
                        f.write("WarnRunningScripts 0\n")
        except OSError:
            pass

    # -- write side ----------------------------------------------------------

    def _write_cmd(self, content: str) -> bool:
        try:
            os.makedirs(self.dir, exist_ok=True)
            tmp = os.path.join(self.dir, CMD_FILENAME + ".tmp")
            with open(tmp, "w", encoding="ascii", newline="\n") as f:
                f.write(content)
            os.replace(tmp, os.path.join(self.dir, CMD_FILENAME))
            return True
        except OSError:
            return False

    def send_color(self, token: str, color_index: int,
                   r: int, g: int, b: int) -> bool:
        """Write a color command; the panel applies it on its next poll.

        Uses a temp file + atomic replace so the panel never reads a
        half-written line. *token* must differ from the previous one for
        the panel to re-apply the command.
        """
        return self._write_cmd(
            f"{token}|{int(color_index)}|{int(r)}|{int(g)}|{int(b)}")

    def send_swap(self, token: str) -> bool:
        """Swap Photoshop's foreground/background (like pressing X).

        The panel performs the exchange atomically in one script run.
        """
        return self._write_cmd(f"{token}|swap")

    # -- read side ------------------------------------------------------------

    def read_state(self) -> dict | None:
        """Return ``{"fg": {...}, "bg": {...}}`` from the panel's last
        state mirror, or ``None`` when nothing has been written yet."""
        try:
            with open(os.path.join(self.dir, STATE_FILENAME),
                      encoding="ascii") as f:
                parts = f.read().split("|")
            if len(parts) >= 6:
                return {
                    "fg": {"r": int(float(parts[0])), "g": int(float(parts[1])),
                           "b": int(float(parts[2]))},
                    "bg": {"r": int(float(parts[3])), "g": int(float(parts[4])),
                           "b": int(float(parts[5]))},
                }
        except (OSError, ValueError):
            pass
        return None

    # -- liveness -------------------------------------------------------------

    def panel_version(self, max_age: float = 5.0) -> int | None:
        """Version of the *running* panel, from ``panel_version.txt``.

        The panel rewrites this file on every poll, so a missing value or
        an older version means the loaded panel predates the current
        deploy (Photoshop has not been restarted since) — callers show a
        "restart Photoshop" hint. Cached *max_age* seconds because this
        is polled on every status snapshot.
        """
        now = time.monotonic()
        if self._panel_version_cache is not None:
            ts, val = self._panel_version_cache
            if now - ts < max_age:
                return val
        val: int | None = None
        try:
            with open(os.path.join(self.dir, PANEL_VERSION_FILENAME),
                      encoding="ascii") as f:
                val = int(f.read().strip())
        except (OSError, ValueError):
            val = None
        self._panel_version_cache = (now, val)
        return val

    def heartbeat_age(self) -> float | None:
        """Seconds since the panel's last heartbeat, or ``None``."""
        try:
            mtime = os.path.getmtime(
                os.path.join(self.dir, HEARTBEAT_FILENAME))
            return time.time() - mtime
        except OSError:
            return None

    def is_alive(self, max_age: float = 8.0) -> bool:
        age = self.heartbeat_age()
        return age is not None and age <= max_age


def _settings_names(ps_dir: str) -> list[str]:
    """Roaming settings folder names for a Photoshop install dir.

    The roaming profile folder is named after the install folder's
    display name (e.g. ``Adobe Photoshop CC 2019``), which usually
    matches the install folder name.
    """
    name = os.path.basename(os.path.normpath(ps_dir))
    names = [name]
    if name.startswith("Adobe "):
        names.append(name[len("Adobe "):])
    return names
