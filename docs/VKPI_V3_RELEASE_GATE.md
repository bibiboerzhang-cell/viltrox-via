# V-KPI v3 Release Gate

This document records the v3 closure gate only. Amazon attribution follow-up work is intentionally excluded from v3 and belongs to v4.

## Scope

- A. AI Weekly Summary through Claude when configured, with deterministic fallback.
- C1. User preferences storage and scope rules.
- C2. Notification settings storage-only path. No outbound email, Slack, WeChat, or in-app sending is enabled by v3.
- C3. Settings page integration for preferences and notification settings.
- Other UI adjustments for the settings switches and platform crawl cards.

## Command

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing
.venv/bin/python scripts/smoke_vkpi_v3_release_gate.py
```

Use `--skip-build` only when a separate frontend build has already passed in the same round.

## Required Result

- The gate exits with code `0`.
- The JSON output contains `"ok": true`.
- Every step in `results[]` has `"ok": true`.
- The individual smokes clean their own marker-scoped test data.

## Non-Scope

- Amazon refund rollback, ASIN reports, CSV scheduled import, SP-API, and related flows are v4 scope.
- LLM async jobs and advanced notification delivery are v4 scope.
