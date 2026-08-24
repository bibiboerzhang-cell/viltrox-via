from __future__ import annotations

import pytest

from app.domains.kol.discovery_filters import (
    _competitor_brand_official,
    discovery_account_gate_verdict,
)
from app.domains.kol.discovery_regional_official import (
    regional_brand_profile_self_attributed,
)


TAMRON_MALAYSIA = {
    "platform": "instagram",
    "handle": "tamronmalaysia",
    "display_name": "Tamron Malaysia",
    "profile_url": "https://www.instagram.com/tamronmalaysia/",
    "bio": "Use the hashtag #tamronlensmy and tag us @tamronmalaysia to be featured!",
}


def test_tamron_malaysia_exact_first_party_account_is_excluded() -> None:
    assert regional_brand_profile_self_attributed(TAMRON_MALAYSIA, "tamron") is True
    assert (
        _competitor_brand_official(
            TAMRON_MALAYSIA,
            competitor_brands={"tamron": []},
        )
        == "tamron"
    )
    assert discovery_account_gate_verdict(TAMRON_MALAYSIA) == "brand_official"


@pytest.mark.parametrize(
    "candidate",
    [
        {
            **TAMRON_MALAYSIA,
            "handle": "tamronmalaysiareviewer",
            "display_name": "Alex reviews Tamron Malaysia",
            "profile_url": "https://www.instagram.com/tamronmalaysiareviewer/",
            "bio": "I'm an independent photographer reviewing Tamron lenses.",
        },
        {
            **TAMRON_MALAYSIA,
            "profile_url": "https://www.instagram.com/tamronmalaysia_fan/",
        },
        {
            **TAMRON_MALAYSIA,
            "bio": "Photography tips and reviews mentioning Tamron Malaysia.",
        },
        {
            **TAMRON_MALAYSIA,
            "bio": "Tag us @anotheraccount for a chance to be featured.",
        },
    ],
)
def test_tamron_malaysia_personal_or_unproven_shapes_fail_open(
    candidate: dict[str, str],
) -> None:
    assert regional_brand_profile_self_attributed(candidate, "tamron") is False
    assert (
        _competitor_brand_official(candidate, competitor_brands={"tamron": []})
        == ""
    )
