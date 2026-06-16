#!/usr/bin/env python3
"""KOL 全量 metadata 抓取器 — Apify 深抓每 KOL 前 N 条有效视频清单,写本地下载队列。

只抓 metadata(视频 URL/标题/时长/播放/日期),不下载、不写 DB、不跑 LLM。
GFW 无关(Apify 跑在云端)。产出供 GCE 下载器消费。

用法:
  python3 scripts/scrape_kol_metadata.py --pilot 3 --per-kol 20
  python3 scripts/scrape_kol_metadata.py --per-kol 20          # 全量
"""
from __future__ import annotations
import argparse, json, re, sys, time
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.db.connection import get_conn  # noqa: E402
from app.platform.industry_crawlers.instagram_crawler import InstagramCrawler  # noqa: E402
from app.platform.industry_crawlers.youtube_crawler import YouTubeCrawler  # noqa: E402
from app.platform.industry_crawlers.tiktok_crawler import TikTokCrawler  # noqa: E402

VALID = {"youtube", "instagram", "tiktok"}  # facebook/x 暂缓(actor 字段差异大)


def _slug(v, fb="x", n=60):
    s = re.sub(r"[^a-z0-9]+", "-", str(v or "").lower()).strip("-")
    return s[:n].strip("-") or fb


def _first(d, *keys):
    for k in keys:
        v = d.get(k)
        if v:
            return v
    return ""


def _video_from_item(platform: str, it: dict) -> dict | None:
    """从 Apify item 提取标准化视频字段;非视频/无 URL 返回 None。"""
    if not isinstance(it, dict):
        return None
    if platform == "youtube":
        vid = _first(it, "id", "videoId", "video_id")
        url = _first(it, "url", "videoUrl") or (f"https://www.youtube.com/watch?v={vid}" if vid else "")
        return {"content_url": url, "title": _first(it, "title"),
                "publish_date": _first(it, "date", "publishedAt", "uploadDate"),
                "duration_seconds": int(it.get("duration") or it.get("durationSeconds") or 0) if str(it.get("duration") or "").isdigit() else 0,
                "view_count": int(it.get("viewCount") or it.get("views") or 0),
                "like_count": int(it.get("likes") or it.get("likeCount") or 0),
                "comment_count": int(it.get("commentsCount") or it.get("commentCount") or 0)} if url else None
    if platform == "instagram":
        t = str(it.get("type") or "").lower()
        vurl = _first(it, "videoUrl", "video_url")
        if not vurl and "video" not in t and it.get("productType") not in ("clips", "reels", "igtv"):
            return None  # 图文帖跳过
        url = _first(it, "url") or (f"https://www.instagram.com/p/{it.get('shortCode')}/" if it.get("shortCode") else "")
        return {"content_url": url, "title": (_first(it, "caption") or "")[:120],
                "publish_date": _first(it, "timestamp"),
                "duration_seconds": int(it.get("videoDuration") or 0) if str(it.get("videoDuration") or "").replace(".", "").isdigit() else 0,
                "view_count": int(it.get("videoViewCount") or it.get("videoPlayCount") or 0),
                "like_count": int(it.get("likesCount") or 0),
                "comment_count": int(it.get("commentsCount") or 0),
                "video_url": vurl} if url else None
    if platform == "tiktok":
        url = _first(it, "webVideoUrl", "url", "postPage")
        return {"content_url": url, "title": (_first(it, "text", "desc") or "")[:120],
                "publish_date": _first(it, "createTimeISO", "createTime"),
                "duration_seconds": int((it.get("videoMeta") or {}).get("duration") or 0),
                "view_count": int((it.get("playCount") or it.get("stats", {}).get("playCount") or 0)),
                "like_count": int((it.get("diggCount") or it.get("stats", {}).get("diggCount") or 0)),
                "comment_count": int((it.get("commentCount") or it.get("stats", {}).get("commentCount") or 0))} if url else None
    return None


def crawler_for(platform: str):
    return {"youtube": YouTubeCrawler, "instagram": InstagramCrawler, "tiktok": TikTokCrawler}[platform]()


def fetch_videos(platform: str, handle: str, profile_url: str, per_kol: int) -> tuple[list[dict], str]:
    cr = crawler_for(platform)
    ref = handle or profile_url
    # 多抓一点再过滤(IG 图文多)
    fetch_n = per_kol * 3 if platform == "instagram" else per_kol + 5
    if platform == "youtube":
        # 本地配了 YOUTUBE_API_KEY 时 crawl_channel_videos 走 Data API(要 channel_id),handle 会 error;
        # 直走 Apify actor(吃 @handle/URL)。
        res = cr._crawl_channel_videos_apify(ref, max_results=fetch_n, fallback_from="metadata_scrape")
    else:
        res = cr.crawl_channel_videos(ref, max_results=fetch_n)
    items = res.get("items") or []
    status = res.get("sync_status") or res.get("provider_status") or "?"
    vids = []
    for it in items:
        v = _video_from_item(platform, it)
        if v and v.get("content_url"):
            vids.append(v)
    # 按播放降序取前 per_kol
    vids.sort(key=lambda x: x.get("view_count", 0), reverse=True)
    return vids[:per_kol], status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-kol", type=int, default=20)
    ap.add_argument("--pilot", type=int, default=None)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    out = Path(args.out) if args.out else ROOT / "exports" / f"kol_metadata_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    out.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    fav = [int(dict(r)["kol_pool_id"]) for r in conn.execute("SELECT DISTINCT kol_pool_id FROM vkpi_kol_pool_favorites").fetchall()]
    ph = ",".join(str(x) for x in fav)
    kols = [dict(r) for r in conn.execute(
        f"SELECT id, platform, handle, display_name, profile_url, followers FROM vkpi_kol_pool "
        f"WHERE id IN ({ph}) AND COALESCE(platform,'') IN ('youtube','instagram','tiktok') ORDER BY id").fetchall()]
    if args.pilot:
        # pilot:每平台各取一个有 handle 的
        seen = {}
        picked = []
        for k in kols:
            if k["platform"] not in seen and (k.get("handle") or k.get("profile_url")):
                seen[k["platform"]] = 1; picked.append(k)
            if len(picked) >= args.pilot:
                break
        kols = picked
    print(f"🚀 metadata 抓取 {len(kols)} 个 KOL,每人前 {args.per_kol} 条视频(并发 {args.workers})→ {out}")
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    dq = (out / "download_queue.jsonl").open("w")
    inv = (out / "inventory_manifest.jsonl").open("w")
    lock = threading.Lock()
    stats = {"kols": 0, "videos": 0, "empty": 0, "elapsed": 0.0, "done": 0}

    def work(k):
        t0 = time.time()
        try:
            vids, status = fetch_videos(k["platform"], k.get("handle", ""), k.get("profile_url", ""), args.per_kol)
        except Exception as e:
            return k, None, str(e)[:80], time.time() - t0
        return k, (vids, status), None, time.time() - t0

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(work, k): k for k in kols}
        for fut in as_completed(futs):
            k, payload, err, el = fut.result()
            with lock:
                stats["done"] += 1; stats["elapsed"] += el
                n = stats["done"]
                if err is not None:
                    print(f"  [{n}/{len(kols)}] ❌ KOL-{k['id']} {k['platform']}/{k['handle']}: {err}")
                    continue
                vids, status = payload
                kol_dir = f"KOL-{int(k['id']):06d}_{k['platform']}_{_slug(k.get('handle'))}"
                inv.write(json.dumps({**k, "local_dir": kol_dir, "video_count": len(vids), "scrape_status": status}, ensure_ascii=False) + "\n")
                inv.flush()
                for v in vids:
                    dq.write(json.dumps({"kol_pool_id": k["id"], "kol_platform": k["platform"], "kol_handle": k.get("handle"),
                                         "local_dir": kol_dir, **v}, ensure_ascii=False) + "\n")
                dq.flush()
                stats["kols"] += 1; stats["videos"] += len(vids)
                if not vids:
                    stats["empty"] += 1
                if n % 10 == 0 or n == len(kols):
                    print(f"  [{n}/{len(kols)}] …累计 {stats['videos']} 视频 / 空号 {stats['empty']}")
    dq.close(); inv.close()
    print(f"\n🎉 完成 {stats['kols']} KOL / {stats['videos']} 视频 | 空号 {stats['empty']} | 总耗 {stats['elapsed']:.0f}s")
    if stats["kols"]:
        print(f"   均 {stats['videos']/stats['kols']:.1f} 视频/KOL · {stats['elapsed']/stats['kols']:.1f}s/KOL")
    print(f"   队列: {out}/download_queue.jsonl")


if __name__ == "__main__":
    main()
