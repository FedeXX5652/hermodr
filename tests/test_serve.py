import json
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from os.path import join
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from conftest import NOW, STORY_SOLAR, World

from hermodr.db import connect
from hermodr.run import run
from hermodr.serve import Handler


def test_votes_are_saved(settings):
    w = World()
    w.add("a", "solar", STORY_SOLAR, 5)
    con = connect(settings.data_dir)
    eid, _ = run(con, settings, use_model=False, transport=w.transport(), now=NOW, log=lambda *a: None)
    story = con.execute("SELECT story_id FROM edition_items").fetchone()[0]

    Handler.data_dir = settings.data_dir
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=join(settings.data_dir, "editions")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def post(d):
        try:
            return urlopen(Request(f"{base}/api/event", json.dumps(d).encode(), {"Content-Type": "application/json"})).status
        except HTTPError as e:
            return e.code
    try:
        assert post({"edition": eid, "story": story, "kind": "useful"}) == 204
        assert post({"edition": eid, "story": 999, "kind": "useful"}) == 404
        assert post({"edition": eid, "story": story, "kind": "anything"}) == 400
        assert "Villa Rosales" in urlopen(f"{base}/").read().decode("utf-8")
    finally:
        srv.shutdown()
    assert con.execute("SELECT kind FROM events").fetchall()[0][0] == "useful"
