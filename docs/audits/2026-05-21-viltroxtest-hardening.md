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

## 2026-05-20 22:27 UTC Follow-Up: Origin Shield

Purpose:

- Keep `viltroxtest.com` public through Cloudflare while blocking direct source-IP access to the Hetzner origin.
- Avoid touching SSH, business data, provider calls, Apify, KOL sync, or LLM settings.

Backups created before mutation:

- `/root/vkpi-hardening-backups/20260520T222617Z/nginx.conf`
- `/root/vkpi-hardening-backups/20260520T222617Z/viltroxtest.com`
- `/root/vkpi-hardening-backups/20260520T222617Z/ufw.tgz`
- `/root/vkpi-hardening-backups/20260520T222617Z/ufw-status-before.txt`
- `/root/vkpi-hardening-backups/20260520T222617Z/ufw-status-after.txt`
- `/root/vkpi-hardening-backups/20260520T222617Z/cloudflare-ips-v4.txt`
- `/root/vkpi-hardening-backups/20260520T222617Z/cloudflare-ips-v6.txt`

Remote config changes:

- Enabled `server_tokens off;` in `/etc/nginx/nginx.conf`.
- Added UFW allow rules for the official Cloudflare IPv4 and IPv6 ranges on ports `80/tcp` and `443/tcp`.
- Removed broad `80/tcp ALLOW IN Anywhere` and `443/tcp ALLOW IN Anywhere` rules.
- Kept `22/tcp` SSH access unchanged.

Verification:

- `https://viltroxtest.com/health` returned `HTTP/2 200` through Cloudflare.
- `/health` still reports `git_short_sha=99bb554e` and `client_matches_server=true`.
- Direct source-IP probe was blocked:
  - `curl http://5.78.200.75/health -H 'Host: viltroxtest.com'`
  - result: timeout, `direct-origin-blocked`
- Local origin loopback still works for Nginx-to-app routing:
  - `curl http://127.0.0.1/health -H 'Host: viltroxtest.com'`
  - result: `HTTP/1.1 200 OK`
- Nginx no longer exposes package version on origin loopback:
  - before: `Server: nginx/1.24.0 (Ubuntu)`
  - after: `Server: nginx`

Current UFW shape after change:

- `22/tcp` allowed from anywhere.
- `80/tcp` and `443/tcp` allowed only from Cloudflare ranges.
- Default incoming policy remains `deny`.

Operational note:

- If Cloudflare proxying is disabled or the DNS record is switched to DNS-only, the site will stop responding publicly until broad 80/443 access is restored or a new origin access path is configured.
