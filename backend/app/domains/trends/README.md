# Trends Domain

Owns pure trend detection rules over existing official-channel metrics and reviewed market-signal rows.

Current migrated slice:

- `trend_detection.py`: read-only rule engine for post deltas, channel deltas, and market event bursts.

Boundary:

- No database reads.
- No provider calls.
- No LLM/Gemini calls.
- No sync or task enqueue.
- Service facades load rows and pass them into this domain.
