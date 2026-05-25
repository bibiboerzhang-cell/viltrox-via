# Settings Domain

Owns account/profile settings, permissions, budget controls, and rules panels.

Current migrated slice: shared settings formatting and guard helpers used by the Settings page. Platform crawl and budget read APIs are exposed through `api.ts`; provider probes, budget writes, and staff permission writes still stay behind existing API/domain facades.
