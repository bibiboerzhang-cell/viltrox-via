#!/usr/bin/env bash
# 模型升级刀 · final_v1 三模型对照评测编排(只打隔离库 54333,绝不碰 prod)。
#
# 每个候选模型一条独立流水线(DB 克隆 → 停用旧队列/标 stale → 入队 → 单 worker 排空 →
# 导出 predictions → (可选)gold 评分 → 时延剖面),最后汇总为「契约有效率 + 与基线一致率」
# JSON(没有真 gold 就不造 gold;--gold 给了才跑 scripts/eval_gemini_final_v1_quality.py)。
#
# 用法:
#   bash scripts/ops/model_upgrade_eval_3way.sh --evidence-ids /path/eval_evidence_ids.txt [选项]
# 选项:
#   --n N                 取 evidence id 文件前 N 条(默认 30)
#   --models "a b c"      候选模型(默认 "gemini-2.5-flash gemini-3.5-flash-lite gemini-3.6-flash")
#   --baseline ID         基线模型(默认 gemini-2.5-flash;必须在 --models 内)
#   --qa-model ID         关键帧裁判模型 env GEMINI_FINAL_V1_QA_MODEL(默认 gemini-3.5-flash-lite)
#   --pg-url BASE         PG 基址(默认 postgresql://postgres@127.0.0.1:54333)
#   --template-db NAME    克隆模板库(默认 vkpi_closeout)
#   --budget-usd N        评测 env 的 LLM_MONTHLY_BUDGET_USD(默认 2000;克隆库里本月已记账也算在内)
#   --out DIR             产物目录(默认 runtime/model_upgrade_eval/<UTC 时间戳>)
#   --gold FILE           真 gold manifest(有则额外跑 eval_gemini_final_v1_quality.py)
#   --worker-timeout-sec  单模型排空上限(默认 7200)
#   --reuse-db            库已存在则复用(跳过 createdb;仍会标 stale + 停旧队列)
#   --keep-db             结束后不 DROP 克隆库(默认保留;--drop-db 删除)
#   --drop-db             每个模型跑完后 DROP 克隆库
#   --dry-run             只打印计划,不建库、不入队、不起 worker、零 LLM 成本
#   --stop-after enqueue  建库+标 stale+入队后停(零 LLM 成本的冒烟;worker/导出不跑)
#
# 依赖(别车道):C 车道 core/gemini_models.py(worker 与入队同一精确模型)、B 车道 3.x 家族
# thinking_level=minimal 映射(否则 3.5/3.6 每次 400)、A 车道注册表/定价(3.6-flash /
# 3.5-flash-lite 登记)。本脚本自身只做编排,每步失败即停(set -euo pipefail)。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="$ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "refusing to run without the project interpreter: $PY" >&2
  exit 2
fi

EVIDENCE_FILE=""
N=30
MODELS="gemini-2.5-flash gemini-3.5-flash-lite gemini-3.6-flash"
BASELINE="gemini-2.5-flash"
QA_MODEL="gemini-3.5-flash-lite"
PG_URL="postgresql://postgres@127.0.0.1:54333"
TEMPLATE_DB="vkpi_closeout"
BUDGET_USD="2000"
OUT_DIR=""
GOLD_FILE=""
WORKER_TIMEOUT_SEC=7200
POLL_SEC=15
REUSE_DB=0
DROP_DB=0
DRY_RUN=0
STOP_AFTER=""
ACTOR_STAFF_ID="${ACTOR_STAFF_ID:-auto}"   # owner/manager staff id used to build the paid-action fence (auto = first is_owner=1)
DATASET_ID="model-upgrade-2026-08"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --evidence-ids) EVIDENCE_FILE="$2"; shift 2 ;;
    --n) N="$2"; shift 2 ;;
    --models) MODELS="$2"; shift 2 ;;
    --baseline) BASELINE="$2"; shift 2 ;;
    --qa-model) QA_MODEL="$2"; shift 2 ;;
    --pg-url) PG_URL="$2"; shift 2 ;;
    --template-db) TEMPLATE_DB="$2"; shift 2 ;;
    --budget-usd) BUDGET_USD="$2"; shift 2 ;;
    --out) OUT_DIR="$2"; shift 2 ;;
    --gold) GOLD_FILE="$2"; shift 2 ;;
    --worker-timeout-sec) WORKER_TIMEOUT_SEC="$2"; shift 2 ;;
    --dataset-id) DATASET_ID="$2"; shift 2 ;;
    --reuse-db) REUSE_DB=1; shift ;;
    --keep-db) DROP_DB=0; shift ;;
    --drop-db) DROP_DB=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --stop-after) STOP_AFTER="$2"; shift 2 ;;
    --actor-staff-id) ACTOR_STAFF_ID="$2"; shift 2 ;;
    -h|--help) sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$EVIDENCE_FILE" && -f "$EVIDENCE_FILE" ]] || { echo "--evidence-ids FILE required" >&2; exit 2; }
[[ "$N" =~ ^[1-9][0-9]*$ ]] || { echo "--n must be a positive integer" >&2; exit 2; }
[[ " $MODELS " == *" $BASELINE "* ]] || { echo "--baseline $BASELINE must be one of --models ($MODELS)" >&2; exit 2; }
for model in $MODELS; do
  case "$model" in
    gemini-3.7*|*-latest|*preview*) echo "forbidden runtime model in --models: $model" >&2; exit 2 ;;
  esac
done
case "$STOP_AFTER" in ""|enqueue) ;; *) echo "--stop-after accepts: enqueue" >&2; exit 2 ;; esac
if [[ -n "$GOLD_FILE" && ! -f "$GOLD_FILE" ]]; then
  echo "--gold file not found: $GOLD_FILE" >&2; exit 2
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${OUT_DIR:-$ROOT/runtime/model_upgrade_eval/$STAMP}"
case "$OUT_DIR" in *[[:space:]]*) echo "--out must not contain whitespace: $OUT_DIR" >&2; exit 2 ;; esac
mkdir -p "$OUT_DIR"
IDS_FILE="$OUT_DIR/evidence_ids.txt"
grep -vE '^\s*(#|$)' "$EVIDENCE_FILE" | head -n "$N" > "$IDS_FILE"
ID_COUNT="$(wc -l < "$IDS_FILE" | tr -d ' ')"
[[ "$ID_COUNT" -gt 0 ]] || { echo "no evidence ids after filtering" >&2; exit 2; }
IDS_SQL_TEXT="'$(paste -sd, "$IDS_FILE" | sed "s/,/','/g")'"
ADMIN_URL="$PG_URL/postgres"
GIT_SHA="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo "")"

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }

db_slug() { printf 'vkpi_eval_%s' "$(printf '%s' "$1" | tr '.-' '__')"; }
psql_q() { psql "$1" -X -At -v ON_ERROR_STOP=1 -c "$2"; }

log "plan: models=[$MODELS] baseline=$BASELINE qa_model=$QA_MODEL n=$ID_COUNT template=$TEMPLATE_DB out=$OUT_DIR"
if [[ "$DRY_RUN" == "1" ]]; then
  for model in $MODELS; do
    log "dry-run: $model -> db=$(db_slug "$model") (createdb -T $TEMPLATE_DB; stale $ID_COUNT rows; enqueue; worker; export; profile)"
  done
  log "dry-run: no database, queue, worker or provider call was touched"
  exit 0
fi

# ---------------------------------------------------------------- per-model pipeline (subshell: env isolated)
run_model() {
  local model="$1"
  local db
  db="$(db_slug "$model")"
  local db_url="$PG_URL/$db"
  local slug="${db#vkpi_eval_}"
  local worker_log="$OUT_DIR/worker_$slug.log"
  local pred="$OUT_DIR/pred_$slug.json"

  # 1) clone
  local exists
  exists="$(psql_q "$ADMIN_URL" "SELECT 1 FROM pg_database WHERE datname='$db'")"
  if [[ "$exists" == "1" ]]; then
    if [[ "$REUSE_DB" != "1" ]]; then
      log "db $db already exists; pass --reuse-db to reuse or drop it first" ; return 1
    fi
    log "reusing existing db $db"
  else
    local others
    others="$(psql_q "$ADMIN_URL" "SELECT count(*) FROM pg_stat_activity WHERE datname='$TEMPLATE_DB' AND pid <> pg_backend_pid()")"
    if [[ "$others" != "0" ]]; then
      log "template $TEMPLATE_DB has $others other sessions; CREATE DATABASE ... TEMPLATE needs zero (stop local admin-web/worker on it)"
      return 1
    fi
    log "createdb $db TEMPLATE $TEMPLATE_DB"
    psql_q "$ADMIN_URL" "CREATE DATABASE \"$db\" TEMPLATE \"$TEMPLATE_DB\"" >/dev/null
  fi

  # 2) neutralise inherited queue + mark the eval targets stale so enqueue re-runs them
  local cancelled stale
  cancelled="$(psql_q "$db_url" "WITH c AS (UPDATE apify_jobs SET status='cancelled', last_error='model_upgrade_eval_clone_neutralised', updated_at=NOW() WHERE status IN ('queued','running','retrying','processing') RETURNING 1) SELECT count(*) FROM c")"
  stale="$(psql_q "$db_url" "WITH s AS (UPDATE vkpi_analysis_cache SET status='stale', updated_at=NOW() WHERE target_type='video' AND derive_method IN ('video_analysis_final_v1','video_analysis_final_v1_keyframe_qa') AND status='ready' AND target_id IN ($IDS_SQL_TEXT) RETURNING 1) SELECT count(*) FROM s")"
  log "$model: cancelled_inherited_jobs=$cancelled stale_marked_cache_rows=$stale"

  # 3) env: model pins BEFORE runtime_env.sh (its .env load never overrides exported keys);
  #    readiness ack AFTER it (runtime/local_operator_env.sh re-exports the ack unconditionally).
  export ENVIRONMENT=local
  export RUNTIME_ENV_QUIET=1
  export LOCAL_DATABASE_URL="$db_url"
  export APIFY_WORKER_GEMINI_MODEL="$model"
  export GEMINI_FINAL_V1_MODELS="$model"
  export GEMINI_FINAL_V1_QA_MODEL="$QA_MODEL"
  export VKPI_GEMINI_MODEL_EXACT="$model"
  export GEMINI_MODEL="$model"
  export VKPI_GEMINI_MODEL="$model"
  export LLM_MONTHLY_BUDGET_USD="$BUDGET_USD"
  export APIFY_WORKER_CLAIM_LANE=all
  export APIFY_WORKER_HEARTBEAT_NAME="model-upgrade-eval-$slug"
  export APIFY_WORKER_LANE_STEAL=1
  export APIFY_WORKER_POLL_SECONDS=2
  unset V2_PRODUCTION_MODE IS_PRODUCTION VKPI_LLM_GATEWAY_FORCE_OFFLINE DATABASE_URL ENV_FILE
  # shellcheck disable=SC1091
  source "$ROOT/scripts/runtime_env.sh"
  export VKPI_LLM_READINESS_OPERATOR_ACK="google/$model,google/$QA_MODEL"
  export DATABASE_URL="$db_url"
  export PYTHONPATH="$ROOT/backend${PYTHONPATH:+:$PYTHONPATH}"
  export PYTHONDONTWRITEBYTECODE=1
  [[ -n "$GIT_SHA" ]] && export APP_GIT_SHA="$GIT_SHA"
  if [[ "${DATABASE_URL}" != "$db_url" ]]; then
    log "DATABASE_URL did not land on the clone (runtime_env override?)"; return 1
  fi
  if [[ -z "${HTTPS_PROXY:-}" ]]; then
    log "warning: HTTPS_PROXY empty (YTDLP_PROXY not set?) — Gemini is unreachable without the proxy on this network"
  fi

  # 4) enqueue through the real production path (readiness/budget preflight included)
  log "$model: enqueue $ID_COUNT evidence ids (APP_ROLE=admin-web ENABLE_SCHEDULER=0)"
  APP_ROLE=admin-web ENABLE_SCHEDULER=0 ENABLE_BROWSER=0 ENABLE_UPLOAD_CLEANUP=0 \
  "$PY" - "$IDS_FILE" "$OUT_DIR/enqueue_$slug.json" "$ACTOR_STAFF_ID" <<'PY'
import json, sys
from pathlib import Path
from app.db.connection import get_conn
from app.domains.kol.video_analysis_enqueue import enqueue_final_v1_video_analysis_batch

ids = [int(line.strip()) for line in Path(sys.argv[1]).read_text().splitlines() if line.strip()]
conn = get_conn()
# Paid-action fence: the worker blocks final_v1 jobs that carry no durable actor/target fence
# (video_analysis_authorization_fence_required). Enqueue exactly like the UI does: a real
# owner/manager staff row + enforce_target_write=True (manager may write any target).
actor_arg = (sys.argv[3] if len(sys.argv) > 3 else "auto").strip().lower()
if actor_arg in ("", "auto"):
    staff_row = conn.execute(
        "SELECT * FROM staff WHERE is_owner=1 AND active=1 ORDER BY id LIMIT 1"
    ).fetchone()
else:
    staff_row = conn.execute("SELECT * FROM staff WHERE id=?", (int(actor_arg),)).fetchone()
if not staff_row:
    print("no owner staff row for the paid-action fence (pass --actor-staff-id)", file=sys.stderr)
    sys.exit(1)
staff = dict(staff_row)
placeholders = ", ".join("?" for _ in ids)
rows = conn.execute(
    f"SELECT id, kol_pool_id FROM vkpi_kol_video_evidence WHERE id IN ({placeholders})", tuple(ids)
).fetchall()
by_id = {int(dict(r)["id"]): int(dict(r)["kol_pool_id"] or 0) for r in rows}
items = [{"kol_pool_id": by_id.get(i, 0), "evidence_id": i} for i in ids]
result = enqueue_final_v1_video_analysis_batch(items=items, staff=staff, enforce_target_write=True)
summary = {
    "requested": result.get("requested"), "queued": result.get("queued"), "skipped": result.get("skipped"),
    "ai_disabled": result.get("ai_disabled"), "errors": result.get("errors"),
    "items": [
        {k: item.get(k) for k in ("status", "evidence_id", "kol_pool_id", "reason", "provider_gate_reason", "model_readiness_status")}
        for item in result.get("items", [])
    ],
}
Path(sys.argv[2]).write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n")
print(json.dumps({k: summary[k] for k in ("requested", "queued", "skipped", "ai_disabled", "errors")}))
if summary["queued"] != len(ids):
    bad = [i for i in summary["items"] if i.get("status") != "queued"]
    print("enqueue incomplete:", json.dumps(bad[:5], ensure_ascii=False, default=str), file=sys.stderr)
    sys.exit(1)
PY

  if [[ "$STOP_AFTER" == "enqueue" ]]; then
    log "$model: --stop-after enqueue (queued jobs left in $db; no worker started)"; return 0
  fi

  # 5) one worker until the eval targets drain
  log "$model: starting worker (APP_ROLE=worker) log=$worker_log"
  ( export APP_ROLE=worker ENABLE_SCHEDULER=0 ENABLE_BROWSER=0 ENABLE_UPLOAD_CLEANUP=0
    cd "$ROOT" && exec "$PY" -m app.workers.apify_jobs_worker ) >"$worker_log" 2>&1 &
  local worker_pid=$!
  local started elapsed active
  started="$(date +%s)"
  while true; do
    sleep "$POLL_SEC"
    if ! kill -0 "$worker_pid" 2>/dev/null; then
      log "$model: worker exited early; tail of log:"; tail -n 30 "$worker_log" >&2; return 1
    fi
    active="$(psql_q "$db_url" "SELECT count(*) FROM apify_jobs WHERE status IN ('queued','running','retrying','processing') AND payload->>'target_id' IN ($IDS_SQL_TEXT)")"
    elapsed=$(( $(date +%s) - started ))
    if [[ "$active" == "0" ]]; then
      log "$model: queue drained after ${elapsed}s"; break
    fi
    if (( elapsed % 120 < POLL_SEC )); then
      log "$model: active_jobs=$active elapsed=${elapsed}s"
    fi
    if (( elapsed > WORKER_TIMEOUT_SEC )); then
      log "$model: worker timeout after ${elapsed}s (active_jobs=$active)"; kill -TERM "$worker_pid" 2>/dev/null || true; wait "$worker_pid" 2>/dev/null || true; return 1
    fi
  done
  kill -TERM "$worker_pid" 2>/dev/null || true
  wait "$worker_pid" 2>/dev/null || true
  psql_q "$db_url" "SELECT status, count(*) FROM apify_jobs WHERE payload->>'target_id' IN ($IDS_SQL_TEXT) AND payload->>'derive_method'='video_analysis_final_v1' AND updated_at >= NOW() - interval '1 day' GROUP BY 1 ORDER BY 1" > "$OUT_DIR/jobs_$slug.txt"
  log "$model: job statuses: $(paste -sd' ' "$OUT_DIR/jobs_$slug.txt")"

  # 6) export predictions (exit 4 = some evidence missing/model mismatch -> stop, partial file kept for triage)
  local export_rc=0
  "$PY" "$ROOT/scripts/ops/export_final_v1_predictions.py" export \
    --database-url "$db_url" --model "$model" --dataset-id "$DATASET_ID" \
    --evidence-ids "$IDS_FILE" --output "$pred" || export_rc=$?
  if [[ "$export_rc" != "0" ]]; then
    log "$model: export rc=$export_rc (see source.missing in $pred)"; return 1
  fi

  # 7) optional gold scoring (rc 4 = metric gate fail, still a valid report; rc 2 = input invalid -> stop)
  if [[ -n "$GOLD_FILE" ]]; then
    local gold_rc=0
    "$PY" "$ROOT/scripts/eval_gemini_final_v1_quality.py" --gold "$GOLD_FILE" --predictions "$pred" \
      --output "$OUT_DIR/quality_$slug.json" --pretty || gold_rc=$?
    case "$gold_rc" in
      0|4) log "$model: gold quality rc=$gold_rc -> $OUT_DIR/quality_$slug.json" ;;
      *) log "$model: gold evaluation input invalid rc=$gold_rc"; return 1 ;;
    esac
  fi

  # 8) latency profile (only today's rows = this run)
  PYTHONPATH="$ROOT:$ROOT/scripts:$ROOT/backend" "$PY" "$ROOT/scripts/ops/profile_video_analysis.py" \
    --database-url "$db_url" --limit "$ID_COUNT" --days 1 --output "$OUT_DIR/profile_$slug.json"
  log "$model: profile -> $OUT_DIR/profile_$slug.json"

  if [[ "$DROP_DB" == "1" ]]; then
    psql_q "$ADMIN_URL" "DROP DATABASE \"$db\"" >/dev/null && log "$model: dropped $db"
  fi
  return 0
}

# ---------------------------------------------------------------- run baseline first, then candidates
ORDERED="$BASELINE"
for model in $MODELS; do
  [[ "$model" == "$BASELINE" ]] || ORDERED="$ORDERED $model"
done
for model in $ORDERED; do
  log "=== $model ==="
  ( run_model "$model" ) || { log "pipeline failed at model=$model; artifacts in $OUT_DIR"; exit 1; }
done

if [[ "$STOP_AFTER" == "enqueue" ]]; then
  log "stopped after enqueue for every model; artifacts in $OUT_DIR"; exit 0
fi

# ---------------------------------------------------------------- side-by-side summary
BASE_SLUG="$(db_slug "$BASELINE")"; BASE_SLUG="${BASE_SLUG#vkpi_eval_}"
CANDIDATE_ARGS=""
for model in $ORDERED; do
  [[ "$model" == "$BASELINE" ]] && continue
  slug="$(db_slug "$model")"; slug="${slug#vkpi_eval_}"
  CANDIDATE_ARGS="$CANDIDATE_ARGS --candidate $OUT_DIR/pred_$slug.json"
done
if [[ -z "$CANDIDATE_ARGS" ]]; then
  log "only the baseline ran; nothing to compare (pred_$BASE_SLUG.json in $OUT_DIR)"; exit 0
fi
# shellcheck disable=SC2086  # deliberate word splitting: one --candidate per model, paths have no spaces
"$PY" "$ROOT/scripts/ops/export_final_v1_predictions.py" compare \
  --baseline "$OUT_DIR/pred_$BASE_SLUG.json" $CANDIDATE_ARGS \
  --output "$OUT_DIR/agreement_summary.json"
log "summary -> $OUT_DIR/agreement_summary.json"
"$PY" - "$OUT_DIR/agreement_summary.json" <<'PY'
import json, sys
report = json.load(open(sys.argv[1]))
rows = [report["baseline"], *report["candidates"]]
print(f"{'model':24} {'n':>3} {'contract':>8} {'brand_agree':>11} {'prod_jacc':>9} {'cost_p50':>9} {'lat_p50_s':>9} {'dims11':>6}")
for row in rows:
    agree = row.get("agreement_vs_baseline") or {}
    cost = row.get("cost_usd") or {}
    lat = row.get("latency_ms") or {}
    fmt = lambda v, d=3: ("-" if v is None else f"{v:.{d}f}")
    print(
        f"{str(row.get('model')):24} {row.get('cases', 0):>3} {fmt(row['contract'].get('validity_rate')):>8} "
        f"{fmt(agree.get('brand_status_agreement_rate')):>11} {fmt(agree.get('products_jaccard_mean')):>9} "
        f"{fmt(cost.get('p50'), 4):>9} {fmt((lat.get('p50') or 0) / 1000 if lat.get('p50') is not None else None, 1):>9} "
        f"{fmt(row.get('llm_dimensions_11_rate'), 2):>6}"
    )
print("claim_status=descriptive_only; gold=none unless quality_*.json present")
PY
log "done: $OUT_DIR"
