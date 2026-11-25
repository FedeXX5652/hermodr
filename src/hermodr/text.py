"""Text helpers: normalizing, numbers, proper names, overlap. No model here."""
import re
import unicodedata

_WORD = re.compile(r"[0-9a-záéíóúüñ]+", re.I)
# a number with its spanish separators: 2.140 / 18,5 / 1.234.567,89 / 63%
_NUM  = re.compile(r"(?<![\w.,])\d{1,3}(?:\.\d{3})+(?:,\d+)?|(?<![\w.,])\d+(?:,\d+)?")
_SCALE = {"mil": 1e3, "millon": 1e6, "millones": 1e6, "mil millones": 1e9, "billon": 1e12, "billones": 1e12}
# capitalized words that are not names when they start a sentence or are just articles
_NOT_NAMES = {"el", "la", "los", "las", "un", "una", "en", "de", "del", "y", "a", "por", "para", "con", "segun",
              "tras", "ante", "sin", "este", "esta", "estos", "estas", "ese", "esa", "su", "sus", "al", "lo",
              "se", "no", "si", "que", "como", "cuando", "mientras", "pero", "ademas", "tambien"}


def norm(t):
    """Lowercase and no accents, to compare without depending on spelling."""
    t = unicodedata.normalize("NFKD", t or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def words(t):
    return _WORD.findall(norm(t))


def shingles(t, k=3, limit=None):
    w = words(t)[:limit] if limit else words(t)
    return {" ".join(w[i:i + k]) for i in range(max(1, len(w) - k + 1))} if w else set()


def jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def sentences(t):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ¿¡\"“])", (t or "").strip()) if s.strip()]


def _to_float(s):
    return float(s.replace(".", "").replace(",", "."))


def numbers(t):
    """Every number in the text, as floats. '18 millones' gives 18 and 18e6, so both forms match."""
    n, out = norm(t), set()
    for m in _NUM.finditer(n):
        v = _to_float(m.group())
        out.add(v)
        rest = n[m.end():m.end() + 16].lstrip()
        for word, mult in sorted(_SCALE.items(), key=lambda kv: -len(kv[0])):
            if rest.startswith(word):
                out.add(v * mult)
                break
    return out


def names(t):
    """Runs of capitalized words that are not the first word of a sentence: people, places, outfits."""
    found = set()
    for s in sentences(t):
        toks = re.findall(r"[\wÁÉÍÓÚÑáéíóúñü]+", s)
        run = []
        for i, tok in enumerate(toks):
            cap = tok[:1].isupper() and norm(tok) not in _NOT_NAMES
            if cap and i > 0:
                run.append(tok)
            else:
                if run:
                    found.add(" ".join(run))
                run = []
        if run:
            found.add(" ".join(run))
    return found


def unsupported(claim, source):
    """Numbers and names in claim that are not in source. Empty = everything checks out."""
    src_nums, src_norm = numbers(source), norm(source)
    bad = [f"number {v:g}" for v in sorted(numbers(claim))
           if not any(abs(v - s) <= max(1e-9, abs(s) * 1e-6) for s in src_nums)]
    bad += [f"name {nm}" for nm in sorted(names(claim)) if not all(w in src_norm for w in words(nm))]
    return bad
