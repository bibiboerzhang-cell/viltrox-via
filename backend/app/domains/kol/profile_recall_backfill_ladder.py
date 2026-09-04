"""本地腿回填梯:精准命中不够 30 人时,按固定顺序逐级放宽,**每一级都带标记、都记账**。

公测阻断 #1(T 车道实测 4_smart_search):检索 60 → 团队收藏排除砍 49 → 11 → 题材硬筛拒 6
→ 证据门拒 2 → 3 → 类型已知 1 → 资质 0 → 最终 0,而且没有任何兜底,外部测试者第一次搜索
就看到空白。本模块补上那道兜底,但坚持三条红线:

* **不冒充精准命中**:每个回填的人都带 ``backfill_tier`` / ``selection_tier=backfill_*`` /
  ``precision_match=False`` / ``counts_toward_target=False``;缺口(``shortfall``)仍然只按精准
  命中计,回填永远不把「缺 27 人」说成「缺 0 人」。
* **不改排序公式、不写 fit_score**:回填区排在所有精准命中之后;区内照用既有 ``ranking_key``。
* **只放宽可放宽的**:平台 / 国家 / 语言 / 粉丝量 / 器材内容这些显式硬筛**永远不放宽**;
  品牌官方 / 零售 / 无效账号 / 市场不符 / 跨来源重复这些硬拒**永远不回填**。能放宽的只有
  四级,顺序固定:

  1. ``team_favorite``   —— 被同事收藏而隐藏的人(**仅在严口径 / 操作员点名隐藏时才有人**:
     松绑口径下他们从一开始就留在主跑里参与排序,只带「已被同事关注」标注,这一级为空);
  2. ``vertical_relaxed`` —— 只因题材(verticals)一项被硬筛掉的人;
  3. ``evidence_relaxed`` —— 过了硬筛但没找到明确产品相关内容的人(保留 ``no_match_evidence``
     语义:``match_evidence=[]``,不伪造证据);
  4. ``qualification_relaxed`` —— 资质门只因「资料未知/待核验」类原因(非硬拒)没过的人,
     以及类型待核验(unknown 桶)的人。

每一级都把「进了几个、补了几个、为什么没补」写进 ``backfill_ladder`` 诊断;门面用
``result_explanation``(人话,不含内部术语)解释「为什么这么少」。

本模块不碰数据库、不调 LLM;所有阶段能力(水合 / 投影 / 排序 / 资质)由编排层以回调注入,
因此这里没有对 ``profile_recall`` 的反向依赖(环只减不增)。
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from app.domains.kol.identity import canonical_creator_aliases, canonical_creator_key
from app.domains.kol import search_relaxation as _relax

BACKFILL_LADDER_SCHEMA = "recall_backfill_ladder_v1"
RESULT_EXPLANATION_SCHEMA = "recall_result_explanation_v1"
BACKFILL_SELECTION_TIER_PREFIX = "backfill_"
#: 活跃度未知桶已有自己的 selection_tier(前端按它渲染),回填时不覆盖。
_DEFERRED_SELECTION_TIER = "deferred_activity_unknown"

TIER_TEAM_FAVORITE = "team_favorite"
TIER_VERTICAL_RELAXED = "vertical_relaxed"
TIER_EVIDENCE_RELAXED = "evidence_relaxed"
TIER_QUALIFICATION_RELAXED = "qualification_relaxed"
TIER_ORDER: tuple[str, ...] = (
    TIER_TEAM_FAVORITE,
    TIER_VERTICAL_RELAXED,
    TIER_EVIDENCE_RELAXED,
    TIER_QUALIFICATION_RELAXED,
)
#: 卡面文案(门面禁内部术语,所以这里只说人话)。
TIER_LABELS: dict[str, str] = {
    TIER_TEAM_FAVORITE: "已被同事关注",
    TIER_VERTICAL_RELAXED: "题材不完全匹配",
    TIER_EVIDENCE_RELAXED: "未验证到明确的产品相关内容",
    TIER_QUALIFICATION_RELAXED: "资料待核验",
}
#: 每级在 ``relaxed_filters`` 里留下的放宽标记(会话快照白名单字段,回放时仍可见)。
TIER_RELAXED_FILTER: dict[str, str] = {
    TIER_TEAM_FAVORITE: "team_favorite",
    TIER_VERTICAL_RELAXED: "verticals",
    TIER_EVIDENCE_RELAXED: "match_evidence",
    TIER_QUALIFICATION_RELAXED: "qualification_unknowns",
}

#: 资质门里「资料未知 / 待核验」类原因:可回填。硬拒(市场不符、粉丝不足、账号类型、重复)
#: **不在**这里,永远不回填。「视频过旧」是唯一一条随口径浮动的:严口径下仍是硬拒,松绑口径
#: 下由 :func:`soft_reasons_for_policy` 并进来(见 :mod:`search_relaxation`)。
SOFT_QUALIFICATION_REASONS: frozenset[str] = frozenset(
    {
        "low_relevance",
        "market_unknown",
        "market_untrusted_source",
        "language_unknown",
        "profile_type_unknown",
        "platform_unknown",
        "followers_unknown",
        "followers_unknown_rejected",
        "latest_video_unknown",
        "activity_unknown_pending_fetch",
    }
)
#: 显式筛选对应的「未知」原因:操作员点名要了某个市场/平台/语言/类型,这一项的未知就不许
#: 放宽(显式硬筛永不放宽;下游 market/platform 后置过滤也会把他们丢掉,回填了也白填)。
_EXPLICIT_FILTER_UNKNOWNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("market", ("market_unknown", "market_untrusted_source")),
    ("platforms", ("platform_unknown",)),
    ("languages", ("language_unknown",)),
    ("profile_types", ("profile_type_unknown",)),
)
#: 证据放宽级只接受「仅因没有产品相关内容」被资质门拒的人。
EVIDENCE_ONLY_REASONS: frozenset[str] = frozenset({"low_relevance"})


def soft_reasons_for_policy(policy: dict[str, Any] | None) -> frozenset[str]:
    """按本次资质策略算出「可放宽的软原因」:操作员显式要求过的维度,其未知一律算硬。

    松绑口径下额外把「视频陈旧」并进来:那道闸量的是我们的抓取跟进度,不是创作者的活跃度,
    降级成「排后面 + 标注」而不是判死。严口径下这一句返回空集,行为逐字不变。
    """

    soft = set(SOFT_QUALIFICATION_REASONS)
    soft.discard("followers_unknown_rejected")
    soft.update(_relax.relaxable_reasons(policy))
    spec = policy if isinstance(policy, dict) else {}
    for key, codes in _EXPLICIT_FILTER_UNKNOWNS:
        value = spec.get(key)
        requested = bool(value) if not isinstance(value, (list, tuple, set)) else bool(list(value))
        if requested:
            soft.difference_update(codes)
    return frozenset(soft)


def evidence_reasons_for_policy(policy: dict[str, Any] | None) -> frozenset[str]:
    """证据放宽级接受的原因集合:默认只认「没有产品相关内容」。

    松绑口径下再加上「视频陈旧」——否则一个既没证据又被判陈旧的人两级都够不着,
    实测正是这条把证据放宽级的出货压住的。
    """

    return EVIDENCE_ONLY_REASONS | _relax.relaxable_reasons(policy)


#: unknown 桶(类型待核验)进入第四级时的合成原因码。
PROFILE_TYPE_UNKNOWN_REASON = "profile_type_unknown"

#: 「为什么没有更多」的人话标签;未登记的原因码统一归为「其他条件未满足」。
GAP_LABELS: dict[str, str] = {
    "platforms": "不在所选平台",
    "countries": "不符合所选国家/地区",
    "languages": "不符合所选语言",
    "followers_min": "粉丝量低于范围",
    "followers_max": "粉丝量高于范围",
    "gear_content": "未见器材相关内容",
    "verticals": "题材不匹配",
    "market_mismatch": "不符合目标市场",
    "followers_below_minimum": "粉丝量不足",
    "followers_above_maximum": "粉丝量超出范围",
    "latest_video_stale": "近期没有更新视频",
    "latest_video_in_future": "视频时间异常",
    "latest_video_not_active_video": "近期没有视频内容",
    "latest_video_identity_missing": "视频来源无法核对",
    "language_mismatch": "不符合所选语言",
    "profile_type_mismatch": "不符合所选 KOL 类型",
    "platform_mismatch": "不在所选平台",
    "account_own_brand": "自有账号已排除",
    "account_brand_official": "品牌官方账号已排除",
    "account_retailer": "零售/经销账号已排除",
    "account_garbage": "无效账号已排除",
    "duplicate_canonical_identity": "跨来源重复",
    "excluded_region": "不在服务地区",
    "low_reach": "触达量不足",
    "product_scene_evidence_missing": "产品使用场景待核验",
    "market_activation_missing": "市场表现数据待补",
    "insufficient_sample": "市场表现样本不足",
    "insufficient_metric_sample": "市场表现样本不足",
    "below_floor": "市场活性未达门槛",
    "row_missing": "资料缺失",
}
_GAP_FALLBACK_LABEL = "其他条件未满足"
_EXPLANATION_GAP_CAP = 6


def _bump(counter: dict[str, int], key: str, amount: int = 1) -> None:
    counter[key] = counter.get(key, 0) + int(amount)


def _reasons_of(item: dict[str, Any]) -> set[str]:
    proof = item.get("qualification_evidence")
    if not isinstance(proof, dict):
        return set()
    return {str(value) for value in (proof.get("rejection_reasons") or ()) if str(value)}


def identity_aliases(item: dict[str, Any]) -> set[str]:
    """同一个人的全部身份别名(与资质门口径一致),用于跨级去重。"""

    aliases = set(canonical_creator_aliases(item))
    proof = item.get("qualification_evidence")
    if isinstance(proof, dict):
        aliases.update(str(value) for value in (proof.get("canonical_aliases") or ()) if str(value))
    if not aliases:
        key = canonical_creator_key(item) or f"pool:{item.get('kol_pool_id') or ''}"
        aliases.add(key)
    return aliases


# ── 投影阶段收集的「被拒但可回填」候选 ─────────────────────────────────────


@dataclass
class ReserveEntry:
    """投影层在某道闸拒掉的一个候选,连同重建条目所需的一切。"""

    hit: Any
    row: dict[str, Any]
    evidence: dict[str, Any]
    vertical_reading: Any
    unknown_fields: list[str]
    rejected_fields: list[str]
    favorited: bool = False


@dataclass
class BackfillReserve:
    """投影循环的旁路账本:只收「只因题材被拒」与「没找到产品相关内容」两类。

    ``enabled=False`` 时是空操作(非 smart-local 车道不回填,行为零漂移)。
    """

    enabled: bool = True
    favorited: bool = False
    vertical: list[ReserveEntry] = field(default_factory=list)
    no_evidence: list[ReserveEntry] = field(default_factory=list)
    hard_filter_gaps: dict[str, int] = field(default_factory=dict)

    def note_hard_filter(self, entry: ReserveEntry) -> None:
        if not self.enabled:
            return
        entry.favorited = self.favorited
        rejected = [str(value) for value in entry.rejected_fields if str(value)]
        if rejected == ["verticals"]:
            self.vertical.append(entry)
            return
        for name in rejected:
            _bump(self.hard_filter_gaps, name)

    def note_no_evidence(self, entry: ReserveEntry) -> None:
        if not self.enabled:
            return
        entry.favorited = self.favorited
        self.no_evidence.append(entry)

    def absorb(self, other: "BackfillReserve") -> None:
        self.vertical.extend(other.vertical)
        self.no_evidence.extend(other.no_evidence)
        for name, count in other.hard_filter_gaps.items():
            _bump(self.hard_filter_gaps, name, count)


# ── 编排层注入的阶段能力 ────────────────────────────────────────────────────


@dataclass(frozen=True)
class LadderPhases:
    """回填梯需要的阶段能力,全部由编排层以回调注入(本模块零反向依赖)。"""

    #: hits -> hydration(至少含 qualification_rows / evidence_by_id)
    hydrate: Callable[[list[Any]], dict[str, Any]]
    #: (hydration, reserve) -> projection(含 buckets:creator/reviewer/unknown)
    project: Callable[[dict[str, Any], BackfillReserve], dict[str, Any]]
    #: entry -> 产品相关内容证据(可能为空列表)
    match_evidence: Callable[[ReserveEntry], list[dict[str, Any]]]
    #: (entry, field_evidence) -> 完整条目
    materialize: Callable[[ReserveEntry, list[dict[str, Any]]], dict[str, Any]]
    #: 就地给一批回填候选打排序分(同一套公式,不改)
    rank: Callable[[list[dict[str, Any]]], None]
    #: (candidates, capacity, excluded_aliases, rows, evidence) -> (accepted, contract)
    qualify: Callable[..., tuple[list[dict[str, Any]], dict[str, Any]]]
    ranking_key: Callable[[dict[str, Any]], Any]


@dataclass
class LadderState:
    capacity: int
    rows: dict[int, dict[str, Any]]
    evidence: dict[int, dict[str, Any]]
    excluded_aliases: set[str]
    soft_reasons: frozenset[str] = SOFT_QUALIFICATION_REASONS
    evidence_reasons: frozenset[str] = EVIDENCE_ONLY_REASONS
    filled: list[dict[str, Any]] = field(default_factory=list)
    filled_by_tier: dict[str, int] = field(default_factory=dict)
    rungs: list[dict[str, Any]] = field(default_factory=list)
    pending_soft: list[dict[str, Any]] = field(default_factory=list)
    gaps: dict[str, int] = field(default_factory=dict)

    def take(self, items: Iterable[dict[str, Any]], tier: str) -> int:
        taken = 0
        for item in items:
            if self.capacity <= 0:
                break
            mark_backfill_item(item, tier)
            self.filled.append(item)
            self.excluded_aliases.update(identity_aliases(item))
            self.capacity -= 1
            taken += 1
        _bump(self.filled_by_tier, tier, taken)
        return taken


@dataclass(frozen=True)
class LadderOutcome:
    items: list[dict[str, Any]]
    diagnostics: dict[str, Any]


# ── 标记 ────────────────────────────────────────────────────────────────────


def _stamp_backfill_notes(item: dict[str, Any], reasons: set[str]) -> None:
    """卡面注脚:放宽了哪一级 + 具体卡在哪一条(都只说人话)。"""

    notes = [TIER_LABELS[code] for code in TIER_ORDER if code in reasons]
    for reason in sorted(_reasons_of(item)):
        label = GAP_LABELS.get(reason)
        if label and label not in notes:
            notes.append(label)
    item["backfill_notes"] = notes
    if TIER_TEAM_FAVORITE in reasons:
        _relax.annotate_team_favorite(item)
    _relax.annotate_stale_activity(item)


def mark_backfill_item(item: dict[str, Any], tier: str) -> dict[str, Any]:
    """给回填条目盖章:不是精准命中、不计入目标、说明放宽了什么。"""

    reasons = set(item.get("backfill_reasons") or ())
    reasons.add(tier)
    if item.get("_backfill_favorited") is True:
        reasons.add(TIER_TEAM_FAVORITE)
    item.pop("_backfill_favorited", None)
    _stamp_backfill_notes(item, reasons)
    item["backfill_tier"] = tier
    item["backfill_label"] = TIER_LABELS.get(tier, tier)
    item["backfill_reasons"] = sorted(reasons)
    item["precision_match"] = False
    item["counts_toward_target"] = False
    item["filter_status"] = "backfill"
    relaxed = [str(value) for value in (item.get("relaxed_filters") or ()) if str(value)]
    for code in sorted(reasons):
        marker = TIER_RELAXED_FILTER.get(code)
        if marker and marker not in relaxed:
            relaxed.append(marker)
    item["relaxed_filters"] = relaxed
    if item.get("selection_tier") != _DEFERRED_SELECTION_TIER:
        item["selection_tier"] = f"{BACKFILL_SELECTION_TIER_PREFIX}{tier}"
    proof = item.get("qualification_evidence")
    if isinstance(proof, dict):
        proof["counts_toward_target"] = False
        proof["backfill_tier"] = tier
    return item


def is_backfill_item(item: Any) -> bool:
    return isinstance(item, dict) and bool(item.get("backfill_tier"))


# ── 梯级执行 ────────────────────────────────────────────────────────────────


def _dedupe_by_identity(
    items: list[dict[str, Any]],
    excluded: set[str],
) -> list[dict[str, Any]]:
    seen = set(excluded)
    output: list[dict[str, Any]] = []
    for item in items:
        aliases = identity_aliases(item)
        if aliases.intersection(seen):
            continue
        seen.update(aliases)
        output.append(item)
    return output


def _run_rung(
    state: LadderState,
    tier: str,
    candidates: list[dict[str, Any]],
    *,
    accept_reasons: frozenset[str],
    phases: LadderPhases,
) -> list[dict[str, Any]]:
    """跑一级:先过资质门(通过者直接补),再按本级允许的「软原因」补;返回没补上的人。"""

    rung: dict[str, Any] = {
        "tier": tier,
        "label": TIER_LABELS.get(tier, tier),
        "candidates": len(candidates),
        "capacity_before": state.capacity,
        "filled": 0,
        "rejected_by_reason": {},
    }
    if not candidates or state.capacity <= 0:
        rung["status"] = "skipped" if candidates else "no_candidates"
        state.rungs.append(rung)
        return list(candidates)
    phases.rank(candidates)
    accepted, contract = phases.qualify(
        candidates,
        state.capacity,
        state.excluded_aliases,
        state.rows,
        state.evidence,
    )
    accepted_ids = {id(item) for item in accepted}
    leftovers = [item for item in candidates if id(item) not in accepted_ids]
    soft = [
        item
        for item in leftovers
        if _reasons_of(item) and _reasons_of(item) <= accept_reasons
    ]
    soft.sort(key=phases.ranking_key, reverse=True)
    accepted_aliases = {alias for item in accepted for alias in identity_aliases(item)}
    soft = _dedupe_by_identity(soft, state.excluded_aliases | accepted_aliases)
    taken = state.take([*accepted, *soft], tier)
    rung["filled"] = taken
    rung["qualified_passed"] = len(accepted)
    rung["soft_accepted"] = max(0, taken - len(accepted))
    rung["rejected_by_reason"] = dict(contract.get("rejected_by_reason") or {})
    rung["status"] = "filled" if taken else "nothing_accepted"
    state.rungs.append(rung)
    filled_ids = {id(item) for item in state.filled}
    return [item for item in candidates if id(item) not in filled_ids]


def _soft_pool(state: LadderState, items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """从已评过资质的人里挑出「只因软原因没过」的;其余是硬拒,由 _note_hard_rejects 记账。"""

    return [item for item in items if _reasons_of(item) and _reasons_of(item) <= state.soft_reasons]


def _note_hard_rejects(state: LadderState, items: Iterable[dict[str, Any]]) -> None:
    for item in items:
        reasons = _reasons_of(item)
        if not reasons or reasons <= state.soft_reasons:
            continue
        for reason in sorted(reasons - state.soft_reasons):
            _bump(state.gaps, reason)


def _bucket_items(projection: dict[str, Any], *names: str) -> list[dict[str, Any]]:
    buckets = projection.get("buckets") if isinstance(projection.get("buckets"), dict) else {}
    return [item for name in names for item in (buckets.get(name) or []) if isinstance(item, dict)]


def _unselected_main_pool(
    projection: dict[str, Any],
    selected: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """主跑里没被选中的 creator/reviewer(已评资质)与 unknown 桶(从未评)。"""

    selected_ids = {id(item) for item in selected}
    typed = [
        item
        for item in _bucket_items(projection, "creator", "reviewer")
        if id(item) not in selected_ids
    ]
    unknown = _bucket_items(projection, "unknown")
    for item in unknown:
        reasons = set(item.get("backfill_reasons") or ())
        reasons.add(PROFILE_TYPE_UNKNOWN_REASON)
        item["backfill_reasons"] = sorted(reasons)
    return typed, unknown


def _rung_team_favorite(
    state: LadderState,
    *,
    favorited_hits: list[Any],
    reserve: BackfillReserve,
    phases: LadderPhases,
) -> None:
    if not favorited_hits:
        state.rungs.append(
            {"tier": TIER_TEAM_FAVORITE, "label": TIER_LABELS[TIER_TEAM_FAVORITE],
             "candidates": 0, "capacity_before": state.capacity, "filled": 0,
             "rejected_by_reason": {}, "status": "no_candidates"}
        )
        return
    hydration = phases.hydrate(list(favorited_hits))
    state.rows.update(hydration.get("qualification_rows") or {})
    state.evidence.update(hydration.get("evidence_by_id") or {})
    favorite_reserve = BackfillReserve(favorited=True)
    projection = phases.project(hydration, favorite_reserve)
    reserve.absorb(favorite_reserve)
    typed = _bucket_items(projection, "creator", "reviewer")
    unknown = _bucket_items(projection, "unknown")
    for item in [*typed, *unknown]:
        item["_backfill_favorited"] = True
    for item in unknown:
        item["backfill_reasons"] = [PROFILE_TYPE_UNKNOWN_REASON]
    leftovers = _run_rung(
        state, TIER_TEAM_FAVORITE, typed, accept_reasons=frozenset(), phases=phases,
    )
    _note_hard_rejects(state, leftovers)
    state.pending_soft.extend(_soft_pool(state, leftovers))
    state.pending_soft.extend(unknown)


def _materialize_entries(
    entries: Iterable[ReserveEntry],
    phases: LadderPhases,
    *,
    with_evidence: bool,
) -> tuple[list[dict[str, Any]], list[ReserveEntry]]:
    """把储备条目变成完整条目;``with_evidence`` 时没证据的转交下一级。"""

    items: list[dict[str, Any]] = []
    carried: list[ReserveEntry] = []
    for entry in entries:
        field_evidence = phases.match_evidence(entry) if with_evidence else []
        if with_evidence and not field_evidence:
            carried.append(entry)
            continue
        item = phases.materialize(entry, field_evidence)
        item["_backfill_favorited"] = bool(entry.favorited)
        reasons: set[str] = set()
        if "verticals" in entry.rejected_fields:
            reasons.add(TIER_VERTICAL_RELAXED)
        if not field_evidence:
            reasons.add(TIER_EVIDENCE_RELAXED)
        item["backfill_reasons"] = sorted(reasons)
        items.append(item)
    return items, carried


def _rung_vertical_and_evidence(
    state: LadderState,
    *,
    reserve: BackfillReserve,
    phases: LadderPhases,
) -> None:
    vertical_items, carried = _materialize_entries(reserve.vertical, phases, with_evidence=True)
    leftovers = _run_rung(
        state, TIER_VERTICAL_RELAXED, vertical_items, accept_reasons=frozenset(), phases=phases,
    )
    _note_hard_rejects(state, leftovers)
    state.pending_soft.extend(_soft_pool(state, leftovers))

    # 没证据的题材候选顺流到证据级(两个放宽标记都带上,由 _materialize_entries 按 entry 判定)。
    evidence_items, _unused = _materialize_entries(
        [*reserve.no_evidence, *carried], phases, with_evidence=False,
    )
    leftovers = _run_rung(
        state, TIER_EVIDENCE_RELAXED, evidence_items, accept_reasons=state.evidence_reasons, phases=phases,
    )
    _note_hard_rejects(state, leftovers)
    state.pending_soft.extend(_soft_pool(state, leftovers))


def run_backfill_ladder(
    *,
    target: int,
    selected: list[dict[str, Any]],
    projection: dict[str, Any],
    hydration: dict[str, Any],
    reserve: BackfillReserve,
    favorited_hits: list[Any],
    phases: LadderPhases,
    soft_reasons: frozenset[str] | None = None,
    evidence_reasons: frozenset[str] | None = None,
) -> LadderOutcome:
    """按固定四级回填到 ``target``;返回回填条目(已盖章)与逐级账目。"""

    capacity = max(0, int(target) - len(selected))
    state = LadderState(
        capacity=capacity,
        rows=dict(hydration.get("qualification_rows") or {}),
        evidence=dict(hydration.get("evidence_by_id") or {}),
        excluded_aliases={alias for item in selected for alias in identity_aliases(item)},
        soft_reasons=soft_reasons if soft_reasons is not None else SOFT_QUALIFICATION_REASONS,
        evidence_reasons=(
            evidence_reasons if evidence_reasons is not None else EVIDENCE_ONLY_REASONS
        ),
    )
    for name, count in reserve.hard_filter_gaps.items():
        _bump(state.gaps, name, count)
    if capacity <= 0:
        return LadderOutcome(items=[], diagnostics=_ladder_diagnostics(state, target, status="not_needed"))

    _rung_team_favorite(state, favorited_hits=favorited_hits, reserve=reserve, phases=phases)
    _rung_vertical_and_evidence(state, reserve=reserve, phases=phases)

    typed, unknown = _unselected_main_pool(projection, selected)
    _note_hard_rejects(state, typed)
    pool = _dedupe_by_identity(
        [*state.pending_soft, *_soft_pool(state, typed), *unknown],
        state.excluded_aliases,
    )
    leftovers = _run_rung(
        state, TIER_QUALIFICATION_RELAXED, pool, accept_reasons=state.soft_reasons, phases=phases,
    )
    _note_hard_rejects(state, leftovers)
    status = "filled" if state.filled else "nothing_to_backfill"
    return LadderOutcome(items=list(state.filled), diagnostics=_ladder_diagnostics(state, target, status=status))


def _ladder_diagnostics(state: LadderState, target: int, *, status: str) -> dict[str, Any]:
    return {
        "schema": BACKFILL_LADDER_SCHEMA,
        "status": status,
        "target": int(target),
        "filled_total": len(state.filled),
        "filled_by_tier": {tier: state.filled_by_tier.get(tier, 0) for tier in TIER_ORDER},
        "remaining_capacity": max(0, state.capacity),
        "rungs": list(state.rungs),
        "gaps": dict(sorted(state.gaps.items())),
        "soft_reasons": sorted(state.soft_reasons),
        "policy": "favorites_soft_excluded;verticals_relaxable;explicit_filters_never_relaxed",
    }


# ── 门面解释(人话) ─────────────────────────────────────────────────────────


def _gap_entries(gaps: dict[str, int]) -> list[dict[str, Any]]:
    entries = [
        {"code": str(code), "label": GAP_LABELS.get(str(code), _GAP_FALLBACK_LABEL), "count": int(count)}
        for code, count in gaps.items()
        if int(count or 0) > 0
    ]
    entries.sort(key=lambda entry: (-entry["count"], entry["code"]))
    return entries[:_EXPLANATION_GAP_CAP]


def _headline(requested: int, precise: int, backfill: int, deferred: int = 0) -> str:
    """先说找到几个人,再说凭什么。

    旧口径以「精准命中 0 人」开头,即使卡面上站着 30 个人也读成「没搜到」——这正是
    「搜索越来越笨」的观感来源。有人就先报人数;真的一个都没有时仍然如实说没有。

    ``deferred``(资料待核验、占了位但不计入目标)和 ``backfill`` 一样算「已标注」,
    绝不并进 ``precise`` —— 那正是旧口径把「精准命中」说虚的地方。
    """

    labelled = int(backfill) + int(deferred)
    total = int(precise) + labelled
    if not total:
        return "本次没有找到符合全部条件的人选"
    if precise >= requested:
        return f"精准命中 {precise} 人"
    if not labelled:
        return f"为你找到 {precise} 人(均为精准命中)"
    if not precise:
        return f"为你找到 {total} 人(均已标注入选原因,暂无精准命中)"
    return f"为你找到 {total} 人:精准命中 {precise} 人,另 {labelled} 人已标注入选原因"


def explain_result(
    *,
    requested: int,
    precise_count: int,
    backfill_by_tier: dict[str, int] | None,
    gaps: dict[str, int] | None,
    favorite_excluded: int = 0,
    favorite_annotated: int = 0,
    deferred_count: int = 0,
) -> dict[str, Any]:
    """门面用的「为什么这么少 / 补了什么」——只说人话,不带内部术语。"""

    tiers = {tier: int((backfill_by_tier or {}).get(tier) or 0) for tier in TIER_ORDER}
    backfill = sum(tiers.values())
    deferred = max(0, int(deferred_count))
    reasons = [
        {"code": tier, "label": TIER_LABELS[tier], "count": count}
        for tier, count in tiers.items()
        if count > 0
    ]
    gap_entries = _gap_entries(dict(gaps or {}))
    note = ""
    if backfill or deferred:
        # 「资料待核验」也属于已标注的人:补充区为空但他们在场时,不许再说「没有可补充的人选」。
        note = "补充的人选不是精准命中,已按原因标注,可按需忽略。"
    elif precise_count < requested:
        note = "没有可补充的人选;可放宽平台/地区/粉丝量筛选再试。"
    return {
        "schema": RESULT_EXPLANATION_SCHEMA,
        "requested": int(requested),
        "precise_count": int(precise_count),
        "backfill_count": backfill,
        "deferred_count": deferred,
        "returned_count": int(precise_count) + backfill + deferred,
        "headline": _headline(int(requested), int(precise_count), backfill, deferred),
        "backfill_reasons": reasons,
        "gaps": gap_entries,
        "favorited_by_team_hidden": max(0, int(favorite_excluded) - tiers[TIER_TEAM_FAVORITE]),
        # 松绑口径下他们没被藏起来,而是带着「已被同事关注」站在结果里——这个数说的是
        # 「有几个人是这么进来的」,和上面那个「被藏了几个」是两件事,不能互相冒充。
        "favorited_by_team_shown": max(0, int(favorite_annotated)),
        "note": note,
    }


def merge_tier_counts(*blocks: dict[str, Any] | None) -> dict[str, int]:
    """把多次(多 cell)回填的 ``filled_by_tier`` 加总。"""

    merged = {tier: 0 for tier in TIER_ORDER}
    for block in blocks:
        counts = block.get("filled_by_tier") if isinstance(block, dict) else None
        for tier, count in (counts or {}).items():
            if tier in merged:
                merged[tier] += int(count or 0)
    return merged


def merge_gaps(*blocks: dict[str, Any] | None) -> dict[str, int]:
    merged: dict[str, int] = {}
    for block in blocks:
        gaps = block.get("gaps") if isinstance(block, dict) else None
        for code, count in (gaps or {}).items():
            _bump(merged, str(code), int(count or 0))
    return merged


__all__ = [
    "BACKFILL_LADDER_SCHEMA",
    "BackfillReserve",
    "LadderOutcome",
    "LadderPhases",
    "RESULT_EXPLANATION_SCHEMA",
    "ReserveEntry",
    "SOFT_QUALIFICATION_REASONS",
    "TIER_EVIDENCE_RELAXED",
    "TIER_LABELS",
    "TIER_ORDER",
    "TIER_QUALIFICATION_RELAXED",
    "TIER_TEAM_FAVORITE",
    "TIER_VERTICAL_RELAXED",
    "explain_result",
    "identity_aliases",
    "is_backfill_item",
    "mark_backfill_item",
    "merge_gaps",
    "merge_tier_counts",
    "run_backfill_ladder",
    "soft_reasons_for_policy",
]
