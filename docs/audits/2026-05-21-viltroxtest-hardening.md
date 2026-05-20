# viltroxtest Hardening Audit

Date: 2026-05-21

## Scope

Public test deployment hardening for `https://viltroxtest.com`.

This package does not change business data, provider calls, Apify runs, or KOL sync jobs.

## Code Commit

- `1b18fde fix(security): keep dev CSP origins off public hosts`

## Remote Config Changes

Backups were created before editing:

- `/opt/viltrox-2.0/runtime/security-backups/nginx-sites-enabled-viltroxtest-pre-forwarded-proto-20260520T205201Z.conf`
- `/opt/viltrox-2.0/runtime/security-backups/nginx-sites-available-viltroxtest-pre-forwarded-proto-20260520T205201Z.conf`
- `/opt/viltrox-2.0/runtime/env-backups/env-pre-trusted-origin-20260520T205201Z.env`

Nginx changes:

- Preserve Cloudflare `X-Forwarded-Proto` instead of overwriting it with nginx `$scheme`.
- Redirect Cloudflare plain HTTP requests to HTTPS when `X-Forwarded-Proto=http`.
- Forward `X-Forwarded-Host`.

Environment changes:

- Added `https://viltroxtest.com` and `https://www.viltroxtest.com` to `ADMIN_TRUSTED_ORIGINS`.

## Verification

Build/health:

- `/health` git short SHA: `1b18fdee`
- `client_matches_server=true`
- `viltrox-2.0-test.service=active/running`
- service restart timestamp: `2026-05-20 20:52:02 UTC`

Headers:

- HTTP `http://viltroxtest.com/` returns `301 Moved Permanently`.
- HTTPS `/health` returns `200`.
- CSP is public-safe:
  - `connect-src 'self'`
  - no `localhost`
  - no `127.0.0.1`
  - no `ws://`
- HSTS is present:
  - `strict-transport-security: max-age=31536000; includeSubDomains`

Same-origin policy:

- Cookie-auth mutating admin request with `Origin: https://viltroxtest.com` passed the origin guard and reached the business handler.
- Cookie-auth mutating admin request with `Origin: https://evil.example` was blocked:
  - `403 {"detail":"Blocked by admin same-origin policy"}`

Dashboard smoke after hardening:

- Dashboard API: `200`, 10 metrics, metric run ready.
- KOL pool summary: `1023` KOL, 30 country buckets.
- Official matrix: 18 accounts, 12,209 posts, 368,323,010 views, 6 platforms.
- Competitor dashboard: 6 rows.
- Brand signals: 10 signals returned.

## Remaining Notes

- `REDIS_URL` is not configured on the test deployment, so the rate limiter currently uses in-process memory. Do not treat this as shared multi-process/global rate limiting until Redis is enabled.
- The local git checkout still has no configured remote, so commits are local-only unless a remote is added.
