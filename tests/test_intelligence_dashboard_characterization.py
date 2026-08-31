from __future__ import annotations

from app.services.intelligence.dashboard import _price_distribution, _series_distribution


PRODUCTS = [
    {"title": "Viltrox 16mm f/1.8 Lens", "price": 499.0, "rating": 4.8},
    {"title": "Viltrox 27mm Pro Lens", "price": 549.0, "rating": 4.6},
    {"title": "Viltrox 85mm f/1.8 Lens", "price": 399.0, "rating": 0.0},
]


def test_price_distribution_preserves_bucket_order_and_positive_rating_average() -> None:
    assert _price_distribution(PRODUCTS) == [
        {
            "tier": "High ($400-699)",
            "count": 2,
            "avg_rating": 4.7,
            "products": ["Viltrox 16mm f/1.8 Lens", "Viltrox 27mm Pro Lens"],
        },
        {
            "tier": "Mid ($200-399)",
            "count": 1,
            "avg_rating": 0.0,
            "products": ["Viltrox 85mm f/1.8 Lens"],
        },
    ]


def test_series_distribution_preserves_price_and_rating_semantics() -> None:
    assert _series_distribution(PRODUCTS) == [
        {
            "series": "STANDARD",
            "count": 2,
            "min_price": 399.0,
            "max_price": 499.0,
            "avg_rating": 4.8,
        },
        {
            "series": "PRO",
            "count": 1,
            "min_price": 549.0,
            "max_price": 549.0,
            "avg_rating": 4.6,
        },
    ]
