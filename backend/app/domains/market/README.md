# Market Domain

Owns external market signals, Google/RSS/Reddit source ingestion, competitor mentions, and trend classification.

Current migrated slice:

- `external_signal_smoke.py`: read-only source matrix, daily candidate plan, and bounded external signal smoke builder.
- `external_signal_reports.py`: JSON/Markdown report rendering for external signal smoke and daily plans.
- `intelligence_cards.py`: UI-safe market IntelligenceCard builders from reviewed market artifacts.
- `llm_quality.py`: deterministic quality gate for market LLM smoke outputs; does not call a model.
- `provider_preflight.py`: read-only provider readiness and LLM budget-gate checks.
- `signal_taxonomy.py`: shared Viltrox, competitor, product, and camera-ecosystem keyword groups.
- `signal_classifier.py`: read-only classifier for raw market mentions and reviewable competitor-signal candidates.
- `signal_write_package.py`: dry-run mapping from provider/review packages to raw market-signal rows.
- `signal_review_package.py`: dry-run human-review package builders for competitor and external market signals.
- `signal_review_reports.py`: JSON/Markdown report rendering for review packages and controlled writes.

Boundary:

- No provider calls from this domain slice.
- No LLM/Gemini calls.
- No promotion writes to competitor-signal tables; promotion still requires review.
- DB persistence is outside this domain and still requires a backup reference.
