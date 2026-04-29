"""
services/scoring/verticals.py — 垂类权重学习（Ridge 回归）
"""
from __future__ import annotations

import json
from typing import Optional
from pydantic import BaseModel

from app.core.constants import TECH_DIMS, MARKETING_DIMS, VERTICAL_WEIGHTS
from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger(__name__)

# ──────────────────────────────────────────────
# Vertical weight learning (runs monthly)
# Reads DB signals, updates VERTICAL_WEIGHTS in-place
# Requires: scikit-learn (pip install scikit-learn)
# ──────────────────────────────────────────────
def learn_vertical_weights(vertical: str, min_samples: int = 30) -> dict | None:
    """
    Use ridge regression to learn which tech dims predict
    high engagement for this vertical.
    Returns updated weight dict or None if insufficient data.
    """
    try:
        import numpy as np
        conn = get_conn()

        rows = conn.execute("""
            SELECT video_analysis, views, likes, comments, shares,
                   detection_status, tech_score, marketing_score
            FROM submissions
            WHERE vertical_category = ?
              AND detection_status = 'confirmed'
              AND tech_score > 0
              AND views > 0
        """, (vertical,)).fetchall()
        
        if len(rows) < min_samples:
            logger.info(
                "vertical learn skipped for insufficient samples | vertical=%s | samples=%s | min=%s",
                vertical,
                len(rows),
                min_samples,
            )
            return None

        X_tech, X_mkt, y_eng, y_approve = [], [], [], []

        for row in rows:
            try:
                va = json.loads(row["video_analysis"] or "{}")
                qs = va.get("quality_scores", {})
                if not qs or len(qs) < 5:
                    continue

                # Feature vectors
                tech_feat = [qs.get(d, 0) for d in TECH_DIMS]
                mkt_feat  = [qs.get(d, 0) for d in MARKETING_DIMS]

                # Engagement signal (log-normalized)
                eng = (
                    (row["views"] or 0) * 0.1 +
                    (row["likes"] or 0) * 2 +
                    (row["comments"] or 0) * 5 +
                    (row["shares"] or 0) * 8
                )
                eng_log = float(np.log1p(eng))

                X_tech.append(tech_feat)
                X_mkt.append(mkt_feat)
                y_eng.append(eng_log)
                y_approve.append(1 if row["detection_status"] == "confirmed" else 0)
            except Exception:
                continue

        if len(X_tech) < min_samples:
            return None

        from sklearn.linear_model import Ridge
        X_tech_arr = np.array(X_tech)
        X_mkt_arr  = np.array(X_mkt)
        y_eng_arr  = np.array(y_eng)

        # Fit tech dims → engagement
        reg_tech = Ridge(alpha=1.0, positive=True)
        reg_tech.fit(X_tech_arr, y_eng_arr)

        # Fit mkt dims → engagement (brand exposure should correlate)
        reg_mkt = Ridge(alpha=1.0, positive=True)
        reg_mkt.fit(X_mkt_arr, y_eng_arr)

        def _to_weights(coefs: np.ndarray, dims: list, current: dict,
                        max_shift: float = 0.20) -> dict:
            """Normalize coefficients to percentages, apply max_shift constraint."""
            coefs = np.maximum(coefs, 0)
            total = coefs.sum()
            if total == 0:
                return current
            raw = {d: round(float(c / total * 100)) for d, c in zip(dims, coefs)}
            # Constraint: no weight moves more than max_shift from current
            result = {}
            for d in dims:
                cur = current.get(d, 10)
                new = raw.get(d, cur)
                delta = new - cur
                clamped = cur + max(min(delta, cur * max_shift), -cur * max_shift)
                result[d] = max(1, round(clamped))
            # Renormalize to 100
            s = sum(result.values())
            for d in result:
                result[d] = round(result[d] / s * 100)
            return result

        cur_vw   = VERTICAL_WEIGHTS.get(vertical, VERTICAL_WEIGHTS["default"])
        new_tech = _to_weights(reg_tech.coef_, TECH_DIMS,    cur_vw["tech"])
        new_mkt  = _to_weights(reg_mkt.coef_,  MARKETING_DIMS, cur_vw["mkt"])

        result = {
            "vertical":     vertical,
            "samples":      len(X_tech),
            "tech":         new_tech,
            "mkt":          new_mkt,
            "learned_at":   datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        # Persist learned weights to DB
        try:
            conn2 = get_conn()
            conn2.execute(
                "INSERT INTO insights_cache(key,value,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (f"learned_weights_{vertical}",
                 json.dumps(result, ensure_ascii=False),
                 result["learned_at"])
            )
            conn2.commit()
        except Exception as e:
            logger.exception("vertical learn save error | vertical=%s | error=%s", vertical, e)

        logger.info("vertical learn updated | vertical=%s | samples=%s", vertical, len(X_tech))
        return result

    except ImportError:
        logger.warning("vertical learn unavailable because scikit-learn is not installed")
        return None
    except Exception as e:
        logger.exception("vertical learn error | vertical=%s | error=%s", vertical, e)
        return None


def load_learned_weights(vertical: str) -> dict | None:
    """Load previously learned weights from cache, if any."""
    try:
        conn = get_conn()
        row = conn.execute(
            "SELECT value FROM insights_cache WHERE key=?",
            (f"learned_weights_{vertical}",)
        ).fetchone()
        if row:
            return json.loads(row[0])
    except Exception:
        logger.exception("failed to load learned weights | vertical=%s", vertical)
    return None


def apply_learned_weights(vertical: str) -> None:
    """Override in-memory VERTICAL_WEIGHTS with learned values if available."""
    learned = load_learned_weights(vertical)
    if learned and learned.get("tech") and learned.get("mkt"):
        if vertical not in VERTICAL_WEIGHTS:
            VERTICAL_WEIGHTS[vertical] = dict(VERTICAL_WEIGHTS["default"])
        VERTICAL_WEIGHTS[vertical]["tech"] = learned["tech"]
        VERTICAL_WEIGHTS[vertical]["mkt"]  = learned["mkt"]
        logger.info(
            "applied learned weights | vertical=%s | samples=%s | learned_at=%s",
            vertical,
            learned.get("samples", 0),
            learned.get("learned_at", ""),
        )






# ──────────────────────────────────────────────
# Pydantic models
# ──────────────────────────────────────────────
class MetricsInput(BaseModel):
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    favorites: int = 0


class HintsInput(BaseModel):
    logo: bool = False
    product: bool = False
    voice: bool = False
    review: bool = False


class UploadedVideoInput(BaseModel):
    video_id: str = ""
    filename: str = ""
    mime_type: str = ""
    size_mb: float = 0.0
    path: str = ""


class AuditRequest(BaseModel):
    url: str = ""
