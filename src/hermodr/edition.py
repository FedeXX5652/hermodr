"""Which stories go in an edition: period, budget, score, sections and why each one is there."""
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .families import family_of
from .text import norm

SHOULD_KNOW = "deberias_saber"
EXPLORE     = "para_explorar"


@dataclass
class Story:
    id: int
    notes: list                      # all its notes, sqlite rows
    family: dict                     # source -> family
    updated: bool = False            # was shown before and has new notes
    score: float = 0.0
    signals: dict = field(default_factory=dict)
    topics: list = field(default_factory=list)
    why: list = field(default_factory=list)
    section: str = ""

    @property
    def own(self):
        """Notes that are not wire copies."""
        return [n for n in self.notes if n["copy_of"] is None]

    @property
    def sources(self):
        return {n["source"] for n in self.own}

    @property
    def families(self):
        return Counter(self.family[n["source"]] for n in self.own)


def period(con, settings, now=None):
    """(start, end) of the next edition: from where the last one ended until now."""
    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    last = con.execute("SELECT period_end FROM editions ORDER BY id DESC LIMIT 1").fetchone()
    if last:
        return datetime.fromisoformat(last["period_end"]), now
    return now - timedelta(hours=settings.general.get("collect", {}).get("first_run_hours", 24)), now


def budget(hours, points):
    """Stories for an edition that covers this many hours. Grows with the square root of the time."""
    if hours <= points[0][0]:
        return max(5, round(points[0][1] * math.sqrt(hours / points[0][0])))
    for (h0, n0), (h1, n1) in zip(points, points[1:]):
        if hours <= h1:
            t = (math.sqrt(hours) - math.sqrt(h0)) / (math.sqrt(h1) - math.sqrt(h0))
            return round(n0 + t * (n1 - n0))
    return points[-1][1]                                     # ceiling


def candidates(con, settings, start, end):
    """Stories with notes in the period. Already shown ones come back only if they got new notes."""
    fam = family_of(settings)
    ids = [r["story_id"] for r in con.execute(
        "SELECT DISTINCT story_id FROM notes WHERE published > ? AND published <= ? AND story_id IS NOT NULL",
        (start.isoformat(), end.isoformat()))]
    out = []
    for sid in ids:
        notes = con.execute("SELECT * FROM notes WHERE story_id = ? AND published <= ? ORDER BY published",
                            (sid, end.isoformat())).fetchall()
        seen = con.execute("SELECT max(notes_seen) AS n FROM edition_items WHERE story_id = ?", (sid,)).fetchone()["n"]
        if seen is not None and len(notes) <= seen:
            continue
        out.append(Story(sid, notes, fam, updated=seen is not None))
    return out


def _topics(story, profile):
    body = norm(" ".join(f"{n['title']} {n['lead']} {n['text'][:600]}" for n in story.own))
    return [t for t in profile.get("topics") or []
            if any(re.search(rf"\b{re.escape(norm(k))}\b", body) for k in t["keywords"])]


def score(story, settings):
    w, cap = settings.weights["score"], settings.weights["caps"]
    own = story.own
    times = [datetime.fromisoformat(n["published"]) for n in own]
    hours = (max(times) - min(times)).total_seconds() / 3600 if times else 0
    story.topics = _topics(story, settings.profile)
    rel = sum(t["weight"] for t in story.topics)
    story.signals = {
        "breadth":     min(1, len(story.sources) / cap["breadth"]),
        "cross":       min(1, len(story.families) / cap["cross"]),
        "persistence": min(1, hours / cap["persistence_h"]),
        "frontpage":   min(1, len({n["source"] for n in own if n["frontpage"]}) / cap["frontpage"]),
        "relevance":   max(-1, min(1, rel)),
        "novelty":     0.5 if story.updated else 1.0,
    }
    story.score = sum(w[k] * v for k, v in story.signals.items())
    sections = Counter(n["section"] for n in own)
    story.section = sections.most_common(1)[0][0] if sections else "argentina"
    story.why = _why(story, hours)
    return story.score


def _why(story, hours):
    """Why it's here, in plain words. This text goes on the page, so it's in spanish."""
    out, fronts = [], len({n["source"] for n in story.own if n["frontpage"]})
    ns, nf = len(story.sources), len(story.families)
    if ns >= 2:
        out.append(f"Cubierta por {ns} medios de {nf} {'familia' if nf == 1 else 'familias'}")
    if fronts:
        out.append(f"En portada de {fronts} {'medio' if fronts == 1 else 'medios'}")
    if hours >= 12:
        out.append(f"Sigue hace {round(hours / 24)} días" if hours >= 36 else f"Sigue hace {round(hours)} horas")
    liked = [t["name"] for t in story.topics if t["weight"] > 0]
    if liked:
        out.append("Tu interés: " + ", ".join(liked))
    if story.updated:
        out.append("Actualizada desde la última edición")
    return out or ["Nota nueva"]


def select(con, settings, start, end):
    """Dict section -> stories, in order. Empty dict = nothing new."""
    stories = candidates(con, settings, start, end)
    if not stories:
        return {}
    for s in stories:
        score(s, settings)
    b = settings.weights["budget"]
    total = budget((end - start).total_seconds() / 3600, b["points"])
    best = max(s.score for s in stories)
    pool = sorted((s for s in stories if s.score >= b["min_relative"] * best), key=lambda s: -s.score)
    main = pool[:max(1, total - b["should_know"] - b["explore"])]
    rest = [s for s in pool if s not in main]

    # important outside the profile: wide coverage, no topic of mine
    know = sorted((s for s in rest if not s.topics and len(s.families) >= 2),
                  key=lambda s: -(s.signals["breadth"] + s.signals["cross"]))[:b["should_know"]]
    for s in know:
        s.why = ["Importante fuera de tu perfil"] + s.why
    rest = [s for s in rest if s not in know]

    # from the families that show up least in what I'm already getting
    seen = Counter(f for s in main for f in s.families)
    explore = sorted(rest, key=lambda s: (min(seen[f] for f in s.families) if s.families else 0, -s.score))
    explore = explore[:b["explore"]]
    for s in explore:
        s.why = ["De medios que leés menos"] + s.why

    order = settings.profile.get("sections") or ["argentina", "mundo", "mercados", "tecnologia"]
    order = list(order) + sorted({s.section for s in main} - set(order))
    out = {sec: [s for s in main if s.section == sec] for sec in order}
    out = {k: v for k, v in out.items() if v}
    out.update({k: v for k, v in ((SHOULD_KNOW, know), (EXPLORE, explore)) if v})
    return out
