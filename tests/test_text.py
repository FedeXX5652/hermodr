from hermodr.text import names, numbers, unsupported

SRC = ("La asamblea aprobó un parque de 12 megavatios. Votaron 2.140 socios. "
       "La inversión es de 18 millones de dólares, según Pablo Ferreyra.")


def test_numbers_in_spanish_format():
    assert {12.0, 2140.0, 18.0, 18e6} <= numbers(SRC)
    assert 2.5 in numbers("subió 2,5%")


def test_names_skip_the_first_word():
    assert "Pablo Ferreyra" in names(SRC)
    assert "La" not in names(SRC)


def test_supported_claim():
    assert unsupported("Votaron 2140 socios y cuesta 18 millones, dijo Ferreyra.", SRC) == []


def test_invented_number_and_name():
    bad = unsupported("Cuesta 20 millones, según Juan Pérez.", SRC)
    assert "number 20" in bad and "name Juan Pérez" in bad
