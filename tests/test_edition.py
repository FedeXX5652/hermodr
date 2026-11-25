from hermodr.edition import budget

POINTS = [[3, 13], [6, 17], [24, 30], [72, 47], [120, 54]]


def test_budget_hits_the_points():
    assert [budget(h, POINTS) for h, _ in POINTS] == [n for _, n in POINTS]


def test_budget_grows_and_has_a_ceiling():
    values = [budget(h, POINTS) for h in (1, 3, 10, 24, 48, 100, 500)]
    assert values == sorted(values)
    assert budget(500, POINTS) == 54
    assert budget(0.5, POINTS) >= 5
