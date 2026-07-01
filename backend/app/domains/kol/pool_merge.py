"""KOL Pool 跨平台同一人归并(P0-4 去重激活)。

只读补丁设计师交付的归并写边界。红线:本模块只动 duplicate_of_id 指针 +
vkpi_kol_pool_aliases 别名表 + 迁移收藏从行→主行,**绝不** touch
viltrox_fit_score / viltrox_fit_reason / rule_v0 任何字节。归并前后 SUM(fit) 守恒
(从行仍在表内,仅被读取点滤掉;任一行 fit 不改)。

Jianbo 决策:高置信自动合并 + 模糊人工——
  - email 完全一致(规整后非空且相等):强信号,调用方传 dry_run=False 自动落指针。
  - handle+name / profile_link:模糊信号,只进人工复核清单(apply_merge 强制 dry_run=True),
    不自动写。

主记录选择(与 migration 109 注释裁决一致):有 FK 引用者优先,均无则低 id。
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger("viltrox.domains.kol.pool_merge")

SCORE_FIELDS = ("viltrox_fit_score", "viltrox_fit_reason")  # 红线只读基准:归并前后必须不变
_STRONG_SIGNALS = {"email"}  # 仅这些信号允许调用方 dry_run=False 自动落指针


def _norm_handle(value: Any) -> str:
    text = str(value or "").strip().lstrip("@").strip("/").lower()
    return re.sub(r"[\s_.-]+", "", text)


def _norm_email(value: Any) -> str:
    return str(value or "").strip().lower()


def _norm_name(value: Any) -> str:
    return re.sub(r"[\s_.-]+", "", str(value or "").strip().lower())


def detect_duplicate_master(
    kol_pool_id: int,
    *,
    conn: Any | None = None,
) -> dict[str, Any]:
    """只读:为给定 pool 行找跨平台同一人的主记录候选 + 信号强弱。不写库。

    返回 {candidate_master_id, signal, auto_eligible} 或 {candidate_master_id: None}。
    auto_eligible=True 仅 email 强信号——调用方可 dry_run=False 自动落;否则进人工清单。
    """
    db = conn or get_conn()
    row = db.execute(
        "SELECT id, platform, handle, display_name, email, bio, profile_url FROM vkpi_kol_pool WHERE id=?",
        (int(kol_pool_id),),
    ).fetchone()
    if not row:
        raise LookupError(f"kol_pool_id not found: {kol_pool_id}")
    me = dict(row)
    email = _norm_email(me.get("email"))
    hand = _norm_handle(me.get("handle"))
    name = _norm_name(me.get("display_name"))

    rows = db.execute(
        """
        SELECT id, platform, handle, display_name, email, bio, profile_url
        FROM vkpi_kol_pool
        WHERE id<>? AND duplicate_of_id IS NULL
        """,
        (int(kol_pool_id),),
    ).fetchall()
    best: dict[str, Any] | None = None
    for cand in rows:
        c = dict(cand)
        signal = ""
        if email and _norm_email(c.get("email")) == email:
            signal = "email"
        elif hand and _norm_handle(c.get("handle")) == hand and name and _norm_name(c.get("display_name")) == name:
            signal = "handle+name"
        elif hand and (hand in _norm_handle(c.get("bio")) or hand in _norm_handle(c.get("profile_url"))):
            signal = "profile_link"
        if not signal:
            continue
        # email 强信号优先于模糊信号;同强度取低 id。
        cand_strong = signal in _STRONG_SIGNALS
        if best is None:
            best = {"id": int(c["id"]), "signal": signal}
        else:
            best_strong = best["signal"] in _STRONG_SIGNALS
            if (cand_strong and not best_strong) or (
                cand_strong == best_strong and int(c["id"]) < int(best["id"])
            ):
                best = {"id": int(c["id"]), "signal": signal}
    if not best:
        return {"candidate_master_id": None}
    return {
        "candidate_master_id": best["id"],
        "signal": best["signal"],
        "auto_eligible": best["signal"] in _STRONG_SIGNALS,
    }


def apply_merge(
    duplicate_id: int,
    master_id: int,
    *,
    signal: str = "",
    confidence: float | None = None,
    dry_run: bool = True,
    conn: Any | None = None,
) -> dict[str, Any]:
    """把 duplicate_id 归并到 master_id:写 duplicate_of_id 指针 + aliases + 迁移收藏。

    红线守卫:归并前后快照两行 SCORE_FIELDS,任何变化即 rollback 报错。
    dry_run 默认 True;Jianbo 决策——email 强信号调用方显式传 dry_run=False 自动落,
    handle+name/profile_link 模糊信号即便调用方误传 dry_run=False 也强制回退 dry-run。
    """
    if int(duplicate_id) == int(master_id):
        raise ValueError("duplicate_id and master_id must differ")
    db = conn or get_conn()
    dup = db.execute(
        "SELECT id, platform, handle, profile_url, viltrox_fit_score, viltrox_fit_reason, duplicate_of_id FROM vkpi_kol_pool WHERE id=?",
        (int(duplicate_id),),
    ).fetchone()
    master = db.execute(
        "SELECT id, viltrox_fit_score, viltrox_fit_reason, duplicate_of_id FROM vkpi_kol_pool WHERE id=?",
        (int(master_id),),
    ).fetchone()
    if not dup or not master:
        raise LookupError("duplicate or master row not found")
    dup_d, master_d = dict(dup), dict(master)
    if master_d.get("duplicate_of_id") is not None:
        raise ValueError(f"master {master_id} is itself a duplicate of {master_d['duplicate_of_id']}")
    if not dry_run and signal not in _STRONG_SIGNALS:
        # 安全护栏:模糊信号绝不自动写,强制回退人工(dry-run)。
        logger.warning("pool_merge refused auto-write for fuzzy signal=%r dup=%s master=%s", signal, duplicate_id, master_id)
        dry_run = True
    before = {
        int(duplicate_id): {f: dup_d.get(f) for f in SCORE_FIELDS},
        int(master_id): {f: master_d.get(f) for f in SCORE_FIELDS},
    }
    plan = {
        "duplicate_id": int(duplicate_id),
        "master_id": int(master_id),
        "signal": signal,
        "alias": {"platform": dup_d.get("platform"), "handle": dup_d.get("handle")},
        "score_before": before,
    }
    if dry_run:
        return {"ok": True, "dry_run": True, **plan}
    try:
        # 1) 指针:从行 duplicate_of_id 指向主。
        db.execute(
            "UPDATE vkpi_kol_pool SET duplicate_of_id=?, updated_at=NOW() WHERE id=?",
            (int(master_id), int(duplicate_id)),
        )
        # 2) 别名:把从行 (platform,handle) 记为主的别名(表 039:47,自带 UNIQUE 幂等)。
        db.execute(
            """
            INSERT INTO vkpi_kol_pool_aliases
                (kol_pool_id, platform, handle, profile_url, confidence, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, NOW())
            ON CONFLICT(platform, handle) DO UPDATE SET
                kol_pool_id=excluded.kol_pool_id,
                profile_url=excluded.profile_url,
                confidence=excluded.confidence,
                metadata_json=excluded.metadata_json
            """,
            (
                int(master_id),
                dup_d.get("platform"),
                dup_d.get("handle"),
                dup_d.get("profile_url") or "",
                confidence,
                json.dumps({"signal": signal, "merged_from_pool_id": int(duplicate_id)}, ensure_ascii=False),
            ),
        )
        # 3) 迁移收藏:从行收藏迁主行(避免读取点滤掉从行后收藏静默消失)。
        db.execute(
            """
            UPDATE vkpi_kol_pool_favorites SET kol_pool_id=?
            WHERE kol_pool_id=?
              AND NOT EXISTS (
                SELECT 1 FROM vkpi_kol_pool_favorites f2
                WHERE f2.kol_pool_id=? AND f2.staff_id=vkpi_kol_pool_favorites.staff_id
              )
            """,
            (int(master_id), int(duplicate_id), int(master_id)),
        )
        db.execute("DELETE FROM vkpi_kol_pool_favorites WHERE kol_pool_id=?", (int(duplicate_id),))
        # 4) 红线守卫:复查两行 SCORE_FIELDS 未变(SUM(fit) 守恒的逐行强约束)。
        after_rows = db.execute(
            "SELECT id, viltrox_fit_score, viltrox_fit_reason FROM vkpi_kol_pool WHERE id IN (?, ?)",
            (int(duplicate_id), int(master_id)),
        ).fetchall()
        after = {int(r["id"]): {f: dict(r).get(f) for f in SCORE_FIELDS} for r in after_rows}
        for pid, snap in before.items():
            if any(snap.get(f) != after.get(pid, {}).get(f) for f in SCORE_FIELDS):
                db.rollback()
                raise RuntimeError(f"viltrox_fit_score changed during merge for pool {pid}")
        db.commit()
        return {"ok": True, "dry_run": False, **plan, "score_after": after}
    except Exception:
        db.rollback()
        raise


# ───────────────────────────────────────────────────────────────────────────
# L6:enroll 落库后去重 hook + 全池 reconcile 作业(归并量从 3 做起来)
# ───────────────────────────────────────────────────────────────────────────
# 排查实证:归并器代码齐且红线安全,但 enroll 落库后没自动跑去重 → duplicate_of_id 仅 3。
# 这里把检测挂到 enroll 落库后:email 强信号自动合并、模糊信号只记日志进人工清单(不写)。
# 并提供 reconcile_pool_duplicates 扫全池(默认 dry_run + 上限,真合并要高置信)。
# 红线:全程只调 detect_duplicate_master / apply_merge——后者已带 before/after fit 守卫,
# 任一行 viltrox_fit_score 变动即 rollback;本模块不新增任何 fit 写点。


def _canonical_master_pair(low_or_high_id: int, candidate_id: int) -> tuple[int, int]:
    """把一对疑似同一人规整成 (duplicate_id, master_id):低 id 当主(与模块主记录裁决"均无 FK 则低 id"对齐),
    高 id 当从。避免 email 对称对(a→b 与 b→a)各跑一次造成往返/环。返回 (dup, master)。
    """
    a, b = int(low_or_high_id), int(candidate_id)
    master = min(a, b)
    duplicate = max(a, b)
    return duplicate, master


def dedupe_enrolled_pool_row(
    kol_pool_id: int,
    *,
    auto_merge: bool = True,
    conn: Any | None = None,
) -> dict[str, Any]:
    """enroll 落库后的去重 hook:为刚入库/刷新的单行找跨平台同一人。

    口径(Jianbo 决策一致):
      - email 强信号(auto_eligible):auto_merge=True 时自动落 duplicate_of_id 指针(apply_merge dry_run=False)。
      - handle+name / profile_link 模糊信号:只记日志进人工复核清单,绝不自动写。
    红线安全:apply_merge 自带 fit before/after 守卫;本函数不直接 touch 任何评分列。
    最佳努力:任何异常只记日志、返回 skipped,绝不阻断 enroll 主流程。
    """
    try:
        db = conn or get_conn()
        det = detect_duplicate_master(int(kol_pool_id), conn=db)
    except Exception as exc:  # noqa: BLE001
        logger.info("dedupe_enrolled_pool_row detect skip id=%s: %s", kol_pool_id, str(exc)[:200])
        return {"ok": False, "skipped": True, "reason": "detect_failed", "kol_pool_id": int(kol_pool_id)}

    candidate = det.get("candidate_master_id")
    if not candidate:
        return {"ok": True, "merged": False, "candidate_master_id": None, "kol_pool_id": int(kol_pool_id)}

    signal = det.get("signal") or ""
    auto_eligible = bool(det.get("auto_eligible"))
    duplicate_id, master_id = _canonical_master_pair(int(kol_pool_id), int(candidate))

    if not (auto_eligible and auto_merge):
        # 模糊信号 / 关闭自动:进人工复核清单(只记日志,不写)。
        logger.info(
            "dedupe_enrolled_pool_row manual-review pair dup=%s master=%s signal=%s (no auto-write)",
            duplicate_id, master_id, signal,
        )
        return {
            "ok": True,
            "merged": False,
            "needs_review": True,
            "candidate_master_id": int(candidate),
            "duplicate_id": duplicate_id,
            "master_id": master_id,
            "signal": signal,
            "kol_pool_id": int(kol_pool_id),
        }
    try:
        res = apply_merge(
            duplicate_id,
            master_id,
            signal=signal,
            confidence=1.0,
            dry_run=False,
            conn=db,
        )
        logger.info("dedupe_enrolled_pool_row auto-merged dup=%s master=%s signal=%s", duplicate_id, master_id, signal)
        return {"ok": True, "merged": True, "signal": signal, "result": res, "kol_pool_id": int(kol_pool_id)}
    except Exception as exc:  # noqa: BLE001
        # 包含 fit 守卫触发的 rollback——绝不让去重炸掉 enroll。
        logger.warning(
            "dedupe_enrolled_pool_row auto-merge failed dup=%s master=%s: %s",
            duplicate_id, master_id, str(exc)[:200],
        )
        return {"ok": False, "merged": False, "reason": "merge_failed", "error": str(exc)[:200], "kol_pool_id": int(kol_pool_id)}


def reconcile_pool_duplicates(
    *,
    dry_run: bool = True,
    auto_merge_high_confidence: bool = False,
    limit: int = 50,
    conn: Any | None = None,
) -> dict[str, Any]:
    """一次性/周期 reconcile:扫全池找跨平台同一人,报告候选 + (可选)对高置信对真合并。

    默认 dry_run=True 且 auto_merge_high_confidence=False —— 纯只读报清单,绝不写库。
    放量(运营在设置/运维口子显式开):传 dry_run=False, auto_merge_high_confidence=True
    才会对 email 强信号对落 duplicate_of_id 指针(走 apply_merge,带 fit 守卫);模糊信号
    任何时候都只进人工清单,绝不自动写。limit 限单次处理的去重「对」数(防一次性大批量)。

    返回 {scanned, auto_pairs:[...], fuzzy_pairs:[...], merged:[...], merged_count, dry_run}。
    auto_pairs/fuzzy_pairs 已按 (master,duplicate) 规整去重(对称 email 对不重复计)。
    """
    db = conn or get_conn()
    rows = db.execute(
        "SELECT id FROM vkpi_kol_pool WHERE duplicate_of_id IS NULL ORDER BY id"
    ).fetchall()
    ids = [int(dict(r)["id"]) for r in rows]

    seen_pairs: set[tuple[int, int]] = set()
    auto_pairs: list[dict[str, Any]] = []
    fuzzy_pairs: list[dict[str, Any]] = []
    for pid in ids:
        try:
            det = detect_duplicate_master(pid, conn=db)
        except Exception as exc:  # noqa: BLE001
            logger.debug("reconcile detect skip id=%s: %s", pid, str(exc)[:120])
            continue
        candidate = det.get("candidate_master_id")
        if not candidate:
            continue
        duplicate_id, master_id = _canonical_master_pair(pid, int(candidate))
        key = (master_id, duplicate_id)
        if key in seen_pairs:
            continue  # 对称对 / 已计 → 只算一次
        seen_pairs.add(key)
        entry = {
            "duplicate_id": duplicate_id,
            "master_id": master_id,
            "signal": det.get("signal") or "",
        }
        if det.get("auto_eligible"):
            auto_pairs.append(entry)
        else:
            fuzzy_pairs.append(entry)

    merged: list[dict[str, Any]] = []
    if not dry_run and auto_merge_high_confidence:
        for entry in auto_pairs[: max(0, int(limit))]:
            try:
                res = apply_merge(
                    entry["duplicate_id"],
                    entry["master_id"],
                    signal=entry["signal"],
                    confidence=1.0,
                    dry_run=False,
                    conn=db,
                )
                merged.append({"pair": entry, "ok": True, "score_after": res.get("score_after")})
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "reconcile auto-merge failed dup=%s master=%s: %s",
                    entry["duplicate_id"], entry["master_id"], str(exc)[:200],
                )
                merged.append({"pair": entry, "ok": False, "error": str(exc)[:200]})

    summary = {
        "scanned": len(ids),
        "auto_pairs": auto_pairs,
        "auto_pair_count": len(auto_pairs),
        "fuzzy_pairs": fuzzy_pairs,
        "fuzzy_pair_count": len(fuzzy_pairs),
        "merged": merged,
        "merged_count": sum(1 for m in merged if m.get("ok")),
        "dry_run": bool(dry_run),
        "auto_merge_high_confidence": bool(auto_merge_high_confidence),
        "limit": int(limit),
    }
    logger.info(
        "reconcile_pool_duplicates scanned=%d auto=%d fuzzy=%d merged=%d dry_run=%s",
        summary["scanned"], summary["auto_pair_count"], summary["fuzzy_pair_count"],
        summary["merged_count"], summary["dry_run"],
    )
    return summary
