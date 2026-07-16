#!/usr/bin/env python3
"""P4.2 role/scope audit smoke.

This is a structural gate, not a substitute for browser multi-account E2E. It
keeps the repo from adding unguarded admin V-KPI endpoints while P4 proceeds.
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import json

from vkpi_scope_audit import run_audit


def main() -> int:
    report = run_audit()
    summary = report["summary"]
    assert report["ok"] is True, report["routers"]["unguarded_admin_endpoints"]
    assert summary["routers_scanned"] >= 20, summary
    assert summary["admin_route_handlers"] >= 100, summary
    assert summary["unguarded_admin_endpoint_count"] == 0, summary
    stdout_out("VKPI_P4_2_SCOPE_AUDIT_SMOKE_OK", json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
