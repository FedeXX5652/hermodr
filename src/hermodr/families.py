"""Media families: from families.yaml, and a suggestion built from what each outlet covers."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import yaml

from .text import jaccard


def family_of(settings):
    """source id -> family. A source nobody listed is its own family."""
    out = {s["id"]: s["id"] for s in settings.sources}
    for fam, members in settings.families.items():
        for sid in members or []:
            out[sid] = fam
    return out


def suggest(con, settings, days=14, threshold=0.35):
    """Groups outlets whose stories overlap the most (average linkage). Wire copies are left out."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    covered = defaultdict(set)
    for r in con.execute("SELECT source, story_id FROM notes WHERE published >= ? AND copy_of IS NULL "
                         "AND story_id IS NOT NULL", (since,)):
        covered[r["source"]].add(r["story_id"])
    primary = {s["id"] for s in settings.sources if s.get("primary")}
    groups = [[sid] for sid in sorted(covered) if sid not in primary]

    def link(a, b):
        return sum(jaccard(covered[x], covered[y]) for x in a for y in b) / (len(a) * len(b))

    while len(groups) > 1:
        best = max(((link(a, b), i, j) for i, a in enumerate(groups) for j, b in enumerate(groups) if i < j))
        if best[0] < threshold:
            break
        _, i, j = best
        groups[i] += groups.pop(j)
    return {f"family-{n + 1}": sorted(g) for n, g in enumerate(sorted(groups, key=len, reverse=True))}


def write_suggestion(path, fams, days):
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Suggested from the last {days} days of coverage. Copy what makes sense into families.yaml.\n")
        yaml.safe_dump({"families": fams}, f, allow_unicode=True, sort_keys=False)
