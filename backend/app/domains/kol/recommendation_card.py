"""KOL 推荐卡 —— 给前端"卡片 + 等级排序 + 为什么推荐"一个干净 payload(只读)。

data_grade:数据完整度档(A/B/C/D)—— 粉丝/联系方式/互动率已知 + 非疑似刷量 + 有证据沉淀。
**这是"数据完整度/可信度"档,不是 fit 推荐分**(红线:relevance/exposure/grade 绝不并入 viltrox_fit_score)。
why_recommended:复用 provenance(视频/项目/深析沉淀)+ 主题,assemble 人话理由。
signals:展示信号(粉丝/互动/主题/可联系/疑似刷量)。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)

_GRADE = {4: "A", 3: "B", 2: "C", 1: "D", 0: "D"}


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def get_recommendation_card(kol_pool_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """组装一张 KOL 推荐卡(只读)。不存在 → status='not_found'。"""
    kid = int(kol_pool_id or 0)
    if kid <= 0 or not table_exists("vkpi_kol_pool"):
        return {"status": "not_found"}
    row = get_conn().execute(
        "SELECT id, display_name, handle, platform, primary_topic, followers, engagement_rate, "
        "email, other_contacts_json, suspect_inflation, avatar_url "
        "FROM vkpi_kol_pool WHERE id = ?",
        (kid,),
    ).fetchone()
    if not row:
        return {"status": "not_found", "kol_pool_id": kid}
    k = dict(row)

    followers = _int(k.get("followers"))
    contactable = bool(str(k.get("email") or "").strip()) or bool(str(k.get("other_contacts_json") or "[]").strip() not in ("", "[]", "null"))
    has_engagement = k.get("engagement_rate") is not None
    suspect = bool(k.get("suspect_inflation"))

    # provenance 沉淀(视频/项目/深析)。
    prov = {}
    try:
        from app.domains.memory import provenance

        prov = provenance.get_kol_provenance(kid, staff=staff)
    except Exception:
        logger.debug("rec_card.provenance_failed", exc_info=True)
    p = prov.get("provenance", {}) if isinstance(prov, dict) else {}
    n_videos = len(p.get("videos", []) or [])
    n_projects = (p.get("roi", {}) or {}).get("total_projects") or 0
    n_analyses = p.get("deep_analyses_count") or 0

    # data_grade:完整度档(非 fit)。
    score = 0
    score += 1 if followers is not None else 0
    score += 1 if contactable else 0
    score += 1 if has_engagement else 0
    score += 1 if not suspect else 0
    has_evidence = (n_videos + int(n_analyses) + int(n_projects)) > 0
    if not has_evidence and score > 1:
        score -= 1  # 零沉淀降一档(数据再全也只是静态档案)
    grade = _GRADE.get(score, "D")

    # 为什么推荐:沉淀 + 主题 + 完整度。
    bits: list[str] = []
    topic = str(k.get("primary_topic") or "").strip()
    if topic:
        bits.append(f"主题「{topic}」")
    if n_videos:
        bits.append(f"{n_videos} 条视频证据")
    if n_projects:
        bits.append(f"合作 {n_projects} 个项目")
    if n_analyses:
        bits.append(f"深析 {n_analyses} 次")
    if followers is not None:
        bits.append(f"{followers:,} 粉丝")
    why = "、".join(bits) + "。" if bits else "资料较少,建议先补全再评估。"

    return {
        "status": "ok",
        "kol_pool_id": kid,
        "name": str(k.get("display_name") or k.get("handle") or f"KOL #{kid}"),
        "platform": str(k.get("platform") or ""),
        "avatar_url": str(k.get("avatar_url") or ""),
        "data_grade": grade,                # A/B/C/D 数据完整度档(非 fit)
        "data_grade_score": score,
        "why_recommended": why,
        "signals": {
            "followers": followers,
            "engagement_known": has_engagement,
            "contactable": contactable,
            "suspect_inflation": suspect,
            "primary_topic": topic,
            "videos": n_videos,
            "projects": n_projects,
            "analyses": int(n_analyses),
        },
        "note": "data_grade 是数据完整度/可信度档,不是推荐分;绝不并入 viltrox_fit_score。",
    }
