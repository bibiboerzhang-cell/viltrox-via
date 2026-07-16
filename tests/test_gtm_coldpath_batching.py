from __future__ import annotations

from app.db.connection import PostgresCompatCursor
from app.domains.products import sku_performance


class _RawCursor:
    def __init__(self) -> None:
        self.description_reads = 0

    @property
    def description(self):
        self.description_reads += 1
        return [("id",), ("label",)]

    def fetchall(self):
        return [(1, "first"), (2, "second"), (3, "third")]


def test_postgres_compat_fetchall_resolves_projection_once() -> None:
    raw = _RawCursor()
    rows = PostgresCompatCursor(raw).fetchall()

    assert raw.description_reads == 1
    assert [(row["id"], row["label"]) for row in rows] == [
        (1, "first"),
        (2, "second"),
        (3, "third"),
    ]


def _evidence(evidence_id: int, title: str, product_presence: str = "") -> dict:
    return {
        "evidence_id": evidence_id,
        "kol_pool_id": evidence_id + 100,
        "title": title,
        "content_url": f"https://example.test/{evidence_id}",
        "platform": "youtube",
        "posted_at": "2026-07-01",
        "publish_date": "2026-07-01",
        "view_count": evidence_id * 100,
        "like_count": evidence_id * 10,
        "comment_count": evidence_id,
        "handle": f"creator-{evidence_id}",
        "display_name": f"Creator {evidence_id}",
        "kol_platform": "youtube",
        "product_presence": product_presence,
        "brand_exposure": "",
        "content_summary": "",
        "marketing_value_raw": None,
    }


def test_multi_sku_aggregate_projection_matches_single_sku_reader(monkeypatch) -> None:
    products = {
        "sku-50": {"sku": "sku-50", "model_name": "Viltrox 50mm"},
        "sku-85": {"sku": "sku-85", "model_name": "Viltrox 85mm"},
    }
    aliases = {
        "sku-50": [{"alias": "50mm", "alias_norm": "50mm", "confidence": 1.0}],
        "sku-85": [{"alias": "85mm", "alias_norm": "85mm", "confidence": 1.0}],
    }
    deep_rows = [
        _evidence(1, "50mm review", '{"products":["50mm"]}'),
        _evidence(2, "85mm review", '{"products":["85mm"]}'),
    ]
    title_rows = [
        _evidence(1, "50mm review"),  # deep row wins and is de-duplicated
        _evidence(2, "85mm review"),
        _evidence(3, "street test with 50mm"),
        _evidence(4, "portrait test with 85mm"),
    ]
    calls = {"deep": 0, "title": 0}

    monkeypatch.setattr(sku_performance, "resolve_sku", lambda sku: products.get(sku))
    monkeypatch.setattr(
        sku_performance,
        "_aliases_for",
        lambda product: aliases[str(product["sku"])],
    )
    monkeypatch.setattr("app.db.connection.get_conn", lambda: object())

    def load_deep(_conn):
        calls["deep"] += 1
        return [dict(row) for row in deep_rows]

    def load_titles(_conn):
        calls["title"] += 1
        return [dict(row) for row in title_rows]

    monkeypatch.setattr(sku_performance, "_deep_rows", load_deep)
    monkeypatch.setattr(sku_performance, "_title_rows", load_titles)
    monkeypatch.setattr(sku_performance, "_content_fit_matches", lambda _conn, _sku: [])

    expected = {
        sku: sku_performance._content_performance(product, aliases[sku])["aggregate"]
        for sku, product in products.items()
    }
    calls.update(deep=0, title=0)

    actual = sku_performance.sku_content_aggregate_briefs(list(products))

    assert actual == expected
    assert calls == {"deep": 1, "title": 1}


def test_multi_sku_aggregate_projection_keeps_not_found_and_empty_state(monkeypatch) -> None:
    monkeypatch.setattr(
        sku_performance,
        "resolve_sku",
        lambda sku: {"sku": sku, "model_name": "No matching content"} if sku == "empty" else None,
    )
    monkeypatch.setattr(sku_performance, "_aliases_for", lambda _product: [])

    result = sku_performance.sku_content_aggregate_briefs(["missing", "empty", "empty"])

    assert result["missing"] is None
    assert result["empty"] == sku_performance._aggregate([])
