"""A small fake world: three outlets, their feeds and articles, served through httpx.MockTransport."""
import io
import shutil
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone
from os.path import dirname, join

import httpx
import pytest
from PIL import Image

from hermodr.settings import init_configs, load

ROOT = dirname(dirname(__file__))
NOW  = datetime(2026, 11, 5, 21, 0, tzinfo=timezone.utc)

STORY_SOLAR = ("La cooperativa de Villa Rosales aprobó un parque solar de 12 megavatios",
               "La asamblea de la Cooperativa Eléctrica de Villa Rosales aprobó el sábado un parque solar de "
               "12 megavatios. Votaron 2.140 socios y la propuesta obtuvo el 63% de los votos. La inversión "
               "estimada es de 18 millones de dólares, según el ingeniero Pablo Ferreyra.")
STORY_RATE = ("El banco central bajó la tasa de referencia al 28%",
              "El banco central redujo la tasa de política monetaria del 30% al 28% anual. La medida rige desde "
              "el lunes y busca acompañar la baja de la inflación, informó la entidad.")
STORY_CHIP = ("Una empresa local presentó un chip para inteligencia artificial",
              "Una empresa de Córdoba presentó un chip diseñado para inteligencia artificial que consume la mitad "
              "de energía. La producción empezaría en 2027, anticipó su director, Lucas Herrera.")


def article(title, body):
    return (f"<html><head><title>{title}</title><meta property='og:image' content='https://img.test/a.png'>"
            f"</head><body><article><h1>{title}</h1>" + "".join(f"<p>{p}</p>" for p in body.split(". "))
            + "</article></body></html>")


def rss(items):
    out = ["<?xml version='1.0'?><rss version='2.0'><channel><title>t</title>"]
    for url, title, lead, when in items:
        out.append(f"<item><title>{title}</title><link>{url}</link><description>{lead}</description>"
                   f"<pubDate>{format_datetime(when)}</pubDate></item>")
    return "".join(out + ["</channel></rss>"])


class World:
    """Feeds that can grow between runs, like the real ones."""

    def __init__(self):
        self.items = {"a": [], "b": [], "c": []}
        self.pages = {}

    def add(self, src, slug, story, hours_ago, title=None):
        url = f"https://{src}.test/nota/{slug}"
        t, body = story
        self.items[src].append((url, title or t, body[:120], NOW - timedelta(hours=hours_ago)))
        self.pages[url] = article(title or t, body)

    def transport(self):
        png = io.BytesIO()
        Image.new("RGB", (900, 500), (200, 90, 40)).save(png, "PNG")

        def handler(req):
            u = str(req.url)
            if u.endswith("/rss"):
                return httpx.Response(200, text=rss(self.items[req.url.host.split(".")[0]]))
            if u in self.pages:
                return httpx.Response(200, text=self.pages[u])
            if req.url.host == "img.test":
                return httpx.Response(200, content=png.getvalue())
            if u.startswith("https://a.test/"):        # front page of a: links to its notes
                return httpx.Response(200, text="".join(f'<a href="{i[0]}">x</a>' for i in self.items["a"]))
            return httpx.Response(404)
        return httpx.MockTransport(handler)


@pytest.fixture
def project(tmp_path):
    """A project folder with the example configs, real names, and sources pointing at the fake world."""
    shutil.copytree(join(ROOT, "config"), tmp_path / "config", ignore=shutil.ignore_patterns("*.toml", "*.yaml"),
                    dirs_exist_ok=True)
    for f in ("hermodr", "model", "sources", "families", "profile", "weights"):
        ext = "toml" if f in ("hermodr", "model") else "yaml"
        shutil.copy(join(ROOT, "config", f"{f}.example.{ext}"), tmp_path / "config" / f"{f}.example.{ext}")
    init_configs(str(tmp_path))
    (tmp_path / "config" / "sources.yaml").write_text("""
sources:
  - id: a
    name: Diario A
    frontpage: https://a.test/
    feeds: [{url: "https://a.test/rss", section: argentina}]
  - id: b
    name: Diario B
    feeds: [{url: "https://b.test/rss", section: mercados}]
  - id: c
    name: Agencia C
    feeds: [{url: "https://c.test/rss", section: tecnologia}]
""", encoding="utf-8")
    (tmp_path / "config" / "families.yaml").write_text("families:\n  uno: [a]\n  dos: [b, c]\n", encoding="utf-8")
    hermodr = (tmp_path / "config" / "hermodr.toml").read_text(encoding="utf-8")
    (tmp_path / "config" / "hermodr.toml").write_text(hermodr.replace("per_host_delay_s = 1.0", "per_host_delay_s = 0"),
                                                      encoding="utf-8")
    return tmp_path


@pytest.fixture
def settings(project):
    return load(str(project), need_model=False)
