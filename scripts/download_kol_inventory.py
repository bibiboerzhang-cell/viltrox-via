#!/usr/bin/env python3
"""KOL 库存视频下载器 — Apify/yt-dlp+Decodo 下载 → ffmpeg 瘦身 → R2 备份 → 本地命名结构。

读 vkpi_kol_pool_favorites 收藏库,每 KOL 取前 N 条(按播放降序)有效视频,
跳过 unknown/media 平台。每条:
  yt-dlp(Decodo 代理)下载 source.mp4 →
  ffmpeg 生成 analysis_proxy_480p.mp4(480p,保留音轨,供 Vertex)+ thumbnail.jpg + frames/(1帧/5秒)→
  source.mp4 上传 R2(viltrox-assets, 前缀 kol_inventory/)备份 →
  写 metadata.json → 默认删本地 source 省盘(--keep-source-local 保留)。

只读 DB(不写库)。零 LLM。可断点续传(已有 metadata.json 跳过)。

用法:
  python3 scripts/download_kol_inventory.py --pilot 2          # 试点前2个有视频的KOL
  python3 scripts/download_kol_inventory.py --limit-per-kol 20 # 全量,每人20条
"""
from __future__ import annotations

from stdout_utils import out as stdout_out
import argparse, json, os, re, subprocess, sys, time
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.db.connection import get_conn  # noqa: E402

SKIP_PLATFORMS = {"unknown", "media", "", None}
R2_PREFIX = "kol_inventory"


def _slug(v, fallback="x", n=60):
    s = re.sub(r"[^a-z0-9]+", "-", str(v or "").lower()).strip("-")
    return (s[:n].strip("-") or fallback)


def _iso_date(v):
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(v or ""))
    return f"{m.group(1)}{m.group(2)}{m.group(3)}" if m else "undated"


def _r2_client():
    import boto3
    return boto3.client(
        "s3", endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    )


def _is_real_video(url: str, dur) -> bool:
    u = str(url or "")
    if dur and int(dur) > 0:
        return True
    if any(k in u for k in ("watch?v=", "/video/", "/reel", "youtu.be")):
        return True
    if "photo" in u or "story.php" in u:
        return False
    return False  # 保守:不确定不下


def select_targets(limit_per_kol: int, pilot: int | None):
    conn = get_conn()
    fav = [int(dict(r)["kol_pool_id"]) for r in conn.execute(
        "SELECT DISTINCT kol_pool_id FROM vkpi_kol_pool_favorites").fetchall()]
    ph = ",".join(str(x) for x in fav)
    kols = [dict(r) for r in conn.execute(
        f"SELECT id, platform, handle, display_name FROM vkpi_kol_pool "
        f"WHERE id IN ({ph}) AND COALESCE(platform,'') NOT IN ('unknown','media','') "
        f"ORDER BY id").fetchall()]
    out = []
    for k in kols:
        evs = [dict(r) for r in conn.execute(
            "SELECT id AS evidence_id, content_url, video_title, publish_date, duration_seconds, "
            "view_count, like_count, comment_count, thumbnail_url FROM vkpi_kol_video_evidence "
            "WHERE kol_pool_id=? AND (is_active IS NULL OR is_active=TRUE) "
            "ORDER BY COALESCE(view_count,0) DESC, id DESC", (k["id"],)).fetchall()]
        vids = [e for e in evs if _is_real_video(e["content_url"], e.get("duration_seconds"))][:limit_per_kol]
        if vids:
            out.append((k, vids))
    if pilot:
        out = out[:pilot]
    return out


def process_video(k, ev, kol_dir: Path, r2, bucket: str, keep_source: bool) -> dict:
    date = _iso_date(ev.get("publish_date"))
    vslug = f"{date}_{k['platform']}_{ev['evidence_id']}_evidence-{ev['evidence_id']}"
    vdir = kol_dir / "videos" / vslug
    meta_path = vdir / "metadata.json"
    if meta_path.exists():
        return {"status": "skip_exists", "dir": str(vdir)}
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "frames").mkdir(exist_ok=True)
    source = vdir / "source.mp4"
    proxy = vdir / "analysis_proxy_480p.mp4"
    thumb = vdir / "thumbnail.jpg"
    t0 = time.time()
    result = {"evidence_id": ev["evidence_id"], "url": ev["content_url"]}
    # 1) yt-dlp 下载(Decodo 代理,<=720p 含音轨)
    proxy_arg = os.environ.get("YTDLP_PROXY", "").strip()
    cmd = ["yt-dlp", "-f", "best[height<=720]/best", "-o", str(source), "--no-playlist",
           "--quiet", "--no-warnings", "--no-progress"]
    if proxy_arg:
        cmd += ["--proxy", proxy_arg]
    cmd.append(ev["content_url"])
    dl = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if dl.returncode != 0 or not source.exists():
        result["status"] = "download_failed"
        result["error"] = (dl.stderr or dl.stdout or "")[-300:]
        return result
    src_mb = source.stat().st_size / 1e6
    # 2) ffmpeg 瘦身 480p(保留音轨)+ 缩略图 + 关键帧(1帧/5秒)
    subprocess.run(["ffmpeg", "-y", "-i", str(source), "-vf", "scale=-2:480",
                    "-c:v", "libx264", "-crf", "30", "-preset", "veryfast", "-c:a", "aac", "-b:a", "64k",
                    str(proxy)], capture_output=True, timeout=300)
    subprocess.run(["ffmpeg", "-y", "-i", str(source), "-vframes", "1", str(thumb)],
                   capture_output=True, timeout=60)
    subprocess.run(["ffmpeg", "-y", "-i", str(source), "-vf", "fps=1/5,scale=-2:480",
                    str(vdir / "frames" / "frame_%04d.jpg")], capture_output=True, timeout=300)
    proxy_mb = proxy.stat().st_size / 1e6 if proxy.exists() else 0
    # 3) 源片上传 R2 备份
    r2_key = f"{R2_PREFIX}/{kol_dir.name}/{vslug}/source.mp4"
    try:
        r2.upload_file(str(source), bucket, r2_key)
        result["r2_key"] = r2_key
    except Exception as e:
        result["r2_error"] = str(e)[:150]
    # 4) metadata.json
    meta = {**ev, "kol_pool_id": k["id"], "platform": k["platform"], "handle": k["handle"],
            "local_dir": str(vdir.relative_to(ROOT)), "r2_source_key": result.get("r2_key"),
            "source_mb": round(src_mb, 1), "proxy_mb": round(proxy_mb, 1),
            "downloaded_at": datetime.now(timezone.utc).isoformat()}
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    # 5) 删本地源片省盘(默认)
    if not keep_source and source.exists():
        source.unlink()
    result.update(status="done", source_mb=round(src_mb, 1), proxy_mb=round(proxy_mb, 1),
                  elapsed_s=round(time.time() - t0, 1))
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-per-kol", type=int, default=20)
    ap.add_argument("--pilot", type=int, default=None, help="只跑前 N 个有视频的 KOL")
    ap.add_argument("--keep-source-local", action="store_true")
    ap.add_argument("--out", default=None, help="导出根目录(默认 exports/kol_video_archive_<stamp>)")
    args = ap.parse_args()

    out_root = Path(args.out) if args.out else ROOT / "exports" / f"kol_video_archive_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    out_root.mkdir(parents=True, exist_ok=True)
    bucket = os.environ["R2_BUCKET_NAME"]
    r2 = _r2_client()

    targets = select_targets(args.limit_per_kol, args.pilot)
    total_videos = sum(len(v) for _, v in targets)
    stdout_out(f"🚀 目标 {len(targets)} 个 KOL / {total_videos} 条视频 → {out_root} | R2 bucket={bucket}")
    stats = {"done": 0, "skip_exists": 0, "download_failed": 0, "src_mb": 0.0, "proxy_mb": 0.0, "elapsed": 0.0}
    for ki, (k, vids) in enumerate(targets, 1):
        kol_dir = out_root / f"KOL-{int(k['id']):06d}_{k['platform']}_{_slug(k['handle'])}"
        kol_dir.mkdir(parents=True, exist_ok=True)
        (kol_dir / "profile.json").write_text(json.dumps(k, ensure_ascii=False, indent=1))
        stdout_out(f"[{ki}/{len(targets)}] KOL-{k['id']} {k['platform']}/{k['handle']} — {len(vids)} 条")
        for ev in vids:
            r = process_video(k, ev, kol_dir, r2, bucket, args.keep_source_local)
            st = r.get("status", "?")
            stats[st] = stats.get(st, 0) + 1
            if st == "done":
                stats["src_mb"] += r.get("source_mb", 0); stats["proxy_mb"] += r.get("proxy_mb", 0)
                stats["elapsed"] += r.get("elapsed_s", 0)
                stdout_out(f"    ✅ ev#{ev['evidence_id']} src={r['source_mb']}MB proxy={r['proxy_mb']}MB {r['elapsed_s']}s")
            elif st == "download_failed":
                stdout_out(f"    ❌ ev#{ev['evidence_id']} 下载失败: {r.get('error','')[:80]}")
            else:
                stdout_out(f"    ⏭  ev#{ev['evidence_id']} {st}")
    stdout_out(f"\n🎉 完成 done={stats['done']} 跳过={stats['skip_exists']} 失败={stats['download_failed']}")
    stdout_out(f"   源片合计 {stats['src_mb']:.0f}MB → R2 | 本地代理 {stats['proxy_mb']:.0f}MB | 总耗时 {stats['elapsed']:.0f}s")
    if stats["done"]:
        stdout_out(f"   单条均值: {stats['src_mb']/stats['done']:.1f}MB源 / {stats['proxy_mb']/stats['done']:.1f}MB代理 / {stats['elapsed']/stats['done']:.1f}s")


if __name__ == "__main__":
    main()
