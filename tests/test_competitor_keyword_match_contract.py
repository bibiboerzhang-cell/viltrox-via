from __future__ import annotations

import random
import re
import string

from app.domains.kol.competitor_text import _keyword_match


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
        assert _keyword_match(text, keyword) is _reference_match(text, keyword)


def test_keyword_match_matches_previous_regex_for_deterministic_fuzz_corpus() -> None:
    rng = random.Random(20260714)
    alphabet = string.ascii_letters + string.digits + " _-@/(),.#"
    keywords = ["sigma", "tamron", "godox", "yn lens", "@meike_global", "x1"]
    for _ in range(500):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 120)))
        keyword = rng.choice(keywords)
        assert _keyword_match(text, keyword) is _reference_match(text, keyword)
