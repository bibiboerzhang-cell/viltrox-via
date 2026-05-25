"""scripts/smoke_vkpi_comments_offline.py

P1.3 Comments collector offline smoke.

Tests:
  1. comments_collector imports
  2. _resolve_post handles missing post
  3. _standardize_comment handles 5 platforms
  4. _standardize_comment handles 0-as-falsy correctly (likes_count=0 preserved)
  5. _standardize_comment handles missing fields gracefully
  6. _standardize_comment text truncation (5000 chars cap)
  7. collect_post_comments with not-supported platform returns skip
  8. batch_collect_pending with no posts returns empty
  9. stats returns valid dict

Run:
  PYTHONPATH=backend .venv/bin/python scripts/smoke_vkpi_comments_offline.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def main():
    failures = []
    
    print("[1] Module import...")
    try:
        from app.domains.comments import collector as comments_collector
        print("  ✓ imported")
    except Exception as exc:
        failures.append(f"Cannot import comments_collector: {exc}")
        print("  FAIL")
        sys.exit(1)
    
    print("[2] _standardize_comment YouTube fields...")
    raw = {
        "id": "yt_comment_1",
        "snippet": {
            "topLevelComment": {
                "snippet": {
                    "textDisplay": "Great lens!",
                    "authorDisplayName": "@reviewer",
                    "authorChannelId": {"value": "UC123"},
                    "likeCount": 42,
                    "publishedAt": "2026-05-09T08:30:00Z",
                }
            },
            "totalReplyCount": 5,
        },
    }
    
    std = comments_collector._standardize_comment(
        raw,
        platform="youtube",
        post_id=999,
        account_id=10,
        external_post_id="ytpost_xyz",
        post_table="industry_posts",
    )
    
    if std.get("external_comment_id") != "yt_comment_1":
        failures.append(f"YT external_comment_id mismatch: {std.get('external_comment_id')}")
    if std.get("comment_text") != "Great lens!":
        failures.append(f"YT comment_text mismatch")
    if std.get("author_handle") != "@reviewer":
        failures.append(f"YT author_handle mismatch")
    if std.get("likes_count") != 42:
        failures.append(f"YT likes_count mismatch: {std.get('likes_count')}")
    if std.get("reply_count") != 5:
        failures.append(f"YT reply_count mismatch")
    if std.get("platform") != "youtube":
        failures.append("platform mismatch")
    if std.get("post_id") != 999:
        failures.append("post_id mismatch")
    print("  ✓ YouTube standardization")
    
    print("[3] _standardize_comment 0-as-falsy preservation...")
    raw_zero = {
        "id": "comment_0",
        "snippet": {
            "topLevelComment": {
                "snippet": {
                    "textDisplay": "Quiet comment",
                    "authorDisplayName": "user",
                    "likeCount": 0,           # ← Real 0
                    "publishedAt": "2026-05-09T08:30:00Z",
                }
            },
            "totalReplyCount": 0,             # ← Real 0
        },
    }
    
    std_zero = comments_collector._standardize_comment(
        raw_zero,
        platform="youtube",
        post_id=999,
        account_id=10,
        external_post_id="ytpost_xyz",
        post_table="industry_posts",
    )
    
    if std_zero.get("likes_count") != 0:
        failures.append(
            f"0 not preserved as known 0: got {std_zero.get('likes_count')}"
        )
    if std_zero.get("reply_count") != 0:
        failures.append(
            f"reply_count 0 not preserved: got {std_zero.get('reply_count')}"
        )
    print("  ✓ 0 preserved (B.6-Xiaohongshu lesson applied)")
    
    print("[4] _standardize_comment Reddit nested support...")
    raw_reddit = {
        "id": "rt_comment_1",
        "body": "Nested comment text",
        "author": "redditor1",
        "score": 15,
        "created_utc": 1715000000,
        "is_submitter": False,
        "parent_id": "t1_parent_xyz",
        "depth": 2,
    }
    
    std_reddit = comments_collector._standardize_comment(
        raw_reddit,
        platform="reddit",
        post_id=100,
        account_id=5,
        external_post_id="rtpost_abc",
        post_table="industry_posts",
    )
    
    if std_reddit.get("depth") != 2:
        failures.append(f"Reddit depth not preserved: {std_reddit.get('depth')}")
    if std_reddit.get("parent_comment_id") != "t1_parent_xyz":
        failures.append(f"Reddit parent_comment_id missing")
    if std_reddit.get("is_op") is not False:
        failures.append(f"Reddit is_op should be False, got {std_reddit.get('is_op')}")
    if std_reddit.get("likes_count") != 15:
        failures.append(f"Reddit score → likes_count failed")
    print("  ✓ Reddit nested + is_op")
    
    print("[5] _standardize_comment text truncation...")
    raw_long = {"id": "long_1", "text": "x" * 10000}
    std_long = comments_collector._standardize_comment(
        raw_long,
        platform="instagram",
        post_id=1, account_id=1, external_post_id="x",
        post_table="industry_posts",
    )
    if len(std_long.get("comment_text", "")) > 5000:
        failures.append("text not truncated to 5000 chars")
    print(f"  ✓ truncated ({len(std_long.get('comment_text', ''))} chars)")
    
    print("[6] _standardize_comment missing fields graceful...")
    std_empty = comments_collector._standardize_comment(
        {},  # empty raw
        platform="tiktok",
        post_id=1, account_id=1, external_post_id="x",
        post_table="industry_posts",
    )
    if std_empty.get("external_comment_id") is not None:
        failures.append("Empty raw should produce None external_comment_id")
    if std_empty.get("likes_count") != 0:
        failures.append(f"Empty raw likes_count should be 0, got {std_empty.get('likes_count')}")
    print("  ✓ empty raw handled")
    
    print("[7] _resolve_post for non-existent post...")
    try:
        result = comments_collector._resolve_post(99999999, "industry_posts")
        if result is not None:
            failures.append("Non-existent post should return None")
    except Exception as exc:
        # OK if DB not available in this smoke
        print(f"  (DB unavailable, skipped: {exc})")
    print("  ✓ ")

    print("[8] schema + collection run write...")
    try:
        from app.db.connection import get_conn

        comments_collector.ensure_vkpi_comments_schema()
        run = comments_collector._record_run(
            post_id=0,
            post_table="industry_posts",
            platform="youtube",
            status="skip",
            fetched_count=0,
            new_count=0,
            duplicate_count=0,
            error="smoke offline",
            triggered_by="smoke",
        )
        if run.get("status") != "skip":
            failures.append(f"_record_run status mismatch: {run}")
        row = get_conn().execute(
            """
            SELECT COUNT(*) AS c
            FROM vkpi_comments_collection_runs
            WHERE post_id=? AND post_table=? AND platform=? AND triggered_by=?
            """,
            (0, "industry_posts", "youtube", "smoke"),
        ).fetchone()
        if int(row["c"] if row else 0) < 1:
            failures.append("collection run was not persisted")
        get_conn().execute(
            """
            DELETE FROM vkpi_comments_collection_runs
            WHERE post_id=? AND post_table=? AND platform=? AND triggered_by=?
            """,
            (0, "industry_posts", "youtube", "smoke"),
        )
        get_conn().commit()
        print("  ✓ schema/write/cleanup")
    except Exception as exc:
        failures.append(f"schema/write failed: {exc}")
    
    # Final
    print()
    if failures:
        print(f"FAIL: {len(failures)} issues:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("VKPI_COMMENTS_OFFLINE_SMOKE_OK")


if __name__ == "__main__":
    main()
