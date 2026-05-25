# Data Quality Domain

Owns the read-only data trust summary used by the management data-quality page.

Initial PoC scope:

- Read current data-quality issue summary.
- Render the summary cards from domain-owned UI.
- Keep issue actions and brand-signal review in the existing legacy page until a later domain slice.

Out of scope for this PoC:

- Triggering sync, provider calls, LLM, Gemini, Apify, or migrations.
- Writing data-quality actions from the domain layer.
- Counting README or index files as business migration progress.
