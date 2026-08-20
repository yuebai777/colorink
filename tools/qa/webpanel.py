"""panel：本地网页面板（127.0.0.1，只读最新记录 + 手测打勾）。"""
from __future__ import annotations

import json
import urllib.parse

from . import common, report


def _latest_or_new() -> dict:
    try:
        return common.load_run(None)
    except SystemExit:
        return common.new_run()


class _Handler:
    _handler = None

    @classmethod
    def make(cls):
        if cls._handler is None:
            from http.server import BaseHTTPRequestHandler

            common_mod = common

            def _state_with_phases():
                state = dict(_latest_or_new())
                state["phases"] = common_mod.load_checklist()["phases"]
                return state

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, fmt, *args):  # 安静
                    pass

                def _send(self, code, ctype, body):
                    self.send_response(code)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def _json(self, obj):
                    self._send(200, "application/json; charset=utf-8",
                               json.dumps(obj, ensure_ascii=False).encode("utf-8"))

                def do_GET(self):
                    parsed = urllib.parse.urlparse(self.path)
                    p = parsed.path
                    if p == "/":
                        body = (common_mod.QA_DIR / "web" / "index.html").read_bytes()
                        self._send(200, "text/html; charset=utf-8", body)
                    elif p == "/api/state":
                        self._json(_state_with_phases())
                    elif p == "/api/runs":
                        runs = sorted((common_mod.RUNS_DIR.glob("run-*.json"))
                                      if common_mod.RUNS_DIR.exists() else [])
                        self._json([
                            {"id": f.stem[4:], "path": str(f)} for f in runs
                        ])
                    elif p == "/api/report":
                        self._send(200, "text/plain; charset=utf-8",
                                   report.generate(_latest_or_new()).encode("utf-8"))
                    elif p.startswith("/shots/"):
                        rel = p[len("/shots/"):]
                        f = (common_mod.SHOTS_DIR / rel).resolve()
                        if f.exists() and str(f).startswith(str(common_mod.SHOTS_DIR.resolve())):
                            self._send(200, "image/png", f.read_bytes())
                        else:
                            self._send(404, "text/plain", b"not found")
                    else:
                        self._send(404, "text/plain", b"not found")

                def do_POST(self):
                    if self.path != "/api/item":
                        self._send(404, "text/plain", b"not found")
                        return
                    n = int(self.headers.get("Content-Length") or 0)
                    data = json.loads(self.rfile.read(n) or b"{}")
                    state = _latest_or_new()
                    state.setdefault("manual", {})[data["item_id"]] = {
                        "result": data["result"],
                        "note": data.get("note", ""),
                        "ts": common_mod.now_ts(),
                    }
                    common_mod.save_run(state)
                    self._json({"ok": True})

            cls._handler = Handler
        return cls._handler


def run_panel(state: dict, args) -> None:
    from http.server import ThreadingHTTPServer

    port = getattr(args, "port", 8799) or 8799
    srv = ThreadingHTTPServer(("127.0.0.1", port), _Handler.make())
    print(f"  面板已启动: http://127.0.0.1:{port}/  （Ctrl+C 停止）")
    print(f"  当前记录: run {state['run_id']}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  面板已停止。")
