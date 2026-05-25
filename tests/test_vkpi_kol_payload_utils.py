from app.domains.kol import payload_utils


def test_kol_payload_utils_normalize_numbers_and_json():
    assert payload_utils._int("12.8") == 12
    assert payload_utils._int("bad", default=7) == 7
    assert payload_utils._float("3.5") == 3.5
    assert payload_utils._float("bad", default=1.25) == 1.25
    assert payload_utils._json_loads('{"a": 1}', {}) == {"a": 1}
    assert payload_utils._json_loads("", {"fallback": True}) == {"fallback": True}


def test_kol_payload_utils_score_and_grade():
    assert payload_utils._clamp_score(-10) == 0
    assert payload_utils._clamp_score(101) == 100
    assert payload_utils._grade(95) == "S"
    assert payload_utils._grade(81) == "A"
    assert payload_utils._grade(66) == "B"
    assert payload_utils._grade(51) == "C"
    assert payload_utils._grade(49) == "D"
