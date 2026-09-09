"""The scanner gets an ephemeral read-only token, never permission to comment."""
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_gitleaks_uses_pinned_action_and_step_scoped_read_only_authentication():
    workflow = yaml.safe_load((ROOT / ".github/workflows/verify.yml").read_text())
    job = workflow["jobs"]["verify"]
    assert job["permissions"] == {"contents": "read", "pull-requests": "read"}
    assert "GITHUB_TOKEN" not in job["env"]
    scan = [step for step in job["steps"] if step.get("name") == "Secret scan (gitleaks)"]
    assert len(scan) == 1
    assert scan[0]["uses"] == "gitleaks/gitleaks-action@e0c47f4f8be36e29cdc102c57e68cb5cbf0e8d1e"
    assert scan[0]["env"] == {
        "GITHUB_TOKEN": "${{ secrets.GITHUB_TOKEN }}",
        "GITLEAKS_ENABLE_COMMENTS": "false",
        "GITLEAKS_ENABLE_UPLOAD_ARTIFACT": "false",
        "GITLEAKS_VERSION": "8.30.1",
    }
    assert not scan[0].get("continue-on-error", False)
    assert "GITLEAKS_LICENSE" not in scan[0]["env"]
    assert all("GITHUB_TOKEN" not in step.get("env", {}) for step in job["steps"] if step is not scan[0])
