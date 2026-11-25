"""Everything around the model: what it gets, what it must answer, and checking it didn't make things up."""
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from importlib.resources import files

from .text import sentences, unsupported

PROMPT = files("hermodr").joinpath("prompts/summary.es.txt").read_text(encoding="utf-8")
PROMPT_VERSION = hashlib.sha256(PROMPT.encode()).hexdigest()[:12]
MAX_NOTES, MAX_WORDS = 8, 350

SCHEMA = {
    "type": "object",
    "properties": {"sentences": {"type": "array", "minItems": 1, "maxItems": 2, "items": {
        "type": "object",
        "properties": {"text": {"type": "string"}, "notes": {"type": "array", "minItems": 1, "items": {"type": "integer"}}},
        "required": ["text", "notes"], "additionalProperties": False}}},
    "required": ["sentences"], "additionalProperties": False,
}

RULE_CROSS  = "Solo podés afirmar hechos que aparezcan en notas de al menos dos familias distintas."
RULE_SINGLE = "Todas las notas son de la misma familia de medios: resumí lo que dicen, sin agregar nada."


def pick_notes(story, name_of):
    """Up to MAX_NOTES own notes, taking turns between families so no family fills the prompt."""
    by_fam = defaultdict(list)
    for n in story.own:
        by_fam[story.family[n["source"]]].append(n)
    picked, queues = [], [list(v) for _, v in sorted(by_fam.items(), key=lambda kv: -len(kv[1]))]
    while queues and len(picked) < MAX_NOTES:
        for q in queues:
            if q and len(picked) < MAX_NOTES:
                picked.append(q.pop(0))
        queues = [q for q in queues if q]
    return [{"n": i + 1, "id": n["id"], "source": n["source"], "name": name_of(n["source"]),
             "family": story.family[n["source"]], "title": n["title"],
             "body": _cut(n["text"] or n["lead"], MAX_WORDS)} for i, n in enumerate(picked)]


def _cut(text, limit):
    """First limit words, keeping the line breaks."""
    out, count = [], 0
    for line in text.splitlines():
        w = line.split()
        if count + len(w) > limit:
            out.append(" ".join(w[:limit - count]))
            break
        out.append(" ".join(w))
        count += len(w)
    return "\n".join(l for l in out if l)


def build_messages(notes, cross, feedback=None):
    rule = RULE_CROSS if cross else RULE_SINGLE
    by_fam = defaultdict(list)
    for x in notes:
        by_fam[x["family"]].append(x)
    parts = []
    for fam, xs in by_fam.items():
        parts.append(f"## Familia {fam}")
        parts += [f"[{x['n']}] {x['name']}: {x['title']}\n{x['body']}" for x in xs]
    user = "\n\n".join(parts)
    if feedback:
        user += "\n\nTu respuesta anterior tenía estos problemas, corregilos:\n- " + "\n- ".join(feedback)
    return [{"role": "system", "content": PROMPT.replace("{regla_familias}", rule)}, {"role": "user", "content": user}]


def verify(answer, notes, cross):
    """(good sentences, problems). Each sentence: its notes exist, span 2+ families if asked, and every
    number and name in it is in those notes."""
    by_n, good, problems = {x["n"]: x for x in notes}, [], []
    for s in answer.get("sentences") or []:
        refs = [by_n[i] for i in dict.fromkeys(s.get("notes") or []) if i in by_n]
        text = (s.get("text") or "").strip()
        if not text or not refs:
            problems.append(f"oracion sin notas validas: {text[:80]!r}")
            continue
        if cross and len({x["family"] for x in refs}) < 2:
            problems.append(f"oracion respaldada por una sola familia: {text[:80]!r}")
            continue
        bad = unsupported(text, "\n".join(f"{x['title']}\n{x['body']}" for x in refs))
        if bad:
            problems.append(f"no esta en sus notas ({', '.join(bad)}): {text[:80]!r}")
            continue
        good.append({"text": text, "notes": [x["id"] for x in refs]})
    return good, problems


def extract(notes):
    """No model, or the model failed twice: the first sentence of the first note, said as what it is."""
    x = notes[0]
    lines = [l for l in x["body"].splitlines() if l.strip() and l.strip() != x["title"].strip()]
    first = (sentences(lines[0]) if lines else [x["title"]])[0]       # first paragraph, first sentence
    return {"kind": "extract", "source": x["name"], "sentences": [{"text": first, "notes": [x["id"]]}]}


def summary_key(notes, model_tag):
    h = hashlib.sha256(json.dumps([[x["id"], x["body"]] for x in notes], ensure_ascii=False).encode())
    return f"{h.hexdigest()[:24]}|{model_tag}|{PROMPT_VERSION}"


def summarize(con, story, name_of, llm=None, log=print):
    """Summary of a story, from the cache if nothing changed. llm=None: extract only."""
    notes = pick_notes(story, name_of)
    cross = len({x["family"] for x in notes}) >= 2
    tag = llm.tag if llm else "no-model"
    key = summary_key(notes, tag)
    row = con.execute("SELECT body FROM summaries WHERE key = ?", (key,)).fetchone()
    if row:
        return json.loads(row["body"])
    body, verified, params = extract(notes), 0, {"model": tag, "prompt": PROMPT_VERSION}
    if llm:
        params = llm.params() | {"prompt": PROMPT_VERSION}
        feedback = None
        for attempt in (1, 2):
            good, feedback = verify(llm.chat(build_messages(notes, cross, feedback), SCHEMA, "resumen"), notes, cross)
            if good and not feedback:
                body, verified = {"kind": "model", "cross": cross, "sentences": good}, 1
                break
            log(f"  story {story.id}: attempt {attempt} rejected ({'; '.join(feedback)[:160]})")
            if good and attempt == 2:                    # keep what did verify, drop the rest
                body, verified = {"kind": "model", "cross": cross, "sentences": good}, 1
    con.execute("INSERT INTO summaries(key, story_id, model, params, body, verified, created) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (key, story.id, tag, json.dumps(params, ensure_ascii=False), json.dumps(body, ensure_ascii=False),
                 verified, datetime.now(timezone.utc).isoformat()))
    con.commit()
    return body


def headlines(story, name_of, limit=3):
    """How they titled it: one headline per family, the biggest families first."""
    out, seen = [], set()
    for fam, _ in story.families.most_common():
        n = next(n for n in story.own if story.family[n["source"]] == fam)
        if fam not in seen:
            seen.add(fam)
            out.append({"source": name_of(n["source"]), "title": n["title"], "url": n["url"], "published": n["published"]})
        if len(out) >= limit:
            break
    return out


def blind_spot(story, cfg):
    """Family name if the story is covered almost only by one family, else None."""
    own = story.own
    if len(own) < cfg["min_notes"]:
        return None
    fam, count = story.families.most_common(1)[0]
    return fam if count / len(own) >= cfg["one_family_share"] else None
