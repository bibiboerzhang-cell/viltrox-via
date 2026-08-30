"""Characterization tests locking _infer_kind's full branch behavior.

Written BEFORE the CC-54 if/elif chain was refactored into an ordered rule
table; every case below enumerates one trigger of the original classifier so
the table-driven rewrite must reproduce it verbatim.
"""
from __future__ import annotations

import pytest

from app.domains.tasks.queue_view import _infer_kind


@pytest.mark.parametrize(
    ("source", "job_type", "purpose", "payload", "expected"),
    [
        # --- exact job_type matches (case-insensitive) ---
        ("apify_jobs", "kol_lookup", "", None, "KOL查找"),
        ("apify_jobs", "KOL_LOOKUP", "", None, "KOL查找"),
        ("apify_jobs", "smart_search_profile_advance", "", None, "智能查找"),
        ("apify_jobs", "session_advance", "", None, "资料补全"),
        ("apify_jobs", "account_dossier_extract", "", None, "账号沉淀"),
        ("apify_jobs", "project_contract_extract", "", None, "合同提取"),
        ("apify_jobs", "contract_invoice_extract", "", None, "发票提取"),
        ("apify_jobs", "contract_polish", "", None, "合同润色"),
        ("apify_jobs", "project_retrospective_aggregate", "", None, "复盘聚合"),
        ("apify_jobs", "video_url_resolve", "", None, "视频解析"),
        ("apify_jobs", "kol_profile_deep_crawl", "", None, "账号分析"),
        ("apify_jobs", "kol_pool_comments_collect", "", None, "评论采集"),
        ("apify_jobs", "kol_audience_stats_refresh", "", None, "受众分析"),
        ("apify_jobs", "kol_content_fit_analysis", "", None, "内容契合"),
        ("apify_jobs", "kol_outreach_draft", "", None, "联系草稿"),
        ("apify_jobs", "logistics_track_sync", "", None, "物流同步"),
        # --- haystack substring matches (purpose / payload feed the haystack) ---
        ("ledger", "", "run kol_lookup now", None, "KOL查找"),
        ("ledger", "", "kol_smart_search_profile_advance step", None, "智能查找"),
        ("ledger", "", "kol_account_dossier_extract", None, "账号沉淀"),
        ("ledger", "", "project_contract_extract", None, "合同提取"),
        ("ledger", "", "contract_invoice_extract", None, "发票提取"),
        ("ledger", "", "contract_polish", None, "合同润色"),
        ("ledger", "", "project_retrospective", None, "复盘聚合"),
        ("ledger", "", "kol_profile_deep_crawl", None, "账号分析"),
        ("ledger", "", "kol_pool_comments_collect", None, "评论采集"),
        ("ledger", "", "audience_stats", None, "受众分析"),
        ("ledger", "", "audience_age", None, "受众分析"),
        ("ledger", "", "kol_content_fit_analysis", None, "内容契合"),
        ("ledger", "", "", {"derive_method": "content_fit_v1"}, "内容契合"),
        ("ledger", "", "kol_outreach_draft", None, "联系草稿"),
        ("ledger", "", "logistics_track_sync", None, "物流同步"),
        ("ledger", "", "keyframe_qa", None, "视频QA"),
        ("ledger", "", "video_qa", None, "视频QA"),
        ("ledger", "", "marketing_advisor", None, "营销顾问"),
        ("ledger", "", "our Advisor", None, "营销顾问"),
        # payload fields that feed the haystack: derive_method/target_type/prompt/script
        ("ledger", "", "", {"target_type": "kol_lookup"}, "KOL查找"),
        ("ledger", "", "", {"prompt": "please keyframe_qa this"}, "视频QA"),
        ("ledger", "", "", {"script": "marketing_advisor.py"}, "营销顾问"),
        # non-dict payload is ignored
        ("ledger", "", "", "kol_lookup", "任务"),
        # --- llm_calls-gated rules ---
        ("llm_calls", "", "sentiment", None, "评论分析"),
        ("llm_calls", "", "comment_reply", None, "评论分析"),
        ("llm_calls", "", "comment_intel", None, "评论分析"),
        ("llm_calls", "", "recall_rerank", None, "智能查找"),
        ("llm_calls", "", "query_plan", None, "智能查找"),
        ("llm_calls", "", "discovery_localize", None, "智能查找"),
        # same tokens without llm_calls source fall through to the default
        ("apify_jobs", "", "sentiment", None, "任务"),
        ("apify_jobs", "", "recall_rerank", None, "任务"),
        # --- generic haystack fallbacks ---
        ("apify_jobs", "", "final_v1", None, "video深析"),
        ("apify_jobs", "", "video_analysis", None, "video深析"),
        ("apify_jobs", "", "video thing", None, "video深析"),
        ("apify_jobs", "", "fetch url", None, "搜索/抓取"),
        ("apify_jobs", "", "profile", None, "搜索/抓取"),
        ("apify_jobs", "", "crawl", None, "搜索/抓取"),
        ("apify_jobs", "", "scan", None, "搜索/抓取"),
        ("apify_jobs", "", "resolve", None, "搜索/抓取"),
        ("apify_jobs", "", "download", None, "搜索/抓取"),
        ("apify_jobs", "", "ingest", None, "搜索/抓取"),
        ("apify_jobs", "", "daily sync", None, "搜索/抓取"),
        ("apify_jobs", "", "report", None, "报告生成"),
        ("apify_jobs", "", "brief", None, "报告生成"),
        ("apify_jobs", "", "summary", None, "报告生成"),
        ("apify_jobs", "", "cache_extract", None, "总结沉淀"),
        ("apify_jobs", "", "deep_result", None, "总结沉淀"),
        ("apify_jobs", "", "post_process", None, "总结沉淀"),
        ("apify_jobs", "", "backfill", None, "总结沉淀"),
        # --- LLM analysis catch-all ---
        ("llm_calls", "", "", None, "LLM分析"),
        ("apify_jobs", "", "gemini", None, "LLM分析"),
        ("apify_jobs", "", "claude", None, "LLM分析"),
        ("apify_jobs", "", "openai", None, "LLM分析"),
        ("apify_jobs", "", "llm", None, "LLM分析"),
        ("apify_jobs", "", "score", None, "LLM分析"),
        # --- default ---
        ("apify_jobs", "", "", None, "任务"),
        ("apify_jobs", "unknown_job", "nothing special", {}, "任务"),
        # --- ordering / precedence locks ---
        # exact job_type beats any later haystack token
        ("apify_jobs", "kol_lookup", "final_v1 video", None, "KOL查找"),
        ("apify_jobs", "video_url_resolve", "report", None, "视频解析"),
        # keyframe_qa wins over the later generic "video" rule
        ("apify_jobs", "", "video keyframe_qa", None, "视频QA"),
        # video深析 wins over 搜索/抓取
        ("apify_jobs", "", "video url", None, "video深析"),
        # 搜索/抓取 wins over 报告生成
        ("apify_jobs", "", "url report", None, "搜索/抓取"),
        # 报告生成 wins over 总结沉淀
        ("apify_jobs", "", "report backfill", None, "报告生成"),
        # llm_calls source still yields 搜索/抓取 before the LLM catch-all
        ("llm_calls", "", "crawl", None, "搜索/抓取"),
        # llm_calls-gated 评论分析 wins over the later video rule
        ("llm_calls", "", "sentiment video", None, "评论分析"),
        # audience token beats generic sync fallback ordering
        ("apify_jobs", "", "audience_stats sync", None, "受众分析"),
    ],
)
def test_infer_kind_characterization(source, job_type, purpose, payload, expected):
    assert _infer_kind(source, job_type=job_type, purpose=purpose, payload=payload) == expected
