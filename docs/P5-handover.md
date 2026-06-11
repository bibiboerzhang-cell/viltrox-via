# P5 单轨执行交接记录

> 单轨执行(Codex 缺席至 P5 收口)。每个**窗口动作**一行:迁移号 / 重载时刻 / PID / 对账结果。
> Codex 回归首日凭此对账。分支 `codex/dashboard-real`,**不 push**。
> 授权令(驻留,批2–批5 有效)见对话回执;部署不变量:**C3 端点切异步必须在 C2 worker 分支重载生效之后**。

## Commit 流水(代码,非执行)
| commit | 内容 | 闸 | 执行影响 |
|---|---|---|---|
| `eea9ff22` | feat(projects): clear materials-tab mock facade(批1 假面清除) | 无 | 纯前端;dist 已 build(`4fd65963`)待浏览器验收 |
| `4fd65963` | feat(projects): contract extract enqueue helper + kernel split(C1) | 无 | 纯 domain 新增,未入队执行、未碰 worker/端点 |
| `(C2)` | feat(worker): project_contract_extract branch + handler | 闸C | **代码已提交,worker 未重载** → 运行中仍是旧 worker |

## 窗口动作(迁移 / worker 重载 / 铁律对账)
| 时刻 | 动作 | migration | worker PID(前→后) | 启动回显 | 对账(fit_score 零写入) |
|---|---|---|---|---|---|
| — | 尚无窗口动作 | 106 未 apply | 运行中 PID 待查(旧代码) | — | — |

## 待执行(gated)
- **Window A(合同异步)**:C2 worker 分支重载 → 然后 C3 端点切异步 + C4 泳道 + C5 前端 → 批2 验收。**无 migration**(合同复用现有预算 scope)。需:②窗口检查(0 running 且 60s 可 claim=0)+ ④单次重载。
- **Window B(复盘)**:C6 migration 106(预算 scope cron=$5/single=$1)+ C7 域 + C8 worker 分支 → apply 106 + 重载 → C9/C10/C11 → 批3 验收。需:①口头确认当日 pg_dump 在工作树之外 + ③迁移三段式过目。
