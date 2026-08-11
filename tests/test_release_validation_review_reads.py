from __future__ import annotations

import pytest

from app.core import release_validation


EXACT_REVIEW_READS = (
    "/api/admin/vkpi/agents/marketing-brain/scorecard",
    "/api/admin/vkpi/agents/learning-status",
    "/api/admin/vkpi/gtm/outreach-truth/coverage",
    "/api/admin/vkpi/gtm/verdicts/pending",
)

DYNAMIC_REVIEW_READS = (
    "/api/admin/vkpi/actions/17/review-candidate",
    "/api/admin/vkpi/gtm/actions/17/outreach-binding-status",
    "/api/admin/vkpi/gtm/outreach-bindings/17/reply-review-candidate",
    "/api/admin/vkpi/skills/runs/17/review-candidate",
)


@pytest.mark.parametrize("method", ("GET", "HEAD"))
@pytest.mark.parametrize("path", EXACT_REVIEW_READS + DYNAMIC_REVIEW_READS)
def test_reviewed_read_is_available_while_fenced(method: str, path: str) -> None:
    assert release_validation.release_validation_request_allowed(method, path)


@pytest.mark.parametrize("method", ("POST", "PUT", "PATCH", "DELETE"))
@pytest.mark.parametrize("path", EXACT_REVIEW_READS + DYNAMIC_REVIEW_READS)
def test_reviewed_read_never_opens_a_mutating_method(method: str, path: str) -> None:
    assert not release_validation.release_validation_request_allowed(method, path)


@pytest.mark.parametrize(
    "path",
    (
        "/api/admin/vkpi/agents/marketing-brain/scorecard/extra",
        "/api/admin/vkpi/agents/learning-status/extra",
        "/api/admin/vkpi/gtm/outreach-truth/coverage/extra",
        "/api/admin/vkpi/gtm/verdicts/pending/extra",
        "/api/admin/vkpi/actions/0/review-candidate",
        "/api/admin/vkpi/actions/-1/review-candidate",
        "/api/admin/vkpi/actions/not-an-id/review-candidate",
        "/api/admin/vkpi/actions/17/review-candidate/extra",
        "/api/admin/vkpi/gtm/actions/0/outreach-binding-status",
        "/api/admin/vkpi/gtm/actions/17/outreach-binding",
        "/api/admin/vkpi/gtm/actions/17/outreach-binding-status/extra",
        "/api/admin/vkpi/gtm/outreach-bindings/0/reply-review-candidate",
        "/api/admin/vkpi/gtm/outreach-bindings/17/reply-verification",
        "/api/admin/vkpi/gtm/outreach-bindings/17/reply-review-candidate/extra",
        "/api/admin/vkpi/skills/runs/0/review-candidate",
        "/api/admin/vkpi/skills/runs/17/review",
        "/api/admin/vkpi/skills/runs/17/review-candidate/extra",
    ),
)
def test_review_read_allowlist_does_not_open_adjacent_paths(path: str) -> None:
    assert not release_validation.release_validation_request_allowed("GET", path)
