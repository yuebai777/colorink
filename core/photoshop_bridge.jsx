// Photoshop ↔ Palette Lite bridge  (non-blocking edition)
// Run via File → Scripts → Browse.  Stops when stop file appears.
// 
// Uses a tight app.refresh()+$.sleep(5) loop so Photoshop stays
// responsive — the 5ms sleep is imperceptible, and the colour file
// is only written every 30 ticks (~150ms) to avoid excessive I/O.
// -------------------------------------------------------------

#target photoshop

var tempDir = Folder.temp;
var colorFile = new File(tempDir + "/palette_lite_ps_color.txt");
var cmdFile   = new File(tempDir + "/palette_lite_ps_cmd.txt");
var stopFile  = new File(tempDir + "/palette_lite_ps_stop.txt");

var lastWrittenColor = "";
var lastCmdMtime = 0;
var tick = 0;

// Clean stale stop file
if (stopFile.exists) stopFile.remove();

while (true) {
    // --- exit signal (check every tick) ---
    if (tick % 10 === 0 && stopFile.exists) {
        stopFile.remove();
        break;
    }

    // --- process incoming colour command (fast — every tick) ---
    if (cmdFile.exists) {
        try {
            var mtime = cmdFile.modified ? cmdFile.modified.getTime() : 0;
            if (mtime !== lastCmdMtime) {
                lastCmdMtime = mtime;
                cmdFile.open("r");
                if (!cmdFile.error) {
                    var cmd = cmdFile.readln();
                    cmdFile.close();
                    var parts = cmd.split(" ");
                    if (parts.length >= 4 && parts[0] === "SET") {
                        var r = parseInt(parts[1]);
                        var g = parseInt(parts[2]);
                        var b = parseInt(parts[3]);
                        if (r >= 0 && r <= 255 && g >= 0 && g <= 255 && b >= 0 && b <= 255) {
                            app.foregroundColor.rgb.red   = r;
                            app.foregroundColor.rgb.green = g;
                            app.foregroundColor.rgb.blue  = b;
                        }
                    }
                } else {
                    cmdFile.close();
                }
            }
        } catch (e) {}
    }

    // --- write current colour (throttled to ~150ms) ---
    if (tick % 30 === 0) {
        try {
            var c = app.foregroundColor.rgb;
            var cr = Math.round(c.red);
            var cg = Math.round(c.green);
            var cb = Math.round(c.blue);
            var current = cr + " " + cg + " " + cb;
            if (current !== lastWrittenColor) {
                colorFile.open("w");
                if (!colorFile.error) {
                    colorFile.write(current);
                    colorFile.close();
                    lastWrittenColor = current;
                } else {
                    colorFile.close();
                }
            }
        } catch (e) {}
    }

    tick++;
    app.refresh();
    $.sleep(5);
}
