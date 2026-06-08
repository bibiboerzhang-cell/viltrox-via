"""
Stage 1 runner.

Usage:
    python -m stage1_apify_ingest.runner
    python -m stage1_apify_ingest.runner --commit
    python -m stage1_apify_ingest.runner --commit --retry-failed
    python -m stage1_apify_ingest.runner --commit --only <kol_id>
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config
from .apify_source import ApifySource, load_kol_list
from .config import SETTINGS
from .manifest import ManifestStore
from .storage import DualStore


def process_kol(
    kol: dict,
    src: ApifySource,
    store: DualStore,
    man: ManifestStore,
    global_manifest: dict,
    retry_only: bool,
) -> None:
    kol_id = kol["kol_id"]
    kol_manifest = man.load_kol(kol_id)

    if kol.get("url_source") == "none":
        kol_manifest["status"] = "skipped_no_url"
        kol_manifest["url_source"] = "none"
        kol_manifest["source_url_status"] = "no_resolvable_url"
        man.save_kol(kol_manifest)
        _mark_skipped_no_url(global_manifest, kol_id)
        man.mark_kol(global_manifest, kol_id, "skipped_no_url", 0)
        man.save_global(global_manifest)
        print(f"[{kol_id}] skipped_no_url")
        return

    channel_key = config.channel_key(kol_id)
    if store.both_exist(channel_key) or store.sync_existing(channel_key):
        channel = store.read_json_local(channel_key) or {}
        if kol.get("url_source") == "handle" and channel.get("url_source") != "handle":
            channel["url_source"] = "handle"
            store.put_json(channel_key, channel)
    else:
        channel = src.fetch_channel(kol["channel"])
        if kol.get("url_source") == "handle":
            channel["url_source"] = "handle"
        store.put_json(channel_key, channel)

    videos = channel.get("videos", [])
    if SETTINGS.max_videos_per_kol:
        videos = videos[: SETTINGS.max_videos_per_kol]
    total_video_count = len(videos)

    if retry_only:
        wanted = set(man.failed_videos(kol_manifest, SETTINGS.max_retries))
        videos = [video for video in videos if video["video_id"] in wanted]

    for video in videos:
        video_id = video["video_id"]
        meta_key = config.video_meta_key(kol_id, video_id)
        transcript_key = config.transcript_key(kol_id, video_id)

        meta_done = store.both_exist(meta_key) or store.sync_existing(meta_key)
        transcript_done = (
            not SETTINGS.fetch_transcript
            or store.both_exist(transcript_key)
            or store.sync_existing(transcript_key)
        )
        if meta_done and transcript_done:
            man.set_video(kol_manifest, video_id, "done")
            man.save_kol(kol_manifest)
            continue

        try:
            if not meta_done:
                store.put_json(meta_key, video)
            if SETTINGS.fetch_transcript and not transcript_done:
                transcript = src.fetch_transcript(video_id)
                store.put_json(transcript_key, transcript)
            # Storyboard intentionally remains off by default until Stage 2 needs frames.
            man.set_video(kol_manifest, video_id, "done")
        except Exception as exc:
            man.set_video(kol_manifest, video_id, "failed", error=repr(exc))
            print(f"  ! {kol_id}/{video_id} failed: {exc!r}")
        finally:
            man.save_kol(kol_manifest)

    done_count = sum(1 for entry in kol_manifest["videos"].values() if entry.get("status") == "done")
    fail_count = sum(1 for entry in kol_manifest["videos"].values() if entry.get("status") == "failed")
    man.save_kol(kol_manifest)
    man.mark_kol(global_manifest, kol_id, "done" if fail_count == 0 else "partial", total_video_count)
    man.save_global(global_manifest)
    print(f"[{kol_id}] {done_count} done / {fail_count} failed / {total_video_count} videos")


def _mark_skipped_no_url(global_manifest: dict, kol_id: str) -> None:
    skipped = global_manifest.setdefault("skipped", [])
    if not isinstance(skipped, list):
        global_manifest["skipped"] = skipped = []
    if not any(item.get("kol_id") == kol_id for item in skipped if isinstance(item, dict)):
        skipped.append({
            "kol_id": kol_id,
            "status": "skipped_no_url",
            "source_url_status": "no_resolvable_url",
        })


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true", help="write to local+R2 (default: dry-run)")
    parser.add_argument("--retry-failed", action="store_true", help="only re-attempt failed videos")
    parser.add_argument("--only", help="restrict to a single kol_id")
    args = parser.parse_args(argv)

    kols = load_kol_list(SETTINGS)
    if args.only:
        kols = [kol for kol in kols if kol["kol_id"] == args.only]

    mode = "COMMIT" if args.commit else "DRY-RUN"
    print(f"== Stage 1 ingest [{mode}] :: {len(kols)} KOLs ==")
    counts = Counter(kol.get("url_source") or "unknown" for kol in kols)
    print(
        "url_source buckets: "
        f"url={counts.get('url', 0)} / handle={counts.get('handle', 0)} / none={counts.get('none', 0)}"
    )
    if not args.commit:
        for kol in kols[:20]:
            print(f"[dry-run] {kol['kol_id']} {kol['platform']} {kol.get('url_source')} {kol.get('channel')}")
        if len(kols) > 20:
            print(f"[dry-run] ... {len(kols) - 20} more")
        print("== dry-run complete; no Apify/R2 writes ==")
        return 0

    store = DualStore(SETTINGS, commit=True)
    man = ManifestStore(store)
    src = ApifySource(SETTINGS)
    global_manifest = man.load_global()

    with ThreadPoolExecutor(max_workers=SETTINGS.concurrency) as pool:
        futures = {
            pool.submit(process_kol, kol, src, store, man, global_manifest, args.retry_failed): kol
            for kol in kols
        }
        try:
            for future in as_completed(futures):
                kol = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    print(f"[{kol['kol_id']}] KOL-level error: {exc!r}")
                    man.mark_kol(global_manifest, kol["kol_id"], "failed", 0)
                    man.save_global(global_manifest)
        except KeyboardInterrupt:
            print("Interrupted; completed assets/manifests remain checkpointed.")
            raise

    print("== done ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
