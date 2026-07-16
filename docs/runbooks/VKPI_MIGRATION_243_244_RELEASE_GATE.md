# V-KPI migration 243→244 离线证据审计

## 当前结论

`scripts/ops/audit_migration_243_244_release_gate.py` 是纯离线、只读、
fail-closed 的证据审计器。它不连接 PostgreSQL、不访问网络、不调用
`pg_restore`/`psql`、不执行 migration，也不持有签名私钥。

它**不是授权控制器**。即使将来所有本地证据检查都通过：

```text
safe_to_apply = false
safe_to_start_separately_authorized_canary = false
migration_execution_authorized_by_this_audit = false
```

当前 producer 与 runner trusted-key allowlist 都是不可变的
`MappingProxyType({})`。仓库内没有“先放一个公钥以后再审批”的过渡配置，
因此正式 key ceremony 完成以前，任何 bundle 都不能成为可信授权证据。

未来若要授权，必须另建并评审一个仓库外控制器，同时满足：

1. 控制器签发不可预测、短时、一次性的 challenge；
2. challenge 绑定目标环境、数据库实例、当前 migration 状态、bundle、HEAD；
3. 控制器在动作前重新读取 live target；
4. challenge 在 durable ledger 中原子消费，不能重放；
5. 授权人与执行人、producer 与 runner key 有职责隔离。

本审计器没有伪造或模拟这些控制，历史 replay API 也永远不能授权。

## 默认运行与退出码

```bash
python3 scripts/ops/audit_migration_243_244_release_gate.py
```

默认输入：

- `runtime/db-backups`
- `runtime/ops/vkpi-migration-244-approved-source-manifest.json`
- `runtime/ops/vkpi-migration-243-isolated-restore-evidence.json`
- `runtime/ops/vkpi-migration-244-rehearsal-evidence.json`

只有显式传入 `--output` 才写报告；否则只写 stdout。离线审计不会返回
“授权成功”退出码：

| 退出码 | 含义 |
|---:|---|
| 1 | 审计完成；结果仅为 advisory，无论证据是否完整都未授权 |
| 2 | 参数、路径、最大年龄或本地读取错误 |

`--now` 只进入历史 replay 路径；不能与 `--output` 组合，也没有 CLI 密钥
注入口：

```bash
python3 scripts/ops/audit_migration_243_244_release_gate.py \
  --now 2026-07-14T00:00:00Z
```

生产 Python API `audit_gate(...)` 不接受 `now`、key mapping 或
`replay_mode`；测试/历史复核只能显式调用 `audit_replay(...)`，其所有
`safe_*` 结果仍固定为 `false`。

## 文件与 JSON 边界

所有决策文件通过以 repo root 为起点的 descriptor-pinned `openat` 链读取：

- 每一层目录和最终文件都使用 `O_NOFOLLOW`；
- `fstat` 验证 regular file、当前 owner、权限、`nlink=1` 和大小上限；
- repo 内父目录不得 group/world writable；
- bytes 与 SHA-256 来自同一个已经打开的 FD；
- 读取后复核 FD 与路径的 device/inode/mode/owner/nlink/size/mtime/ctime；
- symlink、hardlink、路径替换和读取中变化全部 fail closed。

JSON 使用严格 decoder：重复 object key、`NaN`、`Infinity`、未知字段、缺失
字段全部失败。canonical JSON 设置 `allow_nan=false`。输出不回显未信任字段
内容、DSN、密码、token、私钥或完整环境变量。

## Source manifest 与签名

待审批 source manifest 必须精确绑定当前 HEAD 和三个文件：

```text
migrations/243_vkpi_event_radar.sql
migrations/244_vkpi_event_radar_truth_scope.sql
migrations/244_vkpi_event_radar_truth_scope_down.sql
```

每个 source manifest、metadata、restore evidence、rehearsal evidence 和 receipt
都使用 Ed25519 producer attestation，签名覆盖固定 attestation 字段，并通过
`payload_sha256` 绑定完整 canonical JSON。未知 key、自签名、错误或过期签名、
签名早于动作或晚于 finalization 都失败。

当前仓库 allowlist 为空，所以测试私钥只能传给 `audit_replay`；测试 key 绝不
成为 production trust anchor。

## migration state 合同

backup、restore 和 rehearsal 不再只信任一个 `migration_max` 字符串。每个阶段
必须包含：

```json
{
  "version_keys": ["<sorted unique schema_migrations keys>"],
  "version_keys_sha256": "<canonical key-list sha256>",
  "content_sha256": "<database-computed key plus applied_at content sha256>"
}
```

要求：

- pre/restore key set 精确一致，最后一个 key 为 243，且不含 244；
- post-forward key set 精确等于 `pre + [244]`；
- post-rollback key set 和 content digest 精确回到 pre；
- post-forward content digest 必须不同于 pre；
- 每阶段 `schema_migrations` anchor 等于 key 数量；
- pre 必须小于 PostgreSQL signed bigint 上界，`pre + 1` 不得溢出。

所有 exit code、marker count、row count、duration 和 rollback precondition 都
必须是范围内的精确整数；JSON `true/false` 不能代替 `1/0`。

## `pg_restore` runner receipt

单有 `PGDMP` 前缀、`archive_verified=true`、自报命令或一段伪 TOC 不能通过。
`pg_restore_list` 和 `pg_restore_execute` receipt 都必须嵌入独立 runner
attestation，固定字段至少包括：

```text
runner_class
runner key_id and Ed25519 signature
pg_restore binary SHA-256 and exact version
exact argv array
dump SHA-256
stdout SHA-256
stderr SHA-256
critical TOC object list
```

list receipt 还必须保存可本地解析的完整 TOC stdout；唯一 `TOC Entries` 数量
必须与 entry 行数一致，并包含：

```text
TABLE public schema_migrations
TABLE DATA public schema_migrations
TABLE public vkpi_events
TABLE DATA public vkpi_events
```

只有 allowlisted runner key 且 `runner_class=controlled_production` 才可能形成
“真实 runner attestation”。测试 fixture 必须标为 `offline_test_fixture`，因此
即使测试签名有效仍只能 advisory/failed，不能声称运行过真实 restore。

## Restore 与 rehearsal receipts

所需 receipt labels：

```text
backup:
  pg_restore_list
restore:
  pg_restore_execute
  row_anchor_readback
rehearsal:
  migration_244_up
  migration_244_post_apply
  migration_244_down
  migration_244_post_rollback
```

每份 receipt 必须绑定同一 bundle、HEAD、source manifest、dump、严格时间窗和
对应 migration-state/anchor/check digest。restore 必须是 non-live、isolated、
non-exposed，且 readback 为 243 marker=1、244 marker=0。

rehearsal 的 anchor 关系：

| 阶段 | `schema_migrations` | 其他 anchors |
|---|---:|---|
| restore / pre-forward | `pre` | `pre` |
| post-forward | `pre + 1` | 等于 `pre` |
| post-rollback | 回到 `pre` | 等于 `pre` |

所有 receipt 仍只是历史证据；本地签名、mtime 和 hash 不能替代 live target
challenge 与消费账本。

## 受控实施顺序（未来，需另行授权）

1. 审批 producer/runner key ceremony 和仓库外 authorization controller。
2. 重新核验 repo、branch、HEAD、dirty scope 和 live migration state。
3. 冻结并签名 source manifest，同时采集完整 migration key/content state。
4. 在写冻结窗口创建精确 migration-243 custom dump 与 sidecar。
5. 由受控 runner 生成 binary/argv/stdout/stderr/TOC attestation。
6. 在非 live 隔离 PostgreSQL 恢复同一 dump，回读 anchors 与 migration state。
7. 执行 244 forward，回读 key set/content/anchors/checks。
8. 满足 rollback preconditions 后执行 down SQL并证明精确回到 pre。
9. 运行本离线审计，只把结果作为提交外部审批的附件。
10. 外部控制器重新绑定 live target、签发并原子消费一次性 challenge。

未完成第 1 和第 10 步时，禁止把本地 `evidence_bundle_complete` 解释为可上线。

## 契约测试

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_migration_243_244_release_gate.py \
  tests/test_migration_244_event_radar_scope.py
```

测试只使用临时目录、伪 archive bytes、`offline_test_fixture` runner 和测试模块
内私钥；不连接 DB、不访问网络、不调用恢复命令、不执行 migration。对抗覆盖
默认空信任、生产 API 注入、重复 key/非有限数、布尔伪整数、bigint 溢出、
migration key/content 合同、关键 TOC、symlink、hardlink、跨 root 重放、24 小时
上限和千行卫兵。
