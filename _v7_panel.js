
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
        "pv.write('7');pv.close();" +
        "var h=new File(d+'/heartbeat" + suff + ".txt');h.open('w');" +
        "h.write(String(new Date().getTime()));h.close();";
}
function applyScript(d, parts) {
    // parts: <token>|<pid>|<index>|<r>|<g>|<b>  or  <token>|<pid>|swap
    var s = "var d='" + d + "';";
    if (parts[2] === 'swap') {
        s += "var t=app.foregroundColor;" +
            "app.foregroundColor=app.backgroundColor;" +
            "app.backgroundColor=t;";
    } else {
        s += "var c=new SolidColor();" +
            "c.rgb.red=" + parseInt(parts[3], 10) + ";" +
            "c.rgb.green=" + parseInt(parts[4], 10) + ";" +
            "c.rgb.blue=" + parseInt(parts[5], 10) + ";" +
            "if(parseInt(parts[2],10)==1){app.backgroundColor=c;}" +
            "else{app.foregroundColor=c;}";
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
                var m = st ? (st.mtimeMs || st.mtime.getTime()) : 0;
                var changed = (m !== cmdMtime);
                cmdMtime = m;
                if (changed) {
                    var raw = fs.readFileSync(d + '/cmd.txt', 'utf8');
                    var parts = String(raw).split('|');
                    var forUs = (myPid === "" || parts[1] === myPid);
                    if (forUs && parts.length >= 3 && parts[0] !== lastToken) {
                        lastToken = parts[0];
                        evalScript(applyScript(d, parts), function () {});
                        return;  // apply already wrote state back
                    }
                }
            } catch (e) {}
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
                "var t=app.foregroundColor;" +
                "app.foregroundColor=app.backgroundColor;" +
                "app.backgroundColor=t;" +
                "}else if(parts.length>=6){" +
                "var c=new SolidColor();" +
                "c.rgb.red=parseInt(parts[3],10);" +
                "c.rgb.green=parseInt(parts[4],10);" +
                "c.rgb.blue=parseInt(parts[5],10);" +
                "if(parseInt(parts[2],10)==1){app.backgroundColor=c;}" +
                "else{app.foregroundColor=c;}" +
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
                "pv.write('7');pv.close();" +
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
