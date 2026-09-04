"""Candidate ranking for the free-text product resolver."""
from __future__ import annotations

from typing import Any


def official_duplicate_for_model_code(
    rows: list[dict[str, Any]],
    *,
    model_code: str,
    query_model_codes: Any,
    row_words: Any,
    normkey: Any,
) -> dict[str, Any] | None:
    """Select the sole official duplicate only when all rows are one model."""

    matching = [
        row for row in rows
        if model_code in query_model_codes(row_words(row)[0])
    ]
    if not matching:
        return None
    categories = {normkey(row.get("category_main")) for row in matching if normkey(row.get("category_main"))}
    mounts = {normkey(row.get("mount")) for row in matching if normkey(row.get("mount"))}
    if len(categories) > 1 or len(mounts) > 1:
        return None

    def _is_official(row: dict[str, Any]) -> bool:
        if str(row.get("status") or "").strip().lower() == "official":
            return True
        try:
            return float(row.get("source_confidence") or 0) >= 0.9
        except (TypeError, ValueError):
            return False

    official = [row for row in matching if _is_official(row)]
    return official[0] if len(official) == 1 else None


def select_scored_product(
    *,
    text: str,
    probe_tokens: list[str],
    pool: dict[str, dict[str, Any]],
    stopwords: frozenset[str],
    compact_pro_re: Any,
    query_tokens: Any,
    model_code_mentions: Any,
    model_code_score_tokens: Any,
    pro_is_product_series: Any,
    score_product: Any,
    official_duplicate_for_model_code: Any,
    public_product_projection: Any,
    row_words: Any,
) -> dict[str, Any] | None:
    """Rank a constrained candidate pool without changing resolver policy."""

    base = query_tokens(text)
    code_mentions = model_code_mentions(text)
    product_pro = pro_is_product_series(text)
    score_tokens = [
        token
        for token in dict.fromkeys(
            base
            + probe_tokens
            + model_code_score_tokens([code for code, _display in code_mentions])
        )
        if len(token) >= 2
        and token not in stopwords
        and (token != "pro" or product_pro)
    ]
    if not score_tokens:
        return None
    scored = [(score_product(product, score_tokens), product) for product in pool.values()]
    if not scored:
        return None
    best_primary = max((score[0], score[1]) for score, _product in scored)
    if best_primary[1] < 2:
        return None
    winners = [
        (score, product)
        for score, product in scored
        if (score[0], score[1]) == best_primary
    ]
    if len(winners) > 1 and len(code_mentions) == 1:
        model_code, display_code = code_mentions[0]
        canonical = official_duplicate_for_model_code(
            [product for _score_value, product in winners],
            model_code=model_code,
        )
        if canonical is not None:
            projection = public_product_projection(
                canonical,
                match_score=next(score for score, product in winners if product is canonical),
            )
            projection.update({
                "resolution_kind": "model_code_exact",
                "resolved_model_code": display_code,
            })
            return projection
    if len(winners) > 1:
        min_series_len = min(score[2] for score, _product in winners)
        winners = [
            (score, product)
            for score, product in winners
            if score[2] == min_series_len
        ]
    if len(winners) != 1:
        return None
    best_score, best = winners[0]
    compact_codes = compact_pro_re.findall(text.lower())
    if compact_codes and "pro" in score_tokens:
        _blob, _blob_sp, winner_words = row_words(best)
        if "pro" in winner_words and not any(code in winner_words for code in compact_codes):
            return None
    return public_product_projection(best, match_score=best_score)
