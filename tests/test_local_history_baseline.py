"""Synthetic, offline evidence tests: no business dump, database, or provider access."""

from dataclasses import FrozenInstanceError, asdict, replace
import hashlib
import json
import os

import pytest

from scripts.ops import local_history_baseline as history
from scripts.ops.atomic_release_shared import LayoutError, pending_runtime_migrations


CANONICAL = ("001_schema.sql", "083_dashboard_kol_account_picker.sql", "310_refresh.sql")


def dump_text(spec, *, rows=None, tables=None, extra=""):
    selected_tables = spec.tables if tables is None else tables
    parts = ["--\n-- PostgreSQL database dump\n"]
    for table in selected_tables:
        parts.append(
            f"-- Name: {table}; Type: TABLE; Schema: public; Owner: postgres\n"
            f"CREATE TABLE public.{table} (\n    id bigint NOT NULL\n);\n"
        )
    parts.append(extra)
    parts.append("-- Data for Name: schema_migrations; Type: TABLE DATA; Schema: public; Owner: postgres\n")
    parts.append("COPY public.schema_migrations (version_key, applied_at) FROM stdin;\n")
    selected_rows = rows if rows is not None else [
        ("001_schema.sql", "2026-01-01 00:00:00+00"),
        *zip(history.HISTORICAL_KEYS[:spec.key_count], history._APPLIED_AT[:spec.key_count]),
    ]
    parts.extend(f"{key}\t{applied}\n" for key, applied in selected_rows)
    parts.append("\\.\n")
    parts.append(
        "-- Data for Name: vkpi_repair_proposals; Type: TABLE DATA; Schema: public; Owner: postgres\n"
        "COPY public.vkpi_repair_proposals (id, source_payload_json) FROM stdin;\n"
        "7\tPRIVATE_BUSINESS_CONTENT_NOT_EVIDENCE\n\\.\n"
        "-- Name: vkpi_repair_proposals_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres\n"
        "SELECT pg_catalog.setval('public.vkpi_repair_proposals_id_seq', 7, true);\n"
        "-- PostgreSQL database dump complete\n"
    )
    return "".join(parts)


@pytest.fixture
def synthetic_backups(tmp_path, monkeypatch):
    # Test-only pins; the collector exposes no caller-supplied hash or scope override.
    specs = tuple(replace(spec, relative_path=f"dump-{index}.sql")
                  for index, spec in enumerate(history._BACKUPS))

    def write(*, change_index=None, change=None):
        pinned = []
        for index, spec in enumerate(specs):
            source = dump_text(spec)
            if index == change_index:
                source = change(source, spec)
            payload = source.encode()
            (tmp_path / spec.relative_path).write_bytes(payload)
            pinned.append(replace(spec, size_bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest()))
        monkeypatch.setattr(history, "_BACKUPS", tuple(pinned))
        return tmp_path

    write()
    return tmp_path, write


def collect(root, **kwargs):
    return history.collect_historical_baseline(repo_root=root, canonical_manifest=CANONICAL, **kwargs)


def test_review_is_historical_only_and_contains_no_business_data(synthetic_backups):
    root, _write = synthetic_backups
    review = collect(root)
    assert review.status == "historical_backup_verified_current_binding_missing"
    assert review.database_name_scope == "viltrox2"
    assert review.backups[1].tables == history.HISTORICAL_TABLES
    assert tuple(row.version_key for row in review.backups[1].ledger) == history.HISTORICAL_KEYS
    assert review.backups[1].ledger[1].applied_at == history._APPLIED_AT[1]
    assert all(len(item.schema_sha256) == 64 for item in review.backups)
    assert not review.current_database_verified
    assert not review.current_schema_verified
    assert not review.original_migration_source_recovered
    assert not review.backup_restore_verified
    assert not review.migration_exclusion_authorized
    assert review.authorized_exclusions == ()
    rendered = json.dumps(asdict(review))
    assert "PRIVATE_BUSINESS_CONTENT" not in rendered
    assert "setval" not in rendered
    assert "TABLE DATA" not in rendered
    with pytest.raises(FrozenInstanceError):
        review.current_database_verified = True


def test_canonical_sort_order_does_not_change_digest(synthetic_backups):
    root, _write = synthetic_backups
    shuffled = history.collect_historical_baseline(repo_root=root, canonical_manifest=CANONICAL[::-1])
    assert shuffled == collect(root)


@pytest.mark.parametrize("target", ["cloud", "viltrox2_test_release_9d2f7ca7158477ec10b7", "postgres", ""])
def test_cloud_or_other_database_is_rejected_before_read(synthetic_backups, target, monkeypatch):
    root, _write = synthetic_backups
    monkeypatch.setattr(history, "_read_pinned_backup", lambda *_args: pytest.fail("must not read"))
    with pytest.raises(history.HistoricalEvidenceError, match="local_scope_only"):
        collect(root, target_database=target)


@pytest.mark.parametrize("manifest", [
    (), CANONICAL + (CANONICAL[0],), ("../invalid.sql",), ("307_test_down.sql",),
    CANONICAL + (history.HISTORICAL_KEYS[0],), CANONICAL + (history.HISTORICAL_KEYS[1],),
])
def test_invalid_or_forged_canonical_manifest_rejected(synthetic_backups, manifest):
    root, _write = synthetic_backups
    with pytest.raises(history.HistoricalEvidenceError):
        history.collect_historical_baseline(repo_root=root, canonical_manifest=manifest)


@pytest.mark.parametrize("change,reason", [
    (lambda source, spec: source.replace(history._APPLIED_AT[0], "2020-01-01"), "ledger_evidence_mismatch"),
    (lambda source, spec: source.replace("\\.\n", f"{history.HISTORICAL_KEYS[0]}\t{history._APPLIED_AT[0]}\n\\.\n", 1), "ledger_malformed_or_duplicate"),
    (lambda source, spec: source.replace("\\.\n", "999_unknown.sql\t2026-01-01\n\\.\n", 1), "ledger_unknown_key"),
    (lambda source, spec: source.replace("COPY public.schema_migrations", "COPY public.not_the_ledger"), "ledger_evidence_mismatch"),
    (lambda source, spec: dump_text(spec, tables=spec.tables[:-1]), "schema_set_mismatch"),
    (lambda source, spec: dump_text(spec, tables=spec.tables + ("vkpi_repair_unreviewed",)), "object_unknown_or_empty"),
    (lambda source, spec: source.replace("Type: TABLE;", "Type: TRIGGER;", 1), "object_type_unreviewed"),
    (lambda source, spec: source.replace("Schema: public;", "Schema: alternate;", 1), "schema_unexpected"),
    (lambda source, spec: source.replace("-- PostgreSQL database dump complete", "-- truncated"), "dump_boundary_missing"),
])
def test_malformed_historical_evidence_rejected(synthetic_backups, change, reason):
    root, write = synthetic_backups
    write(change_index=1, change=change)
    with pytest.raises(history.HistoricalEvidenceError, match=reason):
        collect(root)


def test_schema_digest_includes_constraint_and_index_definitions(synthetic_backups):
    root, write = synthetic_backups
    original = collect(root).backups[1].schema_sha256
    extra = (
        "-- Name: vkpi_repair_proposals pk; Type: CONSTRAINT; Schema: public; Owner: postgres\n"
        "ALTER TABLE ONLY public.vkpi_repair_proposals ADD CONSTRAINT pk PRIMARY KEY (id);\n"
        "-- Name: idx_vkpi_repair_sample; Type: INDEX; Schema: public; Owner: postgres\n"
        "CREATE INDEX idx_vkpi_repair_sample ON public.vkpi_repair_proposals USING btree (id);\n"
    )
    write(change_index=1, change=lambda _source, spec: dump_text(spec, extra=extra))
    updated = collect(root).backups[1]
    assert updated.schema_sha256 != original
    assert {item.object_type for item in updated.schema_objects} == {"TABLE", "CONSTRAINT", "INDEX"}


def test_shared_table_definition_drift_between_pinned_observations_rejected(synthetic_backups):
    root, write = synthetic_backups
    write(change_index=1, change=lambda source, spec: source.replace("id bigint", "id integer"))
    with pytest.raises(history.HistoricalEvidenceError, match="table_definition_drift"):
        collect(root)


@pytest.mark.parametrize("mode", ["hash", "size", "symlink", "hardlink", "missing"])
def test_unpinned_or_aliased_backup_rejected(synthetic_backups, mode):
    root, _write = synthetic_backups
    path = root / history._BACKUPS[0].relative_path
    if mode == "hash":
        path.write_bytes(path.read_bytes().replace(b"bigint", b"smalli", 1))
    elif mode == "size":
        path.write_bytes(path.read_bytes() + b"extra")
    elif mode == "symlink":
        renamed = path.with_suffix(".real")
        path.rename(renamed)
        path.symlink_to(renamed)
    elif mode == "hardlink":
        os.link(path, root / "alias.sql")
    else:
        path.unlink()
    with pytest.raises(history.HistoricalEvidenceError):
        collect(root)


def test_fabricated_success_flags_do_not_authorize_exclusions(synthetic_backups):
    root, _write = synthetic_backups
    review = collect(root)
    for value in (review, {**asdict(review), "current_database_verified": True,
                          "migration_exclusion_authorized": True}):
        with pytest.raises(history.HistoricalEvidenceError, match="identity_and_schema_binding_missing"):
            history.require_adoptable_review(value)


def test_existing_pending_manifest_still_rejects_both_historical_keys(synthetic_backups):
    root, _write = synthetic_backups
    collect(root)
    with pytest.raises(LayoutError, match="outside the runtime manifest"):
        pending_runtime_migrations(CANONICAL, CANONICAL + history.HISTORICAL_KEYS)
