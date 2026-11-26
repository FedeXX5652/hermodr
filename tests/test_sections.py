import httpx
import pytest

from conftest import rss, NOW

from hermodr.collect import discover, section_of
from hermodr.settings import ConfigError, load

RULES = {"skip": ["/horoscopo/"], "mercados": ["/economia/", "/finanzas/"], "mundo": ["/el-mundo/"]}


def test_section_of():
    auto = {"section": "auto", "fallback": "argentina"}
    assert section_of("https://x.test/economia/dolar-hoy", auto, RULES) == "mercados"
    assert section_of("https://x.test/El-Mundo/china", auto, RULES) == "mundo"         # case doesn't matter
    assert section_of("https://x.test/horoscopo/aries", auto, RULES) is None          # skipped
    assert section_of("https://x.test/politica/ley", auto, RULES) == "argentina"      # no rule: fallback
    assert section_of("https://x.test/economia/x", {"section": "tecnologia"}, RULES) == "tecnologia"  # fixed wins


def _sources(project, text):
    (project / "config" / "sources.yaml").write_text(text, encoding="utf-8")


def test_auto_needs_fallback_and_valid_rules(project):
    _sources(project, """
section_rules:
  mercados: [/economia/]
  deportes: [/deportes/]
sources:
  - id: x
    name: X
    feeds: [{url: "https://x.test/rss", section: auto}]
  - id: y
    name: Y
    feeds: [{url: "https://y.test/rss", section: auto, fallback: argentina}]
""")
    (project / "config" / "families.yaml").write_text("families: {}\n", encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load(str(project), need_model=False)
    text = "\n".join(e.value.errors)
    assert "needs a fallback" in text and "deportes: unknown" in text and "(y)" not in text


def test_collect_splits_an_auto_feed(project):
    from hermodr.db import connect
    from hermodr.collect import collect
    _sources(project, """
section_rules:
  skip: [/horoscopo/]
  mercados: [/economia/]
sources:
  - id: x
    name: X
    feeds: [{url: "https://x.test/rss", section: auto, fallback: argentina}]
""")
    (project / "config" / "families.yaml").write_text("families: {}\n", encoding="utf-8")
    s = load(str(project), need_model=False)
    s.general.setdefault("collect", {})["fetch_articles"] = False
    items = [("https://x.test/economia/dolar", "Dólar", "sube", NOW), ("https://x.test/politica/ley", "Ley", "sale", NOW),
             ("https://x.test/horoscopo/aries", "Aries", "hoy", NOW)]
    t = httpx.MockTransport(lambda r: httpx.Response(200, text=rss(items)) if r.url.path == "/rss" else httpx.Response(404))
    con = connect(s.data_dir)
    new, problems = collect(con, s, t, log=lambda *a: None)
    assert new == 2 and not problems
    got = dict(con.execute("SELECT url, section FROM notes").fetchall())
    assert got == {"https://x.test/economia/dolar": "mercados", "https://x.test/politica/ley": "argentina"}


def test_discover(settings):
    items = [("https://x.test/economia/a", "A", "a", NOW), ("https://x.test/economia/b", "B", "b", NOW),
             ("https://x.test/mundo/c", "C", "c", NOW)]

    def handler(r):
        if r.url.path == "/":
            return httpx.Response(200, text='<link rel="alternate" type="application/rss+xml" href="/feeds/all.xml">')
        if r.url.path in ("/feeds/all.xml", "/feed"):
            return httpx.Response(200, text=rss(items))
        return httpx.Response(404)
    out = []
    discover(settings, ["https://x.test/"], httpx.MockTransport(handler), log=out.append)
    text = "\n".join(out)
    assert "announces 1 feeds" in text
    assert "FEED https://x.test/feeds/all.xml" in text and "FEED https://x.test/feed\n" in text
    assert "/economia/ 2, /mundo/ 1" in text
