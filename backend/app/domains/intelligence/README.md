# Intelligence Domain

Owns intelligence cards, evidence, actions, feedback, prediction display, and agent output contracts.

Provider calls and raw ingestion should enter through platform/provider and source domains first.

Current migrated slice:

- `today_signals.py`: pure daily signal digest and action-item builder from trend, market, and cached-comment rows.
- `weekly_plan.py`: pure weekly action-plan builder from launch acceptance and today signal reports.
