"""Stands in for llama-server in the tests: --help, /health, /v1/models and /v1/chat/completions.
Answers come in order from the json file in HERMODR_FAKE_ANSWERS."""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

FLAGS = ["-m", "--host", "--port", "-ngl", "-fa", "-c", "-np", "--jinja", "--reasoning"]

if "--help" in sys.argv:
    print("\n".join(f"{f} VALUE   something" for f in FLAGS))
    sys.exit(0)

port = int(sys.argv[sys.argv.index("--port") + 1])
answers = json.load(open(os.environ["HERMODR_FAKE_ANSWERS"], encoding="utf-8"))
requests_log = os.environ.get("HERMODR_FAKE_LOG")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, d):
        b = json.dumps(d).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._json(200, {"status": "ok"} if self.path == "/health" else {"data": [{"id": "fake"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if requests_log:
            with open(requests_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(body, ensure_ascii=False) + "\n")
        content = answers.pop(0) if answers else {"ok": True}
        self._json(200, {"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)},
                                      "finish_reason": "stop"}],
                         "timings": {"prompt_n": 100, "prompt_per_second": 900}})


HTTPServer(("127.0.0.1", port), H).serve_forever()
