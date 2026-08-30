"""Fixture-tree tests for the delivery evidence collector (nine metrics).

Every fixture is a real directory tree (post-deploy dirs + outcome.json +
incidents.jsonl + verify receipts) so the collector is exercised end to end
without touching git: commit authorship arrives through an injected resolver.
"""
from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts import vkpi_engineering_health_delivery as delivery


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs/vkpi/engineering-health-score-contract-v1.json"
OBSERVED_AT = datetime(2026, 8, 30, tzinfo=UTC)
WINDOW_START = OBSERVED_AT - timedelta(days=90)
CANDIDATE = {"head": "f" * 40, "worktree_dirty": False}


def _dir_stamp(moment: datetime) -> str:
    return moment.strftime("%Y%m%dT%H%M%SZ")


def _sha12(index: int) -> str:
    return f"{index:012x}"


def _mk_deploy(
    base: Path, moment: datetime, sha12: str, outcome: dict[str, object] | None = None
) -> Path:
    directory = base / "post-deploy" / f"{_dir_stamp(moment)}-{sha12}"
    directory.mkdir(parents=True)
    if outcome is not None:
        (directory / "outcome.json").write_text(
            json.dumps(outcome) + "\n", encoding="utf-8"
        )
    return directory


def _write_incidents(base: Path, lines: list[dict[str, object]]) -> Path:
    path = base / "incidents.jsonl"
    path.write_text(
        "".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8"
    )
    return path


def _write_receipts(base: Path, durations_seconds: list[int]) -> Path:
    directory = base / "verify-receipts"
    directory.mkdir(parents=True)
    for index, duration in enumerate(durations_seconds):
        payload = {
            "schema_version": "vkpi_canonical_gate_receipt_v1",
            "generated_at": (WINDOW_START + timedelta(days=1, minutes=index)).isoformat(),
            "started_at": (WINDOW_START + timedelta(days=1)).isoformat(),
            "duration_seconds": duration,
            "passed": True,
        }
        (directory / f"receipt-{index:04d}.json").write_text(
            json.dumps(payload) + "\n", encoding="utf-8"
        )
    return directory


def _collect(
    base: Path,
    *,
    authored_delta_hours: float = 5.0,
    unresolved: frozenset[str] = frozenset(),
) -> dict[str, object]:
    def authored_at(sha12: str) -> datetime | None:
        if sha12 in unresolved:
            return None
        for entry in sorted((base / "post-deploy").iterdir()):
            stamp, _, entry_sha = entry.name.partition("-")
            if entry_sha == sha12:
                deployed = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
                return deployed - timedelta(hours=authored_delta_hours)
        return None

    return delivery.build_receipt(
        candidate=CANDIDATE,
        post_deploy_dir=base / "post-deploy",
        incidents_path=base / "incidents.jsonl",
        receipts_dir=base / "verify-receipts",
        contract_path=CONTRACT_PATH,
        observed_at=OBSERVED_AT,
        authored_at=authored_at,
    )


@pytest.fixture()
def rich_tree(tmp_path: Path) -> Path:
    """25 spaced deployments + 1 hotfix + rollback + 4 incidents + 55 receipts."""
    base_start = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    moments = [base_start + timedelta(days=3 * index) for index in range(25)]
    for index, moment in enumerate(moments):
        outcome = None
        if index == 3:
            outcome = {
                "result": "rolled_back",
                "rollback": {
                    "started_at": (moment + timedelta(minutes=30)).isoformat(),
                    "completed_at": (moment + timedelta(minutes=40)).isoformat(),
                },
                "hotfix_of": None,
            }
        _mk_deploy(tmp_path, moment, _sha12(index), outcome)
    # Hotfix H lands 6h after deployment 5 and names it — inside the 24h rule.
    _mk_deploy(
        tmp_path,
        moments[5] + timedelta(hours=6),
        _sha12(100),
        {"result": "success", "rollback": None, "hotfix_of": _sha12(5)},
    )
    # Legacy naming must stay outside the receipt.
    legacy = tmp_path / "post-deploy" / "www-1bc327d1-20260716T061329Z"
    legacy.mkdir(parents=True)
    _write_incidents(
        tmp_path,
        [
            {"type": "ledger_opened", "at": "2026-06-01T00:00:00Z"},
            {
                "type": "incident",
                "id": "INC-20260810-1",
                "severity": "p1",
                "detected_at": "2026-08-10T00:00:00Z",
                "resolved_at": "2026-08-10T00:30:00Z",
                "caused_by_release": "unknown",
                "summary": "p1 resolved in 30 minutes",
            },
            {
                "type": "incident",
                "id": "INC-20260811-1",
                "severity": "p2",
                "detected_at": "2026-08-11T00:00:00Z",
                "resolved_at": "2026-08-11T01:40:00Z",
                "caused_by_release": "unknown",
                "summary": "p2 resolved in 100 minutes",
            },
            {
                "type": "incident",
                "id": "INC-20260812-1",
                "severity": "p2",
                "detected_at": "2026-08-12T00:00:00Z",
                "caused_by_release": "unknown",
                "summary": "p2 still open",
            },
            {
                "type": "incident",
                "id": "INC-20260815-1",
                "severity": "critical",
                "detected_at": "2026-08-15T00:00:00Z",
                "resolved_at": "2026-08-15T03:20:00Z",
                "deadline_at": "2026-08-15T02:00:00Z",
                "caused_by_release": "unknown",
                "summary": "critical resolved 80 minutes past deadline",
            },
        ],
    )
    _write_receipts(tmp_path, [600] * 50 + [1200] * 5)
    return tmp_path


def test_rich_tree_shape_and_sources(rich_tree: Path) -> None:
    receipt = _collect(rich_tree)
    assert receipt["schema_version"] == "vkpi_delivery_receipt_v1"
    assert receipt["candidate"] == {"head": "f" * 40, "worktree_dirty": False}
    window = receipt["window"]
    assert window["days"] == 90
    assert window["start"] == WINDOW_START.isoformat()
    assert window["end"] == OBSERVED_AT.isoformat()
    assert window["ledger_covered_days"] == 90.0
    assert set(receipt["metrics"]) == set(delivery.METRIC_NAMES)
    # The legacy www-* directory is not a canonical deployment source.
    assert receipt["sources"] == {
        "post_deploy_dirs": 26,
        "incidents_lines": 5,
        "verify_receipts": 55,
        "outcome_files": 2,
    }


def test_deployment_frequency_uses_evidence_span(rich_tree: Path) -> None:
    metric = _collect(rich_tree)["metrics"]["deployment_frequency_per_week"]
    # 26 deployments over the 85.5-day evidence span => 2.13/week, 12 whole weeks.
    assert metric["status"] == "observed"
    assert metric["value"] == 2.13
    assert metric["sample_count"] == 12
    assert metric["reason"] == "computed_over_evidence_span_85.5_days"


def test_lead_time_p50_from_authored_to_deploy(rich_tree: Path) -> None:
    metric = _collect(rich_tree)["metrics"]["lead_time_p50_hours"]
    assert metric == {"status": "observed", "value": 5.0, "sample_count": 26}


def test_lead_time_skips_unresolved_shas_with_reason(rich_tree: Path) -> None:
    metric = _collect(rich_tree, unresolved=frozenset({_sha12(7)}))["metrics"][
        "lead_time_p50_hours"
    ]
    assert metric["status"] == "observed"
    assert metric["sample_count"] == 25
    assert metric["reason"] == "unresolved_sha_count_1"


def test_zero_rollbacks_across_covered_deployments_is_at_target_not_missing(rich_tree: Path) -> None:
    """零回滚是好状态不是缺证据——把 rich_tree 的唯一回滚样本改成成功后,
    rollback_p95 必须按合同 target 15.0 记 observed(reason=no_rollbacks_at_target),
    样本 = outcome 覆盖的部署数;走不到的分支不许伪装成「不重要」。"""
    # 全部部署补上 success outcome(曝光 26 ≥ 合同下限 20)——只有 2 个覆盖时
    # 采集器按 fail-closed 降级是对的:凭 2 个样本不能宣称「零回滚达标」。
    for directory in rich_tree.glob("post-deploy/*"):
        if not directory.is_dir():
            continue
        payload = {"result": "success", "rollback": None, "hotfix_of": None}
        (directory / "outcome.json").write_text(json.dumps(payload) + "\n")
    metric = _collect(rich_tree)["metrics"]["rollback_p95_minutes"]
    assert metric["status"] == "observed"
    assert metric["value"] == 15.0
    assert metric["reason"] == "no_rollbacks_at_target"
    assert metric["sample_count"] >= 20


def test_zero_outcome_coverage_keeps_rollback_missing(rich_tree: Path) -> None:
    """一个 outcome.json 都没有 = 零暴露,零回滚不能白拿 at-target。"""
    for outcome_path in rich_tree.glob("post-deploy/*/outcome.json"):
        outcome_path.unlink()
    metric = _collect(rich_tree)["metrics"]["rollback_p95_minutes"]
    assert metric["status"] == "missing_or_insufficient"
    assert metric["reason"] == "no_outcome_coverage"


def test_change_failure_rate_counts_rollback_and_24h_hotfix(rich_tree: Path) -> None:
    metric = _collect(rich_tree)["metrics"]["change_failure_rate"]
    # Failures: the rolled-back sha and the sha hotfixed 6h later — 2 of 26.
    assert metric == {"status": "observed", "value": 0.0769, "sample_count": 26}


def test_rollback_p95_downgrades_below_contract_minimum(rich_tree: Path) -> None:
    metric = _collect(rich_tree)["metrics"]["rollback_p95_minutes"]
    # One timed 10-minute rollback: the honest value is kept but 1 < 20 samples.
    assert metric["status"] == "missing_or_insufficient"
    assert metric["value"] == 10.0
    assert metric["sample_count"] == 1
    assert metric["reason"].startswith(delivery.INSUFFICIENT)


def test_mttr_percentiles_over_resolved_incidents(rich_tree: Path) -> None:
    metrics = _collect(rich_tree)["metrics"]
    p50 = metrics["mttr_p50_minutes"]
    p90 = metrics["mttr_p90_minutes"]
    # Resolved durations 30/100/200 minutes; exposure = 26 covered deployments.
    assert (p50["status"], p50["value"], p50["sample_count"]) == ("observed", 100.0, 26)
    assert (p90["status"], p90["value"], p90["sample_count"]) == ("observed", 200.0, 26)
    assert p50["reason"] == "resolved_incidents_3"


def test_p1_p2_sla_rate_counts_open_incident_as_missed(rich_tree: Path) -> None:
    metric = _collect(rich_tree)["metrics"]["p1_p2_sla_rate"]
    assert metric == {"status": "observed", "value": 0.6667, "sample_count": 3}


def test_overdue_critical_counts_deadline_breach(rich_tree: Path) -> None:
    metric = _collect(rich_tree)["metrics"]["overdue_critical_count"]
    assert metric == {"status": "observed", "value": 1.0, "sample_count": 1}


def test_build_test_p95_from_verify_receipt_durations(rich_tree: Path) -> None:
    metric = _collect(rich_tree)["metrics"]["build_test_p95_minutes"]
    # 50 x 10min + 5 x 20min, nearest-rank p95 (rank 53 of 55) => 20 minutes.
    assert metric == {"status": "observed", "value": 20.0, "sample_count": 55}


@pytest.fixture()
def at_target_tree(tmp_path: Path) -> Path:
    """21 covered deployments, ledger open 29 days, zero incidents."""
    start = datetime(2026, 8, 5, tzinfo=UTC)
    for index in range(21):
        _mk_deploy(tmp_path, start + timedelta(days=index), _sha12(index))
    _write_incidents(tmp_path, [{"type": "ledger_opened", "at": "2026-08-01T00:00:00Z"}])
    return tmp_path


def test_empty_ledger_records_at_target_with_reason(at_target_tree: Path) -> None:
    receipt = _collect(at_target_tree)
    assert receipt["window"]["ledger_covered_days"] == 29.0
    metrics = receipt["metrics"]
    for name, value in (
        ("mttr_p50_minutes", 60.0),
        ("mttr_p90_minutes", 240.0),
        ("p1_p2_sla_rate", 0.98),
        ("overdue_critical_count", 0.0),
    ):
        metric = metrics[name]
        assert metric["status"] == "observed", name
        assert metric["value"] == value, name
        assert metric["sample_count"] == 21, name
        assert metric["reason"] == delivery.EMPTY_SAMPLE_AT_TARGET, name


def test_zero_incident_cfr_is_observed_zero_over_covered_deploys(
    at_target_tree: Path,
) -> None:
    metric = _collect(at_target_tree)["metrics"]["change_failure_rate"]
    assert metric == {"status": "observed", "value": 0.0, "sample_count": 21}


@pytest.fixture()
def degraded_tree(tmp_path: Path) -> Path:
    """3 deployments before ledger opening; one untimed rollback; no receipts."""
    start = datetime(2026, 8, 10, tzinfo=UTC)
    _mk_deploy(tmp_path, start, _sha12(1))
    _mk_deploy(
        tmp_path,
        start + timedelta(days=1),
        _sha12(2),
        {
            "result": "rolled_back",
            "rollback": {"started_at": None, "completed_at": None},
            "hotfix_of": None,
        },
    )
    _mk_deploy(
        tmp_path,
        start + timedelta(days=2),
        _sha12(3),
        {"result": "failed", "rollback": None, "hotfix_of": None},
    )
    _write_incidents(tmp_path, [{"type": "ledger_opened", "at": "2026-08-20T00:00:00Z"}])
    return tmp_path


def test_degraded_tree_never_invents_numbers(degraded_tree: Path) -> None:
    receipt = _collect(degraded_tree)
    metrics = receipt["metrics"]
    frequency = metrics["deployment_frequency_per_week"]
    # The failed deployment never reached production: 2 events over 20 days.
    assert frequency["status"] == "missing_or_insufficient"
    assert frequency["value"] == 0.7
    assert frequency["reason"].startswith(delivery.INSUFFICIENT)
    rollback = metrics["rollback_p95_minutes"]
    assert rollback["status"] == "missing_or_insufficient"
    assert rollback["value"] is None
    assert rollback["reason"] == "untimed_rollback_count_1"
    cfr = metrics["change_failure_rate"]
    # Outcome-bearing deployments stay observable even before the ledger opened;
    # the failed deploy is excluded, so the rolled-back one is 1 failure of 1.
    assert cfr["status"] == "missing_or_insufficient"
    assert cfr["value"] == 1.0
    assert cfr["sample_count"] == 1
    for name in ("mttr_p50_minutes", "mttr_p90_minutes", "p1_p2_sla_rate"):
        metric = metrics[name]
        assert metric["status"] == "missing_or_insufficient", name
        assert metric["value"] is None, name
        assert metric["reason"] == "ledger_covered_zero_deployments", name
    build = metrics["build_test_p95_minutes"]
    assert build["status"] == "missing_or_insufficient"
    assert build["reason"] == "no_verify_receipts_with_duration"
    assert receipt["sources"]["outcome_files"] == 2
    assert receipt["sources"]["post_deploy_dirs"] == 3


def test_missing_ledger_fails_soft_with_ledger_not_opened(tmp_path: Path) -> None:
    _mk_deploy(tmp_path, datetime(2026, 8, 10, tzinfo=UTC), _sha12(1))
    receipt = _collect(tmp_path)
    assert receipt["window"]["ledger_covered_days"] == 0.0
    assert receipt["sources"]["incidents_lines"] == 0
    for name in ("mttr_p50_minutes", "mttr_p90_minutes", "p1_p2_sla_rate", "overdue_critical_count"):
        metric = receipt["metrics"][name]
        assert metric["status"] == "missing_or_insufficient", name
        assert metric["reason"] == "ledger_not_opened", name


def test_malformed_ledger_line_fails_closed(tmp_path: Path) -> None:
    _mk_deploy(tmp_path, datetime(2026, 8, 10, tzinfo=UTC), _sha12(1))
    (tmp_path / "incidents.jsonl").write_text("not json\n", encoding="utf-8")
    with pytest.raises(delivery.CollectionError, match="not JSON"):
        _collect(tmp_path)


def test_verify_sh_receipt_carries_timing_fields() -> None:
    source = (ROOT / "scripts/verify.sh").read_text(encoding="utf-8")
    assert "VERIFY_STARTED_EPOCH" in source
    assert '"started_at": started_at' in source
    assert '"duration_seconds": int(duration_seconds)' in source
    assert (
        subprocess.run(
            ["bash", "-n", str(ROOT / "scripts/verify.sh")], check=False
        ).returncode
        == 0
    )


def test_train_sh_writes_outcome_after_deploy() -> None:
    source = (ROOT / "scripts/ops/train.sh").read_text(encoding="utf-8")
    deploy_at = source.index('bash "${ROOT}/scripts/ops/deploy_local_to_cloud.sh"')
    outcome_at = source.index("write_train_outcome")
    assert deploy_at < outcome_at, "outcome.json must be written after the deploy step"
    for required in (
        "outcome.json",
        '"rolled_back"',
        "VKPI_TRAIN_HOTFIX_OF",
        "rollback_started_at",
        "rollback_completed_at",
    ):
        assert required in source, required
    assert (
        subprocess.run(
            ["bash", "-n", str(ROOT / "scripts/ops/train.sh")], check=False
        ).returncode
        == 0
    )
