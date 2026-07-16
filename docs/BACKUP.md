# V-KPI 灾备策略(gate_12)

四件套备份;本地优先、生产同构。

| 资产 | 怎么备 | 频率 | 保留 | 恢复 |
|---|---|---|---|---|
| **DB** | `pg_dump -Fc`(自定义格式) | 每日 + 上线前 | 最近 14 份 | `pg_restore --clean --no-owner -d $DATABASE_URL <dump>` |
| **.env**(密钥) | 文件快照(不进 git) | 每次改 env | 最近 14 份 | 复制回根目录 |
| **迁移** | git(本体)+ `_down.sql`(回滚)+ meta 指纹 | 随提交 | 永久(git) | `git checkout` + 跑 down 脚本 |
| **R2 媒体** | runtime 同步 / 桶版本控制 | 持续 | 桶策略 | 从 R2 拉回 |

## 一键备份(本地)
```bash
bash scripts/ops/backup_local_vkpi.sh        # → runtime/db-backups + runtime/env-backups
RETAIN=30 bash scripts/ops/backup_local_vkpi.sh   # 自定义保留份数
```
产物:`runtime/db-backups/vkpi-<stamp>.dump` + `.meta.json`(含 migration_max)+ `runtime/env-backups/.env.<stamp>`。

## 生产备份
```bash
SSH_TARGET=viltrox bash scripts/ops/backup_prod_vkpi.sh   # ssh 到 prod 后 pg_dump + 拉回本地
```

## 上线前检查清单(gate_12)
- [ ] 跑一次 `backup_local_vkpi.sh`,确认 dump 非空 + meta 的 migration_max = 当前序列 max。
- [ ] 确认 `.env` 快照已生成(线上 `.env` 不随 rsync,需单独备 —— 见 [[vkpi-deploy-env-not-synced]])。
- [ ] 确认每张迁移都有配套 `_down.sql`(回滚路径)。
- [ ] 定期演练一次 `pg_restore` 到临时库,验证可恢复(非只备不验)。

## migration 243→244 专用发布证据门禁

migration 244 在执行前必须通过独立的离线证据门禁。它要求新的
migration-243 dump、SHA/metadata、隔离恢复、row anchors、244 正向和
down SQL 回滚全部绑定到同一归档；缺一项即阻断。

运行：<code>python3 scripts/ops/audit_migration_243_244_release_gate.py</code>

详细契约和操作顺序见
[migration 243→244 证据门禁](runbooks/VKPI_MIGRATION_243_244_RELEASE_GATE.md)。
门禁通过只允许申请单独授权的 canary，不自动授权或执行 migration 244。

## 自动化(可选)
把 `backup_local_vkpi.sh` 挂到 cron / systemd timer(参考 `vkpi-sync-daily.timer` 模式)即每日自动。
