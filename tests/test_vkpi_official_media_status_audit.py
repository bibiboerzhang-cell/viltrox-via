from __future__ import annotations

from scripts import vkpi_official_media_status_audit


def test_official_media_status_audit_counts_platform_statuses(monkeypatch):
    monkeypatch.setattr(
        vkpi_official_media_status_audit.channels,
        "official_account_matrix",
        lambda limit=50: {
            "account_count": 2,
            "platforms": [
                {
                    "platform": "youtube",
                    "accounts": [
                        {"handle": "viltroxofficial", "posts": [{"id": "abc12345678", "media_status": "embed"}]},
                    ],
                },
                {
                    "platform": "instagram",
                    "accounts": [
                        {
                            "handle": "viltrox.official",
                            "posts": [
                                {"id": "one", "media_status": "cached"},
                                {"id": "two", "media_status": "inventory_only"},
                            ],
                        }
                    ],
                },
            ],
        },
    )

    report = vkpi_official_media_status_audit.build_report(limit=50)

    assert report["passed"] is True
    assert report["provider_calls"] is False
    assert report["post_count"] == 3
    assert report["status_counts"] == {"cached": 1, "embed": 1, "inventory_only": 1}


def test_official_media_status_audit_fails_when_status_missing(monkeypatch):
    monkeypatch.setattr(
        vkpi_official_media_status_audit.channels,
        "official_account_matrix",
        lambda limit=50: {
            "account_count": 1,
            "platforms": [
                {
                    "platform": "facebook",
                    "accounts": [{"handle": "viltrox", "posts": [{"id": "post-without-status"}]}],
                },
            ],
        },
    )

    report = vkpi_official_media_status_audit.build_report(limit=50)

    assert report["passed"] is False
    assert report["status_counts"] == {"missing": 1}
    assert report["missing_status"][0]["post_id"] == "post-without-status"
