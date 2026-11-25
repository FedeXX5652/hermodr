"""Groups notes into stories, and marks wire copies so they don't count as more coverage."""
from datetime import datetime, timedelta

from rapidfuzz import fuzz

from .text import jaccard, norm, shingles


def _sig(note, lead_words):
    body = note["text"] or note["lead"]
    return {"title": norm(note["title"]), "lead": shingles(f"{note['title']} {body}", 3, lead_words),
            "full": shingles(body, 3) if body else set()}


def same_story(a, b, c):
    j = jaccard(a["lead"], b["lead"])
    return j >= c["strong_jaccard"] or (fuzz.token_set_ratio(a["title"], b["title"]) >= c["title_ratio"]
                                        and j >= c["lead_jaccard"])


def assign(con, weights):
    """Puts every note without a story into one. Returns how many stories were created."""
    c = weights["clustering"]
    pending = con.execute("SELECT * FROM notes WHERE story_id IS NULL ORDER BY published, id").fetchall()
    if not pending:
        return 0
    oldest = min(n["published"] for n in pending)
    since = (datetime.fromisoformat(oldest) - timedelta(hours=c["window_h"])).isoformat()
    # candidates: notes of stories that moved inside the window
    known = [dict(n) | {"sig": _sig(n, c["lead_words"])} for n in con.execute(
        "SELECT n.* FROM notes n JOIN stories s ON s.id = n.story_id WHERE s.updated >= ? ORDER BY n.published",
        (since,))]
    created = 0
    for row in pending:
        n = dict(row) | {"sig": _sig(row, c["lead_words"])}
        limit = (datetime.fromisoformat(n["published"]) - timedelta(hours=c["window_h"])).isoformat()
        match = next((k for k in reversed(known) if k["published"] >= limit and same_story(n["sig"], k["sig"], c)),
                     None)
        if match:
            n["story_id"] = match["story_id"]
            con.execute("UPDATE stories SET updated = max(updated, ?) WHERE id = ?", (n["published"], n["story_id"]))
        else:
            n["story_id"] = con.execute("INSERT INTO stories(created, updated) VALUES (?, ?)",
                                        (n["published"], n["published"])).lastrowid
            created += 1
        # wire copy: almost the same text as an earlier note of the same story, from another source
        copy = next((k for k in known if k["story_id"] == n["story_id"] and k["source"] != n["source"]
                     and k["copy_of"] is None and jaccard(n["sig"]["full"], k["sig"]["full"]) >= c["wire_jaccard"]),
                    None)
        n["copy_of"] = copy["id"] if copy else None
        con.execute("UPDATE notes SET story_id = ?, copy_of = ? WHERE id = ?", (n["story_id"], n["copy_of"], n["id"]))
        known.append(n)
    con.commit()
    return created
