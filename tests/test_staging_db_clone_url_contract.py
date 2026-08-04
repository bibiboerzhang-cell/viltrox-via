from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "ops"))

import staging_db_clone  # noqa: E402


SECRET = "never-print-this-password"


@pytest.mark.parametrize(
    "query",
    [
        "dbname=other",
        "%64bname=other",
        "database=other",
        "host=attacker.invalid",
        "hostaddr=203.0.113.10",
        "port=6543",
        "user=other",
        "service=other",
        "servicefile=/tmp/other",
        "options=-csearch_path%3Dattacker",
    ],
)
def test_database_url_identity_overrides_fail_closed_without_secret(
    query: str,
) -> None:
    source = staging_db_clone.SOURCE_DATABASE
    unsafe = f"postgresql://app:{SECRET}@db.internal:5432/{source}?{query}"

    with pytest.raises(staging_db_clone.CloneError) as captured:
        staging_db_clone.database_name_from_url(unsafe)

    assert str(captured.value) == (
        "DATABASE_URL query parameters may alter connection identity"
    )
    assert SECRET not in str(captured.value)


def test_database_url_transport_only_parameters_remain_supported() -> None:
    source = staging_db_clone.SOURCE_DATABASE
    safe = (
        f"postgresql://app:{SECRET}@db.internal:5432/{source}"
        "?sslmode=require&application_name=vkpi&connect_timeout=5"
    )

    assert staging_db_clone.database_name_from_url(safe) == source
