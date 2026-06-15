"""Canonical project-stage mapping layer (service-layer, pure, stdlib-only).

Why this module exists
----------------------
`vkpi_project_kol_assignments.stage` accreted multiple historical / dual-vocabulary
writers. The real distinct raw values in the DB (2026-06-14 census, by count):

    device_sent 843, content_posted 695, contacted 209, discovered 170,
    churned 160, agreed 39, replied 33, discovery 23, shipped 7,
    cancelled 6, received 2, measured 1

This module provides a *read-side* canonical vocabulary so the UI has one stable
lens, WITHOUT migrating or rewriting any stored `stage` value. Old columns are
untouched; this is purely additive / derived.

RED LINES honored: no migration, no mutation of stored stage values/fields,
never reads or touches viltrox_fit_score / rule_v0. Pure stdlib, unit-testable.
"""
from __future__ import annotations

# Canonical, ordered vocabulary. UI reads this; raw fields stay preserved.
# Order encodes pipeline progression (discovery -> ... -> closed).
CANONICAL_STAGES: tuple[str, ...] = (
    "discovered",
    "contacted",
    "agreed",
    "shipped",
    "delivered",
    "observation_pending",
    "content_detected",
    "content_published",
    "retrospective_ready",
    "retrospective_done",
    "closed",
)

# Raw assignment.stage value (lowercased) -> canonical stage.
# Documented per the agreed mapping; covers all 12 real raw values plus a few
# adjacent synonyms that the dual-vocabulary writers may emit.
#
#   discovery / discovered      -> discovered          (pre-contact)
#   contacted                   -> contacted
#   replied                     -> contacted           (still in outreach, no deal yet)
#   agreed                      -> agreed
#   device_sent / shipped       -> shipped             (sample dispatched)
#   received / delivered        -> delivered           (sample arrived)
#   content_posted / content_published -> content_published  (confirmed live)
#   content_detected            -> content_detected    (suspected/auto-detected, unconfirmed)
#   measured                    -> retrospective_ready (metrics captured, review pending)
#   retrospective_done          -> retrospective_done
#   churned / cancelled / closed -> closed             (terminal)
#
# Note: `observation_pending` is a canonical stage with no current raw source
# value; it exists in the vocabulary for the observation window between
# delivery and content detection and may be produced by future writers.
_RAW_TO_CANONICAL: dict[str, str] = {
    "discovery": "discovered",
    "discovered": "discovered",
    "contacted": "contacted",
    "replied": "contacted",
    "agreed": "agreed",
    "device_sent": "shipped",
    "shipped": "shipped",
    "received": "delivered",
    "delivered": "delivered",
    "content_posted": "content_published",
    "content_published": "content_published",
    "published": "content_published",
    "content_detected": "content_detected",
    "measured": "retrospective_ready",
    "retrospective_ready": "retrospective_ready",
    "retrospective_done": "retrospective_done",
    "churned": "closed",
    "cancelled": "closed",
    "closed": "closed",
}

# Canonical stage -> Simplified-Chinese label for the UI.
_LABELS_ZH: dict[str, str] = {
    "discovered": "发现",
    "contacted": "已联系",
    "agreed": "已同意",
    "shipped": "已寄样",
    "delivered": "已签收",
    "observation_pending": "观察中",
    "content_detected": "疑似发布",
    "content_published": "已发布",
    "retrospective_ready": "待复盘",
    "retrospective_done": "复盘完成",
    "closed": "已关闭",
}


def to_canonical(raw_stage: str, stage_status: str = "") -> str:
    """Map a raw assignment.stage value to a canonical stage.

    Always returns a string. Unknown values pass through lowercased/trimmed so
    callers never lose information and never crash on novel writers.

    `stage_status` is accepted for forward-compatibility (e.g. distinguishing
    detected-vs-published via a status flag) but is not required by the current
    mapping; it is intentionally unused for now.
    """
    clean = str(raw_stage or "").strip().lower()
    return _RAW_TO_CANONICAL.get(clean, clean)


def raw_aliases(canonical_stage: str) -> tuple[str, ...]:
    """All raw assignment.stage values that map to the given canonical stage.

    P14:让统计/漏斗/next_action 不再各写各的 raw 串。例如 content_published 的 raw
    别名 = (content_posted, content_published, published)。未知 canonical 原样返回。
    """
    cs = str(canonical_stage or "").strip().lower()
    aliases = tuple(sorted(raw for raw, can in _RAW_TO_CANONICAL.items() if can == cs))
    return aliases or (cs,)


def raw_sql_tuple(*canonical_stages: str) -> str:
    """SQL ``(...)`` 字面量,含落到给定 canonical 阶段的全部 raw 别名,供 ``stage IN <...>`` 用。

    单一事实源:funnel / active_campaigns / next_action 共用同一口径,杜绝"同一项目在不同
    聚合里算法不同"。内联可信常量(无注入面)。
    """
    raws: list[str] = []
    for cs in canonical_stages:
        raws.extend(raw_aliases(cs))
    inner = ",".join("'" + r.replace("'", "''") + "'" for r in sorted(set(raws)))
    return f"({inner})"


def canonical_index(stage: str) -> int:
    """Return the position of `stage` in CANONICAL_STAGES, or -1 if absent."""
    clean = str(stage or "").strip().lower()
    try:
        return CANONICAL_STAGES.index(clean)
    except ValueError:
        return -1


def stage_label_zh(stage: str) -> str:
    """Return the zh-CN label for a canonical stage.

    Unknown stages fall back to the cleaned input string so the UI degrades
    gracefully instead of showing an empty cell.
    """
    clean = str(stage or "").strip().lower()
    return _LABELS_ZH.get(clean, clean)


if __name__ == "__main__":  # pragma: no cover - self-check / smoke test
    _REAL_RAW_STAGES = (
        "device_sent", "content_posted", "contacted", "discovered",
        "churned", "agreed", "replied", "discovery",
        "shipped", "cancelled", "received", "measured",
    )
    print("raw -> canonical  (idx)  label_zh")
    ok = True
    for raw in _REAL_RAW_STAGES:
        canon = to_canonical(raw)
        idx = canonical_index(canon)
        valid = canon in CANONICAL_STAGES
        ok = ok and valid
        flag = "OK" if valid else "BAD"
        print(f"  {raw:18s} -> {canon:20s} ({idx:2d})  {stage_label_zh(canon):8s} [{flag}]")
    print(f"\nall 12 real raw stages map to a valid canonical stage: {ok}")
