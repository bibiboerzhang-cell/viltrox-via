# 模型升级刀 · 运维手册(2026-08)

目标:主力模型从 `gemini-3.5-flash / gemini-2.5-flash / claude-sonnet-4-6 / claude-opus-4-7 /
claude-haiku-4-5 / gpt-5.4-mini` 切到 `gemini-3.6-flash / gemini-3.5-flash-lite(裁判)/
claude-sonnet-5 / claude-opus-5 / gpt-5.6-luna`,**代码默认 + env 两层同时切**,线上零降级。

核心事实(违反即事故):

- **readiness 闸 fail-closed**:默认绑定不在 `VKPI_LLM_READINESS_OPERATOR_ACK` 且无签名证据
  → 每次默认 Gemini/Claude/OpenAI 调用静默降级 `rule_v0`(`model_binding_blocked` /
  `readiness_not_production_ready`)。与 `LLM_MONTHLY_BUDGET_USD` 缺失是同一失败类。
- **线上 .env 不随 rsync**(memory: 上线.env不随rsync),`/opt/viltrox-2.0/.env` 手改;
  `/etc/vkpi/vkpi-lane-overrides.env` **只认 9 个数字键**
  (`APIFY_WORKER_GEMINI_QPS / APIFY_WORKER_LLM_CONCURRENCY / APIFY_WORKER_PROFILE_MEDIA_CONCURRENCY /
  APIFY_WORKER_COMMENTS_CONCURRENCY / APIFY_WORKER_GEMINI_VIDEO_CONCURRENCY / LLM_MONTHLY_BUDGET_USD /
  POSTGRES_POOL_MIN_SIZE / POSTGRES_POOL_MAX_SIZE / DB_USE_PGBOUNCER`),
  `deploy_local_to_cloud.sh` 校验并整文件覆盖——**模型 id 绝不能放那里**。
- **PROVIDER_CONFIG / DEFAULT_VIDEO_GEMINI_MODEL / WORKER_GEMINI_MODEL 是 import-time**:
  改 env 后必须全量重启 admin-web(含 scheduler)+ 16 条 worker 车道,HUP 不够。
- admin-web(入队预检)与 worker(执行)必须认同**同一个精确视频模型**,否则每条视频 job
  `model_binding_mismatch`。两边读同一 `/opt/viltrox-2.0/.env`,只要同时重启即一致。
- 3.x 家族 `thinking_budget=0` → 400,必须 `thinking_level=minimal`(B 车道网关/llm_production
  按家族映射);`gemini-3.7-flash` / `gemini-flash-latest`(已漂到 3.7)/ `gemini-pro-latest`
  **禁止作为运行模型**(无 minimal 档,每次烧 ~60 思考 token 并吃 max_output_tokens)。

## 1. 合入顺序

`C → D → B → A(守卫测试最后)→ E`

| 车道 | 内容 | 为什么在这个位置 |
|---|---|---|
| C | `core/gemini_models.py` 唯一视频默认值 + worker/analyzer 去字面 | A 的 `test_no_hardcoded_model_ids` 守卫依赖它 |
| D | brand/market/analyzers 去字面、Via 路由 provider 归一 | 同上;D 的 `anthropic_response_text` 与 B 的 helper 合并点 |
| B | 网关 thinking/reasoning 整形(3.x minimal / 2.5 budget / luna effort=none) | A 的绑定表切到 3.x 后没有 B 就全 400 |
| A | 注册表 / 定价 / PROVIDER_CONFIG / TASK_MODEL_BINDING / 守卫测试 | 守卫要求前面的字面清理已完成 |
| E | 本手册 + 预检 / 评测 / canary 不变量 | canary `EXPECTED_UNIQUE_BINDINGS=6` 只有 A 合入后才绿 |

合入后:全量 `pytest`(.venv)绿;`wc -l` ≤ 1000 每个触碰文件;
`.venv/bin/python scripts/ops/model_upgrade_env_preflight.py .env --extra-env-file runtime/local_operator_env.sh` PASS。

## 2. env 差异表(本地 vs prod → 目标)

键名与目标值;当前 prod 值以登录后 `grep -E '^KEY=' /opt/viltrox-2.0/.env | cut -d= -f1` 核对
(只看键,别把值贴进任何聊天/日志)。

| 键 | 本地现值 | prod 现值(按 memory,上机核对) | 目标值 | 备注 |
|---|---|---|---|---|
| `OPENAI_MODEL` | gpt-5.4-mini | gpt-5.4-mini | **gpt-5.6-luna** | luna 必须 `reasoning.effort='none'`(B) |
| `VKPI_OPENAI_MODEL` | gpt-5.4-mini | gpt-5.4-mini | **gpt-5.6-luna** | 优先级高于 OPENAI_MODEL |
| `GEMINI_MODEL` | gemini-3.5-flash | gemini-flash-latest | **gemini-3.6-flash** | `*-latest` 被绑定表忽略、被预检(e)拒绝 |
| `VKPI_GEMINI_MODEL` | gemini-3.5-flash | gemini-flash-latest | **gemini-3.6-flash** | 同上 |
| `VKPI_GEMINI_MODEL_EXACT` | (无) | (无) | **gemini-3.6-flash** | 别名映射落点,显式写死 |
| `CLAUDE_MODEL` | claude-sonnet-4-6 | claude-sonnet-4-6 | **claude-sonnet-5** | |
| `VKPI_CLAUDE_MODEL` | claude-sonnet-4-6 | claude-sonnet-4-6 | **claude-sonnet-5** | |
| `CLAUDE_HAIKU_MODEL` | claude-haiku-4-5-20251001 | 同 | **claude-sonnet-5** | env 名保留;Haiku 2026-10-15 退役 |
| `VIA_SUMMARY_PROVIDER` | anthropic | anthropic | **openai** | 与 A 的 `via_persona_summary` 绑定对齐 |
| `VIA_SUMMARY_MODEL` | claude-haiku-4-5-20251001 | 同 | **gpt-5.6-luna** | |
| `APIFY_WORKER_GEMINI_MODEL` | (无→代码默认) | (无/旧值) | **gemini-3.6-flash** | 视频主力;入队预检与 worker 同源 |
| `GEMINI_FINAL_V1_QA_MODEL` | (无) | (无) | **gemini-3.5-flash-lite** | 关键帧裁判,直连 SDK + minimal |
| `GEMINI_FINAL_V1_MODELS` | (无) | 可能含 gemini-3-flash-preview | **删除或 =gemini-3.6-flash** | 预检(d)拒绝 preview |
| `LLM_PRIMARY_PROVIDER` | google | google | google(不动) | google 默认链在所有默认调用的关键路径 |
| `LLM_MONTHLY_BUDGET_USD` | 有 | .env 有 + lane-overrides 3000 | 不动 | 缺失=全挡 |
| `VKPI_LLM_READINESS_OPERATOR_ACK` | runtime/local_operator_env.sh(8 项) | .env(8 项) | **追加 5 项,保留旧 8 项**(见 §3) | 最高风险项 |
| `VKPI_ANTHROPIC_THINKING` | (无) | (无) | 不设(默认 disabled,成本中性) | 需要时 `adaptive` |

本地改法:`.env` 改上表键;`runtime/local_operator_env.sh`(gitignored)改 ack。
**本刀不改 .env / local_operator_env.sh 文件本身——由主会话在 eval 时执行。**

## 3. readiness ack 目标清单

```
VKPI_LLM_READINESS_OPERATOR_ACK=anthropic/claude-haiku-4-5-20251001,anthropic/claude-opus-4-7,anthropic/claude-sonnet-4-6,google/gemini-2.5-flash,google/gemini-2.5-pro,google/gemini-3.5-flash,openai/gpt-5.4-mini,openai/gpt-5.5,google/gemini-3.6-flash,google/gemini-3.5-flash-lite,anthropic/claude-sonnet-5,anthropic/claude-opus-5,openai/gpt-5.6-luna
```

- 旧 8 项保留:prod 用 env 钉回旧模型(回滚)时闸仍放行。
- 新 5 项:`google/gemini-3.6-flash`(audit_video_analysis / kol_audience_analysis /
  vkpi_sentiment_annotate / google 默认链)、`google/gemini-3.5-flash-lite`(裁判)、
  `anthropic/claude-sonnet-5`(audit_deep_score / audit_vision_fallback / kol_outreach_pack /
  anthropic 默认链)、`anthropic/claude-opus-5`(deepsight_strategy / ai_today_evidence_strategy /
  contract_pdf_extract / invoice_extract)、`openai/gpt-5.6-luna`(audit_pre_filter / via_chat /
  via_persona_summary / kol_content_fit_analysis / kol_product_fit_reason / openai 默认链)。
- ack 是操作员确认书,不是就绪证据(readiness 目录 `production_ready` 不变,每次放行打
  `readiness_gate_operator_ack` 审计告警)。签名证据管线(`scripts/ops/vkpi_model_evidence_plan.py`
  + Ed25519 探针)交付后可逐项撤 ack。

## 4. 预检(重启前必过)

```bash
# 本地
.venv/bin/python scripts/ops/model_upgrade_env_preflight.py .env \
    --extra-env-file runtime/local_operator_env.sh

# prod:把线上 .env + lane-overrides 拷到本机临时目录(权限 600,用完即删),按 unit 真实叠加顺序预检
scp prod:/opt/viltrox-2.0/.env /tmp/prod.env && chmod 600 /tmp/prod.env
scp prod:/etc/vkpi/vkpi-lane-overrides.env /tmp/prod-lane.env
.venv/bin/python scripts/ops/model_upgrade_env_preflight.py /tmp/prod.env                      # admin-web 视角
.venv/bin/python scripts/ops/model_upgrade_env_preflight.py /tmp/prod.env --extra-env-file /tmp/prod-lane.env   # worker 视角
rm -f /tmp/prod.env /tmp/prod-lane.env
```

退出码 1 = 有 FAIL,不许重启。脚本只打印键名/模型 id/判定,值不落屏。
回滚钉旧模型时加 `--allow-worker-pin`(b 项漂移降为 WARN)。

## 5. 切换与重启顺序(prod)

1. 部署代码(正常 `deploy_local_to_cloud.sh` 流程;本刀无迁移)。
2. 手改 `/opt/viltrox-2.0/.env`(§2 目标值 + §3 ack)。不碰 lane-overrides.env。
3. `scp` 回本机跑 §4 预检两视角,PASS 才继续。
4. 重启(PROVIDER_CONFIG import-time,必须全量):
   ```bash
   sudo systemctl restart viltrox-2.0-test.service            # admin-web + scheduler(ENABLE_SCHEDULER=1 在同一 unit)
   sudo systemctl restart vkpi-worker-interactive.service vkpi-worker-bulk@{1..15}.service
   sudo systemctl restart vkpi-redis-worker.service
   systemctl is-active viltrox-2.0-test.service vkpi-worker-interactive.service vkpi-worker-bulk@{1..15}.service
   ```
   旧 legacy 单元(`viltrox-2.0-scheduler/worker/admin/public.service`)必须保持 inactive。
   本地对应口诀:pkill 拆两条 ssh + `source .env` + setsid(memory: worker重启口诀)。
5. `/health` 的 `git_sha` 对齐部署 sha(`scripts/ops/fetch_runtime_health.py --url ... --env-file /opt/viltrox-2.0/.env`)。
6. 冒烟:每条车道一个 final_v1 job(`scripts/enqueue_final_v1_video_jobs.py --batch recent --limit 1 --commit`),
   看 `vkpi_llm_calls.model` 落的是 `gemini-3.6-flash` 且 status=success;
   `vkpi_analysis_cache.result.llm_execution.model_match=true`。
7. 观察 24h:`model_binding_blocked` / `readiness_not_production_ready` / 降级率埋点为零;
   Gemini 400(thinking)为零;成本曲线对齐 §8 价格。

## 6. canary(文本绑定连通性,非就绪证据)

```bash
# 隔离库 dry-run 拿 authorization_value(零调用)
DATABASE_URL=postgresql://postgres@127.0.0.1:54333/vkpi_closeout \
  .venv/bin/python scripts/ops/vkpi_stage1_model_canary.py --output /tmp/canary_plan.json
# 取 plan 的 authorization_value 后实弹(本地/隔离库;IS_PRODUCTION 必须 false;≤$0.10)
DATABASE_URL=postgresql://postgres@127.0.0.1:54333/vkpi_closeout \
VKPI_LLM_STAGE1_CANARY_LIVE_AUTHORIZATION=<authorization_value> \
  .venv/bin/python scripts/ops/vkpi_stage1_model_canary.py --live \
    --binding anthropic/claude-sonnet-5 --binding anthropic/claude-opus-5 \
    --binding google/gemini-3.6-flash --binding google/gemini-2.5-pro \
    --binding openai/gpt-5.6-luna --binding openai/gpt-5.5 --output /tmp/canary_live.json
```

不变量:`EXPECTED_UNIQUE_BINDINGS = 6`(= A 车道绑定表去重)。A 改绑定表必须同步改
`scripts/ops/vkpi_stage1_model_canary.py` 与 `tests/test_vkpi_stage1_model_canary.py`,否则
`expected_exactly_6_unique_task_bindings` 拦住所有 canary。B 合入后 canary 的请求形状与生产一致,
其 `vkpi_llm_calls` 行才是有效样本;记录 `response_model` 供 `MODEL_RESPONSE_ALIASES` 补快照名。

## 7. 三模型 final_v1 评测(隔离库,决定视频主力)

前置:C(gemini_models)+ B(thinking 映射)+ A(注册/定价)已合入;代理可达
(`HTTPS_PROXY` 由 `scripts/runtime_env.sh` 从 `YTDLP_PROXY` 派生);54333 上 `vkpi_closeout`
无其他会话(`CREATE DATABASE ... TEMPLATE` 要求)。

```bash
bash scripts/ops/model_upgrade_eval_3way.sh --evidence-ids <30 个 evidence id 文件> --n 30 \
    --models "gemini-2.5-flash gemini-3.5-flash-lite gemini-3.6-flash" --baseline gemini-2.5-flash \
    --qa-model gemini-3.5-flash-lite --budget-usd 2000 --out runtime/model_upgrade_eval/<日期>
# 先零成本冒烟:--dry-run(只打印)或 --stop-after enqueue(建库+标 stale+入队,不起 worker)
```

流水线(每模型子 shell 隔离 env):`CREATE DATABASE vkpi_eval_<slug> TEMPLATE vkpi_closeout`
→ 克隆里继承的活跃 apify_jobs 全部 `cancelled`、30 个目标的 final_v1/keyframe_qa cache 标 `stale`
→ 导出模型 env(`APIFY_WORKER_GEMINI_MODEL / GEMINI_FINAL_V1_MODELS / VKPI_GEMINI_MODEL_EXACT /
GEMINI_MODEL / VKPI_GEMINI_MODEL=<id>`,`GEMINI_FINAL_V1_QA_MODEL=gemini-3.5-flash-lite`,
`LLM_MONTHLY_BUDGET_USD`)→ `source scripts/runtime_env.sh`(ENVIRONMENT=local,
LOCAL_DATABASE_URL=克隆)→ ack 覆写为 `google/<id>,google/<qa>` → 真实入队路径
`enqueue_final_v1_video_analysis_batch`(APP_ROLE=admin-web ENABLE_SCHEDULER=0;含 readiness/预算预检,
非 30/30 queued 即停)→ 单 worker `python -m app.workers.apify_jobs_worker`(APP_ROLE=worker,
CLAIM_LANE=all)跑到目标 id 无活跃 job → `export_final_v1_predictions.py export` →
(有 `--gold` 才)`scripts/eval_gemini_final_v1_quality.py` → `profile_video_analysis.py --days 1`
→ `export_final_v1_predictions.py compare` 汇总 `agreement_summary.json` + 表格。

口径:**没有真 gold 不造 gold**。`evals/fixtures/gemini_final_v1_synthetic_gold.json` 是合成件,
不能当 gold。汇总只给:契约有效率(REQUIRED_OUTPUT_SHAPES 全覆盖)、brand_status 与基线一致率、
产品/竞品 Jaccard、unsupported absent、畸形证据、$/video 与时延 p50/p95、llm_dimensions_11 落库率;
`claim_status=descriptive_only`。

注意:旧 cache 行(≤2026-07-17)全部缺 `brand_product_evidence`(prompt 契约后加),契约有效率 0——
**基线必须由 2.5-flash 在克隆里重跑**,不能拿旧行当基线。关键帧裁判对照(3.1-pro-preview vs
3.5-flash-lite)不在脚本内(keyframe_qa 是独立按需 derive_method),需单独入队对比。

决策闸(§eval_plan 8):3.6-flash 契约有效率 100%、与基线一致率在容差内、成本/时延可接受 → 采纳
(代码默认已是 3.6-flash);否则 **只改 env**
`APIFY_WORKER_GEMINI_MODEL=gemini-3.5-flash`(或 2.5-flash)钉住,不回滚代码。

## 8. 回滚

所有旧 id 仍注册 + 定价 + 在 ack 内,回滚 = 改回旧 env 值 + §5 全量重启:

| 键 | 回滚值 |
|---|---|
| `OPENAI_MODEL` / `VKPI_OPENAI_MODEL` | gpt-5.4-mini |
| `GEMINI_MODEL` / `VKPI_GEMINI_MODEL` / `VKPI_GEMINI_MODEL_EXACT` / `APIFY_WORKER_GEMINI_MODEL` | gemini-3.5-flash(或 gemini-2.5-flash);**不要**回 gemini-flash-latest |
| `CLAUDE_MODEL` / `VKPI_CLAUDE_MODEL` | claude-sonnet-4-6 |
| `CLAUDE_HAIKU_MODEL` / `VIA_SUMMARY_MODEL` + `VIA_SUMMARY_PROVIDER` | claude-haiku-4-5-20251001 + anthropic(10-15 前) |

预检加 `--allow-worker-pin`。cache 旧行策略:不重跑(混合语料可接受);失败池用
`scripts/requeue_final_v1_failed_jobs.py --commit` 排水;被错误模型写坏的行用
`scripts/ops/mark_stale_final_v1_sku_context_cache.py` 同款 `status='stale'` 手法标记后重入队。

## 9. 价格与到期提醒(USD / 1M tokens,2026-08-22 官方页)

| 模型 | in / out | 到期 / 待办 |
|---|---|---|
| gemini-3.6-flash | 0.75 / 3.75(缓存 0.075) | **促销至 2026-12-31**,之后 1.50 / 7.50 → 2026-12 月中复核 `model_pricing` / PROVIDER_CONFIG / runtime catalog(A 车道三处同源)并重估月预算 |
| gemini-3.5-flash-lite | 0.30 / 2.50(无缓存) | 裁判档 |
| gemini-2.5-flash | 0.30 / 2.50(音频 in 1.00) | 基线 |
| claude-sonnet-5 | 2.00 / 10.00(batch 1/5;4.7+ tokenizer 多 ~30% token) | 正式价 |
| claude-opus-5 | 5.00 / 25.00 | |
| claude-haiku-4-5(-20251001) | 1.00 / 5.00 | **2026-10-15 起可退役**:删 registry `AVAILABLE_MODELS` 两个 haiku id、`model_pricing`(含 `haiku` 归并分支)、runtime `_EXACT_CATALOG` 两条、ack 中 `anthropic/claude-haiku-4-5-20251001`;`CLAUDE_HAIKU_MODEL` env 名可保留指向 sonnet-5;历史台账按 haiku 价对账不受影响 |
| gpt-5.6-luna | 0.20 / 1.20(缓存 0.02) | `reasoning.effort='none'` |
| gpt-5.6 / gpt-5.5 | 5.00 / 30.00 | 不动 |

## 10. 本刀交付物(E 车道)

- `scripts/ops/model_upgrade_env_preflight.py`(+ `tests/test_model_upgrade_env_preflight.py`)
- `scripts/ops/export_final_v1_predictions.py`(+ `tests/test_export_final_v1_predictions.py`)
- `scripts/ops/model_upgrade_eval_3way.sh`
- `scripts/ops/vkpi_stage1_model_canary.py` `EXPECTED_UNIQUE_BINDINGS=6`(+ 测试同步)
- `scripts/ops/dealer_web_verify.py` / `dealer_physical_store_judge.py` `--model` 默认 gemini-3.6-flash
- 本手册

## 附录 E · 2026-08-22 隔离库三模型 eval 实测(30 条 YouTube,同代码同提示,每模型独立克隆库)

| 指标 | gemini-2.5-flash(原 prod) | gemini-3.5-flash-lite | gemini-3.6-flash(提示强化后) |
|---|---|---|---|
| 成功 | 26/30(4 条 8192 截断→回退下载失败) | 30/30 | 30/30 |
| 六层齐全 / 六分齐全 / verdict 字符串 | 24 / 16 / 17 | 30 / 26 / 18(12 条 verdict 成 dict) | 30 / 30 / 30 |
| 中文合规(verdict / summary) | 100% | 26/30 | 30/30(强化前 21/30、22/30) |
| 成本/条 | $0.040 | $0.021 | $0.046 |
| 端到端 p50 | 50s | 20s | 19–26s |
| 输出 token 均值 | 6235 | 2389 | 2466 |

- 结论:视频主力 `gemini-3.6-flash` + 裁判 `gemini-3.5-flash-lite`;2.5-flash 最啰嗦且被 8192 截断;lite 便宜但 schema 漂移。
- 提示强化:static prompt 末尾加「输出语言与类型硬约束」(简体中文、verdict/hook/summary 必为字符串、scores 扁平六键),prompt_version 变更会自然失效上下文缓存。
- 已知跨模型共性:`brand_product_evidence` 结构块多数写 unknown(散文识别正确,镜头抽取器从散文取)——后刀用 response_json_schema 强约束;`final_v1_quality_eval.REQUIRED_OUTPUT_SHAPES` 把 product_presence 等当 list,与生产提示(字符串)不一致,contract validity 全 0 属评测契约过期,非模型问题。
- 实测踩坑:脚本入队须带 owner 围栏(`--actor-staff-id`);台账 staff 外键 bug(e5d12d21a);decodo 代理阶段性 522 使直链回退下载。
- 数据:`docs/evals/model_upgrade_2026-08-22_agreement.json`。
