"""hermodr <command>. Run it from the project folder (or pass --root)."""
import argparse
import sys
import warnings
from os import getcwd
from os.path import abspath, join

from . import __version__
from .settings import ConfigError, init_configs, load

RED, YELLOW, END = "\033[91m", "\033[93m", "\033[0m"


def _settings(root, need_model=True):
    try:
        return load(root, need_model)
    except ConfigError as e:
        for err in e.errors:
            print(f"{RED}{err}{END}")
        sys.exit(1)


def cmd_init(a):
    made = init_configs(a.root)
    print("created: " + ", ".join(made) if made else "every config file already exists: nothing touched")
    print(f"edit them in {join(a.root, 'config')}, then: hermodr check")


def cmd_check(a):
    _settings(a.root)
    print("config ok")


def cmd_sources(a):
    from .collect import check_sources
    sys.exit(0 if check_sources(_settings(a.root, need_model=False)) else 1)


def cmd_model(a):
    from .llm import Model, ModelError
    s = _settings(a.root)
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
              "additionalProperties": False}
    try:
        with Model(s) as llm:
            mode = s.model.get("output", {}).get("structured")
            try:
                ans = llm.chat([{"role": "user", "content": 'Responde {"ok": true}'}], schema, "check")
                print(f"json with output.structured = {mode}: {ans}")
            except ModelError as e:
                other = "json_object" if mode == "json_schema" else "json_schema"
                llm.m.setdefault("output", {})["structured"] = other
                ans = llm.chat([{"role": "user", "content": 'Responde {"ok": true}'}], schema, "check")
                print(f"{YELLOW}{mode} failed ({e}); {other} works: {ans}. Put {other} in model.toml{END}")
    except ModelError as e:
        print(f"{RED}{e}{END}")
        sys.exit(1)
    print("model ok")


def cmd_run(a):
    from .db import connect
    from .llm import ModelError
    from .run import run
    s = _settings(a.root, need_model=not a.no_model)
    con = connect(s.data_dir)
    try:
        eid, path = run(con, s, use_model=not a.no_model, do_collect=not a.no_collect)
    except ModelError as e:
        print(f"{RED}{e}{END}\nno edition was saved")
        sys.exit(1)
    print(f"edition {eid}: {path}\nto read it with votes and clicks: hermodr serve")


def cmd_redo(a):
    from .db import connect
    from .run import redo_last
    s = _settings(a.root, need_model=False)
    if redo_last(connect(s.data_dir), s) is None:
        print("there is no edition yet")


def cmd_serve(a):
    from .serve import serve
    serve(_settings(a.root, need_model=False))


def cmd_families(a):
    from .db import connect
    from .families import suggest, write_suggestion
    s = _settings(a.root, need_model=False)
    fams = suggest(connect(s.data_dir), s, a.days)
    path = join(a.root, "config", "families.suggested.yaml")
    write_suggestion(path, fams, a.days)
    for name, members in fams.items():
        print(f"{name:<10} {', '.join(members)}")
    print(f"written to {path}: copy what makes sense into families.yaml")


def cmd_stats(a):
    from .db import connect
    s = _settings(a.root, need_model=False)
    con = connect(s.data_dir)
    print("editions:", con.execute("SELECT count(*) FROM editions").fetchone()[0],
          "| notes:", con.execute("SELECT count(*) FROM notes").fetchone()[0],
          "| stories:", con.execute("SELECT count(*) FROM stories").fetchone()[0])
    print("\nby section (shown / clicks / useful / not interested):")
    for r in con.execute(
            "SELECT i.section, count(DISTINCT i.edition_id || '-' || i.story_id) AS shown, "
            "sum(e.kind = 'click') AS c, sum(e.kind = 'useful') AS u, sum(e.kind = 'not_interested') AS n "
            "FROM edition_items i LEFT JOIN events e ON e.edition_id = i.edition_id AND e.story_id = i.story_id "
            "GROUP BY i.section ORDER BY shown DESC"):
        print(f"  {r['section']:<16} {r['shown']:>5} {r['c'] or 0:>7} {r['u'] or 0:>7} {r['n'] or 0:>7}")
    print("\nfeeds with errors:")
    bad = con.execute("SELECT url, last_error, error FROM feed_status WHERE error IS NOT NULL").fetchall()
    for r in bad:
        print(f"  {r['url']}\n    {r['last_error'][:16]}: {r['error']}")
    if not bad:
        print("  none")


def main():
    # urllib3 warns about socks proxies on import; it only matters with a socks proxy, and there is none
    warnings.filterwarnings("ignore", message="SOCKS support in urllib3")
    p = argparse.ArgumentParser(prog="hermodr", description="Personal news digest.")
    p.add_argument("--root", default=getcwd(), type=abspath, help="project folder (default: the current one)")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="creates the config files from the .example ones").set_defaults(f=cmd_init)
    sub.add_parser("check", help="validates every config file").set_defaults(f=cmd_check)
    sub.add_parser("sources", help="tests every feed").set_defaults(f=cmd_sources)
    sub.add_parser("model", help="starts the model, asks for a json and stops it").set_defaults(f=cmd_model)
    r = sub.add_parser("run", help="builds the next edition")
    r.add_argument("--no-model", action="store_true", help="extracts instead of summaries; doesn't start the model")
    r.add_argument("--no-collect", action="store_true", help="uses what is already in the db")
    r.set_defaults(f=cmd_run)
    sub.add_parser("redo", help="deletes the last edition, so the next run covers its period again").set_defaults(f=cmd_redo)
    sub.add_parser("serve", help="local server to read the editions and keep votes").set_defaults(f=cmd_serve)
    fm = sub.add_parser("families", help="suggests media families from the coverage")
    fm.add_argument("--days", type=int, default=14)
    fm.set_defaults(f=cmd_families)
    sub.add_parser("stats", help="editions, clicks, votes and broken feeds").set_defaults(f=cmd_stats)
    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
