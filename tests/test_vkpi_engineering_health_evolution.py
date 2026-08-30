from __future__ import annotations

import copy
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts import vkpi_engineering_health_evolution as evolution
from scripts import vkpi_engineering_health_score as score


def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=root, env=env, capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


def _init(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Test User")
    _git(root, "config", "user.email", "test@example.com")


def _commit(
    root: Path,
    *,
    timestamp: datetime,
    author: str,
    email: str,
    files: dict[str, str],
) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(root, "add", ".")
    stamp = timestamp.astimezone(UTC).isoformat()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": author,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_AUTHOR_DATE": stamp,
        "GIT_COMMITTER_NAME": author,
        "GIT_COMMITTER_EMAIL": email,
        "GIT_COMMITTER_DATE": stamp,
    }
    _git(root, "commit", "-q", "-m", f"change by {author}", env=env)


def _domain_files(suffix: str) -> dict[str, str]:
    return {
        "backend/app/core/security.py": f"AUTH = '{suffix}'\n",
        "backend/app/domains/platform/tenant.py": f"TENANT = '{suffix}'\n",
        "backend/app/domains/kol/search.py": f"KOL = '{suffix}'\n",
        "migrations/001.sql": f"-- {suffix}\n",
        "backend/app/workers/job.py": f"WORKER = '{suffix}'\n",
        "frontend/src/main.ts": f"export const UI = '{suffix}'\n",
    }


def _qualification_receipt(
    root: Path,
    identities_by_domain: dict[str, list[str]],
) -> dict[str, object]:
    head = _git(root, "rev-parse", "HEAD")
    generated_at = _git(root, "show", "-s", "--format=%cI", "HEAD")
    return {
        "schema_version": evolution.QUALIFICATION_SCHEMA_VERSION,
        "candidate": {"head": head},
        "generated_at": generated_at,
        "source": "fixture://independent-maintainer-review",
        "domains": {
            domain: {
                "people": [
                    {
                        "identity": identity,
                        "merged_pr_refs": [
                            f"pr://{domain}/{identity}/1",
                            f"pr://{domain}/{identity}/2",
                            f"pr://{domain}/{identity}/3",
                        ],
                        "independent_review_refs": [
                            f"review://{domain}/{identity}/1",
                            f"review://{domain}/{identity}/2",
                        ],
                        "operational_evidence_refs": [
                            f"drill://{domain}/{identity}/1",
                        ],
                    }
                    for identity in identities
                ]
            }
            for domain, identities in identities_by_domain.items()
        },
    }


def test_incomplete_history_fails_closed_and_dirty_tree_is_not_input(tmp_path: Path) -> None:
    _init(tmp_path)
    end = datetime(2026, 8, 1, tzinfo=UTC)
    _commit(
        tmp_path,
        timestamp=end - timedelta(days=30),
        author="Alice",
        email="alice@example.com",
        files=_domain_files("one"),
    )
    _commit(
        tmp_path,
        timestamp=end,
        author="Bob",
        email="bob@example.com",
        files=_domain_files("two"),
    )

    clean = evolution.build_receipt(tmp_path)
    (tmp_path / "backend/app/domains/kol/search.py").write_text("dirty\n", encoding="utf-8")
    (tmp_path / ".mailmap").write_text(
        "Bob <bob@example.com> Alice <alice@example.com>\n",
        encoding="utf-8",
    )
    dirty = evolution.build_receipt(tmp_path)

    assert clean["status"] == "partial"
    assert clean["window"]["complete"] is False
    assert clean["metrics"]["core_domain_bus_factor_min"]["status"] == "unknown"
    assert clean["metrics"]["temporal_coupling_p95"]["status"] == "unknown"
    assert clean["metrics"] == dirty["metrics"]
    assert clean["details"] == dirty["details"]
    assert clean["candidate"]["worktree_dirty"] is False
    assert dirty["candidate"]["worktree_dirty"] is True
    assert dirty["candidate"]["worktree_is_input"] is False
    assert dirty["history"]["identity_mailmap_source"] == "HEAD:.mailmap"
    assert dirty["history"]["working_tree_mailmap_ignored"] is True


def test_complete_history_observes_bus_factor_and_pair_denominators(tmp_path: Path) -> None:
    _init(tmp_path)
    end = datetime(2026, 8, 1, tzinfo=UTC)
    _commit(
        tmp_path,
        timestamp=end - timedelta(days=200),
        author="Baseline",
        email="baseline@example.com",
        files={"README.md": "old enough\n"},
    )
    _commit(
        tmp_path,
        timestamp=end - timedelta(days=20),
        author="Alice",
        email="alice@example.com",
        files=_domain_files("alice"),
    )
    _commit(
        tmp_path,
        timestamp=end - timedelta(days=10),
        author="Bob",
        email="bob@example.com",
        files={
            "backend/app/domains/kol/search.py": "KOL = 'bob'\nEXTRA = 1\n",
            "backend/app/workers/job.py": "WORKER = 'bob'\nEXTRA = 1\n",
        },
    )
    _commit(
        tmp_path,
        timestamp=end - timedelta(days=5),
        author="release-bot[bot]",
        email="release-bot@noreply.example.com",
        files={"backend/app/domains/kol/search.py": "BOT = True\n"},
    )
    _commit(
        tmp_path,
        timestamp=end,
        author="Carol",
        email="carol@example.com",
        files={"README.md": "head anchor\n"},
    )

    receipt = evolution.build_receipt(tmp_path)

    assert receipt["status"] == "observed"
    assert receipt["window"]["covered_days"] == 180
    assert receipt["history"]["excluded_bot_commits"] == 1
    assert "--no-merges" in receipt["history"]["command"]
    bus = receipt["metrics"]["core_domain_bus_factor_min"]
    assert bus["status"] == "unknown"
    assert bus["reason"] == "identity_mailmap_not_committed"
    assert bus["sample_count"] == 180
    kol = receipt["details"]["bus_factor"]["domains"]["kol"]
    assert kol["people_normalized_bus_factor"] == 1
    assert {row["email"] for row in kol["minimum_contributor_set"]}.isdisjoint(
        {"release-bot@noreply.example.com"}
    )
    coupling = receipt["details"]["temporal_coupling"]
    assert coupling["raw_pair_count"] > 0
    kol_worker = next(
        row
        for row in coupling["raw_top_pairs"]
        if {row["left"], row["right"]}
        == {"backend/app/domains/kol/search.py", "backend/app/workers/job.py"}
    )
    assert kol_worker["cochange_commits"] == 2
    assert kol_worker["union_change_commits"] == 2
    assert kol_worker["coupling"] == 1.0
    assert coupling["qualified_pair_count"] == 0
    assert receipt["metrics"]["temporal_coupling_p95"]["status"] == "unknown"
    assert (
        receipt["metrics"]["temporal_coupling_p95"]["reason"]
        == "insufficient_qualified_pairs"
    )


def test_bus_factor_uses_minimum_set_reaching_half_changed_lines(tmp_path: Path) -> None:
    _init(tmp_path)
    end = datetime(2026, 8, 1, tzinfo=UTC)
    _commit(
        tmp_path,
        timestamp=end - timedelta(days=200),
        author="Old",
        email="old@example.com",
        files={"README.md": "old\n"},
    )
    _commit(
        tmp_path,
        timestamp=end - timedelta(days=3),
        author="Alice",
        email="alice@example.com",
        files=_domain_files("alice"),
    )
    _commit(
        tmp_path,
        timestamp=end - timedelta(days=2),
        author="Bob",
        email="bob@example.com",
        files={path: content + "B = 2\n" for path, content in _domain_files("bob").items()},
    )
    _commit(
        tmp_path,
        timestamp=end - timedelta(days=1),
        author="Carol",
        email="carol@example.com",
        files={path: content + "C = 3\n" for path, content in _domain_files("carol").items()},
    )
    _commit(
        tmp_path,
        timestamp=end,
        author="Anchor",
        email="anchor@example.com",
        files={"README.md": "head\n"},
    )

    receipt = evolution.build_receipt(tmp_path)
    domains = receipt["details"]["bus_factor"]["domains"]
    assert all(row["bus_factor"] >= 1 for row in domains.values())
    assert all(
        row["minimum_contributor_set"][-1]["cumulative_share"] >= 0.5
        for row in domains.values()
    )
    assert all(
        len(row["minimum_contributor_set"]) == row["bus_factor"]
        for row in domains.values()
    )


def test_score_merge_preserves_unknown_for_partial_history(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init(repo)
    end = datetime(2026, 8, 1, tzinfo=UTC)
    _commit(
        repo,
        timestamp=end - timedelta(days=20),
        author="Alice",
        email="alice@example.com",
        files=_domain_files("one"),
    )
    _commit(
        repo,
        timestamp=end,
        author="Anchor",
        email="anchor@example.com",
        files={"README.md": "head\n"},
    )
    receipt = evolution.build_receipt(repo)
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    evidence = {"candidate": {"head": receipt["candidate"]["head"]}, "metrics": {}}

    score.merge_evolution_receipt(evidence, receipt_path, receipt)

    metrics = evidence["metrics"]["evolution"]
    assert metrics["core_domain_bus_factor_min"]["status"] == "unknown"
    assert metrics["temporal_coupling_p95"]["status"] == "unknown"
    assert metrics["core_domain_bus_factor_min"]["sample_count"] == 20


def test_score_merge_rejects_head_mismatch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init(repo)
    end = datetime(2026, 8, 1, tzinfo=UTC)
    _commit(
        repo,
        timestamp=end,
        author="Alice",
        email="alice@example.com",
        files=_domain_files("one"),
    )
    receipt = evolution.build_receipt(repo)
    evidence = {"candidate": {"head": "0" * 40}, "metrics": {}}

    try:
        score.merge_evolution_receipt(evidence, tmp_path / "receipt.json", receipt)
    except score.ContractError as exc:
        assert "head mismatch" in str(exc)
    else:
        raise AssertionError("head mismatch must fail closed")


def test_ambiguous_author_aliases_fail_bus_factor_closed(tmp_path: Path) -> None:
    _init(tmp_path)
    end = datetime(2026, 8, 1, tzinfo=UTC)
    _commit(
        tmp_path,
        timestamp=end - timedelta(days=200),
        author="Old",
        email="old@example.com",
        files={"README.md": "old\n"},
    )
    _commit(
        tmp_path,
        timestamp=end - timedelta(days=1),
        author="Same Person",
        email="first@example.com",
        files=_domain_files("first"),
    )
    _commit(
        tmp_path,
        timestamp=end,
        author="Same Person",
        email="second@example.com",
        files=_domain_files("second"),
    )

    receipt = evolution.build_receipt(tmp_path)

    bus = receipt["metrics"]["core_domain_bus_factor_min"]
    assert receipt["window"]["complete"] is True
    assert receipt["history"]["identity_quality_complete"] is False
    assert receipt["history"]["identity_ambiguities"] == {
        "same person": ["first@example.com", "second@example.com"]
    }
    assert bus["status"] == "unknown"
    assert bus["reason"] == "author_identity_ambiguity_requires_mailmap"
    assert receipt["metrics"]["temporal_coupling_p95"]["status"] == "unknown"
    evidence = {"candidate": {"head": receipt["candidate"]["head"]}, "metrics": {}}
    score.merge_evolution_receipt(evidence, tmp_path / "receipt.json", receipt)
    assert (
        evidence["metrics"]["evolution"]["core_domain_bus_factor_min"]["reason"]
        == "author_identity_ambiguity_requires_mailmap"
    )


def test_committed_head_mailmap_resolves_author_aliases(tmp_path: Path) -> None:
    _init(tmp_path)
    end = datetime(2026, 8, 1, tzinfo=UTC)
    _commit(
        tmp_path,
        timestamp=end - timedelta(days=200),
        author="Old",
        email="old@example.com",
        files={"README.md": "old\n"},
    )
    _commit(
        tmp_path,
        timestamp=end - timedelta(days=2),
        author="Same Person",
        email="first@example.com",
        files=_domain_files("first"),
    )
    _commit(
        tmp_path,
        timestamp=end - timedelta(days=1),
        author="Same Person",
        email="second@example.com",
        files=_domain_files("second"),
    )
    _commit(
        tmp_path,
        timestamp=end,
        author="Anchor",
        email="anchor@example.com",
        files={
            ".mailmap": (
                "Same Person <first@example.com> "
                "Same Person <second@example.com>\n"
            )
        },
    )

    receipt = evolution.build_receipt(tmp_path)

    assert receipt["history"]["identity_quality_complete"] is True
    assert receipt["history"]["identity_ambiguities"] == {}
    assert receipt["metrics"]["core_domain_bus_factor_min"]["status"] == "unknown"
    assert (
        receipt["metrics"]["core_domain_bus_factor_min"]["reason"]
        == "maintainer_qualification_evidence_missing"
    )
    assert receipt["details"]["bus_factor"]["people_normalized_domain_minimum"] == 1
    assert receipt["history"]["identity_mailmap_source"] == "HEAD:.mailmap"
    assert "mailmap.blob=HEAD:.mailmap" in receipt["history"]["command"]


def test_human_github_noreply_is_not_classified_as_bot() -> None:
    human = evolution.Commit(
        oid="a" * 40,
        author_name="Alice Example",
        author_email="12345+alice@users.noreply.github.com",
        authored_at=datetime(2026, 8, 1, tzinfo=UTC),
        changes=(),
    )
    bot = evolution.Commit(
        oid="b" * 40,
        author_name="dependabot[bot]",
        author_email="49699333+dependabot[bot]@users.noreply.github.com",
        authored_at=datetime(2026, 8, 1, tzinfo=UTC),
        changes=(),
    )

    assert evolution._is_bot(human) is False
    assert evolution._is_bot(bot) is True


def test_bus_factor_excludes_test_generated_and_runtime_changes() -> None:
    commit = evolution.Commit(
        oid="c" * 40,
        author_name="Alice",
        author_email="alice@example.com",
        authored_at=datetime(2026, 8, 1, tzinfo=UTC),
        changes=(
            evolution.Change("backend/app/domains/kol/live.py", 4, 1),
            evolution.Change("backend/app/domains/kol/test_live.py", 100, 0),
            evolution.Change("backend/app/domains/kol/__tests__/live.py", 100, 0),
            evolution.Change("backend/app/domains/kol/generated/live.py", 100, 0),
            evolution.Change("backend/app/domains/kol/runtime/live.py", 100, 0),
            evolution.Change("migrations/001.sql", 7, 2),
        ),
    )

    domains, _ = evolution._bus_factor([commit])

    assert domains["kol"]["changed_lines"] == 5
    assert domains["database_migrations"]["changed_lines"] == 9


def test_temporal_p95_excludes_one_off_pair_and_includes_stable_support(tmp_path: Path) -> None:
    _init(tmp_path)
    end = datetime(2026, 8, 20, tzinfo=UTC)
    _commit(
        tmp_path,
        timestamp=end - timedelta(days=200),
        author="Old",
        email="old@example.com",
        files={"README.md": "old\n"},
    )
    # Three cochanges, then seven single-file changes: Jaccard=3/10.
    for index in range(10):
        files: dict[str, str] = {}
        if index < 3 or 3 <= index < 7:
            files["backend/app/domains/kol/left.py"] = f"LEFT = {index}\n"
        if index < 3 or index >= 7:
            files["backend/app/domains/kol/right.py"] = f"RIGHT = {index}\n"
        if index == 0:
            files.update(_domain_files("coverage"))
        _commit(
            tmp_path,
            timestamp=end - timedelta(days=9 - index),
            author="Alice",
            email="alice@example.com",
            files=files,
        )

    receipt = evolution.build_receipt(tmp_path)
    coupling = receipt["details"]["temporal_coupling"]

    assert coupling["raw_pair_count"] > coupling["qualified_pair_count"]
    assert coupling["excluded_low_support_count"] > 0
    pair = next(
        row
        for row in coupling["top_pairs"]
        if {row["left"], row["right"]}
        == {
            "backend/app/domains/kol/left.py",
            "backend/app/domains/kol/right.py",
        }
    )
    assert pair["cochange_commits"] == 3
    assert pair["union_change_commits"] == 10
    assert pair["coupling"] == 0.3
    assert receipt["metrics"]["temporal_coupling_p95"]["status"] == "observed"


def _five_person_complete_history(root: Path) -> list[str]:
    _init(root)
    end = datetime(2026, 8, 20, tzinfo=UTC)
    _commit(
        root,
        timestamp=end - timedelta(days=200),
        author="Old",
        email="old@example.com",
        files={"README.md": "old enough\n"},
    )
    identities = [f"person{index}@example.com" for index in range(1, 6)]
    for index, identity in enumerate(identities, start=1):
        _commit(
            root,
            timestamp=end - timedelta(days=20 - index),
            author=f"Person {index}",
            email=identity,
            files=_domain_files(f"person-{index}"),
        )
    _commit(
        root,
        timestamp=end,
        author="Anchor",
        email="anchor@example.com",
        files={
            "README.md": "head anchor\n",
            ".mailmap": "Person 1 <person1@example.com> Alias One <person1+git@example.com>\n",
        },
    )
    return identities


def test_qualified_people_bus_factor_three_is_observed_and_mergeable(tmp_path: Path) -> None:
    identities = _five_person_complete_history(tmp_path)
    qualification = _qualification_receipt(
        tmp_path,
        {domain: identities for domain in evolution.CORE_DOMAINS},
    )

    receipt = evolution.build_receipt(
        tmp_path,
        qualification_receipt=qualification,
        qualification_source="fixture://qualification-file",
    )

    bus = receipt["metrics"]["core_domain_bus_factor_min"]
    ratio = receipt["metrics"]["qualified_maintainer_domain_ratio"]
    assert bus["status"] == "observed"
    assert bus["value"] == 3
    assert ratio["status"] == "observed"
    assert ratio["value"] == 1.0
    assert receipt["history"]["identity_mailmap_committed"] is True
    assert receipt["maintainer_qualification"]["status"] == "observed"
    domains = receipt["details"]["bus_factor"]["domains"]
    assert all(row["people_normalized_bus_factor"] == 3 for row in domains.values())
    assert all(row["qualified_bus_factor"] == 3 for row in domains.values())
    assert all(row["qualification_ready"] is True for row in domains.values())

    receipt_path = tmp_path / "evolution-receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    evidence = {"candidate": {"head": receipt["candidate"]["head"]}, "metrics": {}}
    score.merge_evolution_receipt(evidence, receipt_path, receipt)
    merged = evidence["metrics"]["evolution"]
    assert merged["core_domain_bus_factor_min"]["value"] == 3
    assert merged["qualified_maintainer_domain_ratio"]["value"] == 1.0


def test_partial_qualification_cannot_turn_people_bus_factor_into_a_pass(tmp_path: Path) -> None:
    identities = _five_person_complete_history(tmp_path)
    qualification = _qualification_receipt(
        tmp_path,
        {domain: identities[:2] for domain in evolution.CORE_DOMAINS},
    )

    receipt = evolution.build_receipt(
        tmp_path,
        qualification_receipt=qualification,
        qualification_source="fixture://qualification-file",
    )

    bus = receipt["metrics"]["core_domain_bus_factor_min"]
    assert receipt["details"]["bus_factor"]["people_normalized_domain_minimum"] == 3
    assert bus["status"] == "unknown"
    assert bus["reason"] == "maintainer_qualification_incomplete"
    assert receipt["metrics"]["qualified_maintainer_domain_ratio"]["value"] == 0.0
    assert all(
        row["qualified_changed_line_share"] < evolution.QUALIFIED_CONTRIBUTION_SHARE
        for row in receipt["details"]["bus_factor"]["domains"].values()
    )


def test_shared_account_cannot_be_laundered_into_a_person_by_mailmap(tmp_path: Path) -> None:
    _init(tmp_path)
    end = datetime(2026, 8, 20, tzinfo=UTC)
    _commit(
        tmp_path,
        timestamp=end - timedelta(days=200),
        author="Old",
        email="old@example.com",
        files={"README.md": "old enough\n"},
    )
    _commit(
        tmp_path,
        timestamp=end - timedelta(days=10),
        author="Shared Team Account",
        email="team@example.com",
        files=_domain_files("shared"),
    )
    _commit(
        tmp_path,
        timestamp=end - timedelta(days=5),
        author="Alice",
        email="alice@example.com",
        files=_domain_files("alice"),
    )
    _commit(
        tmp_path,
        timestamp=end,
        author="Anchor",
        email="anchor@example.com",
        files={
            "README.md": "head\n",
            ".mailmap": (
                "Alice <alice@example.com> Shared Team Account <team@example.com>\n"
            ),
        },
    )

    receipt = evolution.build_receipt(tmp_path)

    assert receipt["history"]["excluded_shared_account_commits"] == 1
    assert receipt["history"]["shared_account_detection_uses_original_and_mailmapped_identity"] is True
    assert all(
        row["shared_account_changed_lines"] > 0
        for row in receipt["details"]["bus_factor"]["domains"].values()
    )


def test_qualification_requires_independent_references(tmp_path: Path) -> None:
    identities = _five_person_complete_history(tmp_path)
    qualification = _qualification_receipt(
        tmp_path,
        {domain: identities for domain in evolution.CORE_DOMAINS},
    )
    qualification["domains"]["kol"]["people"][0]["independent_review_refs"] = []

    try:
        evolution.build_receipt(
            tmp_path,
            qualification_receipt=qualification,
            qualification_source="fixture://qualification-file",
        )
    except evolution.EvolutionEvidenceError as exc:
        assert "independent_review_refs requires at least" in str(exc)
    else:
        raise AssertionError("document-only maintainer claims must fail closed")


def test_score_merge_rejects_tampered_per_domain_qualification(tmp_path: Path) -> None:
    identities = _five_person_complete_history(tmp_path)
    qualification = _qualification_receipt(
        tmp_path,
        {domain: identities for domain in evolution.CORE_DOMAINS},
    )
    receipt = evolution.build_receipt(
        tmp_path,
        qualification_receipt=qualification,
        qualification_source="fixture://qualification-file",
    )
    receipt["details"]["bus_factor"]["domains"]["kol"]["qualification_ready"] = False
    evidence = {"candidate": {"head": receipt["candidate"]["head"]}, "metrics": {}}

    try:
        score.merge_evolution_receipt(evidence, tmp_path / "receipt.json", receipt)
    except score.ContractError as exc:
        assert "complete per-domain qualification" in str(exc)
    else:
        raise AssertionError("tampered qualification details must fail closed")


def test_score_merge_recomputes_qualified_coverage_and_bus_factor(tmp_path: Path) -> None:
    identities = _five_person_complete_history(tmp_path)
    qualification = _qualification_receipt(
        tmp_path,
        {domain: identities for domain in evolution.CORE_DOMAINS},
    )
    valid = evolution.build_receipt(
        tmp_path,
        qualification_receipt=qualification,
        qualification_source="fixture://qualification-file",
    )

    bad_share = copy.deepcopy(valid)
    bad_share["details"]["bus_factor"]["domains"]["kol"][
        "qualified_changed_line_share"
    ] = 0.99
    evidence = {"candidate": {"head": valid["candidate"]["head"]}, "metrics": {}}
    try:
        score.merge_evolution_receipt(evidence, tmp_path / "bad-share.json", bad_share)
    except score.ContractError as exc:
        assert "qualified changed-line coverage" in str(exc)
    else:
        raise AssertionError("recorded qualification coverage must be recomputed")

    bad_factor = copy.deepcopy(valid)
    for row in bad_factor["details"]["bus_factor"]["domains"].values():
        row["qualified_bus_factor"] = 1
        row["qualified_minimum_contributor_set"] = row[
            "qualified_minimum_contributor_set"
        ][:1]
    bad_factor["metrics"]["core_domain_bus_factor_min"]["value"] = 1
    evidence = {"candidate": {"head": valid["candidate"]["head"]}, "metrics": {}}
    try:
        score.merge_evolution_receipt(evidence, tmp_path / "bad-factor.json", bad_factor)
    except score.ContractError as exc:
        assert "independent recomputation" in str(exc)
    else:
        raise AssertionError("recorded bus factor must be recomputed")
