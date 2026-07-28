"""Command-line parser for the atomic release filesystem transaction."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping


Action = Callable[[argparse.Namespace], None]


def build_parser(actions: Mapping[str, Action]) -> argparse.ArgumentParser:
    """Build the CLI while keeping transaction implementations in the caller."""
    required = {
        "seal",
        "verify_seal",
        "worker_layout_preflight",
        "worker_runtime_preflight",
        "prepare",
        "activate",
        "rollback_unit_state",
        "inspect_unit_state",
        "restore",
    }
    if set(actions) != required:
        missing = sorted(required.difference(actions))
        unexpected = sorted(set(actions).difference(required))
        raise ValueError(
            f"atomic release CLI action mismatch: missing={missing}, unexpected={unexpected}"
        )

    result = argparse.ArgumentParser()
    subparsers = result.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", required=True)
    common.add_argument("--release-id", required=True)

    seal_parser = subparsers.add_parser("seal", parents=[common])
    seal_parser.add_argument("--git-sha", required=True)
    seal_parser.add_argument("--pending-migrations", default="")
    seal_parser.add_argument("--compatibility-declaration", default="")
    seal_parser.add_argument("--database-strategy", default="in-place")
    seal_parser.add_argument("--source-database", default="")
    seal_parser.add_argument("--target-database", default="")
    seal_parser.add_argument("--env-fingerprint-before", default="")
    seal_parser.add_argument("--database-owner-release-id", default="")
    seal_parser.add_argument("--owner-uid", type=int)
    seal_parser.add_argument("--owner-gid", type=int)
    seal_parser.set_defaults(action=actions["seal"])

    verify_seal_parser = subparsers.add_parser("verify-seal", parents=[common])
    verify_seal_parser.add_argument("--expected-owner-uid", type=int)
    verify_seal_parser.add_argument("--expected-owner-gid", type=int)
    verify_seal_parser.set_defaults(action=actions["verify_seal"])

    layout_parser = subparsers.add_parser("worker-layout-preflight", parents=[common])
    layout_parser.add_argument("--app-user", required=True)
    layout_parser.add_argument("--app-group", required=True)
    layout_parser.add_argument("--provision-missing", action="store_true")
    layout_parser.set_defaults(action=actions["worker_layout_preflight"])

    runtime_parser = subparsers.add_parser("worker-runtime-preflight")
    runtime_parser.add_argument("--root", required=True)
    runtime_parser.add_argument("--release-path", required=True)
    runtime_parser.add_argument("--app-user", required=True)
    runtime_parser.add_argument("--app-group", required=True)
    runtime_parser.add_argument("--job-results-dir", default="")
    runtime_parser.add_argument("--require-sandbox-readonly", action="store_true")
    runtime_parser.set_defaults(action=actions["worker_runtime_preflight"])

    prepare_parser = subparsers.add_parser("prepare", parents=[common])
    prepare_parser.add_argument("--unit-dir", required=True)
    prepare_parser.add_argument("--unit-name", action="append", default=[])
    prepare_parser.add_argument("--optional-unit-name", action="append", default=[])
    prepare_parser.add_argument("--optional-unit-state", action="append", default=[])
    prepare_parser.add_argument("--pending-migrations", default="")
    prepare_parser.add_argument("--compatibility-declaration", default="")
    prepare_parser.add_argument("--database-strategy", default="in-place")
    prepare_parser.add_argument("--source-database", default="")
    prepare_parser.add_argument("--target-database", default="")
    prepare_parser.add_argument("--env-fingerprint-before", default="")
    prepare_parser.add_argument("--database-owner-release-id", default="")
    prepare_parser.add_argument("--rollback-anchor-release-id", default="")
    prepare_parser.set_defaults(action=actions["prepare"])

    activate_parser = subparsers.add_parser("activate", parents=[common])
    activate_parser.set_defaults(action=actions["activate"])

    state_parser = subparsers.add_parser("rollback-unit-state", parents=[common])
    state_parser.add_argument("--unit-name", required=True)
    state_parser.set_defaults(action=actions["rollback_unit_state"])

    inspect_parser = subparsers.add_parser("inspect-unit-state")
    inspect_parser.add_argument("--unit-dir", required=True)
    inspect_parser.add_argument("--unit-name", required=True)
    inspect_parser.add_argument("--systemctl-bin", default="/usr/bin/systemctl")
    inspect_parser.set_defaults(action=actions["inspect_unit_state"])

    restore_parser = subparsers.add_parser("restore", parents=[common])
    restore_parser.add_argument("--unit-dir", required=True)
    restore_parser.set_defaults(action=actions["restore"])
    return result
