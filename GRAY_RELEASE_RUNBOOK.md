# 灰度阶梯 Runbook — scheduler_tasks 分档放量

V-KPI 后台调度任务(`scheduler_tasks` 表)的灰度上线手册。共 11 个任务,按
`risk_level` 分 **low / medium / high** 三档,逐档放量,每升一档前必须通过 24h 观察窗。

> 铁律:本流程只读写 `scheduler_tasks.enabled` 这一个开关。**绝不触碰
> `viltrox_fit_score` / `rule_v0` 任何既有表**(`scheduler_registry.py` 与
> migration 130 注释均明示零触既有表)。

---

## 0. 前置门禁(不过不灰度)

- **W0 不过不灰度**:W0 验收门禁见 `backend/app/main.py`。W0 验收未通过,则
  **任何档都不得 `--apply`**,也不得手工 `UPDATE ... SET enabled=TRUE`。
- 表已落地:`scheduler_tasks` 由 migration 落地,初始 11 行全部 `enabled=FALSE`。
  迁移来源跨两个文件:
  - `migrations/130_vkpi_scheduler_tasks.sql` — 建表 + 种子前 10 行(low 档只含前 4 个)。
  - `migrations/141_vkpi_action_inbox.sql`(第 75–77 行)— 后补 `daily_action_inbox_generate`
    (`ON CONFLICT DO NOTHING`)。所以 **low=5 成立,但 low 档跨 130+141 两个迁移落地**。

---

## 1. 阶梯总览

```
low  ──(观察 24h)──▶  medium  ──(观察 24h)──▶  high
```

每升一档前**必须通过 24h 观察窗**:上一档全部任务 `last_success_at` 有更新、
`last_error` 为空、ops 告警链路无新增告警,方可放下一档。

| 档位 | risk_level | 任务数 | task_key |
| --- | --- | --- | --- |
| 低 | `low` | 5 | `task_queue_health` / `kol_search_session_reconcile` / `project_shipment_sync` / `official_account_metrics_sync` / `daily_action_inbox_generate` |
| 中 | `medium` | 3 | `project_content_observation_scan` / `provider_pressure_retry` / `kol_profile_incremental_refresh` |
| 高 | `high` | 3 | `batch_video_analysis` / `deep_result_backfill` / `failed_pool_recycle` |

> `risk_level` 是 TEXT,字母序不等于 low<medium<high。排序用显式权重
> `_RISK_ORDER = {"low":0, "medium":1, "high":2}`(见
> `backend/app/domains/ops/scheduler_registry.py:24`),脚本已照搬,勿依赖字母序。

---

## 2. 如何开(逐档放量)

**推荐用脚本**(默认 dry-run,显式 `--apply` 才写库):

```bash
VENV=/Users/bibiboer/Documents/V-KPI——marketing/.venv/bin/python

# 先看现状(11 行 + 每档 OFF/ON)
$VENV scripts/gray_release.py --list

# 第 1 档:low
$VENV scripts/gray_release.py --tier low            # dry-run,先看将改哪些
$VENV scripts/gray_release.py --tier low --apply    # 写入 enabled=TRUE

# (观察 24h 后)第 2 档:medium
$VENV scripts/gray_release.py --tier medium --apply

# (再观察 24h 后)第 3 档:high —— LLM/批量,会先打红线提醒
$VENV scripts/gray_release.py --tier high --apply
```

**等价 SQL**(逐档替 `low` / `medium` / `high`):

```sql
UPDATE scheduler_tasks SET enabled=TRUE WHERE risk_level='low';
UPDATE scheduler_tasks SET enabled=TRUE WHERE risk_level='medium';
UPDATE scheduler_tasks SET enabled=TRUE WHERE risk_level='high';
```

---

## 3. 观察指标(读真表 / 真列)

升档前的 24h 观察窗,看这些**真实**信号:

- 同表 `scheduler_tasks`:
  - `last_run_at` — 是否在按计划跑;
  - `last_success_at` — 最近一次成功时间(应随放量推进);
  - `last_error` — 应为空;非空即该任务出错,**暂停升档并排查**。
- ops 告警链路:`backend/app/services/scheduler/jobs.py` 的
  `job_ops_threshold_alerts` → 写入 `vkpi_alerts`。观察窗内若有新增告警,**不升档**。

快速核现状:`$VENV scripts/gray_release.py --list`(打印 11 行 + 每档 OFF/ON + last_run)。

---

## 4. 回滚

任一档异常,立刻把该档 `enabled` 关回:

```bash
$VENV scripts/gray_release.py --disable-tier high --apply    # 关 high
$VENV scripts/gray_release.py --disable-tier medium --apply
$VENV scripts/gray_release.py --disable-tier low --apply
```

等价 SQL:

```sql
UPDATE scheduler_tasks SET enabled=FALSE WHERE risk_level='high';   -- 逐档替 ?
```

**整表回滚**(极端情况,撤掉整套调度):见
`migrations/130_vkpi_scheduler_tasks_down.sql`(`DROP TABLE`)。

---

## 5. 执行真相提醒

`enabled=TRUE` 只是一个 **config-gate 标记**,本身不触发任何执行。真正读它的是
`backend/app/services/scheduler/jobs.py` 里的
`_scheduler_task_enabled(task_key)`(第 253–276 行):各 job 体内开头
`if not _scheduler_task_enabled(...): return`。

- 环境变量 `OPS_SCHEDULER_FORCE_ENABLE=1` 可**整体强开**(绕过注册表),
  **仅限本地/测试,生产严禁开启**(见 jobs.py 第 261 行)。

---

## 6. 红线(均为真实源文件,引真名;放量任何档都不得越过)

1. **Action execute 永远人审**
   `backend/app/domains/actions/executors.py` 的 `execute_action(action_id, staff)` 双闸:
   - 闸1:`status == 'approved'` 否则 `outcome='skipped'` / `error='not_approved'`;
   - 闸2:`validators.validate_action`(要求 approved + `touches_v6_fit=False` + budget + entity)。
   灰度放量**不改变**这条:任何 Action 落地仍走人审。

2. **LLM 批量 requires_approval**
   `backend/app/domains/actions/producers.py` 的 `requires_approval` 字段。
   high 档(`batch_video_analysis` / `deep_result_backfill`)属 LLM/批量,
   种子默认 `enabled=FALSE`,放量需显式选 high 档并确认红线。

3. **超预算 budget_guard 拦**
   `backend/app/domains/costs/budget_guard.py` 的
   `check_budget(scope, estimated_cost, require_configured=...)`(第 181 行)与
   `check_budget_scopes(..., require_configured=True)`(第 197 行)。
   超预算必拦,灰度不绕过。

4. **W0 不过不灰度**
   W0 门禁见 `backend/app/main.py`。W0 验收未通过则**任何档都不得 `--apply`**(见 §0)。

5. **永不触碰 `viltrox_fit_score` / `rule_v0`**(铁律)
   `scheduler_registry.py` 与 migration 130 注释均明示零触既有表。

---

## 7. 脚本自检

```bash
VENV=/Users/bibiboer/Documents/V-KPI——marketing/.venv/bin/python
$VENV -m py_compile scripts/gray_release.py
$VENV scripts/gray_release.py --list   # 应打印 11 行、初始全 OFF
```

脚本走 `app.db.connection.get_conn`,占位符用 `'?'`(sqlite 风格,
`PostgresCompatCursor` 内部翻成 psycopg `%s`)。默认 **dry-run**,
不带 `--apply` 绝不 `commit()`。
