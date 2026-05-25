# Data Quality Domain

Owns read-only data trust summaries, baseline/delta quality surfaces, and sync-guard visibility.

Initial PoC scope:

- Route the existing data-quality issue summary through a domain facade.
- Do not run sync, provider calls, migrations, LLM, Gemini, or Apify.
- Leave write/remediation actions in the legacy service until the write boundary is explicitly migrated.
