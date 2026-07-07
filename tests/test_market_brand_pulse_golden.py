"""F1 件⑥ golden:brand_pulse 走 published_at_norm 真列(迁移 216)+ 旧路径 COALESCE 保底。

fake conn 喂固定行,断言:
- 证据/深析查询的日期口径 = COALESCE(published_at_norm, posted_at, publish_date 旧路径);
- payload 键结构与聚合口径不变(SoV = V/(V+ΣC)、周分桶、趋势判定);
- 迁移 216 文件存在、幂等(IF NOT EXISTS)、含回填,且零 ASCII 问号 / 零百分号
  (compat 占位符陷阱防线)。
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.domains.market import brand_pulse as bp


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        for key, rows in self.routes.items():
            if key in sql:
                return _Result(rows)
        return _Result([])


def _evidence(eid, title, day, kol_id=1):
    return {
        "evidence_id": eid, "kol_pool_id": kol_id, "platform": "youtube",
        "content_url": f"https://youtube.com/watch?v=b{eid}", "view_count": 100 * eid,
        "video_title": title, "title_alt": None,
        "pub_day": day.isoformat(), "kol_name": f"KOL {kol_id}",
    }


def _make_conn():
    today = datetime.now(timezone.utc).date()
    recent = today - timedelta(days=3)
    recent2 = today - timedelta(days=5)
    prev = today - timedelta(days=80)
    return _Conn({
        # 深析补充文本查询(vkpi_analysis_cache)必须先路由:空
        "vkpi_analysis_cache": [],
        "FROM vkpi_kol_video_evidence e": [
            _evidence(1, "Viltrox 85mm f1.4 review", recent, kol_id=1),
            _evidence(2, "why I bought the viltrox 35mm", recent2, kol_id=2),
            _evidence(3, "Sony FE 50mm street test", prev, kol_id=3),
            _evidence(4, "random vlog no brands", recent, kol_id=4),
        ],
    })


def test_brand_pulse_sql_uses_normalized_column_with_fallback():
    conn = _make_conn()
    rows = bp._evidence_rows(conn, "2026-01-01")
    assert rows  # fake conn 返回了固定行
    evidence_sql = conn.calls[0][0]
    # 新列主路径 + 旧路径保底,两个都必须在(缺一即口径漂移)
    assert "published_at_norm" in evidence_sql
    assert "publish_date" in evidence_sql
    assert "COALESCE(e.published_at_norm, e.posted_at" in evidence_sql
    # compat 红线:占位符全 ?,SQL 零字面百分号
    assert "%" not in evidence_sql

    bp._deep_texts(conn, "2026-01-01")
    deep_sql = conn.calls[-1][0]
    assert "published_at_norm" in deep_sql
    assert "COALESCE(e.published_at_norm, e.posted_at" in deep_sql
    assert "%" not in deep_sql


def test_brand_pulse_payload_golden(monkeypatch):
    conn = _make_conn()
    monkeypatch.setattr(bp, "get_conn", lambda: conn)

    payload = bp.get_brand_pulse(90)

    assert payload["status"] == "ok"
    assert payload["schema_version"] == "brand_pulse_v1"

    viltrox = payload["viltrox"]
    assert viltrox["total_videos"] == 2
    assert viltrox["kol_count"] == 2
    # SoV = V/(V+ΣC) = 2/(2+1)
    assert viltrox["share_of_voice"] == round(2 / 3, 3)
    assert viltrox["rank"] == 1
    # 两条命中都在后半窗、前半窗 0 → rising(ratio≥1.5 且 recent≥2)
    assert viltrox["trend"] == "rising"

    brands = {item["key"]: item for item in payload["brands"]}
    assert "sony" in brands
    assert brands["sony"]["total_videos"] == 1
    # 单样本不硬判升降
    assert brands["sony"]["trend"] == "stable"

    coverage = payload["coverage"]
    assert coverage["videos_scanned"] == 4
    assert coverage["brand_hit_videos"] == 3  # 无品牌的 vlog 不计

    # 键结构不变(industry_benchmark / 战略台在吃)
    for key in ("schema_version", "status", "window_days", "window_start", "window_end",
                "weeks", "viltrox", "brands", "groups", "coverage", "generated_at",
                "provider_calls", "llm_calls", "note"):
        assert key in payload
    for key in ("key", "brand", "total_videos", "kol_count", "weekly", "prev_sum",
                "recent_sum", "trend", "momentum_pct", "top_examples"):
        assert key in brands["sony"]
    week = payload["weeks"][0]
    for key in ("week_start", "videos_scanned", "brand_videos", "sparse"):
        assert key in week


def test_migration_216_file_guard():
    root = Path(__file__).resolve().parents[1]
    up = root / "migrations" / "216_vkpi_evidence_publish_date.sql"
    down = root / "migrations" / "216_vkpi_evidence_publish_date_down.sql"
    assert up.exists() and down.exists()

    text = up.read_text(encoding="utf-8")
    # 幂等 + 转正列 + 回填 + 索引
    assert "ADD COLUMN IF NOT EXISTS published_at_norm TIMESTAMPTZ" in text
    assert "CREATE INDEX IF NOT EXISTS idx_vkpi_evidence_published_at_norm" in text
    assert "UPDATE vkpi_kol_video_evidence" in text
    assert "published_at_norm IS NULL" in text  # 只补空行,可重入
    # compat 占位符陷阱防线:迁移全文零 ASCII 问号、零百分号
    assert "?" not in text
    assert "%" not in text

    down_text = down.read_text(encoding="utf-8")
    assert "DROP COLUMN IF EXISTS published_at_norm" in down_text
    assert "?" not in down_text and "%" not in down_text
