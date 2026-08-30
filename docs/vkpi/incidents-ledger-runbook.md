# 事故台账规范(incidents ledger runbook)

台账文件:`runtime/ops/incidents.jsonl`(运行数据,不入库;本规范入库)。
**起账日 2026-08-30,历史不可回填、不补记** —— 90 天观测窗从起账日起走。
消费方:`scripts/vkpi_engineering_health_delivery.py`(只读采集)→ 交付维
`mttr_p50_minutes / mttr_p90_minutes / p1_p2_sla_rate / overdue_critical_count / change_failure_rate`。

## 每行一条 JSON,字段

| 字段 | 必填 | 含义 |
|---|---|---|
| `type` | 是 | `incident`(事故)或 `ledger_opened`(仅首行) |
| `id` | 是 | `INC-YYYYMMDD-<seq>`,当日递增 |
| `severity` | 是 | `critical` / `p1` / `p2` / `p3`(合同 SLA 只统计 p1/p2;critical 计 overdue) |
| `detected_at` | 是 | ISO-8601 UTC,发现时刻(不是发生时刻——按可观测事实记) |
| `resolved_at` | 恢复后 | ISO-8601 UTC;未填 = 仍 open |
| `caused_by_release` | 是 | 触发部署的 sha12,与 `runtime/ops/post-deploy/` 目录名对齐;查不到写 `"unknown"`,**不许猜** |
| `summary` | 是 | 一句话,写用户可见影响,不写内部猜测 |
| `hotfix_of` | 否 | 若本次修复以 hotfix 部署,填那次部署 sha12(CFR 判定用) |
| `deadline_at` | critical 必填 | 修复承诺时限;`resolved_at > deadline_at` 即 overdue |

## 口径(随合同 v1.1 锁死,此处为同一文本)

- **什么算事故**:生产环境用户可见的功能失效或数据错误,且需要人工干预恢复。
  预算闸拦截、限流、单次重试即愈的抖动**不算**(参照发车失败三判别器口径)。
- **CFR(change failure rate)**:某次部署后触发回滚、或 24h 内为其打 hotfix,该部署计 failure;
  分母 = 窗口内生产部署次数(`post-deploy/` 目录数,去重后)。
- **MTTR**:`resolved_at - detected_at`,分钟;p50/p90 按窗口内已 resolved 的事故算。
- **空样本语义**:窗口内零事故 → MTTR 两项与 SLA 按 at-target 记 observed
  (无事故是好状态,不是无证据;overdue_critical_count=0 同理)。此语义由合同 v1.1 正式承载。

## 纪律

- 事故**当时**记,不事后凭回忆补;`detected_at` 用发现那一刻,不美化。
- 严禁为分数改判 severity 或删行 —— 台账是 append-only,写错就追加一行 `"corrects": "<id>"` 的更正记录。
- 每次发车后若有回滚,`post-deploy` 目录的 `outcome.json` 与本台账都要落(两处对账)。
