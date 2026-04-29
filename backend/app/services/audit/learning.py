"""
services/audit/learning.py — 产品识别学习系统

管理员可以手动纠正识别错误，系统记住这些纠正，下次自动应用。

数据存储:
    data/learned_corrections.json
    {
        "url_corrections": {
            "<url_hash>": {
                "correct_series": "DL",
                "correct_label": "AF 90mm F3.5 DL",
                "corrected_at": "2026-04-07T...",
                "submission_id": 124
            }
        },
        "learned_keywords": {
            "AF 90mm F3.5 DL": [
                "extracted keyword 1",
                "extracted keyword 2"
            ]
        }
    }
"""
from __future__ import annotations
import json
import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

CORRECTIONS_FILE = Path("data/learned_corrections.json")


def _hash_url(url: str) -> str:
    """生成 URL 的稳定哈希用作 key"""
    if not url:
        return ""
    # 去掉 query string 和 trailing slash 来归一化
    normalized = url.split("?")[0].rstrip("/").lower()
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _load_corrections() -> Dict[str, Any]:
    """从 disk 加载学习数据"""
    if not CORRECTIONS_FILE.exists():
        return {"url_corrections": {}, "learned_keywords": {}}
    try:
        return json.loads(CORRECTIONS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("audit_learning.load_failed", extra={"error": str(e)})
        return {"url_corrections": {}, "learned_keywords": {}}


def _save_corrections(data: Dict[str, Any]) -> None:
    """保存到 disk"""
    CORRECTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CORRECTIONS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def _slug_product_key(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (label or "").lower()).strip("-") or "custom-product"


def record_correction(
    submission_id: int,
    url: str,
    correct_series: str,
    correct_label: str,
    learned_text: str = "",
    note: str = "",
) -> Dict[str, Any]:
    """
    管理员标记一条 submission 的正确产品识别。

    Args:
        submission_id: 投稿 ID
        url: 视频 URL（用于下次同一个视频自动匹配）
        correct_series: 正确的产品系列 (LAB / PRO / AIR / EPIC / LUNA / DL etc)
        correct_label: 正确的产品标签 (e.g. "AF 90mm F3.5 DL")
        learned_text: 这条视频的描述文本（用于提取学习关键词）
        note: 管理员笔记

    Returns:
        {"status": "ok", "url_hash": "...", "total_corrections": N}
    """
    data = _load_corrections()
    url_hash = _hash_url(url)

    if url_hash:
        data["url_corrections"][url_hash] = {
            "submission_id": submission_id,
            "url": url,
            "correct_series": correct_series,
            "correct_label": correct_label,
            "corrected_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "note": note,
        }

    # Extract candidate keywords from learned text + note + label itself
    if correct_label:
        learned_keywords = data.setdefault("learned_keywords", {})
        existing = set(learned_keywords.get(correct_label, []))

        # ── Tier 1: Always seed with the label itself (lowercase variants) ──
        label_low = correct_label.lower().strip()
        existing.add(label_low)
        # Strip "AF " prefix if present (e.g. "AF 90mm F3.5 DL" -> "90mm f3.5 dl")
        if label_low.startswith("af "):
            existing.add(label_low[3:])
        # Add "viltrox <label>" variant
        existing.add(f"viltrox {label_low}")
        if label_low.startswith("af "):
            existing.add(f"viltrox {label_low[3:]}")

        # ── Tier 2: Admin note (highest priority — human-curated) ──
        if note:
            note_low = note.lower().strip()
            # Whole note as keyword if short enough
            if 3 <= len(note_low) <= 80:
                existing.add(note_low)
            # Split on common separators and add each phrase
            for piece in re.split(r"[+,，、|/&]", note_low):
                piece = piece.strip()
                if 3 <= len(piece) <= 60:
                    existing.add(piece)
            # Extract gear-like patterns from note
            for m in re.findall(r"viltrox\s+[\w\.\-]+(?:\s+[\w\.\-]+){0,4}", note_low):
                existing.add(m.strip())
            for m in re.findall(r"\d+mm\s*(?:f[\d.]+\s*)?(?:dl|pl|ef|lab|pro|air|evo)", note_low):
                existing.add(m.strip())
            for m in re.findall(r"af\s+\d+mm\s*(?:f[\d.]+)?(?:\s+\w+)?", note_low):
                existing.add(m.strip())

        # ── Tier 3: Auto-extract from analysis text ──
        if learned_text:
            text_low = learned_text.lower()
            for m in re.findall(r"viltrox\s+\w+(?:\s+\w+){0,3}", text_low):
                existing.add(m.strip())
            for m in re.findall(r"af\s+\d+mm\s*(?:f[\d.]+)?(?:\s+\w+)?", text_low):
                existing.add(m.strip())
            for m in re.findall(r"\d+mm\s+(?:dl|pl|ef|lab|pro|air|evo)", text_low):
                existing.add(m.strip())

        # Filter: only keep meaningful phrases (3+ chars, no junk)
        clean_set = set()
        for c in existing:
            c = c.strip()
            if 3 <= len(c) <= 80:
                clean_set.add(c)

        learned_keywords[correct_label] = sorted(clean_set)

    _save_corrections(data)

    knowledge_updated = False
    try:
        from app.services.memory import record_feedback_signal, record_product_signal

        alias_terms = data.get("learned_keywords", {}).get(correct_label, [])
        scene_tags = [piece.strip() for piece in re.split(r"[,|/&]+", note.lower()) if piece.strip()] if note else []
        record_product_signal(
            product_key=_slug_product_key(correct_label),
            label=correct_label,
            family=correct_series,
            alias_terms=alias_terms,
            feature_tags=["human_corrected", "admin_feedback"],
            scene_tags=scene_tags[:8],
            feature_type="human_correction",
            feature_vector={
                "submission_id": submission_id,
                "url_hash": url_hash,
                "keywords": alias_terms[:12],
            },
            asset_role="admin_correction",
            storage_key=url_hash or f"submission:{submission_id}",
            detector_version="human-correction-v1",
        )
        record_feedback_signal(
            source_type="admin",
            source_id=url_hash or str(submission_id),
            event_type="product_correction",
            actor_role="admin",
            submission_id=submission_id,
            payload={
                "correct_series": correct_series,
                "correct_label": correct_label,
                "note": note,
                "keywords": alias_terms[:12],
            },
        )
        knowledge_updated = True
    except Exception as e:
        logger.warning("audit_learning.product_knowledge_update_failed", extra={"error": str(e)})

    return {
        "status": "ok",
        "url_hash": url_hash,
        "total_corrections": len(data["url_corrections"]),
        "learned_keywords_for_label": len(data.get("learned_keywords", {}).get(correct_label, [])),
        "knowledge_updated": knowledge_updated,
    }


def lookup_correction(url: str) -> Optional[Dict[str, str]]:
    """
    查询 URL 是否有学习记录。pipeline 在做产品分类时调用。

    Returns:
        {"correct_series": "DL", "correct_label": "AF 90mm F3.5 DL"} 或 None
    """
    if not url:
        return None
    data = _load_corrections()
    url_hash = _hash_url(url)
    return data.get("url_corrections", {}).get(url_hash)


def get_learned_keywords_for_label(label: str) -> List[str]:
    """获取某个产品标签的学习关键词。classify_product 可以调用扩展匹配。"""
    if not label:
        return []
    data = _load_corrections()
    return data.get("learned_keywords", {}).get(label, [])


def get_all_learned_keywords() -> Dict[str, List[str]]:
    """获取所有学习关键词，按 label 索引。供 classify_product 增强使用。"""
    data = _load_corrections()
    return data.get("learned_keywords", {})


def get_correction_stats() -> Dict[str, Any]:
    """统计学习数据"""
    data = _load_corrections()
    url_corrections = data.get("url_corrections", {})
    learned_keywords = data.get("learned_keywords", {})

    by_label: Dict[str, int] = {}
    for entry in url_corrections.values():
        label = entry.get("correct_label", "unknown")
        by_label[label] = by_label.get(label, 0) + 1

    return {
        "total_corrections": len(url_corrections),
        "total_labels_learned": len(learned_keywords),
        "total_keywords_learned": sum(len(v) for v in learned_keywords.values()),
        "corrections_by_label": by_label,
    }


def list_all_corrections(limit: int = 100) -> List[Dict[str, Any]]:
    """列出所有学习记录"""
    data = _load_corrections()
    items = list(data.get("url_corrections", {}).values())
    items.sort(key=lambda x: x.get("corrected_at", ""), reverse=True)
    return items[:limit]


def delete_correction(url: str) -> bool:
    """删除某个 URL 的学习记录"""
    data = _load_corrections()
    url_hash = _hash_url(url)
    if url_hash in data.get("url_corrections", {}):
        del data["url_corrections"][url_hash]
        _save_corrections(data)
        return True
    return False
