"""Builds the html of an edition (one page plus its img folder) and the archive page.
Everything on the pages is in spanish."""
import io
import json
from datetime import datetime
from os import makedirs
from os.path import join
from zoneinfo import ZoneInfo

from jinja2 import Environment, PackageLoader, select_autoescape
from PIL import Image

from .edition import EXPLORE, SHOULD_KNOW

DAYS   = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
          "noviembre", "diciembre"]
SECTION_NAMES = {"argentina": "Argentina", "mundo": "Mundo", "mercados": "Mercados", "tecnologia": "Tecnología",
                 SHOULD_KNOW: "Deberías saber", EXPLORE: "Para explorar"}
FRONT_SIDE = 3          # stories next to the lead one on the front page
IMG_WIDTH  = 960


def _env():
    return Environment(loader=PackageLoader("hermodr", "templates"), autoescape=select_autoescape(["html", "j2"]))


def _tz(settings):
    return ZoneInfo(settings.general.get("timezone", "America/Argentina/Buenos_Aires"))


def day_es(d, year=False):
    return f"{DAYS[d.weekday()]} {d.day} de {MONTHS[d.month - 1]}" + (f" de {d.year}" if year else "")


def period_es(start, end):
    """'Jueves 5 de noviembre de 2026' for a normal edition, 'Del 3 al 5 de noviembre de 2026' when it covers days."""
    if (end - start).total_seconds() <= 36 * 3600:
        return day_es(end, year=True).capitalize()
    if start.month == end.month:
        return f"Del {start.day} al {end.day} de {MONTHS[end.month - 1]} de {end.year}"
    return f"Del {start.day} de {MONTHS[start.month - 1]} al {end.day} de {MONTHS[end.month - 1]} de {end.year}"


def time_es(iso, end, tz):
    """'14:20' if it's from the edition's day, '4 nov 18:00' if not."""
    t = datetime.fromisoformat(iso).astimezone(tz)
    return f"{t:%H:%M}" if t.date() == end.date() else f"{t.day} {MONTHS[t.month - 1][:3]} {t:%H:%M}"


def save_image(fetcher, url, path):
    """Downloads and shrinks the image. False if it can't: the card goes without image."""
    try:
        im = Image.open(io.BytesIO(fetcher.get(url).content)).convert("RGB")
        if im.width > IMG_WIDTH:
            im = im.resize((IMG_WIDTH, round(im.height * IMG_WIDTH / im.width)))
        im.save(path, "WEBP", quality=78)
        return True
    except Exception:
        return False


def render(edition_id, sections, cards, start, end, settings, folder, model_tag, fetcher=None, empty_since=None,
           stats=None):
    tz = _tz(settings)
    start, end = start.astimezone(tz), end.astimezone(tz)
    makedirs(join(folder, "img"), exist_ok=True)
    for c in cards.values():
        c["time"] = time_es(c["published"], end, tz)
        c["section_name"] = SECTION_NAMES.get(c["section"], c["section"].capitalize())
        c["image"] = None
        if fetcher and c.get("image_url"):
            name = f"{c['id']}.webp"
            c["image"] = f"img/{name}" if save_image(fetcher, c["image_url"], join(folder, "img", name)) else None

    # front page: the best stories of the regular sections, taken out of their sections
    regular = {k: v for k, v in sections.items() if k not in (SHOULD_KNOW, EXPLORE)}
    best = sorted((s for v in regular.values() for s in v), key=lambda s: -s.score)[:1 + FRONT_SIDE]
    on_front = {s.id for s in best}
    # the lead goes with a picture if any of the top ones has one
    lead = next((s for s in best if cards[s.id]["image"]), best[0]) if best else None
    side = [s for s in best if s is not lead]
    topics = [{"key": k, "name": SECTION_NAMES.get(k, k.capitalize()),
               "cards": [cards[s.id] for s in v if s.id not in on_front]} for k, v in regular.items()]
    topics = [t for t in topics if t["cards"]]

    nav = ([{"key": "portada", "name": "Portada"}] if lead else []) + [{"key": t["key"], "name": t["name"]} for t in topics]
    nav += [{"key": k, "name": SECTION_NAMES[k]} for k in (SHOULD_KNOW, EXPLORE) if sections.get(k)]
    html = _env().get_template("edition.html.j2").render(
        title=settings.general.get("edition", {}).get("title", "Hermóðr"), edition_id=edition_id,
        date=period_es(start, end), stacked=(end - start).total_seconds() > 36 * 3600,
        stacked_range=f"del {day_es(start)} al {day_es(end)}", built=f"{end:%H:%M}", nav=nav,
        lead=cards[lead.id] if lead else None, side=[cards[s.id] for s in side], topics=topics,
        should_know=[cards[s.id] for s in sections.get(SHOULD_KNOW, [])],
        explore=[cards[s.id] for s in sections.get(EXPLORE, [])],
        empty_since=empty_since.astimezone(tz).strftime("%H:%M") if empty_since else None,
        no_model=model_tag == "no-model", stats=stats or {"notes": 0, "sources": 0},
        data=json.dumps({"edition": edition_id}))
    with open(join(folder, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    return join(folder, "index.html")


def render_archive(con, settings):
    """editions/archive.html: every edition, newest first."""
    tz = _tz(settings)
    rows = []
    for r in con.execute("SELECT e.*, (SELECT count(*) FROM edition_items i WHERE i.edition_id = e.id) AS n "
                         "FROM editions e ORDER BY e.id DESC"):
        s, e = datetime.fromisoformat(r["period_start"]).astimezone(tz), datetime.fromisoformat(r["period_end"]).astimezone(tz)
        rows.append({"id": r["id"], "date": period_es(s, e), "built": f"{e:%H:%M}", "n": r["n"],
                     "no_model": r["model"] == "no-model"})
    folder = join(settings.data_dir, "editions")
    makedirs(folder, exist_ok=True)
    html = _env().get_template("archive.html.j2").render(
        title=settings.general.get("edition", {}).get("title", "Hermóðr"), editions=rows)
    with open(join(folder, "archive.html"), "w", encoding="utf-8") as f:
        f.write(html)
