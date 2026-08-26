"""库内召回候选写进搜索会话时的**回放字段白名单**(从 search_sessions_attach 抽出)。

抽成兄弟模块的原因很实际:白名单是会随功能增长的清单,原文件已经贴着 1000 行硬顶,
再往里加字段就会撞行数守卫。清单放这里,``search_sessions_attach`` 只管逻辑。

口径不变:会话历史是**可回放的存档面**,不是紧凑缓存;白名单显式列举,免得日后
把无关的第三方 payload 顺手落库。
"""
from __future__ import annotations


_RECALL_SESSION_PAYLOAD_SCHEMA = "kol_recall_candidate_v2"

# Search-session history is a durable replay surface, not a compact card cache.
# Keep this allow-list explicit so the replay preserves search/audit semantics
# without accidentally persisting unrelated future provider payloads.
_RECALL_SESSION_PAYLOAD_FIELDS = (
    "handle",
    "display_name",
    "platform",
    "profile_url",
    "avatar_url",
    "followers",
    "avg_views",
    "avg_likes",
    "avg_comments",
    "engagement_rate",
    "real_er",
    "real_er_sample_n",
    "real_er_computed_at",
    "real_er_method",
    "data_truth",
    "country",
    "language",
    "primary_topic",
    "bio",
    "vector_score",
    "lexical_score",
    "hybrid_rrf_score",
    "retrieval_score",
    "retrieval_method",
    "type_rank_score",
    "type_score",
    "recall_rank_score",
    "recall_rank_score_method",
    "robust_rank_score",
    "robust_rank_method",
    "precision_rank_score",
    "precision_rank_method",
    "ranking_claim_status",
    "ranking_confidence",
    "platform_calibration",
    "display_rank_score",
    "display_relevance_adjust",
    "relevance_flags",
    "relevance_tier_hint",
    "profile_type",
    "provisional_profile_lane",
    "provisional_profile_lane_source",
    "profile_type_confidence",
    "type_label",
    "creator_type_score",
    "reviewer_type_score",
    "type_reason",
    "type_method",
    "match_tier",
    "filter_status",
    "relaxed_filters",
    "unknown_fields",
    # 车道 3:判到的垂类 + 「为什么算他是这一类」,回放时卡面照样说得清。
    "vertical_tags",
    "vertical_evidence",
    "candidate_bucket",
    "candidate_bucket_reason",
    "candidate_bucket_target",
    "business_lane",
    "candidate_lane",
    "recall_reason",
    "why_fit",
    "evidence",
    "sample_title",
    "used_lenses",
    "used_lenses_note",
    "representative_evidence",
    "evidence_confidence",
    "evidence_quality",
)

_RECALL_SESSION_SOURCE_FIELDS = (
    "vector_method",
    "type_method",
    "retrieval_method",
    "retrieval_tier",
    "sufficiency",
    "ranking_method",
)
