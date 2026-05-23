from __future__ import annotations

from scripts import vkpi_official_comment_contract_audit


def test_comment_contract_audit_counts_declared_cached_and_status(monkeypatch):
    monkeypatch.setattr(vkpi_official_comment_contract_audit, "_table_exists", lambda table: table == "vkpi_comments")
    monkeypatch.setattr(
        vkpi_official_comment_contract_audit,
        "_cached_comment_count",
        lambda platform, external_ids, *, table_exists: 2 if "post-1" in external_ids else 0,
    )
    monkeypatch.setattr(
        vkpi_official_comment_contract_audit.channel_comments,
        "_package_reddit_comments",
        lambda package_dir, external_ids: [],
    )
    monkeypatch.setattr(
        vkpi_official_comment_contract_audit.channels,
        "_latest_official_channel_rows",
        lambda staff=None: [
            {"id": 10, "platform": "youtube", "account_handle": "viltroxofficial"},
            {"id": 20, "platform": "instagram", "account_handle": "viltrox.official"},
        ],
    )
    monkeypatch.setattr(
        vkpi_official_comment_contract_audit.channels,
        "_all_posts_for_channel",
        lambda row: (
            [{"id": "post-1", "source_id": "post-1", "url": "https://example.com/post-1", "comments": 5}]
            if row["platform"] == "youtube"
            else [{"id": "post-2", "source_id": "post-2", "url": "https://example.com/post-2", "comments": 0}],
            "snapshot_sample",
            "",
        ),
    )

    report = vkpi_official_comment_contract_audit.build_report(limit_per_account=50, comment_cap=300)

    assert report["passed"] is True
    assert report["provider_calls"] is False
    assert report["collector_calls"] is False
    assert report["totals"]["posts"] == 2
    assert report["totals"]["declared_comments"] == 5
    assert report["totals"]["cached_comment_bodies_visible"] == 2
    assert report["status_counts"] == {"none_declared": 1, "partial": 1}


def test_comment_contract_audit_marks_missing_post_id(monkeypatch):
    monkeypatch.setattr(vkpi_official_comment_contract_audit, "_table_exists", lambda table: False)
    monkeypatch.setattr(
        vkpi_official_comment_contract_audit.channel_comments,
        "_package_reddit_comments",
        lambda package_dir, external_ids: [],
    )
    monkeypatch.setattr(
        vkpi_official_comment_contract_audit.channels,
        "_latest_official_channel_rows",
        lambda staff=None: [{"id": 10, "platform": "youtube", "account_handle": "viltroxofficial"}],
    )
    monkeypatch.setattr(
        vkpi_official_comment_contract_audit.channels,
        "_all_posts_for_channel",
        lambda row: ([{"comments": 3}], "snapshot_sample", ""),
    )

    report = vkpi_official_comment_contract_audit.build_report(limit_per_account=50, comment_cap=300)

    assert report["passed"] is True
    assert report["status_counts"] == {"missing_post_id": 1}
    assert report["samples"][0]["status"] == "missing_post_id"
