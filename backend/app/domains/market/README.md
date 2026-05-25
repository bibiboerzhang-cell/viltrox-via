# Market Domain

Owns external market signals, Google/RSS/Reddit source ingestion, competitor mentions, and trend classification.

Current migrated slice:

- `signal_taxonomy.py`: shared Viltrox, competitor, product, and camera-ecosystem keyword groups.
- `signal_classifier.py`: read-only classifier for raw market mentions and reviewable competitor-signal candidates.

Boundary:

- No provider calls from this domain slice.
- No LLM/Gemini calls.
- No promotion writes to competitor-signal tables; promotion still requires review.
