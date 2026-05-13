# P3.10C + P3.10D Scope / Runtime Audit

日期: 2026-05-13
范围: 权限协作 audit + 版本/handle/status/Top100 数据状态 audit
备份: `/Users/bibiboer/Documents/V-KPI-backups/vkpi-before-p310c-p310d-audit-20260513-090431.tar.gz`

## 结论

P3.10C 不应定义为“权限预埋”。当前系统已经有较完整的权限和数据 scope 基础，真实工作应是 audit + 加固。

P3.10D 应作为基础卫生轮次保留。当前最明确的问题不是 git dirty，而是运行中后端版本滞后于仓库 HEAD，以及 Daily Top100 上游产品监控源为空。

## P3.10C 权限 / Scope 审计

新增脚本:

```bash
PYTHONPATH=backend .venv/bin/python scripts/audit_vkpi_scope_permissions.py
```

当前输出:

```text
status=pass
endpoints=252
guarded=248
public_allowlisted=4
unguarded=0
scoped_services=33/85
```

允许无 staff guard 的 4 个 endpoint:

| 文件 | 函数 | 方法 | 路径 | 原因 |
|---|---|---|---|---|
| `backend/app/api/routers/vkpi.py` | `redirect_link` | GET | `/go/{slug}` | 公共短链跳转 |
| `backend/app/api/routers/vkpi.py` | `shopify_order_webhook` | POST | `/api/vkpi/webhooks/shopify/orders` | Shopify webhook, 由服务层签名校验 |
| `backend/app/api/routers/vkpi.py` | `shopify_order_webhook` | POST | `/api/vkpi/webhooks/shopify` | Shopify webhook, 由服务层签名校验 |
| `backend/app/api/routers/vkpi.py` | `shopify_refund_webhook` | POST | `/api/vkpi/webhooks/shopify/refunds` | Shopify webhook, 由服务层签名校验 |

配套 smoke:

```bash
./scripts/run_smoke.sh smoke_vkpi_scope_collaboration_contract.py
```

结果:

```text
PASS=1 / FAIL=0 / TOTAL=1
```

覆盖内容:

- 普通员工不能通过 query 参数扩大到其他员工 KOL。
- 普通员工不能通过 query 参数扩大到其他员工 project。
- owner/admin 可以查看全部，也可以指定员工 scope。
- out-of-scope KOL/project 写入路径拒绝。
- list payload 返回 scope metadata，供前端“我的/全部/团队”判断。

## P3.10D 版本 / 数据状态审计

新增脚本:

```bash
bash -lc 'source scripts/runtime_env.sh >/tmp/vkpi-runtime-env-audit.log && PYTHONPATH=backend .venv/bin/python scripts/audit_vkpi_runtime_state.py'
```

当前输出摘要:

```text
cwd=/Users/bibiboer/Documents/V-KPI——marketing
git_sha=6f56e83
dirty_count=2
database=viltrox2
server_matches_repo=False
client_matches_server=False
staff_active=11/11
monitored_products=0
outreach_suggestions_new=90
status_conflicts=0
```

解释:

- `dirty_count=2` 是本轮新增的两个审计脚本，不是历史脏改。
- `server_matches_repo=False` 表示 8102 后端仍在跑旧 commit。当前不是 `git_sha=unknown`，而是 server stale。
- `client_matches_server=False` 与上面一致，重启后端/前端后需要复测。
- `monitored_products=0` 是 Daily Top100 的真实上游缺口：候选表已有 90 条 new suggestions，但产品监控列表为空，所以定时 monitor 不会持续生成新候选。
- `status_conflicts=0` 表示当前审计规则未发现账号 synced 与平台配置硬冲突；但 platform/account 状态语义仍需 P3.11/P3.13 页面层展示统一。

当前 DB 摘要:

| 指标 | 值 |
|---|---:|
| DB | `viltrox2` |
| staff_total | 11 |
| staff_active | 11 |
| monitored_products | 0 |
| outreach_suggestions_total | 90 |
| outreach_suggestions_new | 90 |
| daily_digest_runs | 35 |
| daily_digest_items | 89 |
| industry_accounts | 3 |
| empty_industry_handles | 0 |

## 对路线的影响

### P3.10C

状态: 可收口为 audit pass，不需要重写权限模型。

剩余小项:

- 后续每个新增 router 必须跑 `scripts/audit_vkpi_scope_permissions.py`。
- 如新增 public route，必须显式加入 allowlist 并写理由。
- P3.8/P3.10 后续只需要 E2E 双账号验证，不需要大规模回头改 API。

### P3.10D

状态: 部分完成。

需要继续处理:

- 重启后端使 `/health` 的 `git_sha` 对齐当前 HEAD。
- 干净包运行时需要 `APP_GIT_SHA` 或构建 manifest，否则没有 `.git` 时可能显示 unknown。
- Daily Top100 要进入 P3.11：先补 monitored products / trigger source，而不是重做 digest assignment。

### P3.11

真实主题应改为: Daily Top100 候选源诊断 + 触发链路修复。

当前明确事实:

- `vkpi_outreach_suggestions` 不是空，已有 90 条 new。
- `vkpi_monitored_products` 是空，导致 `morning_sync` / `analytics_monitor` 无产品源可跑。
- 下一步不是“重做 Top100”，而是明确产品源登记、手动触发入口、定时任务是否真正创建 suggestions。

## 验证记录

```bash
PYTHONPATH=backend .venv/bin/python -m py_compile scripts/audit_vkpi_scope_permissions.py scripts/audit_vkpi_runtime_state.py
PYTHONPATH=backend .venv/bin/python scripts/audit_vkpi_scope_permissions.py
bash -lc 'source scripts/runtime_env.sh >/tmp/vkpi-runtime-env-audit.log && PYTHONPATH=backend .venv/bin/python scripts/audit_vkpi_runtime_state.py'
./scripts/run_smoke.sh smoke_vkpi_scope_collaboration_contract.py
```

结果:

- py_compile: PASS
- scope audit: PASS, 252 endpoints, 0 unexpected unguarded
- runtime audit: PASS with 2 warnings
- scope smoke: PASS=1 / FAIL=0
