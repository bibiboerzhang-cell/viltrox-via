"""
services/intelligence/account_search_terms.py — 平台搜索的**纯函数**层:检索词整形 + 候选收敛。

车道 2(在线发现分页/多轮)配套拆分:``account_search_discovery.py`` 在软棘轮里锁死
843 行,分页所需的 pageToken 管线要占位置,于是把这批**零 IO、零 provider 依赖**的
纯函数抽到兄弟文件(与 ``account_search_instagram.py`` 同套路),而不是去刷棘轮快照。

行为不变量:原名在 ``account_search_discovery`` 里 re-export,既有 import 点与
monkeypatch 点(``account_scan_service._short_search_queries`` 等)逐字不变。
红线:纯候选层,零触 viltrox_fit_score / rule_v0,不含任何质量判据。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List


def _short_search_queries(query: str, *, max_queries: int = 4) -> List[str]:
    """Turn a planner's comma-separated persona list into short search intents.

    TikTok's keyword actor performs poorly when it receives the whole planner
    sentence as one exact query. The returned list is bounded and deterministic;
    callers divide the original result budget across these variants.
    """
    chunks = [" ".join(part.split()) for part in re.split(r"[,;|，；]+", query or "") if part.strip()]
    if len(chunks) <= 1:
        words = (query or "").split()
        chunks = [" ".join(words[index:index + 5]) for index in range(0, len(words), 5)] or [query]
    out: List[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        value = " ".join(chunk.split()[:8]).strip()[:100]
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        out.append(value)
        if len(out) >= max_queries:
            break
    return out or [" ".join((query or "").split())[:100]]


def _youtube_search_query_variants(search_query: str, *, max_variants: int = 3) -> List[str]:
    """K2 扩量刀·YT 多路短词:planner 的长 persona 整句在 search.list type=channel 上
    命中率极低(实测 20 词整句只回 1 条频道,funnel 997/1089 两轮坐实)。整句 ≤6 词
    直接用(与旧行为一致);更长则拆成 ≤max_variants 条短意图词(复用
    _short_search_queries 的逗号/5 词分块口径),调用方逐条搜后按 channelId 合并去重。
    纯函数零 IO,便于单测。

    车道 2 补注:这批变体**必须是确定性的**——分页游标按变体存(每个变体各有一条
    search.list 页链),变体一变,上一轮的 nextPageToken 就对不上任何一条页链了。
    """
    full_q = " ".join(str(search_query or "").split())
    if not full_q:
        return []
    variants: List[str] = [full_q] if len(full_q.split()) <= 6 else []
    for candidate in _short_search_queries(full_q, max_queries=max_variants):
        if candidate and candidate.lower() not in {v.lower() for v in variants}:
            variants.append(candidate)
    return variants[:max_variants] or [full_q]


def _tiktok_collapse_author_videos(raw_items: List[Dict[str, Any]], safe_limit: int) -> List[Dict[str, Any]]:
    """重复卡修(2026-07-21 sky_vanya 案)·TT 号主收敛:关键词搜出的视频流按 authorMeta.name
    收敛成「每号主一条」(保 actor 相关度序首条)。多路短词变体 + 同号主多视频会让同一人
    吃掉多个候选槽位并重复上墙——YT 快路径按 channelId 合并、IG 有 owner 收敛,TT 此前缺
    这道 (platform, handle 小写) 去重。无 author 的条目保序排尾兜底。纯函数零 IO。"""
    by_author: Dict[str, Dict[str, Any]] = {}
    authorless: List[Dict[str, Any]] = []
    for item in raw_items:
        row = item if isinstance(item, dict) else {}
        author = row.get("authorMeta") if isinstance(row.get("authorMeta"), dict) else {}
        handle = str(author.get("name") or "").strip().lstrip("@").lower()
        if not handle:
            raw_author = row.get("author")
            handle = str(raw_author or "").strip().lstrip("@").lower() if isinstance(raw_author, str) else ""
        if not handle:
            authorless.append(row)
            continue
        if handle not in by_author:
            by_author[handle] = row
    merged = list(by_author.values()) + authorless
    return merged[: max(1, int(safe_limit or 1))]


def _candidate_identity_key(item: Dict[str, Any]) -> str:
    """归一候选身份键 (platform, handle 小写去 @);无 handle 退 channel_url/source_url 小写。
    键为空 → 调用方放行(不误杀)。供聚合层「每号主一条」兜底去重,纯函数零 IO。"""
    platform = str(item.get("platform") or "").strip().lower()
    handle = str(item.get("handle") or "").strip().lstrip("@").lower()
    if handle:
        return f"{platform}:{handle}"
    for key in ("channel_url", "source_url"):
        url = str(item.get(key) or "").strip().lower()
        if url:
            return f"{platform}:{url}"
    return ""
