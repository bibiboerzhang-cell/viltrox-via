from __future__ import annotations

import random
import re
import string
from datetime import date

from app.domains.kol.competitor_text import _keyword_match, _keyword_match_prepared
from app.domains.market import category_tracks


def _reference_match(text: str, keyword: str) -> bool:
    lowered = text.lower()
    key = keyword.lower().strip()
    if not key:
        return False
    if key.startswith("@") or " " in key:
        return key in lowered
    return re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", lowered) is not None


def test_keyword_match_preserves_ascii_boundary_contract() -> None:
    cases = [
        ("Sigma 35mm review", "sigma"),
        ("sigmaphoto launch", "sigma"),
        ("(TAMRON), sharp", "tamron"),
        ("@tamronusa sample", "@tamronusa"),
        ("new YN lens today", "yn lens"),
        ("godox2 is not godox", "godox"),
        ("MEIKE/ROKINON", "rokinon"),
    ]
    for text, keyword in cases:
        expected = _reference_match(text, keyword)
        assert _keyword_match(text, keyword) is expected
        assert _keyword_match_prepared(text.lower(), keyword.lower().strip()) is expected


def test_keyword_match_matches_previous_regex_for_deterministic_fuzz_corpus() -> None:
    rng = random.Random(20260714)
    alphabet = string.ascii_letters + string.digits + " _-@/(),.#"
    keywords = ["sigma", "tamron", "godox", "yn lens", "@meike_global", "x1"]
    for _ in range(500):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 120)))
        keyword = rng.choice(keywords)
        expected = _reference_match(text, keyword)
        assert _keyword_match(text, keyword) is expected
        assert _keyword_match_prepared(text.lower(), keyword.lower().strip()) is expected


def test_category_tracks_prepares_match_inputs_once(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def checked_match(lowered: str, key: str) -> bool:
        assert lowered == lowered.lower()
        assert key == key.lower().strip()
        calls.append((lowered, key))
        return _keyword_match_prepared(lowered, key)

    monkeypatch.setattr(
        category_tracks,
        "prepare_keyword_groups",
        lambda _vocab, _terms: ({"sigma": ("sigma",)}, ("viltrox",), checked_match),
    )
    monkeypatch.setattr(
        category_tracks,
        "_matcher",
        lambda: (_ for _ in ()).throw(AssertionError("generic matcher must stay off the prepared hot path")),
    )
    monkeypatch.setattr(
        category_tracks,
        "_competitor_vocab",
        lambda: {"sigma": {"keywords": [" SIGMA ", ""]}},
    )
    monkeypatch.setattr(category_tracks, "_viltrox_terms", lambda: [" VILTROX "])
    monkeypatch.setattr(category_tracks, "_extract_focals", lambda _blob: set())

    day = date(2026, 8, 24)
    rows = [{"pub_day": day.isoformat(), "video_title": "VILTROX and Sigma"}]
    result = category_tracks._prep_evidence(rows, day, day, day)

    assert result[0]["viltrox"] is True
    assert result[0]["brands"] == {"sigma"}
    assert calls == [
        ("viltrox and sigma ", "sigma"),
        ("viltrox and sigma ", "viltrox"),
    ]
