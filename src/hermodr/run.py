"""One edition, start to end: collect, group, choose, summarize, render."""
import subprocess
from datetime import datetime, timezone
from os.path import join
from shutil import rmtree

from . import cluster, collect
from .edition import period, select
from .llm import Model
from .render import render, render_archive
from .summarize import blind_spot, headlines, summarize


def _cards(con, sections, settings, llm, log):
    name_of = lambda sid: (settings.source(sid) or {"name": sid})["name"]
    cards, total = {}, sum(len(v) for v in sections.values())
    for i, s in enumerate((s for v in sections.values() for s in v), 1):
        body = summarize(con, s, name_of, llm, log)
        heads = headlines(s, name_of)
        img = next((n["image_url"] for n in s.own if n["image_url"]), None)
        cards[s.id] = {"id": s.id, "headline": heads[0]["title"], "headlines": heads, "url": heads[0]["url"],
                       "source": heads[0]["source"], "published": heads[0]["published"], "section": s.section,
                       "sentences": [x["text"] for x in body["sentences"]], "kind": body["kind"],
                       "extract_source": body.get("source"), "cross": body.get("cross", False),
                       "why": s.why, "updated": s.updated, "image_url": img,
                       "blind_spot": blind_spot(s, settings.weights["blind_spot"])}
        log(f"  [{i}/{total}] story {s.id}: {body['kind']}")
    return cards


def run(con, settings, use_model=True, do_collect=True, transport=None, now=None, log=print):
    """Builds the next edition. Returns (edition id, path of the html)."""
    if do_collect:
        log("collecting...")
        new, problems = collect.collect(con, settings, transport, log)
        log(f"{new} new notes")
        for p in problems:
            log(f"\033[93m  {p}\033[0m")
    created = cluster.assign(con, settings.weights)
    log(f"{created} new stories")
    start, end = period(con, settings, now)
    sections = select(con, settings, start, end)
    log(f"edition from {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} UTC: "
        f"{sum(len(v) for v in sections.values())} stories")

    if sections and use_model:
        with Model(settings, log) as llm:
            cards, tag = _cards(con, sections, settings, llm, log), llm.tag
    else:
        cards, tag = (_cards(con, sections, settings, None, log) if sections else {}), "no-model"

    eid = con.execute("INSERT INTO editions(created, period_start, period_end, model, path) VALUES (?, ?, ?, ?, '')",
                      (datetime.now(timezone.utc).isoformat(), start.isoformat(), end.isoformat(), tag)).lastrowid
    for sec, stories in sections.items():
        for rank, s in enumerate(stories):
            con.execute("INSERT INTO edition_items VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (eid, s.id, sec, rank, s.score, " · ".join(s.why), len(s.notes), int(s.updated)))
    stats = con.execute("SELECT count(*) AS notes, count(DISTINCT source) AS sources FROM notes "
                        "WHERE published > ? AND published <= ?", (start.isoformat(), end.isoformat())).fetchone()
    folder = join(settings.data_dir, "editions", str(eid))
    f = collect.Fetcher(settings.general.get("collect", {}), transport) if cards else None
    try:
        path = render(eid, sections, cards, start, end, settings, folder, tag, f, None if sections else start,
                      dict(stats))
    finally:
        if f:
            f.close()
    con.execute("UPDATE editions SET path = ? WHERE id = ?", (path, eid))
    con.commit()
    render_archive(con, settings)
    notify(settings, f"Hermodr: edicion {eid} lista, {len(cards)} historias", log)
    return eid, path


def redo_last(con, settings, log=print):
    """Deletes the last edition so the next run covers its period again (after changing sources or weights)."""
    last = con.execute("SELECT id, period_start FROM editions ORDER BY id DESC LIMIT 1").fetchone()
    if not last:
        return None
    con.execute("DELETE FROM edition_items WHERE edition_id = ?", (last["id"],))
    con.execute("DELETE FROM editions WHERE id = ?", (last["id"],))
    con.commit()
    rmtree(join(settings.data_dir, "editions", str(last["id"])), ignore_errors=True)
    render_archive(con, settings)
    log(f"edition {last['id']} deleted: the next run starts again from {last['period_start'][:16]}")
    return last["id"]


def notify(settings, text, log=print):
    cmd = settings.general.get("notify", {}).get("command") or []
    if not cmd:
        return
    try:
        subprocess.run(list(cmd) + [text], timeout=30, check=True)
    except Exception as e:
        log(f"\033[93mnotify failed: {e}\033[0m")
