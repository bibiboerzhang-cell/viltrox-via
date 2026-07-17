"""开闸刀:X(Twitter)/ 摄影论坛(Reddit)监听真执行体(2026-07-16 接线)。

迸发⑤ 骨架(market_listening.py)把闸/词族/字段映射铺好后留下的两个接线点在本模块落地:
  - Reddit:公开 JSON(hot 列表,免费)——复用 RedditCrawler 的 UA 与解析,
    每请求限速 >= 2 秒;只抓 FORUM_SOURCE_POLICY 里 policy="allowed" 的源
    (DPReview 论坛 / FredMiranda 是 no_scrape 合规禁区,本模块没有任何路径碰它们)。
  - X:Apify pay-per-result actor(XCrawler._apify_run),经 call_apify_actor
    统一预算预检(provider:apify)+ record_apify_run_cost 记账;单轮条数封顶控费。

落表(P5.68 既有 schema,零新表零迁移):
  帖级实体 -> vkpi_market_sources(source_type='listening_post')
            + vkpi_market_mentions(mention_text=标题+摘要,platform='reddit'/'x')。
  幂等:按 platform + source_ref(帖 id / 推文 id)去重,重复帖跳过不重写。
  「近期市场热词」聚合(hashtag_trends.build_hashtag_trends_v0)读 mentions 出词。

红线:不触 viltrox_fit_score / rule_v0;SQL 走 compat 占位符 ?(零字面 LIKE/%);
get_conn 懒 import 且不当上下文管理器用;网络代理认 env(runtime_env.sh 的 HTTPS_PROXY)。
闸(VKPI_X_COLLECT_ENABLED / VKPI_FORUM_COLLECT_ENABLED)由 market_listening.collect_*
统一判定,本模块函数被调到即视为闸已开。
"""
from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator
from urllib.parse import urlencode

from app.core.logging import get_logger
from app.domains.comments.market_listening import (
    FORUM_LISTEN_DAILY_CAP,
    X_LISTEN_DAILY_CAP,
    forum_listen_watchlist,
    x_listen_query_terms,
)

logger = get_logger(__name__)

# 帖级监听实体在 vkpi_market_sources 的类型标记(与既有 reddit_post/rss_feed 区分来源)
LISTEN_SOURCE_TYPE = "listening_post"

REDDIT_PLATFORM = "reddit"
X_PLATFORM = "x"

# 相机圈补充 subreddit(P5.67 watchlist 之外;仍是公开 JSON 合规通道)
REDDIT_EXTRA_SUBREDDITS: tuple[str, ...] = ("cameras", "canon")

# 公开 JSON 限速下限(合规姿态:比官方 60 req/min 宽松一个数量级)
REDDIT_MIN_REQUEST_INTERVAL_SECONDS = 2.0

# Reddit 付费兜底闸(显式 opt-in,默认关):公开 JSON 被 Reddit 按 IP 段挡
# (2026-07-16 实测:代理出口与本机直连都 403 Blocked)时,才允许走 Apify
# 路线(trudax reddit-scraper-lite,call_apify_actor 预算预检 + 记账)。
# 成本实测:单 run 有固定开销(~$0.10-0.13/run),所以兜底把所有失败 sub
# 合并成 1 个 run(每天 1 run ≈ $0.13,月 ≈ $4),绝不按 sub 逐个起 run。
# 免费路先行,免费路通(如部署机 IP 未被挡)则永不触发付费。
REDDIT_APIFY_FALLBACK_GATE = "VKPI_FORUM_APIFY_FALLBACK_ENABLED"

_TRUTHY = {"1", "true", "yes", "on"}


def _reddit_apify_fallback_enabled() -> bool:
    return os.environ.get(REDDIT_APIFY_FALLBACK_GATE, "").strip().lower() in _TRUTHY

# X 单轮默认条数:2026-07-16 实测 apidojo 结算 $2.56/1k(非设计单标的 $0.40/1k),
# 60 条/轮 ≈ $0.15/天 ≈ $4.6/月,守住 ~$10/月 授权(与 Reddit 兜底合计)。
X_DEFAULT_MAX_ITEMS = 60


def _now_z() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json_dump(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _int0(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


@contextmanager
def _provider_execution_fence(task_id: str, *, job_type: str) -> Iterator[None]:
    """付费 Apify 调用的 durable 供应商围栏(worker 同款原语,预算预检硬要求)。

    已在 worker 围栏内(current context 非空)则直接透传,不叠加双围栏;
    否则按 platform+日 拿短命 claim(重跑同日只 bump fence,天然幂等),
    结束按结果 finalize(completed/failed),预算预检与记账仍全在 call_apify_actor。
    """
    from app.platform.apify_budget import (
        acquire_provider_execution_claim,
        apify_execution_context,
        current_apify_execution_context,
        finalize_provider_execution_claim,
    )

    if current_apify_execution_context() is not None:
        yield
        return
    fence = acquire_provider_execution_claim(
        task_id, "listening_executor", job_type=job_type, lease_seconds=900
    )
    state = "completed"
    try:
        with apify_execution_context(task_id, fence):
            yield
    except Exception:
        state = "failed"
        raise
    finally:
        try:
            finalize_provider_execution_claim(task_id, fence, state)
        except Exception:
            logger.debug("listening provider claim finalize failed", exc_info=True)


def _listening_fence_task_id(platform: str) -> str:
    return f"market_listening:{platform}:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"


def default_reddit_watchlist() -> list[str]:
    """采集默认 subreddit:P5.67 watchlist + 相机圈补充,去重保序。"""
    subs: list[str] = []
    for sub in [*forum_listen_watchlist(), *REDDIT_EXTRA_SUBREDDITS]:
        clean = str(sub or "").strip().lstrip("/").replace("r/", "")
        if clean and clean.lower() not in {s.lower() for s in subs}:
            subs.append(clean)
    return subs


# ── Reddit(免费公开 JSON)────────────────────────────────────────────────


def _fetch_subreddit_hot(crawler: Any, subreddit: str, limit: int) -> list[dict[str, Any]]:
    """单 subreddit hot 列表(1 次网络请求);解析复用 RedditJsonPathMixin。"""
    params = urlencode({"limit": max(1, min(100, int(limit or 25))), "raw_json": 1})
    payload = crawler._reddit_get_json(f"https://www.reddit.com/r/{subreddit}/hot.json?{params}")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    children = data.get("children") if isinstance(data.get("children"), list) else []
    posts: list[dict[str, Any]] = []
    for child in children:
        if not isinstance(child, dict) or child.get("kind") != "t3":
            continue
        post_data = child.get("data") if isinstance(child.get("data"), dict) else {}
        if post_data:
            posts.append(crawler._json_post_to_dict(post_data))
    return posts


def _filter_apify_posts(items: Any) -> list[dict[str, Any]]:
    """Apify reddit actor 条目过滤:只留真帖(community/profile 行不算)。"""
    posts: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("dataType") or item.get("type") or "").lower()
        if kind and kind != "post":
            continue
        if not (item.get("id") or item.get("name")):
            continue
        posts.append(item)
    return posts


def _fetch_reddit_batch_via_apify(
    crawler: Any, subreddits: list[str], *, max_total: int
) -> list[dict[str, Any]]:
    """免费路失败的 sub 合并成单个 Apify run(摊薄 ~$0.10/run 固定开销)。

    预算护栏:call_apify_actor 预检 provider:apify + record_apify_run_cost 记账,
    外层套 durable 供应商围栏(platform+日 幂等 task_id)。失败抛 RuntimeError。
    """
    token = str(getattr(crawler, "apify_token", "") or "")
    if not token:
        raise RuntimeError("APIFY_TOKEN not configured")
    try:
        from apify_client import ApifyClient  # type: ignore
    except ImportError as exc:  # pragma: no cover - 环境缺依赖时诚实抛
        raise RuntimeError("apify-client not installed") from exc
    from app.platform.apify_budget import call_apify_actor
    from app.platform.apify_lifecycle import close_apify_client
    from app.platform.industry_crawlers import record_apify_run_cost

    actor_id = str(getattr(crawler, "apify_actor", "") or "trudax~reddit-scraper-lite")
    # 实测该 actor 逐个 startUrl 顺序跑(~40-60 秒/sub),9 sub 批量要 >240 秒;
    # 批量超时下限提到 600 秒(也是成本上限:runtime 计费 ~$0.024/分钟 → 单 run 封顶 ~$0.24)。
    timeout = min(900, max(600, int(getattr(crawler, "run_timeout_seconds", 240) or 240)))
    client: Any | None = None
    try:
        client = ApifyClient(token)
        with _provider_execution_fence(
            _listening_fence_task_id(REDDIT_PLATFORM), job_type="market_listening_reddit"
        ):
            run = call_apify_actor(
                client,
                actor_id,
                platform="reddit",
                operation="listening_batch",
                source="listening_executors",
                run_input={
                    "startUrls": [
                        {"url": f"https://www.reddit.com/r/{sub}/"} for sub in subreddits
                    ],
                    "maxItems": max(1, int(max_total or 1)),
                    "type": "posts",
                    "sort": "hot",
                },
                timeout_secs=timeout,
                wait_secs=timeout,
            )
        status = str((run or {}).get("status") or "").upper()
        if not run or status not in {"SUCCEEDED", "TIMED-OUT"}:
            raise RuntimeError(f"reddit batch actor did not finish: {status or 'unknown'}")
        # TIMED-OUT 的部分数据集也已计费,照常记账 + 采纳(空数据集才算失败)
        record_apify_run_cost(run, platform="reddit", actor_id=actor_id, operation="listening_batch")
        dataset_id = run.get("defaultDatasetId")
        items = list(client.dataset(dataset_id).iterate_items()) if dataset_id else []
        if status == "TIMED-OUT" and not items:
            raise RuntimeError("reddit batch actor timed out with empty dataset")
        return items
    finally:
        close_apify_client(client)


def _reddit_post_record(post: dict[str, Any], *, subreddit: str = "") -> dict[str, Any] | None:
    """帖 dict(公开 JSON mixin 形状 / Apify actor 形状同键族)-> 统一落表 record;缺 id 丢弃。"""
    ref = str(post.get("id") or post.get("name") or "").strip()
    if not ref:
        return None
    title = str(post.get("title") or "").strip()
    excerpt = str(post.get("body") or "").strip()[:400]
    url = str(post.get("permalink") or post.get("url") or "").strip()
    score = _int0(post.get("score") or post.get("upVotes"))
    num_comments = _int0(post.get("numberOfComments") or post.get("num_comments"))
    published_at = str(post.get("createdAt") or "").strip()
    meta = {
        "origin": "listening",
        "subreddit": str(post.get("communityName") or post.get("subreddit") or subreddit or "").strip(),
        "url": url,
        "score": score,
        "num_comments": num_comments,
        "published_at": published_at,
    }
    return {
        "ref": ref,
        "url": url,
        "title": title,
        "excerpt": excerpt,
        "handle": str(post.get("author") or post.get("username") or "").strip(),
        "score": float(score),
        "source_meta": meta,
        "mention_meta": meta,
    }


def run_reddit_listening(
    subreddits: list[str] | None = None,
    *,
    per_sub_limit: int = 25,
    max_items: int | None = None,
    sleep_seconds: float = REDDIT_MIN_REQUEST_INTERVAL_SECONDS,
    conn: Any = None,
    fetch: Callable[[str, int], list[dict[str, Any]]] | None = None,
    batch_fetch: Callable[[list[str], int], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Reddit 监听真执行体:hot 帖 -> vkpi_market_sources/vkpi_market_mentions(幂等)。

    免费公开 JSON 逐 sub 抓(请求间隔 >= 2 秒);失败的 sub 在末尾合并成
    单个 Apify run 兜底(显式闸 REDDIT_APIFY_FALLBACK_GATE,默认关不烧钱)。
    fetch / batch_fetch 可注入(测试零网络零库)。
    """
    subs = [str(s).strip() for s in (subreddits or default_reddit_watchlist()) if str(s).strip()]
    cap = max(1, min(_int0(max_items) or FORUM_LISTEN_DAILY_CAP, FORUM_LISTEN_DAILY_CAP))
    providers: set[str] = set()
    crawler: Any = None
    if fetch is None or batch_fetch is None:
        from app.platform.industry_crawlers.reddit_crawler import RedditCrawler

        crawler = RedditCrawler()
    if fetch is None:

        def fetch(sub: str, limit: int, _crawler: Any = crawler) -> list[dict[str, Any]]:
            return _fetch_subreddit_hot(_crawler, sub, limit)

    if batch_fetch is None:

        def batch_fetch(failed: list[str], total: int, _crawler: Any = crawler) -> list[dict[str, Any]]:
            return _fetch_reddit_batch_via_apify(_crawler, failed, max_total=total)

    records: list[dict[str, Any]] = []
    errors: list[str] = []
    failed_subs: list[str] = []
    network_calls = 0
    for index, sub in enumerate(subs):
        if len(records) >= cap:
            break
        if index:
            time.sleep(max(REDDIT_MIN_REQUEST_INTERVAL_SECONDS, float(sleep_seconds or 0)))
        network_calls += 1
        try:
            posts = fetch(sub, min(max(1, _int0(per_sub_limit) or 25), cap - len(records)))
            providers.add("reddit_public_json")
        except Exception as exc:
            errors.append(f"r/{sub}: {str(exc)[:160]}")
            failed_subs.append(sub)
            continue
        for post in posts:
            record = _reddit_post_record(post, subreddit=sub)
            if record:
                records.append(record)
    # 免费路失败的 sub:闸开才走一次性 Apify 兜底(单 run 摊薄固定开销)
    if failed_subs and len(records) < cap:
        if _reddit_apify_fallback_enabled():
            total = min(cap - len(records), max(1, _int0(per_sub_limit) or 25) * len(failed_subs))
            network_calls += 1
            try:
                posts = _filter_apify_posts(batch_fetch(failed_subs, total))
                providers.add("apify_reddit_scraper")
                for post in posts:
                    record = _reddit_post_record(post)
                    if record:
                        records.append(record)
            except Exception as exc:
                # 兜底路失败要排最前,不被逐 sub 报错挤出截断窗
                errors.insert(0, f"apify_fallback({len(failed_subs)} subs): {str(exc)[:160]}")
        else:
            errors.insert(
                0,
                f"apify_fallback_skipped: {REDDIT_APIFY_FALLBACK_GATE} 未开(免费路失败 {len(failed_subs)} sub,零烧钱)",
            )
    persisted = _persist_listening_posts(conn, REDDIT_PLATFORM, records[:cap])
    if records:
        status = "ok"
    else:
        status = "error" if failed_subs or errors else "empty"
    return {
        "status": status,
        "platform": REDDIT_PLATFORM,
        "provider": "+".join(sorted(providers)) if providers else "reddit_public_json",
        "network_calls": network_calls,
        "fetched": len(records),
        "subreddits": subs,
        "errors": errors[:10],
        **persisted,
    }


# ── X(Apify pay-per-result actor)──────────────────────────────────────


def _parse_x_created_at(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return ""


def _x_item_record(item: dict[str, Any]) -> dict[str, Any] | None:
    """actor 条目 -> 统一落表 record;拿不到推文 id 的行诚实丢弃。"""
    if not isinstance(item, dict):
        return None
    url = str(item.get("url") or item.get("twitterUrl") or "").strip()
    tweet_id = str(item.get("id") or item.get("id_str") or "").strip()
    if not re.fullmatch(r"\d{8,25}", tweet_id):
        match = re.search(r"/status(?:es)?/(\d{8,25})", url)
        tweet_id = match.group(1) if match else ""
    if not tweet_id:
        return None
    text = str(item.get("fullText") or item.get("full_text") or item.get("text") or "").strip()
    author = item.get("author") if isinstance(item.get("author"), dict) else {}
    handle = str(author.get("userName") or author.get("username") or item.get("username") or "").strip()
    likes = _int0(item.get("likeCount") or item.get("favorite_count"))
    retweets = _int0(item.get("retweetCount") or item.get("retweet_count"))
    replies = _int0(item.get("replyCount") or item.get("reply_count"))
    quotes = _int0(item.get("quoteCount") or item.get("quote_count"))
    meta = {
        "origin": "listening",
        "url": url,
        "likes": likes,
        "retweets": retweets,
        "replies": replies,
        "quotes": quotes,
        "published_at": _parse_x_created_at(item.get("createdAt") or item.get("created_at")),
    }
    return {
        "ref": tweet_id,
        "url": url,
        "title": text[:200],
        "excerpt": text[200:600],
        "handle": handle,
        "score": float(likes),
        "source_meta": meta,
        "mention_meta": meta,
    }


def run_x_listening(
    queries: list[str] | None = None,
    *,
    max_items: int = X_DEFAULT_MAX_ITEMS,
    conn: Any = None,
    crawler: Any = None,
) -> dict[str, Any]:
    """X 监听真执行体:词族搜索(Apify actor)-> vkpi_market_sources/vkpi_market_mentions。

    预算护栏在 XCrawler._apify_run 内建:call_apify_actor 预检 provider:apify /
    monthly_total,record_apify_run_cost 记账;被闸时原样透传 blocked 结果不落库。
    """
    if not queries:
        terms = x_listen_query_terms()
        queries = [
            *terms.get("brand", []),
            *terms.get("competitors", []),
            *terms.get("categories", []),
        ]
    queries = [str(q).strip() for q in queries if str(q).strip()]
    cap = max(1, min(_int0(max_items) or X_DEFAULT_MAX_ITEMS, X_LISTEN_DAILY_CAP))
    if crawler is None:
        from app.platform.industry_crawlers.x_crawler import XCrawler

        crawler = XCrawler()
    if not getattr(crawler, "api_token", ""):
        return {
            "status": "not_configured",
            "platform": X_PLATFORM,
            "provider": "apify",
            "network_calls": 0,
            "fetched": 0,
            "new_sources": 0,
            "new_mentions": 0,
            "skipped_existing": 0,
            "queries": queries,
            "reason": "APIFY_TOKEN 未配置,X 监听未执行(如实报,不装)。",
        }
    run_input = {"searchTerms": queries, "maxItems": cap, "sort": "Latest"}
    with _provider_execution_fence(_listening_fence_task_id(X_PLATFORM), job_type="market_listening_x"):
        result = crawler._apify_run(run_input)
    if str(result.get("provider_status") or "") != "ok":
        blocked = bool(result.get("blocked")) or str(result.get("provider_status") or "") == "budget_blocked"
        return {
            "status": "blocked" if blocked else "error",
            "platform": X_PLATFORM,
            "provider": "apify",
            "network_calls": 1,
            "fetched": 0,
            "new_sources": 0,
            "new_mentions": 0,
            "skipped_existing": 0,
            "queries": queries,
            "reason": str(result.get("error") or result.get("reason") or result.get("provider_status") or "")[:240],
        }
    records: list[dict[str, Any]] = []
    for item in result.get("items") or []:
        record = _x_item_record(item)
        if record:
            records.append(record)
    persisted = _persist_listening_posts(conn, X_PLATFORM, records[:cap])
    return {
        "status": "ok" if records else "empty",
        "platform": X_PLATFORM,
        "provider": "apify",
        "actor_id": str((result.get("raw") or {}).get("actor_id") or ""),
        "network_calls": 1,
        "fetched": len(records),
        "queries": queries,
        **persisted,
    }


# ── 落表(共用,幂等)────────────────────────────────────────────────────


def _persist_listening_posts(conn: Any, platform: str, records: list[dict[str, Any]]) -> dict[str, int]:
    """帖级实体幂等落表:platform + source_ref 已存在(任何来源)即跳过。

    与 signal_ingest_use_case 同款写法:RETURNING id 链接 source->mention,
    整批 commit,异常整批 rollback(绝不半截账)。
    """
    if conn is None:
        from app.db.connection import get_conn

        conn = get_conn()
    refs = [str(r.get("ref") or "") for r in records if str(r.get("ref") or "")]
    existing: set[str] = set()
    if refs:
        placeholders = ",".join("?" for _ in refs)
        rows = conn.execute(
            "SELECT source_ref FROM vkpi_market_sources "
            f"WHERE LOWER(COALESCE(platform,''))=? AND source_ref IN ({placeholders})",
            tuple([platform, *refs]),
        ).fetchall()
        existing = {str(dict(row).get("source_ref") or "") for row in rows}
    now = _now_z()
    new_sources = 0
    new_mentions = 0
    skipped = 0
    try:
        for record in records:
            ref = str(record.get("ref") or "")
            if not ref or ref in existing:
                skipped += 1
                continue
            existing.add(ref)  # 同批内自身去重
            cursor = conn.execute(
                """
                INSERT INTO vkpi_market_sources
                    (run_id, source_type, platform, source_ref, source_url, title, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    None,
                    LISTEN_SOURCE_TYPE,
                    platform,
                    ref,
                    str(record.get("url") or "")[:1000],
                    str(record.get("title") or "")[:500],
                    _json_dump(record.get("source_meta") or {}),
                    now,
                ),
            )
            row = cursor.fetchone()
            source_id = int(dict(row)["id"]) if row else None
            new_sources += 1
            title = str(record.get("title") or "").strip()
            excerpt = str(record.get("excerpt") or "").strip()
            mention_text = " · ".join(part for part in (title, excerpt) if part)[:1000]
            conn.execute(
                """
                INSERT INTO vkpi_market_mentions
                    (run_id, source_id, platform, handle, mention_text, product_sku,
                     competitor_product, sentiment, score, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    None,
                    source_id,
                    platform,
                    str(record.get("handle") or "")[:120],
                    mention_text,
                    "",
                    "",
                    "",
                    float(record.get("score") or 0.0),
                    _json_dump(record.get("mention_meta") or {}),
                    now,
                ),
            )
            new_mentions += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {
        "new_sources": new_sources,
        "new_mentions": new_mentions,
        "skipped_existing": skipped,
    }
