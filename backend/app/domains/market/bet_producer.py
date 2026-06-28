"""闭环C · inbox→bet 自动桥(押注自动化生产者)。

现状:vkpi_bet_ledger 仅手工 2 条,无任何 producer 从市场信号自动下注。本模块补这条链:
读「高置信」市场信号/机会窗(prediction.predict_opportunities + 未过期竞品信号),为每个
机会生成一条 **bet 草案**(hypothesis / probability / review_at / evidence_refs),再 materialize
成 vkpi_action_inbox 里 category='bet' 的待审建议——等人审批后才由人手真正 create_bet。

设计红线(对齐 producers.py / bet_ledger.py 范式):
- **绝不自动真下注**:dry_run 默认 True,只返回草案不写任何库;dry_run=False 也只 persist 到
  action_inbox(自身台账,requires_approval=True),**永不**直接 bet_ledger.create_bet。
- **绝不烧大钱**:bet 草案 uses_llm=False、estimated_cost_cents=0(押注是判断,不烧 LLM/Apify)。
- 纯只读合成现成引擎,零伪造平台数据,**零触 viltrox_fit_score**。
- 容错隔离:信号源坏(列漂移/引擎异常)→ 该源返回空,不拖垮产出。
- probability 由信号 confidence 映射(high/med/low → 0.7/0.55/0.4),并按 confidence_cap 封顶;
  无真销量时 prediction 已把 cap 压到 medium,这里据此封顶概率,诚实不浮夸。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# inbox category(新建类;不复用 producers.py 9 类,bet 是独立判断类)。
_CATEGORY = "bet"

# confidence(人话档)→ 概率默认。high/med/low 对应 0.7/0.55/0.4;封顶逻辑见下。
_CONF_TO_PROB: dict[str, float] = {"high": 0.70, "medium": 0.55, "low": 0.40}
_CAP_TO_PROB: dict[str, float] = {"high": 0.85, "medium": 0.60, "low": 0.45}

# 默认验证窗口:机会窗 21 天到期复盘(bet_ledger.scan_due_bets 据 review_at 逼出 outcome)。
_DEFAULT_REVIEW_DAYS = 21


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_today(today: date | str | None) -> date:
    """把传入的 today(date / 'YYYY-MM-DD' 串 / None)收敛成一个 date 锚。

    时间锚必须从 payload/参数传入(可测、可复现),不依赖不可控的墙钟读取;
    缺省才退化到 _now().date()(仅作兜底,生产调用应显式传 today)。
    """
    if isinstance(today, date) and not isinstance(today, datetime):
        return today
    if isinstance(today, datetime):
        return today.date()
    if isinstance(today, str):
        s = today.strip()
        if s:
            try:
                return date.fromisoformat(s[:10])
            except ValueError:
                logger.debug("bet_producer.bad_today", extra={"today": s})
    return _now().date()


def _load_opportunities(*, window_days: int, limit: int) -> dict[str, Any]:
    """只读取 prediction 机会窗(含 confidence_cap)。引擎异常 → 安全空结果(诚实)。"""
    try:
        from app.domains.market import prediction

        return prediction.predict_opportunities(window_days=window_days, limit=limit) or {}
    except Exception:
        logger.debug("bet_producer.prediction_failed", exc_info=True)
        return {}


def _prob_for(confidence: str, cap: str) -> float:
    """confidence(人话)→ 概率,并按 confidence_cap 封顶。无真销量(cap=medium)时压到 ≤0.60。"""
    base = _CONF_TO_PROB.get(str(confidence or "").strip().lower(), 0.40)
    ceil = _CAP_TO_PROB.get(str(cap or "").strip().lower(), 0.60)
    return round(min(base, ceil), 2)


def _opportunity_due_date(opp: dict[str, Any]) -> date | None:
    """机会窗若自带到期日(deadline / expires_at / review_at / due_date)→ 取其 date;否则 None。

    只读机会窗 payload 里已有的时间字段,不引入墙钟;无可解析值则交回默认窗口逻辑。
    """
    for key in ("deadline", "expires_at", "review_at", "due_date"):
        raw = opp.get(key)
        if not raw:
            continue
        s = str(raw).strip()
        if not s:
            continue
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            continue
    return None


def _draft_from_opportunity(opp: dict[str, Any], *, cap: str, idx: int, today: date) -> dict[str, Any]:
    """把一个机会窗映射成 bet 草案 dict(尚未落库;字段对齐 bet_ledger.create_bet 契约)。

    时间锚 today 由调用方传入(参数/payload),据此推出 verification_date(复盘日 = today + 窗口);
    机会窗自带到期(deadline/expires_at/review_at)时优先采信其日期,否则用默认窗口。
    """
    title = str(opp.get("title") or f"机会窗 #{idx}").strip()
    basis = str(opp.get("basis") or "").strip()
    action = str(opp.get("suggested_action") or "").strip()
    conf = str(opp.get("confidence") or "low").strip().lower()
    prob = _prob_for(conf, cap)
    # verification_date:优先用机会窗自身到期(若有),否则 today + 默认复盘窗口。
    opp_due = _opportunity_due_date(opp)
    verify_date = opp_due or (today + timedelta(days=_DEFAULT_REVIEW_DAYS))
    verification_date = verify_date.isoformat()
    # review_at:bet_ledger 用的 ISO datetime(复盘日当天 00:00 UTC);与 verification_date 同一天。
    review_at = datetime(
        verify_date.year, verify_date.month, verify_date.day, tzinfo=timezone.utc
    ).isoformat()
    # dedupe_key:同一机会标题在窗口内只产一注草案(幂等;避免每日重复堆叠 inbox)。
    dedupe_basis = title.lower().replace(" ", "_")[:80] or f"opp_{idx}"
    hypothesis = (
        f"押注:{title} —— 若按建议「{action or '试投一轮观察'}」推进,"
        f"{_DEFAULT_REVIEW_DAYS}天内该方向出现可量化正向信号(命中)。"
    )
    evidence_refs = [
        {"type": "market_opportunity", "title": title, "basis": basis,
         "confidence": conf, "confidence_cap": cap,
         "data_status": str(opp.get("data_status") or "")},
    ]
    return {
        "dedupe_key": f"bet:opportunity:{dedupe_basis}",
        "hypothesis": hypothesis,
        "probability": prob,
        "verification_date": verification_date,  # 复盘日(YYYY-MM-DD;锚自传入 today,非墙钟)
        "review_at": review_at,
        "risk_level": "high" if prob < 0.5 else "medium",  # 低概率押注=高风险
        "expected_gain": "命中则验证一个市场方向,沉淀 hypothesis→outcome→lesson 校准回路",
        "evidence_refs": evidence_refs,
        "suggested_action": action,
        "confidence": conf,
        "source": "prediction.opportunity",
    }


def _to_suggestion(draft: dict[str, Any]) -> dict[str, Any]:
    """把 bet 草案物化成 inbox 'bet' 类建议(requires_approval;不烧钱、不写业务表)。

    复用 producers.make_suggestion 统一结构,保证落 vkpi_action_inbox 字段对齐。
    payload 携带 create_bet 所需契约字段,人审通过后由审批端点据此调 bet_ledger.create_bet。
    """
    from app.domains.actions.producers import make_suggestion

    title = str(draft.get("hypothesis") or "")[:80]
    return make_suggestion(
        category=_CATEGORY,
        dedupe_key=str(draft["dedupe_key"]),
        title=f"押注草案 · {title}",
        detail=str(draft.get("hypothesis") or ""),
        reason="高置信市场机会窗 surface 出可下注方向;生成 bet 草案等人审,审批后写入 bet_ledger 形成校准回路。",
        priority="medium",
        entity_type="market_opportunity",
        entity_id=str(draft.get("dedupe_key")),
        suggested_endpoint="POST /api/admin/vkpi/market/bets",  # 人审通过 → 真 create_bet
        estimated_cost_cents=0,            # 押注是判断,不烧 LLM/Apify
        writes_business_data=False,        # 草案本身不写业务表;create_bet 走人审闸
        uses_llm=False,
        requires_approval=True,            # 红线:绝不自动真下注
        expected_gain=str(draft.get("expected_gain") or ""),
        risk_level=str(draft.get("risk_level") or "medium"),
        evidence_refs=list(draft.get("evidence_refs") or []),
        verification_plan=[
            f"审批通过 → bet_ledger.create_bet 写入一注(outcome=open,复盘日={draft.get('verification_date')})",
            "到期 scan_due_bets 逼出 outcome(won/lost/void)+ lesson",
        ],
        affected_tables=["vkpi_bet_ledger"],
        payload={
            # create_bet 契约字段(审批端点据此真下注)。
            "hypothesis": draft.get("hypothesis"),
            "probability": draft.get("probability"),
            "verification_date": draft.get("verification_date"),
            "review_at": draft.get("review_at"),
            "risk_level": draft.get("risk_level"),
            "expected_gain_cents": 0,
            "cost_cents": 0,
            "evidence": {"refs": draft.get("evidence_refs") or [],
                         "suggested_action": draft.get("suggested_action"),
                         "confidence": draft.get("confidence"),
                         "source": draft.get("source")},
        },
    )


def produce_bet_drafts(
    *,
    dry_run: bool = True,
    window_days: int = 30,
    limit: int = 10,
    min_confidence: str = "medium",
    today: date | str | None = None,
) -> dict[str, Any]:
    """闭环C 主入口:读高置信机会窗 → 生成 bet 草案 → (非 dry_run)物化成 inbox 'bet' 待审建议。

    - dry_run=True(默认):只返回草案 + 对应 suggestion 结构,**不写任何库**。
    - dry_run=False:把 suggestion persist 进 vkpi_action_inbox(status='suggested',requires_approval)。
      **永不**直接调 bet_ledger.create_bet —— 真下注是人审后的事。

    min_confidence:只为 >= 此档(low<medium<high)的机会出草案,避免低信号噪音乱押。
    today:复盘日(verification_date)的时间锚,由调用方/调度从 payload 传入('YYYY-MM-DD'/date);
      缺省才退化到当天(仅兜底)。可测、可复现,不依赖不可控墙钟。
    """
    rank = {"low": 0, "medium": 1, "high": 2}
    floor = rank.get(str(min_confidence or "medium").strip().lower(), 1)
    anchor = _coerce_today(today)

    pred = _load_opportunities(window_days=window_days, limit=limit)
    cap = str(pred.get("confidence_cap") or "medium")
    opportunities = pred.get("opportunities") if isinstance(pred.get("opportunities"), list) else []

    drafts: list[dict[str, Any]] = []
    suggestions: list[dict[str, Any]] = []
    skipped_low_conf = 0
    for idx, opp in enumerate(opportunities, start=1):
        if not isinstance(opp, dict):
            continue
        conf = str(opp.get("confidence") or "low").strip().lower()
        if rank.get(conf, 0) < floor:
            skipped_low_conf += 1
            continue
        draft = _draft_from_opportunity(opp, cap=cap, idx=idx, today=anchor)
        drafts.append(draft)
        try:
            suggestions.append(_to_suggestion(draft))
        except Exception:
            logger.warning("bet_producer.materialize_failed",
                           extra={"dedupe_key": draft.get("dedupe_key")}, exc_info=True)

    persisted = 0
    if not dry_run and suggestions:
        try:
            from app.domains.actions import inbox

            persisted = int(inbox.persist_suggestions(suggestions) or 0)
        except Exception:
            logger.warning("bet_producer.persist_failed", exc_info=True)
            persisted = 0

    return {
        "status": "ok",
        "dry_run": bool(dry_run),
        "confidence_cap": cap,
        "min_confidence": str(min_confidence or "medium").strip().lower(),
        "today": anchor.isoformat(),
        "default_review_days": _DEFAULT_REVIEW_DAYS,
        "candidates_seen": len(opportunities),
        "skipped_low_confidence": skipped_low_conf,
        "drafts": drafts,
        "suggestions": suggestions,
        "persisted": persisted,
        "note": (
            "inbox→bet 自动桥:高置信机会窗→bet 草案→inbox 'bet' 待审;dry_run 只返不写;"
            "绝不自动真下注/不烧 LLM;零触 viltrox_fit_score。"
        ),
    }
