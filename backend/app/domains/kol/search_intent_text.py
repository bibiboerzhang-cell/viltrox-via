"""Pure helpers for keeping negated search constraints out of positive intent."""
from __future__ import annotations

import re
from typing import Any


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


_POSITIVE_AND_BOUNDARY = (
    r"(?=\s*\band\s+(?:also\s+)?(?:shoot(?:s|ing)?|cover(?:s|ing)?|"
    r"film(?:s|ing)?|creat(?:e|es|ing)|mak(?:e|es|ing)|do(?:es|ing)?|"
    r"work(?:s|ing)?)\b)"
)
_BOUNDARY = (
    rf"(?:{_POSITIVE_AND_BOUNDARY}|"
    r"(?=\s*(?:[,，。；;]|只拍|只要|只做|改找|而是|但是|但|然后|"
    r"还要|同时|\b(?:and\s+who|who|that|which|while|but|instead|then)\b|$)))"
)
_NEGATED_SPANS = (
    re.compile(
        r"(?:不限|不限制|无需指定)\s*(?:镜头|器材|设备|产品|型号|sku)",
        re.IGNORECASE,
    ),
    # Chinese action negation consumes the verb and its object.  It runs before
    # requirement negation so "不要拍婚礼" cannot turn wedding positive.
    re.compile(
        rf"(?:不要\s*(?:找|拍|做|接|使用)|不找|不拍|不做|不接)\s*"
        rf"[^,，。；;的]*?(?:的(?=\S)|{_BOUNDARY})",
        re.IGNORECASE,
    ),
    # Requirement/equipment negation may be followed directly by a positive
    # predicate: "不用闪光灯拍婚礼" keeps "拍婚礼" searchable.
    re.compile(
        rf"(?:不要|不需要|不要求|不用|无需|不使用|不打|排除)\s*"
        rf"[^,，。；;的拍做会]*?(?=拍|做|会|还要|同时|[,，。；;]|$)",
        re.IGNORECASE,
    ),
    # Attributive negative role, e.g. "非器材评测的婚礼摄影师".
    re.compile(r"非\s*(?:器材\s*)?(?:评测|测评)(?:博主|达人|创作者|人)?\s*的?", re.IGNORECASE),
    re.compile(r"非\s*(?!洲)[^,，。；;的]{1,24}\s*的", re.IGNORECASE),
    # Paired/exception exclusions must disappear before positive segment
    # extraction, otherwise an excluded wedding/portrait becomes an AND cell.
    re.compile(rf"\bneither\b[^,，。；;]*?\bnor\b[^,，。；;]*?{_BOUNDARY}", re.IGNORECASE),
    re.compile(rf"\bexcept(?:\s+for)?\b[^,，。；;]*?(?:{_POSITIVE_AND_BOUNDARY}|{_BOUNDARY})", re.IGNORECASE),
    re.compile(rf"\bexclud(?:e|es|ed|ing)\b[^,，。；;]*?(?:{_POSITIVE_AND_BOUNDARY}|{_BOUNDARY})", re.IGNORECASE),
    re.compile(rf"\bavoid(?:s|ed|ing)?\b[^,，。；;]*?(?:{_POSITIVE_AND_BOUNDARY}|{_BOUNDARY})", re.IGNORECASE),
    re.compile(
        rf"\b(?:who\s+)?never\s+(?:shoot(?:s|ing)?|cover(?:s|ing)?|"
        rf"film(?:s|ing)?|mak(?:e|es|ing)|creat(?:e|es|ing)|do(?:es|ing)?|"
        rf"work(?:s|ing)?|uses?|using)\b[^,，。；;]*?{_BOUNDARY}",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:is|are|was|were|can|could|should|would)n['’]t\s+"
        rf"[^,，。；;]*?(?:{_POSITIVE_AND_BOUNDARY}|{_BOUNDARY})",
        re.IGNORECASE,
    ),
    # English verb negation: "do not need 35 LAB", "don't find reviewers".
    re.compile(
        rf"\b(?:do\s+not|does\s+not|don't|doesn't)\s+"
        rf"(?:need|want|require|find|include|shoot|cover|do|make|create)\s+"
        rf"[^,，。；;]*?{_BOUNDARY}",
        re.IGNORECASE,
    ),
    # Bounded noun negation: "not gear reviewers".
    re.compile(rf"\bnot\s+[^,，。；;]*?{_BOUNDARY}", re.IGNORECASE),
    # Requirement phrases may follow a positive clause after a comma.
    re.compile(
        rf"\bno\s+(?:specific\s+|particular\s+|any\s+)?"
        rf"(?:lens|gear|equipment|sku|product|model)s?"
        rf"(?:\s+(?:is\s+)?required|\s+requirements?)?[^,，。；;]*?{_BOUNDARY}",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:without|regardless\s+of)\s+(?:any\s+)?"
        rf"(?:lens|gear|equipment|sku|product|model)s?"
        rf"(?:\s+requirements?)?[^,，。；;]*?{_BOUNDARY}",
        re.IGNORECASE,
    ),
    re.compile(rf"\b(?:without|excluding)\s+[^,，。；;]*?{_BOUNDARY}", re.IGNORECASE),
    # A negative lighting preference is not positive product capability.
    # Remove only the capability phrase so the requested person and scene stay
    # searchable ("no-flash street photographers" -> "street photographers").
    re.compile(
        r"\bno(?:[-\s]+)(?:on[-\s]+camera[-\s]+)?"
        r"(?:flash(?:es)?|strobe(?:s)?|speedlights?|speedlites?)\b",
        re.IGNORECASE,
    ),
    # Controlled hyphenated exclusion.  Keep this narrow: phrases such as
    # "non-profit documentary" are a positive vertical, while "non-wedding"
    # is an explicit exclusion that must never become a required scene.
    re.compile(
        r"\bnon[-\s]+(?:wedding|bridal)(?:[-\s]+(?:photo(?:graphers?|graphy)|"
        r"video(?:graphers?|graphy)|filmmakers?))?\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:who\s+)?never\s+(?:(?:uses?|using|needs?|requ(?:ire|ires|iring))\s+|"
        rf"shoot(?:s|ing)?\s+with\s+)(?:an?\s+)?(?:on[-\s]+camera[-\s]+)?"
        rf"(?:flash(?:es)?|strobe(?:s)?|speedlights?|speedlites?)\b[^,，。；;]*?{_BOUNDARY}",
        re.IGNORECASE,
    ),
)


def affirmative_search_text(value: Any) -> str:
    """Return only affirmative clauses used for resolver and people planning.

    This is intentionally conservative: it removes explicit negated spans but
    does not attempt sentiment analysis or rewrite the operator's request.
    """

    text = re.sub(r"\bnot\s+only\b", "both", _text(value), flags=re.IGNORECASE)
    for pattern in _NEGATED_SPANS:
        text = pattern.sub(" ", text)
    # A contrast connector left behind after deleting its negative clause is
    # grammar, not a vertical or occupation ("never weddings but portraits").
    text = re.sub(r"(?<![a-z])(?:but|instead)(?![a-z])|但是|但", " ", text, flags=re.IGNORECASE)
    return _text(re.sub(r"^[,，。；;\s]+|[,，。；;\s]+$", " ", text))


__all__ = ["affirmative_search_text"]
