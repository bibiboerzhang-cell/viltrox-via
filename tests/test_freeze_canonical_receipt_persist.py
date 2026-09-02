"""freeze 必须把 canonical 门回执留在采集器能读的地方(2026-09-02 实测缺口)。

背景:freeze 在 phase-A 沙箱里跑 verify.sh,回执写在沙箱内,沙箱随手删除——
13 次发车零 build_test 样本。修法:最终源树断言之后复制到 runtime/ops/verify-receipts/。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from scripts.ops.freeze_receipt_persist import (
    VERIFY_RECEIPTS_RELATIVE,
    collector_eligible,
    persist_build_test_receipt,
    receipt_file_name,
)
from scripts.ops.freeze_worktree_candidate import freeze_candidate
from scripts.vkpi_engineering_health_delivery import _load_verify_durations
from tests.freeze_worktree_candidate_fixtures import _freeze_args, _repo

_RECEIPT = {
    "schema_version": "vkpi_canonical_gate_receipt_v1",
    "generated_at": "2026-09-02T01:52:23+00:00",
    "duration_seconds": 422,
    "passed": True,
    "candidate": {"git_head": "abc", "release_head": "abc", "clean_worktree": True},
}


def _fake_writer(path: Path, payload: object) -> tuple[int, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return (7, 11)


def test_collector_eligibility_mirrors_delivery_collector_rule() -> None:
    assert collector_eligible(_RECEIPT) is True
    assert collector_eligible({**_RECEIPT, "duration_seconds": None}) is False
    assert collector_eligible({**_RECEIPT, "duration_seconds": True}) is False  # bool 不是时长
    assert collector_eligible({**_RECEIPT, "generated_at": ""}) is False
    assert collector_eligible("not a dict") is False


def test_receipt_file_name_is_filesystem_safe_and_stamped() -> None:
    name = receipt_file_name(Path("/x/www-release-candidate-78b8f2f66"), _RECEIPT)
    assert name.startswith("www-release-candidate-78b8f2f66-20260902T015223")
    assert name.endswith(".canonical-gate.json")
    assert "/" not in name and ":" not in name


def test_persist_writes_under_collector_dir_and_reports_eligibility(tmp_path: Path) -> None:
    source = tmp_path / "repo"
    record = persist_build_test_receipt(
        source=source, output=tmp_path / "candidate", canonical=_RECEIPT, writer=_fake_writer,
    )
    target = Path(record["path"])
    assert target.parent == source / VERIFY_RECEIPTS_RELATIVE
    assert json.loads(target.read_text()) == _RECEIPT  # 逐字节原样,不裁字段
    assert record["collector_eligible"] is True
    assert record["identity"] == [7, 11]
    # 真采集器读同一个目录、同一条规则:这就是 build_test 样本
    window = (datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 3, tzinfo=UTC))
    durations, parsed = _load_verify_durations(target.parent, *window)
    assert parsed == 1 and durations == [422 / 60.0]


def test_freeze_without_verify_persists_nothing(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    payload = freeze_candidate(_freeze_args(root, tmp_path / "candidate"))
    assert payload["verification"]["build_test_receipt"] is None
    assert not (root / VERIFY_RECEIPTS_RELATIVE).exists()
