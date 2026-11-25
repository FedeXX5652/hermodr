"""Local server to read the editions and keep the clicks and votes. Standard library only."""
import json
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from os.path import join

from .db import connect

KINDS = {"click", "useful", "not_interested"}


class Handler(SimpleHTTPRequestHandler):
    data_dir = ""

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            con = connect(self.data_dir)
            last = con.execute("SELECT id FROM editions ORDER BY id DESC LIMIT 1").fetchone()
            con.close()
            self.send_response(302 if last else 404)
            self.send_header("Location", f"/{last['id']}/" if last else "/")
            self.end_headers()
            if not last:
                self.wfile.write(b"no editions yet: hermodr run")
            return
        super().do_GET()

    def do_POST(self):
        if self.path != "/api/event":
            return self.send_error(404)
        try:
            d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0))
            edition, story, kind = int(d["edition"]), int(d["story"]), str(d["kind"])
            if kind not in KINDS:
                raise ValueError(kind)
        except Exception:
            return self.send_error(400)
        con = connect(self.data_dir)
        ok = con.execute("SELECT 1 FROM edition_items WHERE edition_id = ? AND story_id = ?", (edition, story)).fetchone()
        if ok:
            con.execute("INSERT INTO events(edition_id, story_id, kind, at) VALUES (?, ?, ?, ?)",
                        (edition, story, kind, datetime.now(timezone.utc).isoformat()))
            con.commit()
        con.close()
        self.send_response(204 if ok else 404)
        self.end_headers()


def serve(settings, log=print):
    cfg = settings.general.get("serve", {})
    host, port = cfg.get("host", "127.0.0.1"), cfg.get("port", 8077)
    Handler.data_dir = settings.data_dir
    srv = ThreadingHTTPServer((host, port), partial(Handler, directory=join(settings.data_dir, "editions")))
    log(f"reading at http://{'localhost' if host == '127.0.0.1' else host}:{port}  (ctrl+c to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
