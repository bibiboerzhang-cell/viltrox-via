"""People-normalized maintainer evidence for engineering-health evolution."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


BUS_FACTOR_SHARE = 0.50
QUALIFICATION_SCHEMA_VERSION = "vkpi_maintainer_qualification_receipt_v1"
QUALIFIED_CONTRIBUTION_SHARE = 0.90
MIN_QUALIFIED_MERGED_PRS = 3
MIN_QUALIFIED_INDEPENDENT_REVIEWS = 2
MIN_QUALIFIED_OPERATIONAL_EVIDENCE = 1
BOT_NAME_PATTERN = re.compile(
    r"(?:\[bot\]|\bdependabot\b|\brenovate(?:bot)?\b|\bgithub-actions(?:\[bot\])?\b)",
    re.IGNORECASE,
)
BOT_LOCAL_PART_PATTERN = re.compile(
    r"^(?:bot|.+[+._-]bot|dependabot|renovate(?:bot)?|github-actions)$",
    re.IGNORECASE,
)
SHARED_NAME_PATTERN = re.compile(
    r"\b(?:shared|service|team|release|engineering|developer)\s+(?:account|user|identity)\b",
    re.IGNORECASE,
)
SHARED_LOCAL_PART_PATTERN = re.compile(
    r"^(?:admin|automation|build|ci|deploy|devops|engineering|git|ops|release|root|service|shared|team)"
    r"(?:[+._-].*)?$",
    re.IGNORECASE,
)
SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".css", ".sql", ".sh"}
SKIP_PARTS = {
    "__tests__", "dist", "generated", "migrations_generated", "node_modules",
    "runtime", "tests", "test",
}
CORE_DOMAINS: dict[str, tuple[str, ...]] = {
    "authentication": (
        "backend/app/core/security.py", "backend/app/core/passwords.py",
        "backend/app/core/permissions.py", "backend/app/services/auth/",
        "backend/app/domains/access/", "backend/app/api/dependencies/auth.py",
        "backend/app/api/dependencies/perms.py",
    ),
    "tenant_boundary": (
        "backend/app/core/tenant.py", "backend/app/domains/platform/",
        "backend/app/api/dependencies/tenant.py",
    ),
    "kol": (
        "backend/app/domains/kol/", "backend/app/services/kol/",
        "backend/app/api/routers/kol",
    ),
    "database_migrations": ("migrations/", "backend/app/db/"),
    "workers": ("backend/app/workers/", "backend/app/domains/local_workers/"),
    "frontend_delivery": (
        "frontend/src/", "frontend/package.json", "frontend/package-lock.json",
        "frontend/vite.config.ts", "scripts/ops/atomic_release", "scripts/ops/deploy_",
    ),
}


@dataclass(frozen=True)
class Change:
    path: str
    added: int
    deleted: int

    @property
    def changed(self) -> int:
        return self.added + self.deleted


@dataclass(frozen=True)
class Commit:
    oid: str
    author_name: str
    author_email: str
    authored_at: datetime
    changes: tuple[Change, ...]
    raw_author_name: str = ""
    raw_author_email: str = ""


def _identity(commit: Commit) -> str:
    email = " ".join(commit.author_email.casefold().split())
    name = " ".join(commit.author_name.casefold().split())
    return email or f"name:{name}"


def _identity_pairs(commit: Commit) -> tuple[tuple[str, str], ...]:
    return (
        (commit.author_name, commit.author_email),
        (
            commit.raw_author_name or commit.author_name,
            commit.raw_author_email or commit.author_email,
        ),
    )


def _is_bot(commit: Commit) -> bool:
    return any(
        BOT_NAME_PATTERN.search(name)
        or BOT_LOCAL_PART_PATTERN.fullmatch(email.partition("@")[0].strip())
        for name, email in _identity_pairs(commit)
    )


def _is_shared_account(commit: Commit) -> bool:
    """Reject generic accounts even when mailmap aliases them to a person."""
    return any(
        SHARED_NAME_PATTERN.search(name)
        or SHARED_LOCAL_PART_PATTERN.fullmatch(email.partition("@")[0].strip())
        for name, email in _identity_pairs(commit)
    )


def _canonical_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _reference_list(person: dict[str, Any], name: str, minimum: int) -> list[str]:
    value = person.get(name)
    if not isinstance(value, list):
        raise ValueError(f"qualification {name} must be a list")
    normalized = [str(item).strip() for item in value]
    if any(not item for item in normalized) or len(set(normalized)) != len(normalized):
        raise ValueError(f"qualification {name} must contain unique non-empty references")
    if len(normalized) < minimum:
        raise ValueError(f"qualification {name} requires at least {minimum} references")
    return normalized


def _qualification_policy() -> dict[str, int | float]:
    return {
        "minimum_merged_pr_references": MIN_QUALIFIED_MERGED_PRS,
        "minimum_independent_review_references": MIN_QUALIFIED_INDEPENDENT_REVIEWS,
        "minimum_operational_evidence_references": MIN_QUALIFIED_OPERATIONAL_EVIDENCE,
        "minimum_qualified_changed_line_share_per_domain": QUALIFIED_CONTRIBUTION_SHARE,
    }


def _qualification_header(
    payload: dict[str, Any], *, head: str, source: str
) -> tuple[str, str, dict[str, Any]]:
    if payload.get("schema_version") != QUALIFICATION_SCHEMA_VERSION:
        raise ValueError("unsupported maintainer qualification receipt schema")
    candidate = payload.get("candidate") if isinstance(payload.get("candidate"), dict) else {}
    if candidate.get("head") != head:
        raise ValueError("maintainer qualification receipt head mismatch")
    generated_at = str(payload.get("generated_at") or "")
    if not generated_at:
        raise ValueError("maintainer qualification receipt generated_at is required")
    receipt_source = str(payload.get("source") or source or "").strip()
    if not receipt_source:
        raise ValueError("maintainer qualification receipt source is required")
    raw_domains = payload.get("domains")
    if not isinstance(raw_domains, dict):
        raise ValueError("maintainer qualification receipt domains must be an object")
    unknown_domains = set(raw_domains) - set(CORE_DOMAINS)
    if unknown_domains:
        names = ", ".join(sorted(unknown_domains))
        raise ValueError(f"unknown maintainer qualification domains: {names}")
    return generated_at, receipt_source, raw_domains


def _qualified_people(raw_domains: dict[str, Any]) -> dict[str, set[str]]:
    qualified: dict[str, set[str]] = {domain: set() for domain in CORE_DOMAINS}
    for domain, domain_payload in raw_domains.items():
        people = domain_payload.get("people") if isinstance(domain_payload, dict) else None
        if not isinstance(people, list):
            raise ValueError(f"qualification domain {domain} people must be a list")
        for person in people:
            if not isinstance(person, dict):
                raise ValueError(f"qualification domain {domain} person must be an object")
            identity = " ".join(str(person.get("identity") or "").casefold().split())
            if not identity or "@" not in identity:
                raise ValueError("qualification identity must be a canonical email")
            if identity in qualified[domain]:
                raise ValueError(f"duplicate qualification identity for {domain}")
            _reference_list(person, "merged_pr_refs", MIN_QUALIFIED_MERGED_PRS)
            _reference_list(person, "independent_review_refs", MIN_QUALIFIED_INDEPENDENT_REVIEWS)
            _reference_list(person, "operational_evidence_refs", MIN_QUALIFIED_OPERATIONAL_EVIDENCE)
            qualified[domain].add(identity)
    return qualified


def _qualification_evidence(
    payload: dict[str, Any] | None, *, head: str, source: str, timestamp_parser: Any
) -> tuple[dict[str, set[str]] | None, dict[str, Any]]:
    policy = _qualification_policy()
    if payload is None:
        return None, {
            "status": "missing", "source": source,
            "schema_version": QUALIFICATION_SCHEMA_VERSION,
            "candidate_head": head, "policy": policy,
        }
    generated_at, receipt_source, raw_domains = _qualification_header(
        payload, head=head, source=source
    )
    timestamp_parser(generated_at)
    qualified = _qualified_people(raw_domains)
    return qualified, {
        "status": "observed", "source": receipt_source,
        "schema_version": QUALIFICATION_SCHEMA_VERSION, "candidate_head": head,
        "generated_at": generated_at, "sha256": _canonical_sha256(payload), "policy": policy,
        "qualified_people_by_domain": {
            domain: len(identities) for domain, identities in qualified.items()
        },
        "qualified_identities_by_domain": {
            domain: sorted(identities) for domain, identities in qualified.items()
        },
    }


def _domains(path: str) -> tuple[str, ...]:
    return tuple(
        domain for domain, prefixes in CORE_DOMAINS.items()
        if any(path == prefix or path.startswith(prefix) for prefix in prefixes)
    )


def _eligible_source(path: str) -> bool:
    parts = Path(path).parts
    name = Path(path).name
    is_test = name.startswith("test_") or ".test." in name or ".spec." in name
    return (
        Path(path).suffix in SOURCE_SUFFIXES
        and not is_test
        and not any(part in SKIP_PARTS for part in parts)
    )


def _identity_ambiguities(commits: Iterable[Commit]) -> dict[str, list[str]]:
    by_name: dict[str, set[str]] = defaultdict(set)
    for commit in commits:
        if any(_domains(change.path) for change in commit.changes):
            name = " ".join(commit.author_name.casefold().split())
            if name:
                by_name[name].add(_identity(commit))
    return {
        name: sorted(identities) for name, identities in sorted(by_name.items())
        if len(identities) > 1
    }


def _domain_changed_lines(commits: Iterable[Commit]) -> Counter[str]:
    totals: Counter[str] = Counter()
    for commit in commits:
        for change in commit.changes:
            if _eligible_source(change.path):
                for domain in _domains(change.path):
                    totals[domain] += change.changed
    return totals


def _ranked_bus_factor(
    rows: dict[str, Counter[str]], author_labels: dict[str, dict[str, str]],
    *, threshold_denominator: int | None = None,
) -> tuple[int | None, int, list[dict[str, Any]]]:
    contribution_total = sum(row["changed"] for row in rows.values())
    denominator = threshold_denominator if threshold_denominator is not None else contribution_total
    ranked = sorted(rows.items(), key=lambda item: (-item[1]["changed"], item[0]))
    cumulative = 0
    factor = 0
    authors: list[dict[str, Any]] = []
    for identity, counts in ranked:
        cumulative += counts["changed"]
        factor = factor or len(authors) + 1
        authors.append({
            "identity": identity, **author_labels[identity], **dict(counts),
            "share": counts["changed"] / denominator if denominator else 0.0,
            "cumulative_share": cumulative / denominator if denominator else 0.0,
        })
        if denominator and cumulative / denominator < BUS_FACTOR_SHARE:
            factor = 0
    observed = bool(contribution_total and cumulative >= denominator * BUS_FACTOR_SHARE)
    return factor if observed else None, contribution_total, authors


def _author_domain_rows(
    commits: Iterable[Commit],
) -> tuple[dict[str, dict[str, Counter[str]]], dict[str, dict[str, str]]]:
    author_domain: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    labels: dict[str, dict[str, str]] = {}
    for commit in commits:
        identity = _identity(commit)
        labels[identity] = {"name": commit.author_name, "email": commit.author_email}
        touched: set[str] = set()
        for change in commit.changes:
            if not _eligible_source(change.path):
                continue
            for domain in _domains(change.path):
                touched.add(domain)
                row = author_domain[domain][identity]
                row["added"] += change.added
                row["deleted"] += change.deleted
                row["changed"] += change.changed
        for domain in touched:
            author_domain[domain][identity]["commits"] += 1
    return author_domain, labels


def _domain_bus_details(
    domain: str, rows: dict[str, Counter[str]], labels: dict[str, dict[str, str]],
    qualified_by_domain: dict[str, set[str]] | None, shared_changed: Counter[str],
) -> dict[str, Any]:
    value, total, authors = _ranked_bus_factor(rows, labels)
    qualified = qualified_by_domain.get(domain, set()) if qualified_by_domain is not None else set()
    unmatched = sorted(qualified - set(rows))
    qualified_rows = {identity: row for identity, row in rows.items() if identity in qualified}
    denominator = total + shared_changed[domain]
    q_factor, q_total, q_authors = _ranked_bus_factor(
        qualified_rows, labels, threshold_denominator=denominator
    )
    q_share = q_total / denominator if denominator else 0.0
    ready = bool(
        qualified_by_domain is not None and q_total and not unmatched
        and q_share + 1e-12 >= QUALIFIED_CONTRIBUTION_SHARE
    )
    return {
        "paths": list(CORE_DOMAINS[domain]), "changed_lines": total,
        "changed_lines_including_shared_accounts": denominator,
        "shared_account_changed_lines": shared_changed[domain],
        "contributing_author_count": len(rows), "bus_factor": value,
        "people_normalized_bus_factor": value, "threshold_share": BUS_FACTOR_SHARE,
        "minimum_contributor_set": authors[:value] if value is not None else [],
        "authors": authors, "qualified_author_count": len(qualified_rows),
        "qualified_changed_lines": q_total, "qualified_changed_line_share": q_share,
        "qualified_bus_factor": q_factor,
        "qualified_minimum_contributor_set": q_authors[:q_factor] if q_factor is not None else [],
        "qualification_ready": ready, "unmatched_qualification_identities": unmatched,
    }


def _bus_factor(
    commits: Iterable[Commit], *, qualified_by_domain: dict[str, set[str]] | None = None,
    shared_commits: Iterable[Commit] = (),
) -> tuple[dict[str, Any], int | None]:
    author_domain, labels = _author_domain_rows(commits)
    shared_changed = _domain_changed_lines(shared_commits)
    details = {
        domain: _domain_bus_details(
            domain, author_domain.get(domain, {}), labels, qualified_by_domain, shared_changed
        )
        for domain in CORE_DOMAINS
    }
    factors = [row["people_normalized_bus_factor"] for row in details.values()]
    observed = all(factor is not None for factor in factors)
    return details, min(factors) if observed else None
