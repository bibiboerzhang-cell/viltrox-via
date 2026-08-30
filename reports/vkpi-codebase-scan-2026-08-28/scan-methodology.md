# V-KPI full-worktree scan methodology

Generated: 2026-08-29T02:39:56Z

## Snapshot identity

- Repository: current V-KPI worktree
- Branch: `codex/optimization-v1-local`
- HEAD: `7c2c5837af71`
- Dirty state at final evidence refresh: 53 modified/staged status entries and 33 untracked status entries
- Analysis mode: read-only code, AST, import-graph, test-contract, and production-snapshot inspection. Report files are the only files created by this audit.

## Scope

The main-engineering scope includes tracked and relevant untracked source, tests, migrations, scripts, styles, schemas, and configuration. It excludes dependencies, build output, caches, runtime output, generated reports, media, uploads, exports, backups, and temporary files. The all-workspace scope additionally reports ignored `.integration` and `video-production-platform` auxiliary prototypes separately.

Counts use physical lines (`splitlines()`), UTF-8 Unicode code points for characters, disk bytes, and Unicode word-like tokens as a lexical proxy. Whole-line comments are conservatively classified; Python docstrings and inline-comment lines remain code. The executable/test/style/schema scope excludes JSON/YAML and other configuration/data assets.

## Scale results

- Main engineering scope: 4,699 files; 1,142,341 physical lines; 117,675 blank lines; 27,096 whole-line comments; 997,570 code/config lines; 44,775,674 UTF-8 characters; 46,778,865 bytes; 4,342,254 word-like tokens.
- Executable/test/style/schema scope: 4,572 files; 1,092,211 lines; 42,906,904 UTF-8 characters; 44,899,213 bytes; 3,746,417 ASCII identifier tokens.
- All-workspace scope including ignored auxiliary prototypes: 4,714 files; 1,148,592 lines; 44,939,564 UTF-8 characters; 46,951,611 bytes. The ignored auxiliary delta is 15 files and 6,251 lines.
- Tests: 1,039 files and 271,776 lines in the main-engineering scope. Test volume is not coverage or business-quality proof.

## Static structure and algorithm results

- Backend product graph: 1,550 modules, 5,193 resolved internal-import edges, 29 cyclic strongly connected components, 111 modules participating in a cycle.
- Frontend relative-import graph: 845 modules, 2,319 edges, 6 cyclic strongly connected components, 28 modules participating in a cycle.
- Backend production concurrency syntax: 1,215 `await`, 21 `asyncio.gather`, 22 `create_task`, 12 semaphore sites, 342 `to_thread`, and 52 loops containing `await`.
- Frontend production concurrency syntax: 809 `await`, 31 `Promise.all`, and 19 `Promise.allSettled` sites.
- Algorithm-family audit: 44 families grouped as 18 deterministic, 11 heuristic/rule, 7 model/LLM/trainable, and 8 concurrency/scheduling/control families.
- Backend product Python AST: 13,878 functions; 1,559 have branch-complexity proxy at least 15; 374 at least 30; 115 at least 50; 18 at least 100. The proxy is `1 +` decision, boolean-branch, and comprehension nodes, not a formal cyclomatic metric.

## Learning-contract verification

Command:

```shell
.venv/bin/pytest -q -rs tests/test_gemini_final_v1_quality_eval.py tests/test_jobs_registry_learning_closeout.py tests/test_kol_search_relevance_eval.py tests/test_recommendation_outcomes_learning.py tests/test_recommendation_outcomes_pg.py tests/test_recommendation_sentiment_llm_production_boundary.py tests/test_replay_final_v1_quality_gate.py tests/test_rerank_holdout_and_offline_eval.py tests/test_training_export_point_in_time.py tests/test_vertical_multi_route_signals.py tests/test_vertical_scoring_training_truth.py tests/test_vkpi_learning_signals_truth.py tests/test_vkpi_recommendation_agent_v0.py
```

Result: 163 passed, 6 skipped, 1 warning in 3.96 seconds. The six skips are PostgreSQL/live-service tests disabled by default; the warning is a Google GenAI Python 3.14 deprecation warning. This proves local contracts only, not live model quality or business outcomes.

## Production learning evidence

The latest inspected production-sync dump is `runtime/prod-sync/20260828T173053Z/prod-db.dump` (344,766,339 bytes). Counts were obtained read-only from the dump. Key facts: 9,896 KOL recommendations, 9,992 outcome rows, only 2 shortlisted, 4 claimed, 4 recommendation-feedback rows, 1,000 rerank feature snapshots with no labels and `arm=off`, one inactive rerank-model row with sample count 0, zero training exports, zero GTM outcomes, zero experiments and bandit arms, and 50 skill runs with zero skill reviews. The weekly evaluation program ran, but the rerank holdout had zero usable samples; a passing job status is not an algorithm-quality pass.

## Boundaries

- No full test suite, code coverage, mutation testing, load test, cloud deployment, or browser acceptance was run in this audit.
- Syntactic concurrency sites and import cycles are static proxies; each candidate needs runtime profiling and ownership review before refactoring.
- Production snapshot facts are a point-in-time read-only observation, not proof of current live behavior after that snapshot.
