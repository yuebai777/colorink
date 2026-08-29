"""User-level CEP bridge — the single sync path for every Photoshop.

One hidden CEP background extension is deployed into the **user-level**
CEP extensions folder (``%APPDATA%\\Adobe\\CEP\\extensions\\ColorinkBridge``)
which every Photoshop edition — registered (genuine), green/portable or
cracked-with-COM — auto-loads on startup.  There is exactly one deployed
copy for the whole machine, so the old per-install copies (one per
Photoshop directory) and their fixed-extension-ID fights are gone; the
user-level folder needs no admin rights.

Multi-instance routing is done by **PID**: command lines carry the target
Photoshop's process id, each panel applies only commands addressed to its
own ``$.pid``, and every panel writes per-PID state/heartbeat/version
files.  Colorink reads the files of the instance it is syncing with.

The extension manifest mirrors Adobe's own ``com.adobe.Butler.backend``
structure (``Version 7.0``, ``AutoVisible false`` + ``StartOn
applicationActivate``, ``Type Custom``) — the only manifest pattern proven
to auto-load on portable builds.  The panel's JS runs in a persistent CEF
runtime with Node.js enabled.

File protocol (ASCII, one line, ``|``-separated), all inside the
extension folder:

- ``cmd.txt``               ``<token>|<pid>|<index>|<r>|<g>|<b>``  — write
                            target for the Photoshop with that pid
                            (``<token>|<pid>|swap`` = exchange fg/bg)
- ``state_<pid>.txt``       ``<fg_r>|<fg_g>|<fg_b>|<bg_r>|<bg_g>|<bg_b>``
- ``heartbeat_<pid>.txt``   millisecond epoch, refreshed every 4 s
- ``panel_version_<pid>.txt`` panel protocol version, refreshed every 4 s
- ``applied_<pid>.txt``     last-applied token (fallback-path dedup)
- ``nodefs.txt``            diagnostics: '1' = Node fs path active

Deploying only takes effect after Photoshop restarts; ``is_alive(pid)``
reports whether the panel loaded in that Photoshop is polling.
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
# detect. The panel rewrites panel_version_<pid>.txt every 4 s; a running
# panel with an older protocol keeps writing the old value (or nothing),
# so Colorink can tell "deployed file is new" from "running panel is
# new" — only the latter clears the "restart Photoshop" hint.
PANEL_VERSION: Final = 9

EXTENSION_ID: Final = "com.colorink.bridge"
EXTENSION_DIR_NAME: Final = "ColorinkBridge"

# Legacy per-install deploy location (v1..v5) — cleaned up on connect.
_CEP_RELATIVE_DIR: Final = os.path.join(
    "Required", "CEP", "extensions", EXTENSION_DIR_NAME)


def user_cep_dir() -> str:
    """User-level CEP extensions folder shared by every Adobe host."""
    appdata = os.environ.get("APPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(appdata, "Adobe", "CEP", "extensions",
                        EXTENSION_DIR_NAME)


def _pid_filename(base: str, pid: int) -> str:
    return f"{base}_{int(pid)}.txt"


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

# The panel uses a two-path design. All work runs through evalScript into
# Photoshop's ExtendScript engine, but the command-mailbox *check* prefers
# Node fs (the manifest enables --enable-nodejs), so the panel does not
# touch Photoshop's single-threaded script engine just to look for work:
#
#   * command mailbox checked every 100 ms via Node fs.statSync /
#     readFileSync (zero ExtendScript traffic while idle); a command
#     addressed to this instance (PID match) is applied on the next tick
#     and the state is read back immediately,
#   * state read-back every 5 ticks (0.5 s),
#   * heartbeat + panel_version every 40 ticks (4 s) — is_alive() in
#     Colorink tolerates an 8 s gap, so 4 s is ample,
#   * fallback: CEP builds without a working Node fs run one lightweight
#     evalScript per tick that reads cmd.txt only (writes nothing when
#     idle) instead of the old five-file-write-every-tick script that
#     starved TourBox / Coolorus.
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
    useNodeFs = (typeof fs !== 'undefined' && typeof fs.statSync === 'function' && typeof fs.readFileSync === 'function');
} catch (e) { useNodeFs = false; }
function evalScript(script, cb) {
    if (window.__adobe_cep__) {
        window.__adobe_cep__.evalScript(script, function (code, result) {
            if (cb) cb(code, result);
        });
    } else if (cb) { cb(-1, null); }
}
// Resolve this Photoshop's PID for multi-instance routing. Not every
// ExtendScript engine exposes $.pid (green builds return undefined), so
// try the CEP host environment first (appPid), then $.pid via
// evalScript. When neither works the panel falls back to shared
// (non-suffixed) bridge files — sync still works, but every running
// instance shares one state (no per-instance routing).
var myPid = "";
var panelDir0 = String(dir).replace(/\\/g, "/");
try {
    var env = window.__adobe_cep__.getHostEnvironment();
    if (env && env.appPid !== undefined && env.appPid !== null) {
        myPid = String(env.appPid).trim();
    }
} catch (e) {}
evalScript("try{var pv=$.pid;pv!==undefined&&pv!==null?String(pv):''}catch(e){''}", function (code, result) {
    if (myPid === "" && code === 0 && result !== null && result !== undefined) {
        var v = String(result).trim();
        if (v !== "" && v !== "undefined") { myPid = v; }
    }
    // Diagnostics: record which pid the panel resolved to ('' = shared
    // mode) so Colorink / support can see the routing mode.
    try {
        if (useNodeFs) {
            fs.writeFileSync(panelDir0 + '/mypid.txt', myPid === "" ? "(shared)" : myPid);
        }
    } catch (e) {}
});
function pidSuffix() { return myPid !== "" ? "_" + myPid : ""; }
// On load, DELETE a command that predates this panel (a leftover from
// before Photoshop restarted) so it is not re-applied. A command written
// AFTER this panel started (mtime >= panelStart) must survive — with
// multiple instances sharing cmd.txt this is what stops a freshly
// started Photoshop from eating a live command.
var panelStart = Date.now();
var claimed = false;
function claimStaleCmd() {
    try {
        var d0 = String(dir).replace(/\\/g, "/");
        if (useNodeFs) {
            var st = fs.existsSync(d0 + '/cmd.txt') ? fs.statSync(d0 + '/cmd.txt') : null;
            var m = st ? (st.mtimeMs || st.mtime.getTime()) : 0;
            if (m > 0 && m < panelStart) {
                try { fs.unlinkSync(d0 + '/cmd.txt'); } catch (e) {}
            }
            claimed = true;
        } else {
            var s = "var f=new File('" + d0 + "/cmd.txt');" +
                "if(f.exists){var t=f.modified?f.modified.getTime():0;if(t>0&&t<" + panelStart + "){f.remove();}}true;";
            evalScript(s, function (code, result) { claimed = true; });
        }
    } catch (e) { claimed = true; }
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
var STATE_EVERY = 5;   // state read-back every 5 ticks (0.5 s)
var BEAT_EVERY = 40;   // heartbeat + panel_version every 40 ticks (4 s)
var cmdMtime = 0;
var lastToken = "";

function stateScript(d) {
    var suff = pidSuffix();
    return "var d='" + d + "';" +
        "var fg=app.foregroundColor;var bg=app.backgroundColor;" +
        "var s=new File(d+'/state" + suff + ".txt');s.open('w');" +
        "s.write(Math.round(fg.rgb.red)+'|'+Math.round(fg.rgb.green)+'|'+Math.round(fg.rgb.blue)+'|'+Math.round(bg.rgb.red)+'|'+Math.round(bg.rgb.green)+'|'+Math.round(bg.rgb.blue));" +
        "s.close();";
}
function beatScript(d) {
    // Keep PANEL_VERSION in sync with PANEL_VERSION_FILENAME below
    var suff = pidSuffix();
    return "var d='" + d + "';" +
        "var pv=new File(d+'/panel_version" + suff + ".txt');pv.open('w');" +
        "pv.write('9');pv.close();" +
        "var h=new File(d+'/heartbeat" + suff + ".txt');h.open('w');" +
        "h.write(String(new Date().getTime()));h.close();";
}
function applyScript(d, parts) {
    // parts: <token>|<pid>|<index>|<r>|<g>|<b>  or  <token>|<pid>|swap
    // Mutate the existing SolidColor objects in place: constructing
    // `new SolidColor()` fails with "EvalScript error" on this green
    // build, while reading/mutating app.foregroundColor works.
    var s = "var d='" + d + "';";
    if (parts[2] === 'swap') {
        s += "var fg=app.foregroundColor;var bg=app.backgroundColor;" +
            "var fr=fg.rgb.red;var fg2=fg.rgb.green;var fb=fg.rgb.blue;" +
            "var br=bg.rgb.red;var bg3=bg.rgb.green;var bb=bg.rgb.blue;" +
            "fg.rgb.red=br;fg.rgb.green=bg3;fg.rgb.blue=bb;" +
            "bg.rgb.red=fr;bg.rgb.green=fg2;bg.rgb.blue=fb;";
    } else {
        var target = (parseInt(parts[2], 10) === 1) ? "app.backgroundColor" : "app.foregroundColor";
        s += "var c=" + target + ";" +
            "c.rgb.red=" + parseInt(parts[3], 10) + ";" +
            "c.rgb.green=" + parseInt(parts[4], 10) + ";" +
            "c.rgb.blue=" + parseInt(parts[5], 10) + ";";
    }
    // Apply writes the state back immediately.
    return s + stateScript(d);
}
function poll() {
    try {
        tick++;
        var d = String(dir).replace(/\\/g, "/");
        var doState = (tick % STATE_EVERY === 0);
        var doBeat  = (tick % BEAT_EVERY === 0);
        if (useNodeFs) {
            try {
                var st = fs.existsSync(d + '/cmd.txt') ? fs.statSync(d + '/cmd.txt') : null;
                var m = 0;
                try {
                    m = st ? (st.mtimeMs !== undefined ? st.mtimeMs : st.mtime.getTime()) : 0;
                } catch (e2) { m = 0; }
                var changed = (m !== cmdMtime);
                cmdMtime = m;
                if (changed) {
                    var raw = fs.readFileSync(d + '/cmd.txt', 'utf8');
                    var parts = String(raw).split('|');
                    var forUs = (myPid === "" || parts[1] === myPid);
                    if (forUs && parts.length >= 3 && parts[0] !== lastToken) {
                        lastToken = parts[0];
                        evalScript(applyScript(d, parts), function (code, result) {
                            // Diagnostics: surface any apply failure.
                            try {
                                if (code !== 0) {
                                    fs.writeFileSync(d + '/apply_error.txt', "code=" + code + " result=" + String(result) + " cmd=" + raw);
                                }
                            } catch (e3) {}
                        });
                        return;  // apply already wrote state back
                    }
                }
            } catch (e) {
                // Diagnostics: never silently swallow a broken poll path.
                try {
                    fs.writeFileSync(d + '/error.txt', String(e && e.message ? e.message : e));
                } catch (e4) {}
            }
        } else {
            // Fallback: one lightweight script per tick — reads cmd.txt,
            // applies only when addressed to this instance (or in shared
            // mode when myPid is empty) and un-applied, writes nothing
            // while idle. State/heartbeat are throttled.
            var suff = pidSuffix();
            var script =
                "var d='" + d + "';var my='" + myPid + "';" +
                "var ap=new File(d+'/applied" + suff + ".txt');var last='';" +
                "if(ap.exists){ap.open('r');last=ap.read();ap.close();}" +
                "var line='';var cf=new File(d+'/cmd.txt');" +
                "if(cf.exists){cf.open('r');line=cf.read();cf.close();}" +
                "var parts=String(line).split('|');" +
                "var applied=false;" +
                "if(parts.length>=3&&(my==''||parts[1]==my)&&parts[0]!=last){" +
                "if(parts[2]=='swap'){" +
                "var fg=app.foregroundColor;var bg=app.backgroundColor;" +
                "var fr=fg.rgb.red;var fg2=fg.rgb.green;var fb=fg.rgb.blue;" +
                "var br=bg.rgb.red;var bg3=bg.rgb.green;var bb=bg.rgb.blue;" +
                "fg.rgb.red=br;fg.rgb.green=bg3;fg.rgb.blue=bb;" +
                "bg.rgb.red=fr;bg.rgb.green=fg2;bg.rgb.blue=fb;" +
                "}else if(parts.length>=6){" +
                "var c=(parseInt(parts[2],10)==1)?app.backgroundColor:app.foregroundColor;" +
                "c.rgb.red=parseInt(parts[3],10);" +
                "c.rgb.green=parseInt(parts[4],10);" +
                "c.rgb.blue=parseInt(parts[5],10);" +
                "}" +
                "var a=new File(d+'/applied" + suff + ".txt');a.open('w');" +
                "a.write(parts[0]);a.close();" +
                "applied=true;" +
                "}" +
                (doState ? "if(applied||true){" : "if(applied){") +
                "var fg=app.foregroundColor;var bg=app.backgroundColor;" +
                "var s=new File(d+'/state" + suff + ".txt');s.open('w');" +
                "s.write(Math.round(fg.rgb.red)+'|'+Math.round(fg.rgb.green)+'|'+Math.round(fg.rgb.blue)+'|'+Math.round(bg.rgb.red)+'|'+Math.round(bg.rgb.green)+'|'+Math.round(bg.rgb.blue));" +
                "s.close();" +
                "}" +
                (doBeat ?
                "var pv=new File(d+'/panel_version" + suff + ".txt');pv.open('w');" +
                "pv.write('9');pv.close();" +
                "var h=new File(d+'/heartbeat" + suff + ".txt');h.open('w');" +
                "h.write(String(new Date().getTime()));h.close();" : "");
            evalScript(script, function () {});
            return;
        }
        if (doState) { evalScript(stateScript(d), function () {}); }
        if (doBeat)  { evalScript(beatScript(d), function () {}); }
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
    """User-level CEP bridge shared by every Photoshop install/version.

    Exactly one deployed copy exists (user-level CEP folder), and all
    instances route through it by PID. Colorink talks to the instance it
    selected by addressing commands to that instance's process id and
    reading that instance's per-PID state/heartbeat files.
    """

    def __init__(self) -> None:
        self.dir: str = user_cep_dir()
        self._panel_version_cache: dict[int, tuple[float, int | None]] = {}

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
        propagate to already-deployed installs. Takes effect on the next
        Photoshop restart.
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
            return True
        except OSError:
            return False

    @staticmethod
    def cleanup_install_dirs() -> list[str]:
        """Remove legacy per-install (application-level) ColorinkBridge
        copies from every *running* Photoshop install.

        v1..v5 deployed one extension per Photoshop directory; those
        copies auto-load whenever that Photoshop starts and, with the
        shared user-level panel now in place, would run a second, older
        panel in the same process. Removing them keeps exactly one panel
        per instance.
        """
        removed: list[str] = []
        try:
            from core.photoshop_instances import detect_instances
            for inst in detect_instances():
                ps_dir = os.path.dirname(os.path.abspath(inst.exe_path))
                ext = os.path.join(ps_dir, *_CEP_RELATIVE_DIR.split(os.sep))
                if (os.path.isdir(ext)
                        and os.path.isfile(os.path.join(
                            ext, "CSXS", MANIFEST_FILENAME))):
                    shutil.rmtree(ext, ignore_errors=True)
                    removed.append(ps_dir)
        except Exception:
            pass
        return removed

    def remove(self) -> bool:
        """Delete the user-level extension directory.

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

    def send_color(self, token: str, pid: int, color_index: int,
                   r: int, g: int, b: int) -> bool:
        """Write a color command for the Photoshop with *pid*; the panel
        loaded there applies it on its next poll (<=100 ms). Uses a temp
        file + atomic replace so a panel never reads a half-written line.
        *token* must differ from the previous one for re-application.
        """
        return self._write_cmd(
            f"{token}|{int(pid)}|{int(color_index)}|{int(r)}|{int(g)}|{int(b)}")

    def send_swap(self, token: str, pid: int) -> bool:
        """Swap that Photoshop's foreground/background (like pressing X)."""
        return self._write_cmd(f"{token}|{int(pid)}|swap")

    # -- read side ------------------------------------------------------------

    def read_state(self, pid: int) -> dict | None:
        """Return ``{"fg": {...}, "bg": {...}}`` from the panel loaded in
        the Photoshop with *pid*, or ``None`` when nothing is written.

        Per-PID file preferred; the shared (non-suffixed) file is the
        fallback for panels that could not resolve their PID (shared
        mode — see the panel JS).
        """
        for path in (os.path.join(self.dir, _pid_filename(STATE_FILENAME, pid)),
                     os.path.join(self.dir, STATE_FILENAME)):
            try:
                with open(path, encoding="ascii") as f:
                    parts = f.read().split("|")
                if len(parts) >= 6:
                    return {
                        "fg": {"r": int(float(parts[0])), "g": int(float(parts[1])),
                               "b": int(float(parts[2]))},
                        "bg": {"r": int(float(parts[3])), "g": int(float(parts[4])),
                               "b": int(float(parts[5]))},
                    }
            except (OSError, ValueError):
                continue
        return None

    # -- liveness -------------------------------------------------------------

    def panel_version(self, pid: int, max_age: float = 5.0) -> int | None:
        """Version of the *running* panel in that Photoshop, from
        ``panel_version_<pid>.txt``. Cached *max_age* seconds because this
        is polled on every status snapshot."""
        now = time.monotonic()
        cached = self._panel_version_cache.get(pid)
        if cached is not None:
            ts, val = cached
            if now - ts < max_age:
                return val
        val: int | None = None
        for path in (os.path.join(
                        self.dir, _pid_filename(PANEL_VERSION_FILENAME, pid)),
                     os.path.join(self.dir, PANEL_VERSION_FILENAME)):
            try:
                with open(path, encoding="ascii") as f:
                    val = int(f.read().strip())
                break
            except (OSError, ValueError):
                val = None
                continue
        self._panel_version_cache[pid] = (now, val)
        return val

    def heartbeat_age(self, pid: int) -> float | None:
        """Seconds since that panel's last heartbeat, or ``None``.

        Per-PID file preferred; shared file is the fallback (shared
        mode — see the panel JS).
        """
        for path in (os.path.join(
                        self.dir, _pid_filename(HEARTBEAT_FILENAME, pid)),
                     os.path.join(self.dir, HEARTBEAT_FILENAME)):
            try:
                mtime = os.path.getmtime(path)
                return time.time() - mtime
            except OSError:
                continue
        return None

    def is_alive(self, pid: int, max_age: float = 8.0) -> bool:
        age = self.heartbeat_age(pid)
        return age is not None and age <= max_age
