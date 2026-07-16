# V-KPI Stage-1 exact-model canary — 2026-07-15

## Scope and claim boundary

This run exercised the 13 current task bindings, deduplicated to 8 exact
provider/model bindings. It was a plan-bound, budget-reserved Stage-1
connectivity canary. It is **descriptive-only** and does not grant production
authorization.

- Provider credentials configured: OpenAI, Google, Anthropic.
- Required budget scopes configured and allowed.
- Original 8-binding planning ceiling: `$0.003659`.
- Pre-accounting-fix successful-call cost recorded in `vkpi_llm_calls`:
  `$0.002506` (not a provider invoice total).
- Exact bindings with a successful provider response: `8/8`.
- Production-ready bindings: `0/8`.

Production readiness remains blocked because the code-reviewed exact-probe and
evaluation trust roots are empty and there is no independently signed 30-case
actual evaluation artifact for any binding. Runtime code must not generate or
store those private signing keys.

## Exact response observations

| Requested binding | Observed response model | Result |
| --- | --- | --- |
| `anthropic/claude-haiku-4-5-20251001` | `claude-haiku-4-5-20251001` | success |
| `anthropic/claude-opus-4-7` | `claude-opus-4-7` | success |
| `anthropic/claude-sonnet-4-6` | `claude-sonnet-4-6` | success |
| `google/gemini-2.5-flash` | `gemini-2.5-flash` | success |
| `google/gemini-2.5-pro` | `gemini-2.5-pro` | success after transport fix |
| `google/gemini-3.5-flash` | `gemini-3.5-flash` | success after transport fix |
| `openai/gpt-5.4-mini` | `gpt-5.4-mini-2026-03-17` | success, reviewed alias |
| `openai/gpt-5.5` | `gpt-5.5-2026-04-23` | success after transport fix, reviewed alias |

The canary stored response digests and usage only. It did not persist or print
prompt/response content or provider credentials.

## Defects found and fixed

1. The Google adapter forced `thinkingBudget=0` for every Gemini model.
   Gemini 2.5 Pro rejects that configuration, and Gemini 3 uses
   `thinkingLevel`. The adapter now uses:
   - Gemini 2.5 Pro: `thinkingBudget=128`;
   - Gemini 3: `thinkingLevel=minimal`;
   - Gemini 2.5 Flash and compatible low-cost extractors: `thinkingBudget=0`.
2. The OpenAI adapter sent `temperature=0.2` to every exact model. The
   account-visible `gpt-5.5` Responses route rejects that optional sampling
   parameter. GPT-5 requests now omit `temperature`; non-GPT-5 behavior is
   unchanged.
3. The Stage-1 CLI could only retry the prefix of the model inventory. It now
   accepts repeatable `--binding` selectors, rejects unknown/duplicate values
   before I/O, and binds the exact subset into the authorization hash. This
   allowed failed bindings to be retried without paying for successful models
   again.
4. Gemini returns billable `thoughtsTokenCount` separately from visible
   `candidatesTokenCount`. The gateway previously priced only the visible
   output. It now records both fields and uses their sum as billable
   `output_tokens`. The `$0.002506` figure above is the ledger value captured
   before this accounting correction and must not be presented as a provider
   invoice total.

Google's current model contract is documented at
<https://ai.google.dev/gemini-api/docs/generate-content/thinking>.

## Audit artifacts

- `runtime/ops/llm-readiness-20260715/runtime-preflight.json`
- `runtime/ops/llm-readiness-20260715/budget-acceptance.json`
- `runtime/ops/llm-readiness-20260715/model-evidence-plan.json`
- `runtime/ops/llm-readiness-20260715/stage1-canary-live.json`
- `runtime/ops/llm-readiness-20260715/stage1-canary-gemini25pro-live.json`
- `runtime/ops/llm-readiness-20260715/stage1-canary-remaining-live.json`
- `runtime/ops/llm-readiness-20260715/stage1-canary-gpt55-live.json`

## Remaining production gate

The generated evaluation manifest requires 30 actual cases per exact binding:
240 minimum provider generations, or 248 as a conservative probe-plus-eval
ceiling. Its current text-only estimate is `$2.7063`. Execution requires a
separate plan-bound operator budget approval, a reviewed dataset, and two
independent offline signer roles. Until those exist, the correct runtime state
is `readiness_not_production_ready`; AI-off/rule fallback remains enabled.

Two initial HTTP-400 attempts are conservatively retained as `unknown` budget
reservations (combined estimate `$0.00143`) even though their call ledger cost
is zero. They must be reconciled against provider billing evidence rather than
silently released.
