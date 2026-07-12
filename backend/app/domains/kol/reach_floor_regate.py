"""补全回填后的触达门槛第二道闸(用户 live 实锤 2026-07-12,kol_pool 12297 两粉号案)。

发现时 followers=NULL 按「不误杀」放行(第一道闸,正确);档案补全/enrichment 回填真实
followers 后在此**重过闸**:命中 → 给该 pool 行的 raw_platform_data JSON 打 LOW_REACH_FLAG_KEY
标(复用现成 json 列,不建新列不迁移);不命中 → 摘标。推荐/发现/召回三出口读
「实时判据 + 该标」双保险(见 discovery_filters._reach_display_state)。

red line:判据 100% 复用 discovery_filters._reach_floor_reason 单一真源,绝不造第二套;
只写 raw_platform_data 一列;零触 viltrox_fit_score / rule_v0 / 任何评分列;
落库≠推荐——打标只挡推荐面,行保留不删。best-effort:任何失败不抛,绝不阻断补全主流程。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.kol.discovery_filters import (
    LOW_REACH_FLAG_KEY,
    _reach_floor_enabled,
    _reach_floor_min_followers,
    _reach_floor_reason,
)

logger = get_logger(__name__)


def _raw_payload_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (str, bytes)):
        text = value.decode() if isinstance(value, bytes) else value
        if text.strip():
            try:
                parsed = json.loads(text)
                return parsed if isinstance(parsed, dict) else {"raw": parsed}
            except Exception:
                return {"raw_text": text}
    return {}


def evaluate_low_reach_stamp(row: dict[str, Any] | None) -> dict[str, Any]:
    """纯函数:对一行 pool 数据重过闸,算出「应打/应摘标 + 新 raw JSON」。

    返回 {"reason", "flagged", "changed", "raw_json"}:changed=False 时 raw_json=None
    (无需写)。判据走 _reach_floor_reason 单一真源。可独立测试,零 IO。
    """
    if not isinstance(row, dict):
        return {"reason": "", "flagged": False, "changed": False, "raw_json": None}
    reason = _reach_floor_reason(row)
    payload = _raw_payload_dict(row.get("raw_platform_data"))
    had_flag = bool(payload.get(LOW_REACH_FLAG_KEY))
    if reason:
        stamp = {
            "flag": True,
            "reason": reason,
            "floor": _reach_floor_min_followers(),
            "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "method": "reach_floor_regate_v1",
        }
        return {
            "reason": reason,
            "flagged": True,
            "changed": True,  # 命中恒重写(刷新 reason/checked_at,补全后每次重估)
            "raw_json": json.dumps({**payload, LOW_REACH_FLAG_KEY: stamp}, ensure_ascii=False, default=str),
        }
    if had_flag:
        payload.pop(LOW_REACH_FLAG_KEY, None)
        return {
            "reason": "",
            "flagged": False,
            "changed": True,  # 曾被打标、现已达标 → 摘标放行
            "raw_json": json.dumps(payload, ensure_ascii=False, default=str),
        }
    return {"reason": "", "flagged": False, "changed": False, "raw_json": None}


def reapply_reach_floor(kol_pool_id: int, *, conn: Any | None = None) -> dict[str, Any]:
    """回填 followers 后的重过闸入口(pool_enrich / profile_basics 写完即调)。

    读该行(followers/互动族/raw_platform_data)→ evaluate_low_reach_stamp →
    只在 changed 时 UPDATE raw_platform_data 一列。best-effort:任何异常吞掉记日志。
    总开关关闭 → 跳过且**不摘标**(保持现状,重开后语义不变)。
    """
    result: dict[str, Any] = {"kol_pool_id": int(kol_pool_id), "flagged": False, "changed": False}
    if not _reach_floor_enabled():
        result["skipped"] = "env_off"
        return result
    try:
        db = conn or get_conn()
        row = db.execute(
            """
            SELECT id, followers, avg_views, avg_comments, engagement_rate, raw_platform_data
            FROM vkpi_kol_pool
            WHERE id=?
            """,
            (int(kol_pool_id),),
        ).fetchone()
        if not row:
            result["skipped"] = "not_found"
            return result
        verdict = evaluate_low_reach_stamp(dict(row))
        result["flagged"] = bool(verdict["flagged"])
        result["reason"] = verdict["reason"]
        if verdict["changed"] and verdict["raw_json"] is not None:
            db.execute(
                "UPDATE vkpi_kol_pool SET raw_platform_data=? WHERE id=?",
                (verdict["raw_json"], int(kol_pool_id)),
            )
            db.commit()
            result["changed"] = True
            if verdict["flagged"]:
                logger.info(
                    "reach_floor_regate flagged kol_pool_id=%s reason=%s",
                    kol_pool_id, verdict["reason"],
                )
    except Exception:
        try:
            (conn or get_conn()).rollback()
        except Exception:
            logger.debug("reach_floor_regate rollback skipped", exc_info=True)
        logger.warning("reach_floor_regate skipped kol_pool_id=%s(不阻断补全)", kol_pool_id, exc_info=True)
        result["skipped"] = "error"
    return result
