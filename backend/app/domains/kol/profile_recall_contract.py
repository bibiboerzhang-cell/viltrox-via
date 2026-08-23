"""Shared constants and value types for KOL profile recall."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
import re
from typing import Any


COLLECTION_NAME = "vkpi_kol_profile_index_v1"
METHOD = "vector_recall"
EMBEDDING_MODEL = "text-embedding-3-small"
VECTOR_SIZE = 1536
PROJECT_ROOT = Path(__file__).resolve().parents[4]


def _qdrant_local_path() -> Path:
    """Qdrant 本地索引目录:env VKPI_KOL_QDRANT_PATH > VKPI_RUNTIME_DATA_DIR/runtime/vkpi_qdrant > 仓库 runtime/。
    prod 发布树只读且 worker 沙箱把 runtime 设为只读时,打开 .lock 会 Errno 30 → 召回整段降级(2026-07-26 起 14 次);
    单元模板已把 runtime/vkpi_qdrant 放进 ReadWritePaths,这里再给运维一个可移路径。"""
    import os as _os

    explicit = _os.environ.get("VKPI_KOL_QDRANT_PATH", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    data_root = _os.environ.get("VKPI_RUNTIME_DATA_DIR", "").strip()
    if data_root:
        return Path(data_root).expanduser() / "runtime" / "vkpi_qdrant"
    return PROJECT_ROOT / "runtime" / "vkpi_qdrant"


QDRANT_LOCAL_PATH = _qdrant_local_path()
OPENAI_EMBEDDING_PRICE_PER_1M = Decimal("0.02")
MAX_CANDIDATE_LIMIT = 500
DEFAULT_RESULT_LIMIT = 30
SUPPORTED_RECALL_FILTERS = frozenset(
    {
        "platforms",
        "countries",
        "languages",
        "followers_min",
        "followers_max",
        "follower_min",
        "follower_max",
        "verticals",
        "gear_content",
    }
)
SEARCH_STRATEGY_BUCKET_POLICIES: dict[str, dict[str, int]] = {
    "balanced": {"core_vertical": 18, "expansion": 9, "exploration": 3},
    "vertical": {"core_vertical": 24, "expansion": 5, "exploration": 1},
    "expansion": {"core_vertical": 15, "expansion": 12, "exploration": 3},
}
LENS_MENTION_RE = re.compile(
    r"\b(?:Viltrox[\s-]*)?(?:AF[\s-]*)?(?:1[356]5|90|85|75|56|55|50|35|28|27|25|24|16)\s*mm"
    r"(?:\s*[fF]/?\s*(?:1\.2|1\.4|1\.7|1\.8|2\.0|2|3\.5|4\.5))?"
    r"(?:\s*(?:Pro|LAB|EVO|AIR|DL|FE|Z|STM|VCM|APO))*\b",
    re.IGNORECASE,
)
PROFILE_REASON_KEYWORDS = (
    ("人像", ("portrait", "portraits", "人像", "model", "fashion", "bokeh")),
    ("街拍", ("street", "街拍", "street photography", "stranger")),
    ("婚礼", ("wedding", "engagement", "婚礼")),
    ("纪实", ("documentary", "storytelling", "cinematic", "film", "filmmaker", "video", "叙事", "电影感")),
    ("测评", ("review", "comparison", "test", "unboxing", "评测", "对比")),
    ("旅行", ("travel", "landscape", "city", "城市", "风光")),
)


@dataclass(frozen=True)
class RecallHit:
    kol_pool_id: int
    vector_score: float | None
    qdrant_point_id: str
    lexical_score: float | None = None
    retrieval_score: float | None = None
    retrieval_method: str = "vector_v1"
    retrieval_tier: str = "relaxed"
    hybrid_rrf_score: float | None = None
    retrieval_meta: dict[str, Any] = field(default_factory=dict)


def _clean_text(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


__all__ = [
    "COLLECTION_NAME",
    "DEFAULT_RESULT_LIMIT",
    "EMBEDDING_MODEL",
    "LENS_MENTION_RE",
    "MAX_CANDIDATE_LIMIT",
    "METHOD",
    "OPENAI_EMBEDDING_PRICE_PER_1M",
    "PROFILE_REASON_KEYWORDS",
    "PROJECT_ROOT",
    "QDRANT_LOCAL_PATH",
    "RecallHit",
    "SEARCH_STRATEGY_BUCKET_POLICIES",
    "SUPPORTED_RECALL_FILTERS",
    "VECTOR_SIZE",
    "_clean_text",
]
