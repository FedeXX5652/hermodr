"""Loads the config folder. Collects ALL the problems and stops before doing anything."""
import tomllib
from dataclasses import dataclass, field
from os.path import isabs, isfile, join
from shutil import copyfile

import yaml

CONFIG_FILES = ("hermodr.toml", "model.toml", "sources.yaml", "families.yaml", "profile.yaml", "weights.yaml")
SECTIONS     = ("argentina", "mundo", "mercados", "tecnologia")


class ConfigError(Exception):
    def __init__(self, errors):
        super().__init__("\n".join(errors))
        self.errors = errors


@dataclass
class Settings:
    root: str
    general: dict
    model: dict
    sources: list
    families: dict
    profile: dict
    weights: dict
    data_dir: str = field(init=False)

    def __post_init__(self):
        d = self.general.get("data_dir", "data")
        self.data_dir = d if isabs(d) else join(self.root, d)

    def source(self, sid):
        return next((s for s in self.sources if s["id"] == sid), None)


def init_configs(root):
    """Copies each .example to its real name if it does not exist yet. Returns the ones created."""
    created = []
    for name in CONFIG_FILES:
        real = join(root, "config", name)
        base, ext = name.rsplit(".", 1)
        example = join(root, "config", f"{base}.example.{ext}")
        if not isfile(real) and isfile(example):
            copyfile(example, real)
            created.append(name)
    return created


def _read(path, errors):
    if not isfile(path):
        errors.append(f"missing {path} (hermodr init creates it from the .example)")
        return None
    try:
        with open(path, "rb") as f:
            return tomllib.load(f) if path.endswith(".toml") else (yaml.safe_load(f) or {})
    except Exception as e:
        errors.append(f"{path}: can't be read ({e})")
        return None


def load(root, need_model=True):
    errors = []
    raw    = {n: _read(join(root, "config", n), errors) for n in CONFIG_FILES if need_model or n != "model.toml"}
    if errors:
        raise ConfigError(errors)

    # ---- sources ----
    sources, ids = raw["sources.yaml"].get("sources") or [], set()
    if not sources:
        errors.append("sources.yaml: no sources")
    for i, s in enumerate(sources):
        where = f"sources.yaml > #{i + 1}"
        if not isinstance(s, dict) or not s.get("id") or not s.get("name"):
            errors.append(f"{where}: needs id and name")
            continue
        if s["id"] in ids:
            errors.append(f"{where}: id {s['id']!r} repeated")
        ids.add(s["id"])
        if not s.get("feeds"):
            errors.append(f"{where} ({s['id']}): no feeds")
        for j, fd in enumerate(s.get("feeds") or []):
            if not isinstance(fd, dict) or not str(fd.get("url", "")).startswith("http"):
                errors.append(f"{where} ({s['id']}) > feed #{j + 1}: needs an http url")
            elif fd.get("section") not in SECTIONS:
                errors.append(f"{where} ({s['id']}) > {fd['url']}: section must be one of {', '.join(SECTIONS)}")

    # ---- families: every listed source has to exist, and only once ----
    fams, seen = (raw["families.yaml"].get("families") or {}), {}
    for fam, members in fams.items():
        for sid in members or []:
            if sid not in ids:
                errors.append(f"families.yaml > {fam}: unknown source {sid!r}")
            if sid in seen:
                errors.append(f"families.yaml: {sid!r} is in {seen[sid]!r} and in {fam!r}")
            seen[sid] = fam

    # ---- profile ----
    prof = raw["profile.yaml"]
    for i, t in enumerate(prof.get("topics") or []):
        if not t.get("name") or not isinstance(t.get("weight"), (int, float)) or not t.get("keywords"):
            errors.append(f"profile.yaml > topic #{i + 1}: needs name, weight (number) and keywords")
    bad = [s for s in prof.get("sections") or [] if s not in SECTIONS]
    if bad:
        errors.append(f"profile.yaml > sections: unknown {bad}")

    # ---- weights ----
    w = raw["weights.yaml"]
    for key in ("score", "caps", "budget", "clustering", "blind_spot"):
        if not isinstance(w.get(key), dict):
            errors.append(f"weights.yaml: missing block {key!r}")
    pts = (w.get("budget") or {}).get("points") or []
    if len(pts) < 2 or any(len(p) != 2 for p in pts) or [p[0] for p in pts] != sorted(p[0] for p in pts):
        errors.append("weights.yaml > budget > points: needs 2+ pairs [hours, stories], hours ascending")

    # ---- model ----
    if need_model:
        m = raw["model.toml"]
        srv = m.get("server") or {}
        for key in ("exe", "model", "sha256", "port", "args"):
            if key not in srv:
                errors.append(f"model.toml > server: missing {key!r}")
        if srv.get("sha256") and len(str(srv["sha256"])) != 64:
            errors.append("model.toml > server > sha256: needs the 64 hex characters")
        for key in ("-m", "--model", "--host", "--port"):
            if key in (srv.get("args") or []):
                errors.append(f"model.toml > server > args: {key} goes in its own field, not in args")
        for key in ("temperature", "top_p", "top_k", "min_p"):
            if key not in (m.get("sampling") or {}):
                errors.append(f"model.toml > sampling: missing {key!r} (all of them are sent, none left to defaults)")
        if (m.get("output") or {}).get("structured") not in ("json_schema", "json_object"):
            errors.append("model.toml > output > structured: json_schema or json_object")

    if errors:
        raise ConfigError(errors)
    return Settings(root=root, general=raw["hermodr.toml"], model=raw.get("model.toml") or {},
                    sources=sources, families=fams, profile=prof, weights=w)
