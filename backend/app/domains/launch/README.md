# Launch Domain

Owns pure launch-planning and acceptance-estimation rules.

Current migrated slice:

- `acceptance.py`: rule-based new-launch acceptance scoring from a product campaign card and trend report.

Boundary:

- No database reads.
- No provider calls.
- No LLM/Gemini calls.
- No project creation, outreach, sync, or task enqueue.
- Service facades gather input reports and pass them into this domain.
