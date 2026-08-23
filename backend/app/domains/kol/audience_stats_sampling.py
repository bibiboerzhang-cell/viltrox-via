"""受众画像 · 抽样层(从 audience_stats 拆出,行为不变)。

YouTube Data API 抽评论者(免费额度;本地被墙需 HTTPS_PROXY)+ IG/TT 复用库里已抓的
vkpi_comments + 关注图谱 v0(公开订阅)。纯抽样,不推断、不聚合、不写 pool 行。
monkeypatch 兼容:对 audience_stats._yt_get / _yt_api_key / _resolve_channel_id 的补丁经 _live() 生效。
红线:绝不写 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

_YT_API_BASE = "https://www.googleapis.com/youtube/v3"


def _live(name: str) -> Any:
    """经门面解析协作函数:tests 在 app.domains.kol.audience_stats 上 monkeypatch 的同名符号仍生效。"""
    facade = sys.modules.get("app.domains.kol.audience_stats")
    target = getattr(facade, name, None) if facade is not None else None
    return target if target is not None else globals()[name]


# ── YouTube Data API 抽样层 ──

def _yt_api_key() -> str:
    for key in ("YOUTUBE_API_KEY", "GOOGLE_API_KEY", "GOOGLE_YOUTUBE_API_KEY"):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    return ""


def _proxy_hint() -> str:
    if (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "").strip():
        return ""
    return "本地网络到 googleapis 需代理:export HTTPS_PROXY=<YTDLP_PROXY 值>(见 scripts/runtime_env.sh)"


def _yt_get(endpoint: str, params: dict[str, Any], *, timeout: int = 20) -> dict[str, Any]:
    """GET youtube/v3 端点。urllib 默认吃 env 代理(HTTPS_PROXY)。失败 raise RuntimeError(带提示)。"""
    api_key = _live("_yt_api_key")()
    if not api_key:
        raise RuntimeError("missing YOUTUBE_API_KEY / GOOGLE_API_KEY")
    query = {k: v for k, v in params.items() if v is not None and v != ""}
    query["key"] = api_key
    url = f"{_YT_API_BASE}/{endpoint}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "ViltroxMarketing/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - fixed Google API host.
            payload = json.loads(response.read().decode("utf-8") or "{}")
            if not isinstance(payload, dict):
                raise ValueError("youtube api expected a JSON object")
            return payload
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8")[:400]
        except Exception:
            body = ""
        raise RuntimeError(f"youtube api http {exc.code}: {body}") from exc
    except Exception as exc:
        hint = _proxy_hint()
        raise RuntimeError(f"youtube api unreachable: {exc}" + (f" | {hint}" if hint else "")) from exc


def _resolve_channel_id(channel_id_or_handle: str) -> str:
    """UC 开头当 channel_id 直用;否则按 handle 走 channels.list(forHandle)。解析不到返回空串。"""
    raw = str(channel_id_or_handle or "").strip()
    if not raw:
        return ""
    if raw.startswith("UC") and len(raw) >= 20 and "/" not in raw:
        return raw
    # 从 URL 里抠 /channel/UC…
    match = re.search(r"/channel/(UC[0-9A-Za-z_-]{10,})", raw)
    if match:
        return match.group(1)
    handle = raw
    handle_match = re.search(r"@([^/?#\s]+)", raw)
    if handle_match:
        handle = handle_match.group(1)
    payload = _live("_yt_get")("channels", {"part": "id", "forHandle": handle.strip("@")})
    items = payload.get("items") or []
    if items and isinstance(items[0], dict):
        return str(items[0].get("id") or "")
    return ""


def sample_youtube_commenters(channel_id_or_handle: str, max_comments: int = 400) -> dict[str, Any]:
    """YouTube 免费 Data API 抽评论者:commentThreads(全频道) -> channels.list 批量补档案。

    v2 白捡字段:同一批 channels.list 换 part=snippet,statistics,零额外配额顺手带回
    bio(description)/channel_created_at(publishedAt)/subscriber_count/video_count。
    同时带回原始评论列表 comments(text/created_at/like_count/video_key,给 comment_intel 用)。
    返回 {status, channel_id, commenters:[...], comments:[...], comments_scanned, reply_total, api_calls}。
    网络/配置失败诚实返回 {status:..., reason:...}(不 raise)。
    """
    started = time.time()
    if not _live("_yt_api_key")():
        return {"status": "not_configured", "reason": "missing YOUTUBE_API_KEY / GOOGLE_API_KEY"}
    api_calls = 0
    try:
        channel_id = _live("_resolve_channel_id")(channel_id_or_handle)
        api_calls += 1
    except RuntimeError as exc:
        return {"status": "network_error", "reason": str(exc)[:400]}
    if not channel_id:
        return {"status": "channel_not_found", "reason": f"cannot resolve channel from {channel_id_or_handle!r}"}

    by_author: dict[str, dict[str, Any]] = {}
    comments: list[dict[str, Any]] = []
    comments_scanned = 0
    reply_total = 0
    page_token = ""
    partial_reason = ""
    try:
        while comments_scanned < int(max_comments or 400):
            payload = _live("_yt_get")(
                "commentThreads",
                {
                    "part": "snippet",
                    "allThreadsRelatedToChannelId": channel_id,
                    "textFormat": "plainText",
                    "maxResults": min(100, int(max_comments) - comments_scanned),
                    "pageToken": page_token or None,
                },
            )
            api_calls += 1
            items = payload.get("items") or []
            for thread in items:
                thread_snippet = (thread or {}).get("snippet") or {}
                snippet = (thread_snippet.get("topLevelComment") or {}).get("snippet", {})
                if not isinstance(snippet, dict):
                    continue
                comments_scanned += 1
                reply_total += int(thread_snippet.get("totalReplyCount") or 0)
                text = str(snippet.get("textOriginal") or snippet.get("textDisplay") or "")
                display_name = str(snippet.get("authorDisplayName") or "").strip()
                comments.append(
                    {
                        "text": text[:500],
                        "author": display_name,
                        "created_at": str(snippet.get("publishedAt") or ""),
                        "like_count": int(snippet.get("likeCount") or 0),
                        "is_reply": False,
                        "video_key": str(snippet.get("videoId") or thread_snippet.get("videoId") or ""),
                    }
                )
                author_id = str(((snippet.get("authorChannelId") or {}).get("value")) or "").strip()
                if not author_id:
                    continue
                entry = by_author.setdefault(
                    author_id,
                    {
                        "platform": "youtube",
                        "author_key": author_id,
                        "display_name": display_name,
                        "comment_text": "",
                        "declared_country": "",
                        "bio": "",
                        "channel_created_at": "",
                        "subscriber_count": None,
                        "video_count": None,
                    },
                )
                # 同一评论者最多攒 3 条评论合喂(2026-07-02:单条判不出年龄,多条口吻/话题互证)。
                if text and str(entry["comment_text"]).count(" || ") < 2:
                    entry["comment_text"] = (
                        f"{entry['comment_text']} || {text[:160]}" if entry["comment_text"] else text[:200]
                    )[:500]
            page_token = str(payload.get("nextPageToken") or "")
            if not page_token or not items:
                break
    except RuntimeError as exc:
        if not by_author:
            return {"status": "network_error", "reason": str(exc)[:400]}
        logger.warning("audience_stats yt comment paging partial: %s", exc)
        partial_reason = "youtube_comment_paging_unavailable"

    # 批量 channels.list(part=snippet,statistics, 50/批):自报 country(强信号 .9)+ 白捡档案字段。
    author_ids = list(by_author.keys())
    declared_hits = 0
    profile_batches_failed = 0
    for index in range(0, len(author_ids), 50):
        chunk = author_ids[index : index + 50]
        try:
            payload = _live("_yt_get")(
                "channels", {"part": "snippet,statistics", "id": ",".join(chunk), "maxResults": 50}
            )
            api_calls += 1
        except RuntimeError as exc:
            logger.warning("audience_stats yt channels.list batch failed: %s", exc)
            profile_batches_failed += 1
            continue
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("id") or "")
            if cid not in by_author:
                continue
            snippet = item.get("snippet") or {}
            stats = item.get("statistics") or {}
            entry = by_author[cid]
            country = str(snippet.get("country") or "").strip().upper()
            if country:
                entry["declared_country"] = country
                declared_hits += 1
            entry["bio"] = str(snippet.get("description") or "").strip()[:500]
            entry["channel_created_at"] = str(snippet.get("publishedAt") or "").strip()
            # E 路头像视觉用:同一批白捡头像缩略图 URL(不落库,仅本次刷新内存用)。
            try:
                thumbs = snippet.get("thumbnails") or {}
                entry["avatar_url"] = str(
                    (thumbs.get("default") or {}).get("url") or (thumbs.get("medium") or {}).get("url") or ""
                ).strip()
            except Exception:
                entry["avatar_url"] = ""
            try:
                if not stats.get("hiddenSubscriberCount"):
                    entry["subscriber_count"] = int(stats.get("subscriberCount"))
            except (TypeError, ValueError):
                pass
            try:
                entry["video_count"] = int(stats.get("videoCount"))
            except (TypeError, ValueError):
                pass
    result = {
        "status": "ok",
        "channel_id": channel_id,
        "commenters": list(by_author.values()),
        "comments": comments,
        "comments_scanned": comments_scanned,
        "reply_total": reply_total,
        "declared_country_hits": declared_hits,
        "api_calls": api_calls,
        "elapsed_sec": round(time.time() - started, 2),
    }
    if partial_reason:
        result["partial"] = True
        result["reason"] = partial_reason
    elif profile_batches_failed:
        result["partial"] = True
        result["reason"] = "youtube_commenter_profile_enrichment_unavailable"
    if profile_batches_failed:
        result["profile_batches_failed"] = profile_batches_failed
    return result


# ── IG / TikTok:复用库里已抓评论(不新写抓取)──

def sample_local_commenters(kol_pool_id: int, *, conn: Any = None, limit: int = 800) -> dict[str, Any]:
    """从 vkpi_comments 读该 KOL 的评论者(author_handle + comment_text)。

    两条桥(与 audience_language_for_kol 同款):account_id=kol_pool_id;
    或 post_table 属 evidence 口径 + post_id 落在该 KOL 的 video evidence 上
    (历史写入既有 'evidence' 也有 'vkpi_kol_video_evidence',两个都认)。
    """
    from app.db.connection import get_conn

    db = conn or get_conn()
    rows = db.execute(
        "SELECT author_handle, author_id, raw_data_json, comment_text FROM vkpi_comments "
        "WHERE account_id=? AND post_table IN ('evidence','vkpi_kol_video_evidence') LIMIT ?",
        (int(kol_pool_id), int(limit)),
    ).fetchall()
    if not rows:
        ev = db.execute(
            "SELECT id FROM vkpi_kol_video_evidence WHERE kol_pool_id=? LIMIT 100",
            (int(kol_pool_id),),
        ).fetchall()
        eids = [int(dict(e)["id"]) for e in ev]
        if eids:
            placeholders = ",".join(["?"] * len(eids))
            rows = db.execute(
                "SELECT author_handle, author_id, raw_data_json, comment_text FROM vkpi_comments "
                f"WHERE post_table IN ('evidence','vkpi_kol_video_evidence') AND post_id IN ({placeholders}) LIMIT ?",
                (*eids, int(limit)),
            ).fetchall()
    by_author: dict[str, dict[str, Any]] = {}
    comments_scanned = 0
    for r in rows:
        rec = dict(r)
        comments_scanned += 1
        handle = str(rec.get("author_handle") or "").strip()
        if not handle:
            # 救援链:部分抓取批次 author_handle 为空(TikTok 批次作者在 raw 的 uniqueId,
            # 或仅有 author_id)。逐级兜底,救不出才跳过 —— 治 no_commenters 假空。
            handle = str(rec.get("author_id") or "").strip()
        if not handle:
            try:
                import json as _rj

                _raw = rec.get("raw_data_json")
                _rd = _rj.loads(_raw) if isinstance(_raw, str) else (_raw or {})
                if isinstance(_rd, dict):
                    handle = str(
                        _rd.get("uniqueId") or _rd.get("username") or _rd.get("ownerUsername") or _rd.get("uid") or ""
                    ).strip()
            except Exception:
                handle = ""
        if not handle:
            continue
        author_key = handle.lower()
        entry = by_author.setdefault(
            author_key,
            {
                "platform": "",  # 由 refresh 按 pool 行平台补
                "author_key": author_key,
                "display_name": handle,
                "comment_text": "",
                "declared_country": "",
            },
        )
        _txt = str(rec.get("comment_text") or "").strip()
        # 同一评论者最多攒 3 条评论合喂(多条口吻/话题互证,提年龄判定率)。
        if _txt and str(entry["comment_text"]).count(" || ") < 2:
            entry["comment_text"] = (
                f"{entry['comment_text']} || {_txt[:160]}" if entry["comment_text"] else _txt[:200]
            )[:500]
        # E 路头像视觉用:从 raw 白捡头像缩略图(Apify TikTok/IG 评论批次字段名不一,逐个兜)。
        if not entry.get("avatar_url"):
            try:
                _raw = rec.get("raw_data_json")
                _rd = json.loads(_raw) if isinstance(_raw, str) else (_raw or {})
                if isinstance(_rd, dict):
                    _av = (
                        _rd.get("avatarThumbnail") or _rd.get("ownerProfilePicUrl")
                        or _rd.get("profile_pic_url") or ""
                    )
                    if not _av and isinstance(_rd.get("user"), dict):
                        _u = _rd["user"]
                        _av = _u.get("avatarThumb") or _u.get("avatar_thumb") or ""
                        if isinstance(_av, dict):
                            _av = (_av.get("url_list") or [""])[0]
                    entry["avatar_url"] = str(_av or "").strip()
            except Exception:
                entry["avatar_url"] = ""
    return {
        "status": "ok",
        "commenters": list(by_author.values()),
        "comments_scanned": comments_scanned,
        "source": "vkpi_comments_pool_evidence",
    }


def _youtube_channel_ref(row: dict[str, Any]) -> str:
    """从 pool 行找 YouTube channel 引用:raw 里的 channelId > profile_url /channel/UC > handle。"""
    handle = str(row.get("handle") or "").strip()
    if handle.startswith("UC") and len(handle) >= 20:
        return handle
    try:
        raw = row.get("raw_platform_data")
        data = json.loads(raw) if isinstance(raw, str) and raw.strip() else (raw if isinstance(raw, dict) else {})
        for key in ("channel_id", "channelId"):
            value = str((data or {}).get(key) or "").strip()
            if value.startswith("UC"):
                return value
        identity = (data or {}).get("identity") or {}
        value = str(identity.get("channel_id") or identity.get("channelId") or "").strip()
        if value.startswith("UC"):
            return value
    except Exception:
        logger.debug("payload 提取 channel_id 失败,退 profile_url 正则(best-effort)", exc_info=True)
    profile_url = str(row.get("profile_url") or "")
    match = re.search(r"/channel/(UC[0-9A-Za-z_-]{10,})", profile_url)
    if match:
        return match.group(1)
    return handle or profile_url


AFFINITY_MAX_LOOKUPS = 80  # 关注图谱 v0:最多探多少个评论者的公开订阅(quota 1 单位/次,多数私密快速 403)
AFFINITY_MIN_SHARED = 2    # 至少 2 人共同关注才上榜(单人无统计意义)


def _yt_audience_affinity(commenter_channel_ids: list[str], self_channel_id: str) -> dict[str, Any]:
    """关注图谱 v0(Modash「audience also follows」同款思路,只用免费 YT API):

    抽样评论者 -> subscriptions.list(channelId=评论者)拿其公开订阅(约 2-3 成用户公开),
    聚合成「这群受众还关注谁」Top 榜。私密订阅返回 403 属常态,逐个跳过;quota 打穿即止。
    """
    from collections import Counter as _Counter

    sub_counter: _Counter = _Counter()
    titles: dict[str, str] = {}
    sampled = 0
    public_hits = 0
    for cid in commenter_channel_ids[:AFFINITY_MAX_LOOKUPS]:
        if not str(cid or "").startswith("UC"):
            continue
        sampled += 1
        try:
            payload = _live("_yt_get")("subscriptions", {"part": "snippet", "channelId": cid, "maxResults": 50}, timeout=8)
        except RuntimeError as exc:
            msg = str(exc)
            if "quota" in msg.lower():
                logger.warning("audience affinity quota hit after %s lookups", sampled)
                break
            continue  # 403 subscriptionForbidden = 订阅私密,常态
        items = payload.get("items") or []
        if items:
            public_hits += 1
        for it in items:
            sn = it.get("snippet") or {} if isinstance(it, dict) else {}
            rid = str(((sn.get("resourceId") or {}).get("channelId")) or "")
            if not rid or rid == self_channel_id:
                continue
            sub_counter[rid] += 1
            if rid not in titles:
                titles[rid] = str(sn.get("title") or "")[:60]
    top = [
        {"channel_id": k, "title": titles.get(k, ""), "shared": v}
        for k, v in sub_counter.most_common(12)
        if v >= AFFINITY_MIN_SHARED
    ][:10]
    return {
        "items": top,
        "sampled": sampled,
        "public_subs_found": public_hits,
        "method": "yt_public_subscriptions_v0",
        "note": "受众公开订阅抽样(约两三成用户订阅公开);shared=共同关注人数",
    }


