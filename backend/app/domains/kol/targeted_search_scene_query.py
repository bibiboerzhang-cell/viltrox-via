"""Preserve controlled scene phrases when constructing server search queries."""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from app.domains.kol.search_intent_text import affirmative_search_text
from app.domains.kol.targeted_search_terms import controlled_aliases_for, required_role_terms_for


def _contains_phrase(query: str, phrase: str) -> bool:
    def fold(value: str) -> str:
        return re.sub(r"[-_\s]+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()

    text, term = fold(query), fold(phrase)
    if any("\u4e00" <= char <= "\u9fff" for char in term):
        return term in text
    return bool(term and re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text))


def preserve_controlled_scene_phrases(query: str, scenes: Iterable[str]) -> str:
    """Keep server-known scenes without changing occupations or inventing intent.

    A default role such as ``product photographer`` may become ``product
    content creator``. Preserve the missing *scene* independently of that role.
    Unknown/custom scenes have no static translation and are left untouched.
    This is construction, not repair of provider-bound queries at validation.
    """
    value = " ".join(query.split())
    if not value:
        return value
    for scene in scenes:
        aliases = controlled_aliases_for("scene", scene)
        if not aliases or any(_contains_phrase(affirmative_search_text(value), alias) for alias in aliases):
            continue
        # Prefer a scene phrase, not a default profession that would add a
        # second occupation to a request for e.g. content creators or educators.
        phrase = next((alias for alias in aliases if not required_role_terms_for(alias)), "")
        if phrase:
            value = f"{value} {phrase}"
    return value
