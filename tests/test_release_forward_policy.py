"""Offline plan-only bindings: these fixtures never certify production drain."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts.ops.atomic_release_shared import _database_release_metadata
from scripts.ops.atomic_release_units import LayoutError
from scripts.ops.release_forward_policy import (
    EVIDENCE_SCHEMA, FORWARD_ONLY_POLICY, MIGRATION_SHA256,
    assert_restore_allowed, forward_only_metadata, inspect_policy, migration_names,
)


ROOT = Path(__file__).resolve().parents[1]
NAMES = tuple(MIGRATION_SHA256)


def plan(names=NAMES):
    return {
        "schema": EVIDENCE_SCHEMA, "release_id": "synthetic-forward-release",
        "source_git_sha": "0" * 40, "target_git_sha": "1" * 40,
        "database_identity": {"system_identifier": "7000000000000000001", "oid": 16384, "name": "viltrox2_test"},
        "env_sha256": "a" * 64, "backup_receipt_sha256": "b" * 64,
        "unknown_manifest_sha256": "c" * 64, "pending_migrations": list(names),
    }


def build(evidence=None, **overrides):
    arguments = dict(
        release_policy=FORWARD_ONLY_POLICY, strategy="in-place", source_database="",
        target_database="", env_fingerprint_before="", database_owner_release_id="",
        pending_migrations=",".join(NAMES), compatibility_declaration="",
        evidence=plan() if evidence is None else evidence, migrations_dir=ROOT / "migrations",
    )
    arguments.update(overrides)
    return forward_only_metadata(**arguments)


def sealed(metadata=None):
    return {"release_id": plan()["release_id"], "git_sha": "1" * 40,
            "pending_migrations": list(NAMES), "forward_compatible_migrations": [],
            **(build() if metadata is None else metadata)}


@pytest.mark.parametrize("bits", range(1, 16))
def test_every_nonempty_ordered_subset_has_exact_bytes_and_plan_semantics(bits):
    names = tuple(name for index, name in enumerate(NAMES) if bits & (1 << index))
    evidence = plan(names)
    before = copy.deepcopy(evidence)
    metadata = build(evidence, pending_migrations=",".join(names))
    assert metadata["forward_only_migrations"] == [{"name": name, "sha256": MIGRATION_SHA256[name]} for name in names]
    assert metadata["live_drain_authorized"] is False
    assert metadata["claim_status"] == "seal_plan_binding_only"
    assert metadata["preserve_unknown_budget"] is True
    assert metadata["app_rollback_allowed"] is metadata["database_rollback_allowed"] is False
    assert "forward_compatibility_evidence" not in metadata
    assert evidence == before


@pytest.mark.parametrize("declaration", ["", ",".join(reversed(NAMES)), NAMES[0] + "," + NAMES[0],
                                         NAMES[0] + ",", " " + NAMES[0], "999_unknown.sql"])
def test_bad_or_unknown_migration_sequence_is_rejected(declaration):
    with pytest.raises(LayoutError):
        migration_names(declaration)


@pytest.mark.parametrize("field", tuple(plan()))
def test_each_missing_plan_field_is_rejected(field):
    evidence = plan()
    del evidence[field]
    with pytest.raises(LayoutError):
        build(evidence)


@pytest.mark.parametrize("value", [None, [], "yes", True, {"same_database": True}])
def test_nonobject_or_boolean_database_claim_is_not_evidence(value):
    evidence = plan()
    evidence["database_identity"] = value
    with pytest.raises(LayoutError):
        build(evidence)


@pytest.mark.parametrize(("field", "value"), [
    ("system_identifier", 7000000000000000001), ("system_identifier", "0"),
    ("system_identifier", "01"), ("system_identifier", str(2**64)),
    ("oid", True), ("oid", 0), ("oid", "16384"), ("name", "wrong/database"),
])
def test_database_identity_is_strictly_typed(field, value):
    evidence = plan()
    evidence["database_identity"][field] = value
    with pytest.raises(LayoutError):
        build(evidence)


@pytest.mark.parametrize("field", ["source_git_sha", "target_git_sha", "env_sha256", "backup_receipt_sha256", "unknown_manifest_sha256"])
def test_invalid_or_missing_digest_is_not_accepted(field):
    evidence = plan()
    evidence[field] = ""
    with pytest.raises(LayoutError):
        build(evidence)


@pytest.mark.parametrize("field", ["same_database", "live_drain_passed", "provider_settled"])
def test_unreviewed_live_claims_cannot_be_added_to_plan(field):
    evidence = plan()
    evidence[field] = True
    with pytest.raises(LayoutError):
        build(evidence)


def test_new_clone_rollback_declaration_and_unknown_policy_are_rejected():
    for overrides in ({"strategy": "staging-clone"}, {"compatibility_declaration": ",".join(NAMES)},
                      {"release_policy": "unreviewed"}, {"target_database": "viltrox2_test"}):
        with pytest.raises(LayoutError):
            build(**overrides)


def test_existing_clone_reuse_requires_the_exact_owner_database_and_environment():
    evidence = plan()
    owner = "original-release"
    name = "viltrox2_test_release_" + hashlib.sha256(owner.encode()).hexdigest()[:20]
    evidence["database_identity"]["name"] = name
    arguments = dict(strategy="reuse-active-clone", target_database=name,
                     database_owner_release_id=owner, env_fingerprint_before=evidence["env_sha256"])
    metadata = build(evidence, **arguments)
    assert metadata["target_database"] == name
    assert metadata["database_rollback"] == "forbidden-forward-only"
    for key, bad in (("target_database", "viltrox2_test"), ("database_owner_release_id", "other"),
                     ("env_fingerprint_before", "d" * 64), ("source_database", "viltrox2_test")):
        with pytest.raises(LayoutError):
            build(evidence, **{**arguments, key: bad})


@pytest.mark.parametrize("name", NAMES)
def test_even_comment_only_sql_drift_is_rejected(tmp_path, name):
    for migration in NAMES:
        (tmp_path / migration).write_bytes((ROOT / "migrations" / migration).read_bytes())
    with (tmp_path / name).open("ab") as handle:
        handle.write(b"\n-- unreviewed change\n")
    with pytest.raises(LayoutError, match="not reviewed"):
        build(migrations_dir=tmp_path)


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_linked_migration_source_is_rejected(tmp_path, link_kind):
    source = ROOT / "migrations" / NAMES[0]
    if link_kind == "symlink":
        (tmp_path / NAMES[0]).symlink_to(source)
    else:
        local = tmp_path / "local-copy"
        local.write_bytes(source.read_bytes())
        (tmp_path / NAMES[0]).hardlink_to(local)
    with pytest.raises(LayoutError, match="single-link"):
        build(plan((NAMES[0],)), pending_migrations=NAMES[0], migrations_dir=tmp_path)


def test_default_metadata_contract_is_unchanged_and_still_rejects_308():
    args = dict(strategy="in-place", source_database="", target_database="",
                env_fingerprint_before="", pending_migrations=NAMES[0], compatibility_declaration=NAMES[0])
    old = _database_release_metadata(**args)
    assert old["forward_compatibility_evidence"]["policy_id"] == "vkpi-additive-nullable-defaultless-v1"
    assert "release_policy" not in old
    assert inspect_policy(old) == {"release_policy": "rollback-compatible", "forward_only": False}
    assert_restore_allowed(old)
    with pytest.raises(LayoutError, match="not reviewed"):
        _database_release_metadata(**{**args, "pending_migrations": NAMES[1], "compatibility_declaration": NAMES[1]})
    with pytest.raises(LayoutError):
        _database_release_metadata(**args, forward_only_evidence=plan())


@pytest.mark.parametrize("field", ["release_policy", "preserve_unknown_budget", "app_rollback_allowed",
                                    "database_rollback_allowed", "live_drain_authorized", "recovery_mode",
                                    "forward_only_evidence", "forward_only_evidence_sha256", "forward_only_migrations"])
def test_incomplete_sealed_contract_is_never_downgraded_to_default(field):
    metadata = sealed()
    del metadata[field]
    with pytest.raises(LayoutError):
        inspect_policy(metadata)


def test_valid_sealed_policy_is_read_only_and_restore_is_unconditionally_refused():
    metadata = sealed()
    before = json.dumps(metadata, sort_keys=True)
    assert inspect_policy(metadata)["forward_only"] is True
    with pytest.raises(LayoutError, match="keep the fence"):
        assert_restore_allowed(metadata)
    assert json.dumps(metadata, sort_keys=True) == before
