"""发车链两条新契约(2026-09-02):班车自己等排水;定时自动发车四道守卫。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRAIN = ROOT / "scripts/ops/train.sh"
AUTO = ROOT / "scripts/ops/auto_train.sh"


def test_train_waits_for_release_drain_between_alignment_and_deploy() -> None:
    src = TRAIN.read_text(encoding="utf-8")
    assert subprocess.run(["bash", "-n", str(TRAIN)], check=False).returncode == 0
    align = src.index("\nwait_for_alignment\n")
    wait = src.index("\nwait_for_release_drain\n")
    deploy = src.index("deploy 开始")
    assert align < wait < deploy, "排水等待必须在本地栈对齐之后、deploy 之前"
    # 与 deploy 用同一探针,同一远端调用形态,库名不离开远端 shell
    assert 'verify_release_drain.py"' in src and "--expected-database" in src and "--current-migration" in src
    assert "sed -nE" in src and "DATABASE_URL" in src
    # 探针自身出错不盲等;=0 可关闭
    assert "不盲等" in src and 'VKPI_TRAIN_DRAIN_WAIT_SECONDS:-5400' in src
    assert 'assert_clean_tree "排水后"' in src


def test_auto_train_has_four_guards_and_never_deploys_dirty_or_red() -> None:
    src = AUTO.read_text(encoding="utf-8")
    assert subprocess.run(["bash", "-n", str(AUTO)], check=False).returncode == 0
    assert os.access(AUTO, os.X_OK)
    for needle in (
        "pgrep -f \"scripts/ops/train.sh|freeze_worktree_candidate.py|deploy_local_to_cloud.sh\"",
        "git status --porcelain",
        "outcome.json",
        "completed/success",
        "bash scripts/ops/train.sh",
    ):
        assert needle in src, needle
    # CI 读不到 = 不发(宁缺毋滥)
    assert 'unreadable' in src and 'skip "CI 对 HEAD' in src
