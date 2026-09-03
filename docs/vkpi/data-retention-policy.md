# V-KPI 第三方数据保留期与隐私处理策略(S-07 / S-08 / S-09)

生效日期:2026-09-02(公测前)。适用范围:V-KPI marketing 平台从第三方平台抓取或由员工录入的
创作者数据(联系方式、评论、视频证据、Apify 原始载荷)以及平台自身签发的一次性访问凭证。

本策略是**代码可执行的**:每一条都对应一段代码与一个测试;没有落地代码的条目会明确标「待办」。

## 1. 数据分类与保留期

| 类别 | 表 / 列 | 保留期 | 到期动作 | 执行者 |
| --- | --- | --- | --- | --- |
| Apify 原始载荷(抓取输入/输出) | `apify_jobs.payload` | **90 天**(自 `created_at`,仅终态 done/failed/blocked 行) | `payload` 置 NULL,盖章 `payload_purged_at`;行保留(被 search_sessions / tracking 表 FK 引用,且是计费/记账证据) | 日任务 `vkpi_data_retention_purge` |
| 第三方评论(原始 UGC) | `vkpi_comments`(按 `fetched_at`,缺则 `created_at`)、`kol_comments`(`created_at`) | **180 天** | 整行删除;情感/支柱/意图等**聚合结果**在别的表,不受影响 | 同上 |
| 创作者联系方式 | `vkpi_kol_pool_contacts` 行、`vkpi_kol_pool.email` | **随抑制即时清**(无年龄阈值) | 命中活跃 `vkpi_kol_contact_suppressions` 指纹的联系方式立即删除 / 清空;评估只走 HMAC 指纹,不比对明文 | 同上,每次运行都扫 |
| 视频证据 / 账号档案 | `vkpi_kol_video_evidence` 等 | 待办(见 §6) | — | — |

保留天数可用 env 调整(非法值回落默认):`VKPI_RETENTION_APIFY_PAYLOAD_DAYS`(90)、
`VKPI_RETENTION_COMMENTS_DAYS`(180)、`VKPI_RETENTION_BATCH_LIMIT`(5000,每桶每表每次上限,防长事务)。

## 2. 执行机制:每日任务,默认只报数

- 任务:`backend/app/services/scheduler/jobs_retention.py::job_vkpi_data_retention_purge`,
  注册于 `jobs.py`,每日 03:10(中国时区),`id="vkpi_data_retention_purge"`。
- **默认关**:未开闸时为 dry-run——只 `COUNT` 候选、零写,产出一条结构化日志
  `scheduler.vkpi_data_retention_purge`(dry_run=true,各桶 candidates)。这就是「若放量会删多少」的每日体检。
- 放量条件(任一):env `VKPI_DATA_RETENTION_PURGE=1`,或 `scheduler_tasks` 注册表里
  `task_key='vkpi_data_retention_purge'` 的 `enabled=TRUE`(运维 Ops 页;注册表行由运维手工种子,见交接包)。
- 幂等:已盖章的 Apify 行、已删除的评论、已清的联系方式不会再次成为候选。
- 诚实降级:迁移 308 未应用(缺 `payload_purged_at`)→ Apify 桶跳过并注明;抑制指纹密钥
  (`VKPI_CONTACT_SUPPRESSION_HMAC_KEY`)缺失 → 联系方式桶 fail-closed 跳过并注明;表缺失 → 该桶注明 unavailable。
- 日志只记数字,绝不记邮箱 / 电话 / token / 指纹原文。

## 3. 一次性凭证(邀请 / 重置 / 验证 / 门户 token)

- `email_tokens.token` 与 `vkpi_kol_portal_tokens.token` 自本版起只存 `sha256$<hex>` 摘要;
  原文只在签发瞬间出现一次(邮件、激活链接、issue-token 响应)。
- 校验端用 `token IN (摘要, 原文)` 兼容切换前签发、尚未过期的明文行;提交值若本身是 `sha256$` 形态,
  不走原文分支(库泄后拿摘要当 token 不得过闸)。明文行随 TTL(邀请 48h / 重置 1h / 验证 7d)自然清零。
- 门户 token 默认 **90 天**过期(`VKPI_PORTAL_TOKEN_TTL_DAYS`);迁移 308 前的老行 `expires_at` 为 NULL,
  读端按 `created_at + 90 天` 判。重新发放即**轮换**(旧链接立即失效),不再幂等复用。
- 公开端点 `POST /api/admin/staff/accept-invite`、`GET /api/admin/staff/invite/status` 挂
  `login_register` 限流桶(匿名 10 次 / 60 秒 / IP);`invite/status` 只回 `valid/state/message/时间戳`,不回 email / 姓名。

## 4. 联系方式读端脱敏

- 新池(`/api/admin/vkpi/kol-pool/*`):列表/工作台一律 `e***@d***`;真值只经带审计 + 限速的 reveal 端点。
- 旧 kol_ops(`/api/admin/kol/kols`、`/kols/{id}`):自本版起列表/详情同口径脱敏(`_mask_kol_contacts`),
  且不再按 `contact_email` 模糊搜索。前端当前无调用方(grep 无 `/api/admin/kol/kols`),故保留路由但脱敏,不下线。

## 5. 创作者的删除 / 查询请求(DSAR)

当前通道:`vkpi_kol_contact_suppressions`(`reason='legal_request'` 等)——记录一条抑制后,
联系方式在下一次日任务运行时即被清除(开闸状态下);评论与原始载荷按 §1 到期清。
待办:面向创作者的自助入口与 30 天内人工响应流程(产品侧,不在本策略代码范围)。

## 6. 待办 / 已知缺口(诚实清单)

1. `vkpi_kol_video_evidence`、账号档案快照的保留期未定(需先确认 fit / 推荐链对历史证据的依赖)。
2. `vkpi_kol_pool.other_contacts_json` 是展示快照,抑制清理只清 `contacts` 行与 `email` 列;快照重建依赖后续 enrich 写路径。
3. 抑制写入时的**同步**清理(`record_suppression` 钩子)未加,目前是「下一次日任务」级别的即时(≤24h)。
4. 视频 / 媒体在 R2 的对象保留期与备份(gpg)保留期未纳入本任务。
5. `scheduler_tasks` 注册表种子未随迁移 308 下发(308 只加列/索引),需运维手工插入(见交接包)。

## 7. 验收

```bash
.venv/bin/python -m pytest tests/test_s09_data_retention_purge.py tests/test_s08_token_hashing_and_portal_expiry.py tests/test_s07_kol_ops_contact_masking.py -q
```
