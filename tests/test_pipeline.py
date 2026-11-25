from datetime import timedelta

from conftest import NOW, STORY_CHIP, STORY_RATE, STORY_SOLAR, World

from hermodr.db import connect
from hermodr.run import redo_last, run

SOLAR_B = ("Villa Rosales tendrá un parque solar de 12 megavatios",
           "Los socios de la cooperativa de Villa Rosales votaron a favor del parque solar de 12 megavatios en una "
           "asamblea que duró cinco horas. El proyecto costaría 18 millones de dólares.")


def _run(con, settings, world, now):
    return run(con, settings, use_model=False, transport=world.transport(), now=now, log=lambda *a: None)


def test_full_edition_without_model(settings):
    w = World()
    w.add("a", "solar", STORY_SOLAR, 5)
    w.add("b", "solar-b", SOLAR_B, 4)
    w.add("b", "tasa", STORY_RATE, 3)
    w.add("c", "tasa-c", STORY_RATE, 2)                   # same text as b: wire copy
    w.add("c", "chip", STORY_CHIP, 1)
    con = connect(settings.data_dir)
    eid, path = _run(con, settings, w, NOW)

    assert con.execute("SELECT count(*) FROM notes").fetchone()[0] == 5
    assert con.execute("SELECT count(*) FROM stories").fetchone()[0] == 3
    assert con.execute("SELECT count(*) FROM notes WHERE copy_of IS NOT NULL").fetchone()[0] == 1
    assert con.execute("SELECT frontpage FROM notes WHERE source = 'a'").fetchone()[0] == 1
    html = open(path, encoding="utf-8").read()
    assert "Villa Rosales" in html and "Cómo la titularon" in html and "Extracto de" in html
    assert "img/" in html                                  # the image was saved next to the page


def test_nothing_new_and_updates(settings):
    w = World()
    w.add("a", "solar", STORY_SOLAR, 5)
    con = connect(settings.data_dir)
    _run(con, settings, w, NOW)

    _, path = _run(con, settings, w, NOW + timedelta(hours=1))
    assert "Sin novedades desde las" in open(path, encoding="utf-8").read()

    w.add("b", "solar-b", SOLAR_B, -1.5)
    _, path = _run(con, settings, w, NOW + timedelta(hours=2))
    assert "Actualizado" in open(path, encoding="utf-8").read()


def test_redo_covers_the_same_period(settings):
    w = World()
    w.add("a", "solar", STORY_SOLAR, 5)
    con = connect(settings.data_dir)
    first, _ = _run(con, settings, w, NOW)
    assert redo_last(con, settings, log=lambda *a: None) == first
    _, path = _run(con, settings, w, NOW)
    assert "Villa Rosales" in open(path, encoding="utf-8").read()
