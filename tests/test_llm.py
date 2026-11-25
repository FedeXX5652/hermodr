import hashlib
import json
import socket
import sys

import pytest

from hermodr.llm import Model, ModelError
from hermodr.settings import load


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def fake_model(project, tmp_path, monkeypatch):
    """model.toml pointing at the fake server, with a real sha256 of a dummy gguf."""
    gguf = tmp_path / "dummy.gguf"
    gguf.write_bytes(b"GGUF fake weights")
    script = __file__.replace("test_llm.py", "fake_llama_server.py")
    if sys.platform == "win32":
        exe = tmp_path / "llama-server.cmd"
        exe.write_text(f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8")
    else:
        exe = tmp_path / "llama-server"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        exe.chmod(0o755)
    answers, log = tmp_path / "answers.json", tmp_path / "requests.jsonl"
    monkeypatch.setenv("HERMODR_FAKE_ANSWERS", str(answers))
    monkeypatch.setenv("HERMODR_FAKE_LOG", str(log))

    def make(answer_list, sha=None, args='["-ngl", "99", "--reasoning", "off"]'):
        answers.write_text(json.dumps(answer_list, ensure_ascii=False), encoding="utf-8")
        (project / "config" / "model.toml").write_text(f"""
name = "fake"
[server]
exe = '{exe}'
model = '{gguf}'
sha256 = "{sha or hashlib.sha256(gguf.read_bytes()).hexdigest()}"
port = {free_port()}
args = {args}
startup_timeout_s = 20
[sampling]
temperature = 1.0
top_p = 0.95
top_k = 64
min_p = 0.0
[guards]
min_prompt_tps = 200
max_other_vram_mb = 0
refuse_if_other_llama_server = false
[output]
structured = "json_schema"
""", encoding="utf-8")
        return load(str(project)), log
    return make


def test_starts_answers_and_stops(fake_model):
    s, log = fake_model([{"ok": True}])
    with Model(s, log=lambda *a: None) as llm:
        assert llm.chat([{"role": "user", "content": "x"}], {"type": "object"}) == {"ok": True}
        proc = llm.proc
    assert proc.poll() is not None                       # stopped on the way out
    sent = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert sent["min_p"] == 0.0 and sent["top_k"] == 64 and sent["response_format"]["type"] == "json_schema"


def test_wrong_sha_does_not_start(fake_model):
    s, _ = fake_model([], sha="0" * 64)
    with pytest.raises(ModelError, match="sha256"):
        with Model(s, log=lambda *a: None):
            pass


def test_unknown_option_does_not_start(fake_model):
    s, _ = fake_model([], args='["--flag-that-does-not-exist"]')
    with pytest.raises(ModelError, match="flag-that-does-not-exist"):
        with Model(s, log=lambda *a: None):
            pass


def test_edition_with_the_model(fake_model, project):
    from conftest import NOW, STORY_RATE, World
    from hermodr.db import connect
    from hermodr.run import run
    good = {"sentences": [{"text": "El banco central bajó la tasa al 28%.", "notes": [1, 2]}]}
    invented = {"sentences": [{"text": "El banco central bajó la tasa al 25%.", "notes": [1, 2]}]}
    s, _ = fake_model([invented, good])                  # the first answer is rejected, the retry passes
    w = World()
    w.add("a", "tasa", STORY_RATE, 3, title="Baja la tasa al 28%")
    w.add("b", "tasa", ("Tasa al 28%", "El banco central fijó la tasa en 28% anual desde el lunes."), 2)
    con = connect(s.data_dir)
    eid, path = run(con, s, transport=w.transport(), now=NOW, log=lambda *a: None)
    html = open(path, encoding="utf-8").read()
    assert "bajó la tasa al 28%" in html and "25%" not in html
    row = con.execute("SELECT model, verified, params FROM summaries").fetchone()
    assert row["verified"] == 1 and row["model"].startswith("fake@") and json.loads(row["params"])["sampling"]["min_p"] == 0.0
