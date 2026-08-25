"""保留策略必须认「数据库拥有者 release」(2026-08-25 车道 3·(2))。

**病根。** 当天清理 125 个历史 release,保留规则是「最近 5 个 + current +
rollback-anchor」,漏掉了**拥有当前克隆库的那个 release**。线上库名 =
``clone_prefix + sha256(database_owner_release_id)[:20]``;那天的拥有者是三周前的
``20260804T065125Z-c9d89af320b0``,排在「最近 5 个」之外,被删掉后部署直接拒绝::

    Refusing viltroxtest deploy because the remote database identity is unreadable.

本测试把「拥有者怎么推导出来」这条判据钉死:从
``.release-controller/rollbacks/*/database-clone.json`` 里找 ``target_database``
== 线上库名 的那条,其 ``release_id`` 即拥有者 —— 并且必须用 sha256 反算回线上库名
才算数,收据说了不算。

**边界。** ``scripts/ops/release_retention_policy.py`` 是纯函数:不碰文件系统、
不碰数据库、不起子进程、**不删任何东西**,输入含糊就抛 ``RetentionError``
(失败方向 = 全留 / 停手)。把自动删库接进部署流水线的做法当天已被安全闸拦下,
本车道只产出判据。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "scripts" / "ops"
if str(OPS) not in sys.path:
    sys.path.insert(0, str(OPS))

import release_retention_policy as policy  # noqa: E402
import staging_db_clone  # noqa: E402


# --------------------------------------------------------------------------
# 2026-08-25 线上实况(只读探针取自 viltrox:/opt/viltrox-2.0)
# --------------------------------------------------------------------------

LIVE_DATABASE = "viltrox2_test_release_9d2f7ca7158477ec10b7"
OWNER = "20260804T065125Z-c9d89af320b0"
CURRENT = "20260825T174529Z-1d2b9c6f65e7"
PREVIOUS = "20260825T071246Z-a05e48dd3802"

PLAIN_RELEASES = (
    OWNER,
    "20260825T050034Z-e8285ba3b9c2",
    "20260825T060800Z-5638eb099735",
    "20260825T070341Z-a05e48dd3802",
    PREVIOUS,
    "20260825T173559Z-1d2b9c6f65e7",
    CURRENT,
)
ANCHOR_RELEASES = (
    "rollback-anchor-20260728T070922Z-262d42dfa394-7168de2b5b4f",
    "rollback-anchor-20260728T072748Z-d21b5bece44d-7168de2b5b4f",
    "rollback-anchor-20260728T081832Z-c6d80aee5f24-7168de2b5b4f",
    "rollback-anchor-20260728T174258Z-3051727a0e5a-c6d80aee5f24",
    "rollback-anchor-20260728T180026Z-3051727a0e5a-c6d80aee5f24",
    "rollback-anchor-20260728T183510Z-55332f54fe29-c6d80aee5f24",
    "rollback-anchor-20260728T185849Z-233188b5cd4d-c6d80aee5f24",
    "rollback-anchor-20260824T145119Z-63ab58b61a82-153a4f92e730",
    "rollback-anchor-20260824T171521Z-199781536fdd-df5d6cad9970",
    "rollback-anchor-20260825T071246Z-a05e48dd3802-5638eb099735",
)
LIVE_RELEASES = PLAIN_RELEASES + ANCHOR_RELEASES


def _receipt(
    release_id: str,
    target_database: str,
    *,
    state: str = "activated",
    strategy: str = "staging-clone",
    source: str = "viltrox2_test_release_445e2033dc113ffd7a7e",
) -> dict[str, object]:
    return {
        "release_id": release_id,
        "database_strategy": strategy,
        "source_database": source,
        "target_database": target_database,
        "state": state,
        "secrets_included": False,
        "schema": 1,
    }


def _live_receipts() -> dict[str, dict[str, object]]:
    """17 份收据里只有 1 份指向线上库(线上实测数字)。"""

    receipts: dict[str, dict[str, object]] = {
        OWNER: _receipt(OWNER, LIVE_DATABASE),
    }
    for release_id in PLAIN_RELEASES:
        if release_id == OWNER:
            continue
        receipts[release_id] = _receipt(
            release_id,
            policy.clone_name_for_release(release_id),
            state="superseded",
        )
    return receipts


def _live_inputs(**overrides: object) -> policy.RetentionInputs:
    kwargs: dict[str, object] = {
        "release_ids": LIVE_RELEASES,
        "current_release_id": CURRENT,
        "previous_release_id": PREVIOUS,
        "live_database_name": LIVE_DATABASE,
        "clone_receipts": _live_receipts(),
        "keep_recent": 5,
    }
    kwargs.update(overrides)
    return policy.RetentionInputs(**kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# 病根复现
# --------------------------------------------------------------------------


def _buggy_keep_rule(release_ids, current, anchors_prefix="rollback-anchor-"):
    """当天用的规则:最近 5 个 + current + rollback-anchor。"""

    dated = sorted(
        (name for name in release_ids if not name.startswith(anchors_prefix)),
        reverse=True,
    )
    keep = set(dated[:5]) | {current}
    keep |= {name for name in release_ids if name.startswith(anchors_prefix)}
    return keep


def test_the_2026_08_25_rule_would_have_deleted_the_database_owner() -> None:
    """先坐实病根:旧规则确实会删掉拥有者。"""

    keep = _buggy_keep_rule(LIVE_RELEASES, CURRENT)
    assert OWNER not in keep, "旧规则若已保留拥有者,本测试的前提就不成立了"
    assert PREVIOUS in keep


def test_policy_keeps_the_database_owner_even_when_it_is_the_oldest() -> None:
    plan = policy.compute_retention(_live_inputs())

    assert plan.database_owner_release_id == OWNER
    assert OWNER in plan.keep
    assert policy.KEEP_DATABASE_OWNER in plan.why(OWNER)
    # 拥有者是全场最老的一个,任何以「新旧」为唯一判据的规则都会漏掉它。
    assert OWNER == min(PLAIN_RELEASES)
    assert policy.KEEP_RECENT not in plan.why(OWNER)


def test_policy_only_frees_the_one_genuinely_unreferenced_release() -> None:
    plan = policy.compute_retention(_live_inputs())

    assert plan.deletable == ("20260825T050034Z-e8285ba3b9c2",)
    assert plan.why(CURRENT) == (policy.KEEP_CURRENT, policy.KEEP_RECENT)
    assert policy.KEEP_PREVIOUS in plan.why(PREVIOUS)
    for anchor in ANCHOR_RELEASES:
        assert anchor in plan.keep
        assert policy.KEEP_UNRECOGNISED in plan.why(anchor)


def test_describe_names_the_owner() -> None:
    text = policy.describe(policy.compute_retention(_live_inputs()))
    assert OWNER in text and policy.KEEP_DATABASE_OWNER in text
    assert "manual step" in text


# --------------------------------------------------------------------------
# 库名推导:收据说了不算,sha256 说了才算
# --------------------------------------------------------------------------


def test_clone_name_matches_the_deploy_controller() -> None:
    """本模块的推导必须和 staging_db_clone 逐字一致,否则判据会漂。"""

    for release_id in PLAIN_RELEASES:
        assert policy.clone_name_for_release(release_id) == (
            staging_db_clone.clone_name_for_release(release_id)
        )
    assert policy.clone_name_for_release(OWNER) == LIVE_DATABASE


def test_owner_is_derived_from_the_receipt_that_targets_the_live_database() -> None:
    assert policy.database_owner_release_id(_live_receipts(), LIVE_DATABASE) == OWNER


def test_legacy_base_database_has_no_owner() -> None:
    assert policy.database_owner_release_id({}, policy.LEGACY_SOURCE_DATABASE) == ""


def test_unknown_database_name_is_refused() -> None:
    with pytest.raises(policy.RetentionError, match="reviewed release clone"):
        policy.database_owner_release_id(_live_receipts(), "postgres")


def test_missing_receipt_refuses_to_guess() -> None:
    receipts = {k: v for k, v in _live_receipts().items() if k != OWNER}
    with pytest.raises(policy.RetentionError, match="no rollback receipt claims"):
        policy.database_owner_release_id(receipts, LIVE_DATABASE)


def test_forged_receipt_is_caught_by_the_digest() -> None:
    """收据自称拥有线上库,但它的 release_id 反算不出这个库名。"""

    receipts = _live_receipts()
    receipts["20260825T060800Z-5638eb099735"] = _receipt(
        "20260825T060800Z-5638eb099735", LIVE_DATABASE
    )
    with pytest.raises(policy.RetentionError, match="does not produce"):
        policy.database_owner_release_id(receipts, LIVE_DATABASE)


def test_receipt_in_the_wrong_directory_is_refused() -> None:
    """部署会去 rollbacks/<release_id> 找,目录名对不上就等于找不到。"""

    receipts = _live_receipts()
    receipts["rollbacks-elsewhere"] = receipts.pop(OWNER)
    with pytest.raises(policy.RetentionError, match="does not match the release"):
        policy.database_owner_release_id(receipts, LIVE_DATABASE)


def test_non_activated_owner_receipt_is_refused() -> None:
    receipts = _live_receipts()
    receipts[OWNER] = _receipt(OWNER, LIVE_DATABASE, state="prepared")
    with pytest.raises(policy.RetentionError, match="not 'activated'"):
        policy.database_owner_release_id(receipts, LIVE_DATABASE)


def test_non_staging_clone_owner_receipt_is_refused() -> None:
    receipts = _live_receipts()
    receipts[OWNER] = _receipt(OWNER, LIVE_DATABASE, strategy="reuse-active-clone")
    with pytest.raises(policy.RetentionError, match="not a staging-clone receipt"):
        policy.database_owner_release_id(receipts, LIVE_DATABASE)


def test_receipt_without_a_release_id_is_refused() -> None:
    receipts = _live_receipts()
    receipts[OWNER] = _receipt("", LIVE_DATABASE)
    with pytest.raises(policy.RetentionError, match="without naming a release"):
        policy.database_owner_release_id(receipts, LIVE_DATABASE)


def test_owner_claims_reports_the_single_hit() -> None:
    claims = policy.owner_claims(_live_receipts(), LIVE_DATABASE)
    assert len(claims) == 1
    assert claims[0].release_id == OWNER
    assert claims[0].rollback_directory == OWNER
    assert claims[0].state == "activated"


def test_traversal_shaped_release_id_is_refused() -> None:
    with pytest.raises(policy.RetentionError, match="safe directory name"):
        policy.clone_name_for_release("../escape")
    with pytest.raises(policy.RetentionError, match="safe directory name"):
        policy.clone_name_for_release("..")


# --------------------------------------------------------------------------
# 计划本身:含糊即停手
# --------------------------------------------------------------------------


def test_owner_already_deleted_stops_the_computation() -> None:
    """拥有者已经不在 releases/ 里 = 部署已经坏了,不许再出删除计划。"""

    survivors = tuple(name for name in LIVE_RELEASES if name != OWNER)
    with pytest.raises(policy.RetentionError, match="already missing from"):
        policy.compute_retention(_live_inputs(release_ids=survivors))


def test_dangling_previous_pointer_stops_the_computation() -> None:
    with pytest.raises(policy.RetentionError, match="previous pointer .* is dangling"):
        policy.compute_retention(_live_inputs(previous_release_id="20260101T000000Z-deadbeef"))


def test_dangling_rollback_anchor_stops_the_computation() -> None:
    with pytest.raises(policy.RetentionError, match="rollback anchor .* is dangling"):
        policy.compute_retention(
            _live_inputs(rollback_anchor_release_ids=("20260101T000000Z-deadbeef",))
        )


def test_named_rollback_anchor_is_kept_with_its_own_reason() -> None:
    plan = policy.compute_retention(
        _live_inputs(rollback_anchor_release_ids=("20260825T050034Z-e8285ba3b9c2",))
    )
    assert plan.deletable == ()
    assert policy.KEEP_ROLLBACK_ANCHOR in plan.why("20260825T050034Z-e8285ba3b9c2")


def test_current_pointer_must_exist() -> None:
    with pytest.raises(policy.RetentionError, match="current pointer"):
        policy.compute_retention(_live_inputs(current_release_id="20260101T000000Z-deadbeef"))
    with pytest.raises(policy.RetentionError, match="current release pointer is unset"):
        policy.compute_retention(_live_inputs(current_release_id=""))


def test_duplicate_and_empty_release_lists_are_refused() -> None:
    with pytest.raises(policy.RetentionError, match="duplicates"):
        policy.compute_retention(_live_inputs(release_ids=LIVE_RELEASES + (CURRENT,)))
    with pytest.raises(policy.RetentionError, match="release list is empty"):
        policy.compute_retention(_live_inputs(release_ids=()))


def test_keep_recent_must_be_positive() -> None:
    with pytest.raises(policy.RetentionError, match="keep_recent"):
        policy.compute_retention(_live_inputs(keep_recent=0))


def test_recent_window_widens_monotonically() -> None:
    """放宽窗口只能让保留集变大,永远不会把已保留的甩出去。"""

    previous_keep: frozenset[str] = frozenset()
    for window in range(1, 8):
        plan = policy.compute_retention(_live_inputs(keep_recent=window))
        assert previous_keep <= plan.keep
        assert OWNER in plan.keep
        previous_keep = plan.keep
    assert previous_keep == frozenset(LIVE_RELEASES)


def test_unrecognised_names_are_never_proposed_for_deletion() -> None:
    """未来发明的新目录名(legacy 快照等)默认保留,不默认删除。"""

    exotic = "legacy-before-20260825T174529Z-1d2b9c6f65e7"
    plan = policy.compute_retention(_live_inputs(release_ids=LIVE_RELEASES + (exotic,)))
    assert exotic in plan.keep
    assert plan.why(exotic) == (policy.KEEP_UNRECOGNISED,)
    assert exotic not in plan.deletable


def test_legacy_base_host_still_produces_a_plan() -> None:
    plan = policy.compute_retention(
        _live_inputs(live_database_name=policy.LEGACY_SOURCE_DATABASE, clone_receipts={})
    )
    assert plan.database_owner_release_id == ""
    assert OWNER in plan.deletable  # 无 clone 可护时它才只是个旧 release


def test_plan_partitions_the_release_list_exactly() -> None:
    plan = policy.compute_retention(_live_inputs())
    assert plan.keep | set(plan.deletable) == set(LIVE_RELEASES)
    assert not (plan.keep & set(plan.deletable))


# --------------------------------------------------------------------------
# 安全闸:这个模块永远不许长出执行删除的能力
# --------------------------------------------------------------------------


def test_policy_module_cannot_execute_anything() -> None:
    source = (OPS / "release_retention_policy.py").read_text(encoding="utf-8")
    forbidden = (
        "import os",
        "import shutil",
        "import subprocess",
        "import psycopg",
        "rmtree",
        "unlink(",
        "DROP DATABASE",
        "dropdb",
        "os.remove",
        "Popen",
    )
    offenders = [token for token in forbidden if token in source]
    assert not offenders, (
        "release_retention_policy.py 是纯判据模块,不许出现执行/删除能力:"
        f"{offenders}。真要清理,人工看 describe() 的输出后自己动手。"
    )
