#!/usr/bin/env python3
"""Which release directories must survive a cleanup — judgement only.

Why this exists
---------------
On 2026-08-25 a cleanup of 125 historical releases kept "the 5 newest +
``current`` + the rollback anchors" and deleted everything else.  That rule is
incomplete: the **live PostgreSQL clone is owned by a release**, and on that
day the owner was three weeks old (``20260804T065125Z-c9d89af320b0``, well
outside the newest five).  Deleting it made the next deploy refuse with::

    Refusing viltroxtest deploy because the remote database identity is unreadable.

``scripts/ops/deploy_local_to_cloud.sh`` resolves ``releases/<owner>`` with
``resolve(strict=True)`` and re-validates its ``.vkpi-release.json`` lineage
before it will touch the database, so the owner directory is load-bearing
forever — not merely "recent".

The live database name is ``clone_prefix + sha256(owner_release_id)[:20]``
(see ``staging_db_clone.clone_name_for_release``), which is what lets this
module *prove* an owner claim instead of trusting a receipt.

Scope — deliberately narrow
---------------------------
This module is a **pure decision function**.  It performs no filesystem
access, no database access, no subprocess, and it deletes nothing.  It answers
one question — *which release ids must be kept* — and refuses to answer at all
(``RetentionError``) whenever the inputs are ambiguous, so the fail direction
is "keep everything / stop", never "delete something".

Wiring an automatic deletion path into the deploy pipeline is explicitly out
of scope and was rejected on review; the output here is meant to be read by a
human before anything is removed.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

CLONE_PREFIX = "viltrox2_test_release_"
LEGACY_SOURCE_DATABASE = "viltrox2_test"
CLONE_NAME_RE = re.compile(r"^viltrox2_test_release_[0-9a-f]{20}$")
RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
# Ordinary deploy release directory: ``20260825T174529Z-1d2b9c6f65e7``.
PLAIN_RELEASE_ID_RE = re.compile(r"^(\d{8}T\d{6}Z)-[0-9a-f]{6,40}$")
DEFAULT_KEEP_RECENT = 5

KEEP_CURRENT = "current-pointer"
KEEP_PREVIOUS = "previous-pointer"
KEEP_RECENT = "recent-window"
KEEP_ROLLBACK_ANCHOR = "rollback-anchor"
KEEP_DATABASE_OWNER = "database-owner"
KEEP_UNRECOGNISED = "unrecognised-release-name"


class RetentionError(RuntimeError):
    """Inputs are ambiguous — no deletion plan may be derived from them."""


def clone_name_for_release(release_id: str) -> str:
    """Reproduce the deploy controller's clone name for a release id.

    Mirrors ``scripts/ops/staging_db_clone.clone_name_for_release``;
    ``tests/test_release_retention_policy.py`` pins the two together so they
    cannot drift.
    """

    if not RELEASE_ID_RE.fullmatch(release_id) or release_id in {".", ".."}:
        raise RetentionError(f"release id is not a safe directory name: {release_id!r}")
    digest = hashlib.sha256(release_id.encode("utf-8")).hexdigest()[:20]
    return f"{CLONE_PREFIX}{digest}"


@dataclass(frozen=True)
class OwnerClaim:
    """One rollback receipt that claims to own the live database."""

    release_id: str
    rollback_directory: str
    state: str
    database_strategy: str
    source_database: str


def owner_claims(
    clone_receipts: Mapping[str, Mapping[str, object]],
    live_database_name: str,
) -> tuple[OwnerClaim, ...]:
    """Return every receipt whose target database is the live one.

    A receipt only counts when the digest of its ``release_id`` reproduces
    ``live_database_name``.  A receipt that names the live database but whose
    release id hashes to something else is a corrupted or transplanted record
    and stops the whole computation rather than being silently ignored.
    """

    claims: list[OwnerClaim] = []
    for directory, payload in sorted(clone_receipts.items()):
        if not isinstance(payload, Mapping):
            raise RetentionError(f"rollback receipt is not an object: {directory!r}")
        if str(payload.get("target_database") or "") != live_database_name:
            continue
        release_id = str(payload.get("release_id") or "")
        if not release_id:
            raise RetentionError(
                f"rollback receipt {directory!r} claims the live database "
                "without naming a release"
            )
        if clone_name_for_release(release_id) != live_database_name:
            raise RetentionError(
                f"rollback receipt {directory!r} names release {release_id!r} but "
                f"sha256({release_id!r})[:20] does not produce {live_database_name!r}"
            )
        if directory != release_id:
            raise RetentionError(
                f"rollback capture directory {directory!r} does not match the "
                f"release it records ({release_id!r}); the deploy controller "
                "resolves rollbacks/<release_id> and would not find it"
            )
        claims.append(
            OwnerClaim(
                release_id=release_id,
                rollback_directory=directory,
                state=str(payload.get("state") or ""),
                database_strategy=str(payload.get("database_strategy") or ""),
                source_database=str(payload.get("source_database") or ""),
            )
        )
    return tuple(claims)


def database_owner_release_id(
    clone_receipts: Mapping[str, Mapping[str, object]],
    live_database_name: str,
) -> str:
    """Return the release that owns ``live_database_name``.

    ``""`` means the host is still on the legacy base database and no release
    owns it.  Anything ambiguous raises: a cleanup that cannot name the owner
    must not proceed.
    """

    live = str(live_database_name or "").strip()
    if live == LEGACY_SOURCE_DATABASE:
        return ""
    if not CLONE_NAME_RE.fullmatch(live):
        raise RetentionError(
            f"live database name is not a reviewed release clone: {live!r}"
        )
    claims = owner_claims(clone_receipts, live)
    if not claims:
        raise RetentionError(
            f"no rollback receipt claims {live!r}; the database owner cannot be "
            "proven, so nothing may be deleted"
        )
    distinct = {claim.release_id for claim in claims}
    if len(distinct) != 1:
        raise RetentionError(
            f"{len(distinct)} releases claim {live!r}: {sorted(distinct)}"
        )
    claim = claims[0]
    if claim.database_strategy != "staging-clone":
        raise RetentionError(
            f"owner receipt {claim.release_id!r} is not a staging-clone receipt "
            f"({claim.database_strategy!r}); deploy re-validates this field"
        )
    if claim.state != "activated":
        raise RetentionError(
            f"owner receipt {claim.release_id!r} is in state {claim.state!r}, "
            "not 'activated'; deploy re-validates this field"
        )
    return claim.release_id


@dataclass(frozen=True)
class RetentionInputs:
    """Everything the decision needs, already read from disk by the caller."""

    release_ids: Sequence[str]
    current_release_id: str
    live_database_name: str
    previous_release_id: str = ""
    rollback_anchor_release_ids: Sequence[str] = ()
    # Empty is not "no owner": it makes the owner unprovable, which stops the
    # computation.  Silence is never read as permission to delete.
    clone_receipts: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    keep_recent: int = DEFAULT_KEEP_RECENT


@dataclass(frozen=True)
class RetentionPlan:
    """Which releases must be kept, why, and which are merely unreferenced."""

    keep: frozenset[str]
    deletable: tuple[str, ...]
    database_owner_release_id: str
    reasons: Mapping[str, tuple[str, ...]]

    def why(self, release_id: str) -> tuple[str, ...]:
        return tuple(self.reasons.get(release_id, ()))


def _release_sort_key(release_id: str) -> str:
    match = PLAIN_RELEASE_ID_RE.match(release_id)
    if match is None:  # pragma: no cover - callers filter first
        raise RetentionError(f"not a plain release id: {release_id!r}")
    return match.group(1)


def compute_retention(inputs: RetentionInputs) -> RetentionPlan:
    """Return the keep/deletable split, or raise rather than guess.

    Everything not positively classified as an ordinary dated release stays in
    ``keep`` under ``unrecognised-release-name`` — rescue anchors, legacy
    snapshots and anything a future deploy invents are never proposed for
    deletion by this policy.
    """

    releases = list(inputs.release_ids)
    duplicates = {name for name in releases if releases.count(name) > 1}
    if duplicates:
        raise RetentionError(f"release list contains duplicates: {sorted(duplicates)}")
    known = set(releases)
    if not known:
        raise RetentionError("release list is empty")
    if inputs.keep_recent < 1:
        raise RetentionError("keep_recent must be at least 1")

    current = str(inputs.current_release_id or "").strip()
    if not current:
        raise RetentionError("current release pointer is unset")
    if current not in known:
        raise RetentionError(
            f"current pointer {current!r} is not among the release directories"
        )

    reasons: dict[str, set[str]] = {}

    def note(release_id: str, reason: str) -> None:
        reasons.setdefault(release_id, set()).add(reason)

    note(current, KEEP_CURRENT)

    previous = str(inputs.previous_release_id or "").strip()
    if previous:
        if previous not in known:
            raise RetentionError(
                f"previous pointer {previous!r} is dangling; rollback is already "
                "broken and no deletion plan may be derived"
            )
        note(previous, KEEP_PREVIOUS)

    for anchor in inputs.rollback_anchor_release_ids:
        anchor_id = str(anchor or "").strip()
        if not anchor_id:
            continue
        if anchor_id not in known:
            raise RetentionError(
                f"rollback anchor {anchor_id!r} is dangling; rollback is already "
                "broken and no deletion plan may be derived"
            )
        note(anchor_id, KEEP_ROLLBACK_ANCHOR)

    plain = sorted(
        (name for name in releases if PLAIN_RELEASE_ID_RE.match(name)),
        key=_release_sort_key,
        reverse=True,
    )
    for name in releases:
        if not PLAIN_RELEASE_ID_RE.match(name):
            note(name, KEEP_UNRECOGNISED)
    for name in plain[: inputs.keep_recent]:
        note(name, KEEP_RECENT)

    owner = database_owner_release_id(inputs.clone_receipts, inputs.live_database_name)
    if owner:
        if owner not in known:
            raise RetentionError(
                f"database owner release {owner!r} is already missing from "
                f"releases/; the deploy controller resolves releases/{owner} "
                "strictly and will refuse to deploy until it is restored"
            )
        note(owner, KEEP_DATABASE_OWNER)

    keep = frozenset(reasons)
    deletable = tuple(sorted(name for name in releases if name not in keep))
    return RetentionPlan(
        keep=keep,
        deletable=deletable,
        database_owner_release_id=owner,
        reasons={name: tuple(sorted(values)) for name, values in sorted(reasons.items())},
    )


def describe(plan: RetentionPlan) -> str:
    """Render a human-readable plan; this is the only intended "output"."""

    lines = [
        f"database owner release: {plan.database_owner_release_id or '(legacy base, none)'}",
        f"keep ({len(plan.keep)}):",
    ]
    for release_id, why in plan.reasons.items():
        lines.append(f"  {release_id}  <- {', '.join(why)}")
    lines.append(f"unreferenced ({len(plan.deletable)}):")
    lines.extend(f"  {release_id}" for release_id in plan.deletable)
    lines.append(
        "This module decides nothing else. Removing anything is a reviewed, "
        "manual step."
    )
    return "\n".join(lines)


__all__ = [
    "CLONE_NAME_RE",
    "CLONE_PREFIX",
    "DEFAULT_KEEP_RECENT",
    "KEEP_CURRENT",
    "KEEP_DATABASE_OWNER",
    "KEEP_PREVIOUS",
    "KEEP_RECENT",
    "KEEP_ROLLBACK_ANCHOR",
    "KEEP_UNRECOGNISED",
    "LEGACY_SOURCE_DATABASE",
    "OwnerClaim",
    "RetentionError",
    "RetentionInputs",
    "RetentionPlan",
    "clone_name_for_release",
    "compute_retention",
    "database_owner_release_id",
    "describe",
    "owner_claims",
]
