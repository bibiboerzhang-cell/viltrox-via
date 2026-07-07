"""发布 SOP 时间线(launch_sop v0,G4 件①)—— 同行标杆发布节奏编成任务模板。

build_sop_timeline(schedule) -> dict:按目标发布日(T0)倒排七节点,每节点=
任务模板(标题/负责人角色/依赖),payload 带 basis,纯模板生成不真建任务:
  T-30 rumor_seed            rumor 站投料(传闻站热场拉搜索基线)
  T-21 embargo_brief         embargo brief 定稿(统一解禁时间与信息口径)
  T-14 sample_batch_ship     批量寄样 —— 吃发射台③排期段的个人制作周期
                             (签收→发布历史)数据,周期超窗成员出逐人 ship_by
  T-7  asset_pack_ready      素材包+LUT 短链就位
  T0   embargo_lift          多评测人同日解禁(声量峰值)
  T+3  first_data_check      数据首查(对照 forecast 段预测)
  T+7  review_cluster_retest 差评聚类→固件工单→复测邀约(吃 miss_review 思路)

T0 锚定:排期段最晚个人发布日(全员制作周期都赶得上同日解禁)与
今天+最小跑道(T-30 不能早于今天)取较晚者;排期段不可用诚实退跑道锚。

调用方:launch_assembly._sop_timeline_block(守卫调用);本模块零 SQL、零 LLM、
零写库,输入只有排期段 payload。红线:绝不写 viltrox_fit_score、不碰 rule_v0;
生成物是任务模板草稿,绝不真建任务/外联。
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

SCHEMA_VERSION = "sop_template_v0"
# rumor 投料到解禁的最小跑道天数(保证 T-30 节点不早于今天)。
SOP_MIN_RUNWAY_DAYS = 30
# T-14 批量寄样节点给到的默认制作窗口(天);个人周期超窗需提前寄样。
SOP_SHIP_WINDOW_DAYS = 14

# ── 七节点任务模板(offset_days, key, label, owner_role, depends_on, task_title, detail)──
SOP_NODE_TEMPLATES: tuple[tuple[int, str, str, str, tuple[str, ...], str, str], ...] = (
    (-30, "rumor_seed", "rumor 站投料", "PR/市场传播", (),
     "向 rumor 站投放新品线索(焦段/卡口级信息留悬念,口径经产品负责人审定)",
     "同行标杆节奏:发布前 30 天由传闻站先热场,拉高搜索与讨论基线;只给可公开线索。"),
    (-21, "embargo_brief", "embargo brief 定稿", "产品营销", ("rumor_seed",),
     "定稿评测 embargo brief(规格/卖点/禁发时间/素材清单),发所有受邀评测人",
     "统一解禁时间与信息口径是同日解禁的前提;brief 定稿后寄样与素材包才能并行。"),
    (-14, "sample_batch_ship", "批量寄样", "履约/物流", ("embargo_brief",),
     "按 roster 批量寄出评测样品(随附 brief 与回寄/留用说明)",
     f"默认制作窗口 {SOP_SHIP_WINDOW_DAYS} 天(寄样→解禁);个人制作周期超窗的成员按 ship_by 提前寄样。"),
    (-7, "asset_pack_ready", "素材包+LUT 短链就位", "素材/设计", ("embargo_brief",),
     "产品素材包(官方样片/规格图/LUT)与短链落地页就位,链接发全体评测人",
     "评测人剪辑期直接取用官方素材与 LUT;短链统一归因入口,解禁日零等待。"),
    (0, "embargo_lift", "多评测人同日解禁", "项目负责人/KOL 运营", ("sample_batch_ship", "asset_pack_ready"),
     "解禁日协调:确认全员视频定时发布,官号同步官宣贴,监控首小时数据",
     "同日解禁制造声量峰值(同行标杆打法);官号协同建议见 official_synergy 段。"),
    (3, "first_data_check", "数据首查", "数据分析", ("embargo_lift",),
     "解禁后 72 小时首查:各视频播放/互动/评论意向,对照 forecast 段预测值",
     "首查只看方向不下结论;异常低的排查发布时间/标题封面,异常高的准备二次投放。"),
    (7, "review_cluster_retest", "差评聚类→固件工单→复测邀约", "产品+KOL 运营", ("first_data_check",),
     "聚类首周差评/负反馈 → 可修复项开固件/品控工单 → 修复后邀约原评测人复测",
     "吃 miss_review 思路(learning.miss_review):差评是复测邀约的素材,不是公关灭火对象。"),
)


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _text(value: Any, limit: int = 300) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


def _parse_day(value: Any) -> date | None:
    """'2026-08-06' / ISO 时间戳 / date / datetime → date;解析不了回 None(不硬猜)。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _t_label(offset_days: int) -> str:
    if offset_days == 0:
        return "T0"
    return f"T{offset_days}" if offset_days < 0 else f"T+{offset_days}"


def _ship_plan(schedule: dict[str, Any], t0: date, ship_node_date: date) -> list[dict[str, Any]]:
    """T-14 逐人寄样计划:吃排期段个人制作周期(签收→发布)数据。

    ship_by = 解禁日 − 个人周期(天,向上取整);早于 T-14 节点日 = 需提前寄样。
    排期段不 ready 时返回空列表(节点模板仍在,诚实不硬造逐人数据)。
    """
    plan: list[dict[str, Any]] = []
    if not isinstance(schedule, dict) or schedule.get("status") != "ready":
        return plan
    for item in schedule.get("items") or []:
        lead = _float_or_none(item.get("leadtime_days"))
        if lead is None:
            continue
        lead_days = max(0, math.ceil(lead))
        ship_by = t0 - timedelta(days=lead_days)
        basis = item.get("leadtime_basis") or {}
        plan.append(
            {
                "kol_pool_id": item.get("kol_pool_id"),
                "handle": item.get("handle"),
                "display_name": item.get("display_name"),
                "leadtime_days": lead,
                "leadtime_confidence": _text(basis.get("confidence"), 20),
                "leadtime_method": _text(basis.get("method"), 120),
                "ship_by": ship_by.isoformat(),
                "needs_early_ship": ship_by < ship_node_date,
            }
        )
    plan.sort(key=lambda r: (str(r.get("ship_by") or ""), str(r.get("handle") or "")))
    return plan


def build_sop_timeline(schedule: dict[str, Any]) -> dict[str, Any]:
    """发布 SOP 时间线:七节点倒排真日期 + 任务模板;纯生成,零 SQL、零写库。"""
    today = datetime.now(timezone.utc).date()
    min_t0 = today + timedelta(days=SOP_MIN_RUNWAY_DAYS)
    latest_publish: date | None = None
    if isinstance(schedule, dict) and schedule.get("status") == "ready":
        for item in schedule.get("items") or []:
            day = _parse_day(item.get("publish_date"))
            if day is None:
                continue
            if latest_publish is None or day > latest_publish:
                latest_publish = day

    if latest_publish is not None and latest_publish > min_t0:
        t0 = latest_publish
        anchor_method = "排期段最晚个人发布日(全员制作周期都赶得上同日解禁),晚于最小跑道锚故取之"
    else:
        t0 = min_t0
        anchor_method = (
            f"今天+{SOP_MIN_RUNWAY_DAYS} 天最小跑道锚(T-30 rumor 投料不能早于今天)"
            + (";排期段无可用发布日" if latest_publish is None else ";排期段最晚发布日早于跑道锚,已覆盖")
        )

    ship_node_date = t0 - timedelta(days=SOP_SHIP_WINDOW_DAYS)
    nodes: list[dict[str, Any]] = []
    for offset, key, label, owner_role, depends_on, task_title, detail in SOP_NODE_TEMPLATES:
        node: dict[str, Any] = {
            "key": key,
            "t_label": _t_label(offset),
            "offset_days": offset,
            "date": (t0 + timedelta(days=offset)).isoformat(),
            "label": label,
            "task_template": {
                "title": task_title,
                "owner_role": owner_role,
                "depends_on": list(depends_on),
            },
            "basis": {"source": f"{SCHEMA_VERSION}(同行标杆发布节奏,模板常量)", "detail": detail},
        }
        if key == "sample_batch_ship":
            plan = _ship_plan(schedule, t0, ship_node_date)
            node["member_ship_plan"] = plan
            node["basis"]["ship_plan_source"] = (
                "排期段个人制作周期(leadtime_competing 签收→发布历史);"
                "ship_by=解禁日−个人周期(向上取整);needs_early_ship=须早于本节点寄出"
                if plan
                else "排期段无逐人周期数据,只出节点模板不出逐人 ship_by(诚实缺口)"
            )
        nodes.append(node)

    return {
        "status": "ready",
        "target_publish_date": t0.isoformat(),
        "kickoff_date": today.isoformat(),
        "nodes": nodes,
        "basis": {
            "anchor_method": anchor_method,
            "schedule_status": _text((schedule or {}).get("status"), 40) if isinstance(schedule, dict) else "",
            "latest_member_publish_date": latest_publish.isoformat() if latest_publish else None,
            "min_runway_days": SOP_MIN_RUNWAY_DAYS,
            "method": f"T-30/T-21/T-14/T-7/T0/T+3/T+7 七节点按目标发布日倒排({SCHEMA_VERSION})",
        },
        "note": (
            "纯模板生成,不真建任务、不写任何任务表;负责人为角色占位,落地时由项目负责人指派;"
            "T-14 逐人寄样计划吃排期段周期数据,T+7 差评聚类吃 miss_review 思路(learning.miss_review)。"
        ),
    }
