from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.db import connection
from app.domains.market import industry_benchmark as subject


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
        return value if tz is not None else value.replace(tzinfo=None)


class _BrandPulse:
    def __init__(self, rows):
        self._rows = rows

    @staticmethod
    def _clamp_window(value):
        return max(14, min(365, int(value or 90)))

    @staticmethod
    def _week_starts(start, end):
        cursor = start - timedelta(days=start.weekday())
        weeks = []
        while cursor <= end:
            weeks.append(cursor)
            cursor += timedelta(days=7)
        return weeks

    @staticmethod
    def _parse_day(value):
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None

    @staticmethod
    def _monday(value):
        return value - timedelta(days=value.weekday())

    def _evidence_rows(self, _conn, _start_day):
        return self._rows

    @staticmethod
    def _deep_texts(_conn, _start_day):
        return {3: "Sony field test with a 35mm lens"}

    @staticmethod
    def _competitor_vocab():
        return {
            "sony": {
                "keywords": ["sony"],
                "brand_type": "camera",
                "category": "hybrid",
            }
        }

    @staticmethod
    def _viltrox_terms():
        return ["viltrox"]

    @staticmethod
    def _matcher():
        return lambda text, term: str(term).lower() in str(text).lower()

    @staticmethod
    def _classify_trend(previous, recent, _previous_scanned, _recent_scanned):
        return ("up" if recent > previous else "flat", 0.0)

    @staticmethod
    def _display_brand(key):
        return {"viltrox": "Viltrox", "sony": "Sony"}[key]


def _focal_tools(*, catalog_error: bool = False):
    def build_catalog(_rows):
        if catalog_error:
            raise RuntimeError("catalog unavailable")
        return {
            "focals": {
                "35mm": {
                    "official_sku_count": 1,
                    "sku_count": 2,
                    "flagship": "35 Pro",
                },
                "85mm": {
                    "official_sku_count": 0,
                    "sku_count": 1,
                    "flagship": "",
                },
            }
        }

    return {
        "extract": lambda text: {
            focal for focal in ("35mm", "85mm") if focal in text
        },
        "sort_mm": lambda focal: float(focal.removesuffix("mm")),
        "load_products": lambda _conn: [],
        "build_catalog": build_catalog,
    }


def test_benchmark_stage_refactor_preserves_brand_and_focal_contract(monkeypatch):
    rows = [
        {
            "evidence_id": 1,
            "pub_day": "2026-08-29",
            "video_title": "Viltrox 35mm vs Sony 85mm",
            "title_alt": "",
            "kol_pool_id": 10,
            "view_count": "1000",
            "content_url": "https://example.test/1",
            "platform": "youtube",
            "kol_name": "Alpha",
        },
        {
            "evidence_id": 2,
            "pub_day": "2026-08-28",
            "video_title": "Sony 85mm field test",
            "title_alt": "",
            "kol_pool_id": 11,
            "view_count": "not-a-number",
            "content_url": "https://example.test/2",
            "platform": "instagram",
            "kol_name": "Beta",
        },
        {
            "evidence_id": 3,
            "pub_day": "2026-08-27",
            "video_title": "",
            "title_alt": "",
            "kol_pool_id": 12,
            "view_count": 0,
            "content_url": "https://example.test/3",
            "platform": "tiktok",
            "kol_name": "Gamma",
        },
        {
            "evidence_id": 4,
            "pub_day": "invalid",
            "video_title": "Viltrox",
            "title_alt": "",
            "kol_pool_id": 13,
            "view_count": 50,
            "content_url": "https://example.test/4",
            "platform": "youtube",
            "kol_name": "Out of window",
        },
    ]
    fake_conn = object()
    monkeypatch.setattr(subject, "datetime", _FixedDateTime)
    monkeypatch.setattr(subject, "_brand_pulse_mod", lambda: _BrandPulse(rows))
    monkeypatch.setattr(subject, "_focal_tools", _focal_tools)
    monkeypatch.setattr(
        subject,
        "_engagement_map",
        lambda _conn, _start: {1: (10, 2), 2: (1, 1), 3: (0, 0)},
    )
    monkeypatch.setattr(connection, "get_conn", lambda: fake_conn)

    result = subject.benchmark(window_days=90)

    assert result["status"] == "ok"
    assert result["basis"] == {
        "videos_scanned": 3,
        "videos_with_text": 3,
        "deep_analyzed_in_window": 1,
        "brand_hit_videos": 4,
        "engagement_rows_in_window": 3,
        "prev_half_scanned": 0,
        "recent_half_scanned": 3,
        "vocab_brands": 1,
        "unit": "视频×品牌(一条视频同品牌只记 1 次),与 brand_pulse 同口径",
    }
    assert result["viltrox"]["videos"] == 1
    assert result["viltrox"]["avg_views"] == 1000
    assert result["competitors"][0]["videos"] == 3
    assert result["competitors"][0]["views_known"] == 2
    assert result["competitors"][0]["avg_views"] == 500
    assert result["focal_grid"]["status"] == "ready"
    cells = {item["focal"]: item for item in result["focal_grid"]["cells"]}
    assert cells["35mm"]["official_sku_count"] == 1
    assert cells["85mm"]["sku_weak"] is True
    assert result["provider_calls"] is False
    assert result["llm_calls"] is False


def test_optional_views_and_confidence_boundaries_are_honest():
    assert subject._optional_int("not-a-number") is None
    assert subject._optional_int("") is None
    assert subject._optional_int("12") == 12
    assert subject._status_and_confidence(
        videos_scanned=0,
        brand_hit_videos=0,
    )[0] == "no_data_in_window"
    assert subject._status_and_confidence(
        videos_scanned=10,
        brand_hit_videos=0,
    )[0] == "no_brand_signal"
    assert subject._status_and_confidence(
        videos_scanned=500,
        brand_hit_videos=150,
    )[1]["level"] == "high"
