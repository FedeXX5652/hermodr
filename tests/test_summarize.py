from hermodr.summarize import verify

NOTES = [{"n": 1, "id": 10, "family": "uno", "title": "Baja la tasa", "body": "El central bajó la tasa al 28%."},
         {"n": 2, "id": 11, "family": "dos", "title": "Tasa al 28%", "body": "La tasa quedó en 28% anual."}]


def test_good_sentence():
    good, problems = verify({"sentences": [{"text": "La tasa bajó al 28%.", "notes": [1, 2]}]}, NOTES, cross=True)
    assert problems == [] and good[0]["notes"] == [10, 11]


def test_one_family_is_not_enough_when_there_are_two():
    good, problems = verify({"sentences": [{"text": "La tasa bajó al 28%.", "notes": [1]}]}, NOTES, cross=True)
    assert not good and "una sola familia" in problems[0]


def test_invented_number_is_dropped():
    good, problems = verify({"sentences": [{"text": "La tasa bajó al 25%.", "notes": [1, 2]}]}, NOTES, cross=True)
    assert not good and "number 25" in problems[0]


def test_unknown_note():
    good, problems = verify({"sentences": [{"text": "La tasa bajó.", "notes": [7]}]}, NOTES, cross=False)
    assert not good and problems
