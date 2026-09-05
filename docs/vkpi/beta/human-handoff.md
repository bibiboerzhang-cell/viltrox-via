# 公测人工交接包(prod 步骤 = 用户执行包 · v0.3 · 2026-09-02)

> **原则**
> - 本包只列「人做的事」:每条都是 **步骤 → 验收 → 回退** 三段;prod 上的 `.env` 我不读不写,**这里只列键名,不写任何值**。
> - 凡需要重启服务的动作,一律放进维护窗做(见 `maintenance-window.md`);改 `.env` 前先做快照(`docs/BACKUP.md` 口径)。
> - 标 `verify_on_prod` 的项,仓内无证据,以 prod 上跑出来的结果为准;标 `推断` 的数字不是实测。
> - 来源:v0.2 取证时的 `scratchpad/beta/plan.json`(human_items / blockers / should)+ `A/architecture.json`(两份 JSON 为会话易失文件,本次合成时已不在,编号沿用);
>   v0.3 按 2026-09-02 21:56 EDT 的共享工作树(基线 HEAD ec804ed6,43 个修改 + 40 个新文件,**全部未提交、未上 prod**)核对了键名、迁移与脚本。
> - **v0.3 新增**:§0 键表用 W8 真实键名替换占位,并补 W6 / W9 / W11 / A1-W1 引入的键;H-06 升级为 P0(launchd 代理仍在);新增 H-13~H-18;§3 改用 `scripts/ops/alert_egress_check.py`;H-08 修正 pip-audit 为「仅告警」。

prod 事实(仓内证据):主机单机,SSH 别名 `viltrox`;应用根 `/opt/viltrox-2.0`(`.env` 在根、代码在 `current/`、解释器 `.venv/bin/python`);
web 单元 `viltrox-2.0-test.service`(gunicorn ×2,`Environment=ENABLE_SCHEDULER=1`,内含 scheduler);车道 `vkpi-worker-interactive.service` + `vkpi-worker-bulk@1..15.service`;`vkpi-redis-worker.service`;
定时器(UTC,`scripts/ops/systemd/*.timer` 实测)`vkpi-backup-r2.timer` 00/06/12/18、`vkpi-health-sentinel.timer` 01:30、`vkpi-sync-daily.timer` 04:00、`vkpi-sync-deadman.timer` 10:15。
**EnvironmentFile 顺序**:worker 单元先读 `/opt/viltrox-2.0/.env`,再读 `/etc/vkpi/vkpi-lane-overrides.env`(后者覆盖前者;且每次 deploy 会用仓库模板 `scripts/ops/systemd/vkpi-lane-overrides.env` 原样重装)——
**在 prod 手改 lane-overrides 会被下一次发车覆盖**,要改它必须走代码车道改模板;`.env` 不随 rsync,手改安全。
最近一次发车:`runtime/ops/post-deploy/20260902T035013Z-b6a1b14484d3/outcome.json` = `failed`(排水非空);prod 仍在 b5c9d0ff6(9/1 落地)。

---

## 0. 速查:prod 上要动的键(只列名)

### 0.1 公测前必看

| 键 | 用途 | 谁读 / 改后重启 | 公测期方向 | 出处 |
|---|---|---|---|---|
| `ALLOWED_EXTERNAL_STAFF_DOMAINS` | 允许邀请的外部邮箱域(逗号分隔),**优先用它** | web | 只放测试者域 | BK-02 |
| `ALLOW_EXTERNAL_STAFF_EMAILS` | 放开任意外域(1/true/yes),比上面宽 | web | 不建议 | BK-02 |
| `SITE_URL` | 激活 / 重置链接的域;缺省时链接指向一个 `/activate` 404 的域 | web | `https://viltroxtest.com`(以 H-04 确认为准) | BK-02 |
| `SMTP_HOST` `SMTP_PORT` `SMTP_USER` `SMTP_PASS` `FROM_EMAIL` | 邀请 / 找回密码邮件;`email_service_available()` = **HOST/USER/PASS 三者齐** | web | 必配(L-entry 接 Resend 前唯一通道) | BK-02 |
| `RESEND_API_KEY` `RESEND_API_KEY_PREVIOUS` | L-entry 落地后可替代 SMTP | web | 视 L-entry 回执 | BK-02 |
| `VKPI_ALERT_WEBHOOK_URL` `VKPI_ALERT_WEBHOOK_KIND`(feishu/slack/generic)`VKPI_ALERT_WEBHOOK_SECRET`(飞书签名,可选) | 告警出站唯一通道 | 哨兵 / 看门狗 timer 每次运行读 `.env`;web/worker 重启后生效 | 必配 | BK-07 |
| `VKPI_ALERT_WEBHOOK_TIMEOUT_S` `VKPI_ALERT_DEDUPE_HOURS`(默认 6)`VKPI_ALERT_ESCALATE_AFTER`(默认 3)`VKPI_ALERT_SILENCE_KEYS` `VKPI_ALERT_NOTIFY_RECOVERY` | 出站细调 | 同上 | 默认即可 | BK-07 |
| `SENTRY_DSN` | 已设;W8 已把 import 包成可选(缺 `sentry_sdk` 只告警不崩);`sentry_sdk` **仍不在 requirements.txt** | web | 不动;想真上报要先加依赖(代码车道) | BK-07 |
| `LLM_MONTHLY_BUDGET_USD` | AI 总闸;缺=0=全挡 | worker(**现值在 lane-overrides 模板里**) | 显式;公测总闸建议 ≤$300/月(改模板走代码) | BK-04 / F10 |
| `VKPI_DISCOVERY_DAILY_BUDGET_USD` | 在线发现日闸(全局,`profile_discovery_rounds.py`) | worker | 视 H-12 | BK-04 |
| `VKPI_USER_QUOTA_ENABLED`(默认 1) | 人均日额度总开关(W8) | web | 保持 1 | BK-04 |
| `VKPI_USER_DAILY_QUOTA_SMART_SEARCH_ONLINE`(30)`VKPI_USER_DAILY_QUOTA_VIDEO_DEEP_ANALYSIS`(20)`VKPI_USER_DAILY_QUOTA_DEEP_CRAWL`(40)`VKPI_USER_DAILY_QUOTA_OUTREACH_SEND`(60) | 人均 UTC 日次数闸;≤0 = 该项不限 | web | 公测建议先用默认;超预算再压 | BK-04 |
| `VKPI_USER_RATE_LIMIT_EXPENSIVE`(`12/60`) | 人均高成本操作连点闸 | web | 默认 | BK-04 |
| `BOARD_RBAC_ENFORCE` | 板块可见性闸:0=只记 `board_rbac.would_block` 日志,1=真拦(`main_request_guards.py:276`) | web | 观察 24h 后置 1(§5) | SH-10 |
| `VKPI_DRAIN_BLOCKING_SCOPE` | 发车排水阻断范围:`all`(默认,历史口径)/ `interactive`(只算交互车道,批量行只报数) | 发车探针(远端 `env -i` 下只认 prod `.env`) | 公测期建议 `interactive`(H-17) | BK-09 / A1-W1 |
| `SMART_SEARCH_SESSION_MAX_RUNNING_SEC`(1800,夹 [60, 86400]) | 智能搜索会话停滞收敛上限 | worker | 默认 | SH-01 / W9 |
| `VKPI_PORTAL_TOKEN_TTL_DAYS`(90) | KOL 门户 token 有效期 | web | 默认 | S-08 / W6 |
| `VKPI_CONTACT_SUPPRESSION_HMAC_KEY` | 联系方式抑制指纹密钥;**缺失 → 保留期任务的 `suppressed_contacts` 桶 fail-closed 整桶跳过**,DSAR 勿联系也依赖它 | web + scheduler | 核实存在(H-14) | S-09 / W6 / W11 |
| `VKPI_DATA_RETENTION_PURGE` | 保留期 purge 放量闸;不设 = 每日 03:10(中国)只报数 | scheduler(现在 web 内) | 公测期**不设**(只看报数) | S-09 / W6 |
| `VKPI_RETENTION_APIFY_PAYLOAD_DAYS`(90)`VKPI_RETENTION_COMMENTS_DAYS`(180)`VKPI_RETENTION_BATCH_LIMIT`(5000) | 保留期参数;隐私页 `/api/public/legal/policy` 也读同名键 | scheduler + web | 默认 | S-09 / W11 |
| `VKPI_PRIVACY_CONTACT_EMAIL` | 隐私页与 DSAR 回执显示的联系邮箱(缺省显示占位 `privacy@viltrox.com`) | web | H-05 定稿后填 | BK-08 / W11 |
| `VKPI_DSAR_CAPTCHA_MODE`(off/shared_secret)`VKPI_DSAR_CAPTCHA_SECRET` `VKPI_DSAR_IP_HASH_KEY` `VKPI_DSAR_BRAND_SCOPE` | 公开 DSAR 表单(按 IP 5 次/小时)的验证码占位 / IP 哈希盐 / 品牌范围 | web | 公测期 `off`;`IP_HASH_KEY` 缺省回落到 suppression 密钥 | BK-08 / W11 |

### 0.2 运维 / 可选

| 键 | 用途 | 谁读 | 公测期方向 | 出处 |
|---|---|---|---|---|
| `QDRANT_URL` | 有值走 server,无值走本地文件(并发撞锁) | web + worker | 按 `docs/vkpi/qdrant-server-runbook.md` 起 docker 后设(H-16,可选) | SH-05 / F3 |
| `ENABLE_SCHEDULER` | web 单元里现为 1;独立 `vkpi-scheduler.service` 启用后 web 置 0(**单独的 owner 变更**) | web | 随 H-15,公测期不切 | SH-06 |
| `VKPI_DEPLOY_SEPARATE_SCHEDULER`(**控制器 env,非 prod .env**) | =1 时 deploy 安装 / 启用 `deploy/systemd/vkpi-scheduler.service` | train / deploy | 公测期不导出 | A1-W1 |
| `VKPI_TRAIN_DRAIN_WAIT_SECONDS`(5400)`VKPI_TRAIN_DRAIN_PROBE_INTERVAL_SECONDS`(120)(**控制器 env**) | 班车排水等待 | train.sh | 窗内 `3600` / `60` | BK-09 |
| `POSTGRES_POOL_MAX_SIZE` | 代码缺省 64;timer/脚本进程要显式给小值 | 各 timer | 随 F4 | F4 |
| `APIFY_TOKEN` `APIFY_TOKEN_PREVIOUS` | 单 token 无池 | worker | 视 H-12 | C4 |
| `R2_BACKUP_BUCKET` `R2_BACKUP_ENDPOINT` `R2_BACKUP_ACCESS_KEY_ID` `R2_BACKUP_SECRET_ACCESS_KEY` | 备份专用 token;缺则回退共享 `R2_*` | backup timer | 核实存在 | BK-11 |
| (DB 表,非 env)`vkpi_provider_budget_caps` 的 `monthly_total` / `provider:apify` / `provider:gemini` / `provider:claude` / `provider:openai` 行 | scope 级闸 | 运行时读表 | 压到公测预算 | BK-04 |
| (DB 表,非 env)`scheduler_tasks`(迁移 130)`task_key='vkpi_data_retention_purge'` | 保留期 purge 的注册表闸(与 env 二选一放量) | scheduler | 公测期不种 / `enabled=false` | S-09 |

改 `.env` 的标准动作(每次都一样):

```bash
ssh viltrox
sudo -u viltrox install -d -m 0700 /opt/viltrox-2.0/env-backups
sudo -u viltrox cp -p /opt/viltrox-2.0/.env "/opt/viltrox-2.0/env-backups/.env.$(date -u +%Y%m%dT%H%M%SZ)"
sudo -u viltrox "${EDITOR:-nano}" /opt/viltrox-2.0/.env        # 只加/改键;保持 0600
sudo systemctl restart viltrox-2.0-test.service                  # web 键
# worker 键才需要(会打断在飞任务,只在维护窗做):
# sudo systemctl restart vkpi-worker-interactive.service 'vkpi-worker-bulk@{1..15}.service' vkpi-redis-worker.service
curl -s -m 10 https://viltroxtest.com/health | head -c 200 ; echo
```

回退(通用):`sudo -u viltrox cp -p /opt/viltrox-2.0/env-backups/.env.<stamp> /opt/viltrox-2.0/.env && sudo systemctl restart viltrox-2.0-test.service`。

---

## 1. 逐条:human_items

### H-01 拍板测试者范围(NDA 内测 vs 独立 beta 实例)+ 是否有英文测试者

- 步骤:读 §2 决策表 → 在 §6「登记」写下选择 → 通知车道:甲 → L-search-core/L-quota 照常,L-ops-A1 的 beta 库分支不做;乙 → L-ops-A1 W3 并行分支开工。
  英文测试者:有 → SH-17 英文化 3–5 人日排进 L-ui-facade 之后;无 → 隐藏语言开关(0.5h,L-ui-facade)。
- 验收:BK-03 / SH-17 有 owner 写明的决定与日期;测试者手册 §5「语言」按决定改写。
- 回退:甲→乙可随时升级(数据不回流);乙→甲只需停 beta 单元。
- 耗时:决策 0.5h。

### H-02 prod `.env`:外域邮箱 / SMTP / SITE_URL / 告警 / 预算 / RBAC(`verify_on_prod`)

- 步骤:
  1. **改前基线**:用 owner 账号在浏览器登录后,同一浏览器打开 `https://viltroxtest.com/api/admin/staff/invite/capabilities`,记下 JSON(字段:`email_available` / `external_emails_allowed` / `allowed_domains` / `token_ttl_hours`(=48)/ `delivery_methods` / `site_url_configured`;预期改前 `email_available:false`、`site_url_configured:false`)。
  2. 按 §0 标准动作加键:`ALLOWED_EXTERNAL_STAFF_DOMAINS`、`SITE_URL`、`SMTP_HOST/PORT/USER/PASS/FROM_EMAIL`(或等 L-entry 回执后用 `RESEND_API_KEY`)、`VKPI_ALERT_WEBHOOK_URL`(+`_KIND`,飞书再加 `_SECRET`)、`VKPI_DISCOVERY_DAILY_BUDGET_USD`、`VKPI_PRIVACY_CONTACT_EMAIL`;`BOARD_RBAC_ENFORCE` 先**不**加(§5 观察后再加)。
     `LLM_MONTHLY_BUDGET_USD` 现值在 lane-overrides 模板里——要改请开代码车道改 `scripts/ops/systemd/vkpi-lane-overrides.env`,不要手改 prod 文件。
  3. `sudo systemctl restart viltrox-2.0-test.service`。
- 验收:
  - capabilities:`email_available:true`、`external_emails_allowed:true`(或 `allowed_domains` 含测试者域)、`site_url_configured:true`,`delivery_methods` 首项 `email_magic_link`;
  - 用**你自己控制的外域邮箱**发一封邀请:邮件到达,链接域名 = `viltroxtest.com`,点开 `/activate` 200 并能设密;激活页**不再回显**邮箱 / 姓名(W6 S-08 上线后的预期);
  - 「忘记密码」流程(L-entry 上线前用 owner 生成重置链接)邮件到达,链接 1 小时内有效;
  - §3 告警实发一条到 IM(≤5 分钟)。
- 回退:恢复 `.env` 快照 + 重启 web。
- 依赖:L-entry(Resend / 忘记密码链接 / `/login` 404)未落地时,capabilities 仍以 SMTP 三键判断。
- 耗时:0.5–1h(不含申请 SMTP 账号)。

### H-03 prod 备份核实 + 恢复演练(`verify_on_prod`)

- 步骤(核实 timer):
  ```bash
  ssh viltrox 'systemctl is-enabled vkpi-backup-r2.timer; systemctl is-active vkpi-backup-r2.timer; systemctl list-timers vkpi-backup-r2.timer --no-pager'
  ssh viltrox 'sudo journalctl -u vkpi-backup-r2.service --since "24 hours ago" --no-pager | tail -60'
  ssh viltrox 'mountpoint -q /mnt/HC_Volume_106700445 && echo volume-ok || echo VOLUME-MISSING'
  ```
  若 `is-enabled` 不是 `enabled`:`sudo systemctl enable --now vkpi-backup-r2.timer`;若单元不存在:
  `sudo install -m 0644 /opt/viltrox-2.0/current/scripts/ops/systemd/vkpi-backup-r2.{service,timer} /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now vkpi-backup-r2.timer`。
  手动跑一次:`sudo systemctl start vkpi-backup-r2.service` 再看 journal。
- 步骤(恢复演练,在本机隔离集群上做,不碰 prod):
  1. 拉最新 dump:`SSH_TARGET=viltrox bash scripts/ops/backup_prod_vkpi.sh`(或从 R2 `vkpi-db/YYYY/MM/DD/` 下载 `.dump.gz` + `.sha256`);
  2. `.venv/bin/python scripts/ops/postgres_restore_rehearsal.py --help` 按提示用 `--execute` + 确认环境变量在**隔离集群根**跑(它拒绝 prod、自建 `vkpi_restore_rehearsal_*` 库、结束自动 drop);
  3. 注意:按脚本设计,即使成功也**退出非零**(diagnostic-only),以它输出的 receipt JSON 为准;把 receipt 存到 `runtime/ops/restore-rehearsal/<stamp>.json`,并把路径交给 L-ops-A1 勾 `docs/BACKUP.md` 的「定期演练 pg_restore」项。
- 验收:timer `enabled` + `active`,`NEXT` ≤6h;journal 近 24h 出现上传 `vkpi-db/.../prod-db-<stamp>.dump.gz` 且读回校验通过;R2 控制台该前缀下有 24h 内对象;演练 receipt 含 row anchors。
- 回退:无破坏性动作;若 timer 启用后 journal 报 `boto3_missing` / 卷未挂载,`systemctl disable --now` 并转 L-ops-A1。
- 耗时:核实 15 分钟;演练 1–2h。

### H-04 确认公开可达 URL + 是否独立 beta 子域与证书

- 步骤:
  ```bash
  for u in https://viltroxtest.com/health https://viltroxtest.com/activate https://viltroxtest.com/reset https://viltroxtest.com/login https://viltroxtest.com/legal/privacy https://www.viltroxvia.com/activate https://viltroxvia.com/health; do printf '%-48s ' "$u"; curl -s -o /dev/null -w '%{http_code}\n' -m 10 "$u"; done
  ```
  预期:200 / 200 / 200 / 404(L-entry 修后 200)/ 200(W11 上线后;上线前 404)/ 404 / 301。文档里的 `admin.viltrox.com` `lab.viltrox.com`(nginx 模板 `server_name`)本机不可解析、`5.78.200.75` 直连超时(源站只接 Cloudflare 回源,是好事)。
  决定是否给 beta 独立子域(如 `beta.<域>`):Cloudflare 加代理记录 → nginx `server_name` 加名 → 源站证书(Cloudflare Origin CA 或 certbot)→ `SITE_URL` 与之一致;若走 §2 的「乙」,子域指向 beta web 单元端口。
- 验收:测试者手册 `<BETA_URL>` 填入且探针按预期;若独立子域,`curl -sI https://beta.<域>/health` 200 且证书有效。
- 回退:删 DNS 记录 / 还原 nginx 配置并 `nginx -t && systemctl reload nginx`。
- 耗时:0.5h(无子域)/ 2h(有子域)。

### H-05 法务文本

- 步骤:准备四段文本(隐私政策、服务条款、数据来源声明、「勿联系 / 删除」申请说明),审定后交 W11/L-legal-dsar。后端已备四个地址 `/legal/terms` `/legal/privacy` `/legal/data-sources` `/legal/request`(`dsar_public.py` 只做 SPA 分发)与匿名接口 `/api/public/legal/policy`(保留期键名 + 当前天数 + 联系邮箱)、`/api/public/dsar/requests`(按 IP 5 次/小时);**前端目前没有 legal 页面组件**,正文页面要另开一刀。
  同时把 `VKPI_PRIVACY_CONTACT_EMAIL` 填进 prod `.env`(H-02)。
- 验收:四页 200 且有正文、登录页可点;`/api/public/legal/policy` 返回的 `contact_email` 不是占位;测试者手册 §5 删「上线前以 NDA 为准」。
- 回退:页面纯静态,回滚即下线链接。
- 耗时:文本 2–4h(人)+ 前端页面 0.5–1 人日(代码车道)。

### H-06 【P0】公测期发车纪律(关 auto-train、维护窗拍板)

- 现状(2026-09-02 21:56 EDT 实测):`launchctl print gui/501/com.vkpi.auto-train` **找得到服务**(state = not running,calendar Hour 0 / Minute 30 已登记),`com.vkpi.verify-receipt` 同样在;两份 plist 16:41 重写过。记忆里「17:05 / 18:25 已 bootout」与现状不符——**今晚 00:30 EDT(= 09-03 04:30 UTC)它会再判一次**。此刻守卫 ②「树净」会拒(11 车道未提交),但一旦主会话提交推送、CI 转绿而人还没关它,就会无公告发车。
- 步骤:读 `maintenance-window.md` §4 推荐方案 → 拍板窗口 → **立刻**按其 §8 关掉 `com.vkpi.auto-train`(顺手关 `com.vkpi.verify-receipt`)→ 把窗口写进测试者手册 §5。
- 验收:`launchctl print gui/$(id -u)/com.vkpi.auto-train` 报 `Could not find service`;`runtime/logs/auto-train.log` 不再新增;公告窗外 7 天零发车(`runtime/ops/post-deploy/` 无新目录)。
- 回退:`maintenance-window.md` §8 的 bootstrap 命令(或 `bash scripts/ops/install_local_launchd.sh` 一次恢复三个代理)。
- 耗时:5 分钟(关)+ 0.5h(拍板)。

### H-07 购机:Linux 控制器小机(架构 A1 W2)

- 步骤:Hetzner CX22 级(≈€5–25/月增量,**推断**)→ 装 git / python3.12 / uv / rsync / ssh → 搬 `~/.ssh/config` 的 `Host viltrox` 与密钥 → secrets 改 sops/age(`runtime/ops/local-health.env` 等)→ 在小机上原样跑通一次 `freeze → verify → deploy`(先不拆 5399 行脚本)→ 再把 auto-train 守卫改成「GitHub CI success 触发」。
  是否单独起 Qdrant 小机:公测 ≤10 人先同机 docker(H-16),不单起。
- 验收:小机上 `runtime/ops/post-deploy/<stamp>-<sha12>/outcome.json` 出现一次 `"result": "success"`;笔记本合盖 24h 发车仍可发生。
- 回退:笔记本继续当控制器,小机停机不计费。
- 耗时:3–4 人日(含 L-ops-A1 的脚本改造)。

### H-08 依赖大版本升级后的全量回归发车

- 现状:W3 已在树:`PyJWT 2.9→2.13`、`starlette 1.0→1.6`、`fastapi 0.135.3→0.136.3`、`python-multipart 0.0.22→0.0.32`、`aiohttp 3.12→3.14`、`Pillow 12.2→12.3`、`cryptography 46→50`、`urllib3` / `idna` / `sse-starlette` / `click` / `httplib2` / `pyasn1` 小升。CI 的 `pip-audit` job(`.github/workflows/verify.yml`)是 **`continue-on-error: true` 的告警位,不是闸**。
- 步骤:等 W3 合入且 CI 绿 → 在维护窗 `bash scripts/ops/train.sh` → 发车后手工冒烟:登录、邀请 → 激活 → 登录(PyJWT + cookie 会话)、反馈弹窗带截图上传(python-multipart)、头像上传、`/go/<slug>` 短链、Shopify webhook 签名校验(starlette)、`/health` 200;`sudo journalctl -u viltrox-2.0-test.service --since "30 min ago" | grep -iE "starlette|multipart|jwt|traceback"` 为空。
- 验收:上述冒烟全通;CI `pip-audit` 的 summary 步骤零 `::warning`(**人工看 Actions 注解**,不是自动闸)。
- 回退:班车自带失败回滚;若回滚也失败,按 `docs/OPERATIONS_RUNBOOK.md` §6.1 手工切回上一 release(回滚路径曾失败过一次:deploy-03d7584a2.log:282,所以**回滚也要在窗内演练一次**)。
- 耗时:窗内 1.5h。

### H-09 确认 prod `/uploads/student_cards`、`reward_images` 是否为空

- 步骤:
  ```bash
  ssh viltrox 'grep -E "^(UPLOAD|MEDIA|STATIC)[A-Z_]*=" /opt/viltrox-2.0/.env | cut -d= -f1'      # 只看键名,定位上传根
  ssh viltrox 'sudo find /opt/viltrox-2.0 -maxdepth 5 -type d \( -name student_cards -o -name reward_images \) -print -exec sh -c "find \"\$1\" -type f | wc -l" _ {} \;'
  ```
- 验收:两目录文件数记录到 §6「登记」;0 → 告 L-entry 直接卸载挂载;>0 → 先迁 R2 私有前缀再卸载。
- 回退:只读命令,无回退。
- 耗时:10 分钟。

### H-10 确认 prod 旧 `kols` 表是否有邮箱行

- 现状:W6 已把 `kol_ops` 读端(列表 / 详情)脱敏、并去掉按 `contact_email` 模糊搜索;所以这一条从「阻断」降为「知道数字」。
- 步骤(只读 SELECT,按 `staging_db_clone.py` 的本机 peer 认证口径):
  ```bash
  ssh viltrox
  DB="$(sudo -u viltrox sed -nE 's#^DATABASE_URL=.*/([A-Za-z0-9_]+)(\?.*)?$#\1#p' /opt/viltrox-2.0/.env | head -1)"
  sudo -u postgres psql -d "$DB" -Atc "SELECT count(*) AS total, count(*) FILTER (WHERE coalesce(email,'')<>'') AS with_email FROM public.kols"
  ```
- 验收:数字登记;>0 时用测试者账号打开旧 KOL 列表确认邮箱显示为 `a***@…`。
- 回退:无。
- 耗时:5 分钟。

### H-11 公测期间独立 staging(验收环境不被车道重启)

- 步骤:本轮本地栈曾被其他车道中途重启并把 HEAD 推走,验收数据混入旧构建。三选一:
  (a) 本机第二套栈:另一端口(8103 已被 `vkpi-scheduler.service` 的回环健康口预留,**选 8104**)+ 隔离 PG 集群(`~/.cache` 下,`scripts/ops/restore_persistent_staging_db.py` / `staging_db_clone.py`),`APP_ROLE=admin-web ENABLE_SCHEDULER=0`;
  (b) H-07 的小机兼作 staging(推荐,顺路);
  (c) 车道纪律:发车 / 验收在飞时禁止其它 Agent 重启本地栈(memory「班车在飞禁开新工地」)。
- 验收:一次 2h 验收跑完,`/health` 的 `git_sha` 全程不变。
- 回退:停掉第二套栈即可。
- 耗时:(a) 0.5 人日;(b) 随 H-07;(c) 0。

### H-12 Apify 单 token / 发现日闸 / 调度 23 任务收敛

- 步骤:
  1. 决策:公测期是否申请第二 token(`APIFY_TOKEN_PREVIOUS` 已支持双钥轮换,但无池)——≤10 人建议不申请,改压日闸;
  2. `VKPI_DISCOVERY_DAILY_BUDGET_USD` 由 5 下调(建议 2–3,**推断**)——`.env` worker 键,窗内重启车道;
  3. 调度收敛:先只读列出 `SELECT task_key, enabled FROM public.scheduler_tasks ORDER BY task_key;`(表名按迁移 130,列名按 `jobs_retention.py` 的读法),在**窗内**关掉与公测无关的常开任务(保留:每日同步、哨兵、看门狗、备份、履约闭环、推荐刷新;`vkpi_data_retention_purge` 保持不放量),关前把 enabled 列表存到「登记」。
- 验收:`provider:apify` 当日花费 ≤ 日闸;`scheduler_tasks` enabled 数 ≤ 登记的目标数;哨兵日报无 `apify_spend_spike`。
- 回退:按登记列表 UPDATE 回 enabled;`.env` 快照恢复。
- 耗时:1h。

### H-13 演示种子(SH-03,W11 `seed_beta_demo.py`)是否在 prod 落

- 步骤:先决定要不要种(反对理由:真实池已有数据;赞成理由:项目 / 审批 / 周报 / 活动 8 个端点对新账号是空态)。要种:
  ```bash
  ssh viltrox; cd /opt/viltrox-2.0/current
  sudo -u viltrox bash -c 'set -a; . /opt/viltrox-2.0/.env; set +a; PYTHONPATH=backend /opt/viltrox-2.0/.venv/bin/python backend/scripts_local/seed_beta_demo.py --staff-id <测试者 staff_id> --json'          # dry-run
  sudo -u viltrox bash -c 'set -a; . /opt/viltrox-2.0/.env; set +a; PYTHONPATH=backend /opt/viltrox-2.0/.venv/bin/python backend/scripts_local/seed_beta_demo.py --staff-id <测试者 staff_id> --apply'
  ```
  所有行带「[演示]」前缀与 `is_demo` 标记(业务真相聚合自动排除);GMV 刻意不种。
- 验收:dry-run 的 `summary` 与 `--apply` 后一致;测试者账号首页 / 项目 / 发射台能看到「[演示]」条目;`shopify.gmv` 仍诚实空态。
- 回退:同命令加 `--purge --apply`(只删自己按自然键种的行)。
- 耗时:15 分钟;**必须在发布目录之外运行前先确认「发布目录禁跑 python」的口径**(memory 0822)——建议从 `/opt/viltrox-2.0/current` 用 `.venv` 跑,不要 `cd` 进 release 目录写文件。

### H-14 保留期 purge 与抑制密钥(S-09,W6)

- 步骤:公测期**不设** `VKPI_DATA_RETENTION_PURGE`,不种 `scheduler_tasks` 行 → 每日 03:10(中国)只出一条 `scheduler.vkpi_data_retention_purge dry_run=true` 报数日志。
  只核实一件事:`ssh viltrox 'grep -c "^VKPI_CONTACT_SUPPRESSION_HMAC_KEY=" /opt/viltrox-2.0/.env'` 应为 1(缺失 → `suppressed_contacts` 桶与 DSAR 勿联系都 fail-closed 跳过,不是崩)。
- 验收:上线次日 `journalctl -u viltrox-2.0-test.service --since "yesterday" | grep vkpi_data_retention_purge` 有 dry_run 行且各桶 candidates 数字合理(apify_payload 应为四位数量级,**推断**)。
- 回退:无写入,无回退;若日志报密钥缺失,补键 + 重启 web。
- 耗时:5 分钟。

### H-15 独立 scheduler 单元(A1-W1,`vkpi-scheduler.service`,默认关)——公测期不切

- 步骤(只在 A1 W3 之前想提前拆时做):控制器 `export VKPI_DEPLOY_SEPARATE_SCHEDULER=1` 后发车一次(deploy 原子安装单元 → `systemd-analyze verify` → enable → restart → 等 active ≤30s);之后每次发车都要带这个 env,否则单元不再被管理(仍跑旧树)。web 侧 `ENABLE_SCHEDULER=0` 是**另一笔 owner 变更**(`scripts/ops/systemd/viltrox-2.0-test.service` + `scripts/start_admin.sh:61`),未落地前两候选靠 advisory lock 共存,只有一个真跑。
- 验收:`systemctl is-active vkpi-scheduler.service`;`journalctl -u vkpi-scheduler --since -5m | grep scheduler.fleet_leader` 恰一个 leader。
- 回退:`sudo systemctl disable --now vkpi-scheduler.service && sudo rm -f /etc/systemd/system/vkpi-scheduler.service && sudo systemctl daemon-reload`;web 内 scheduler 一个租约周期内重新拿锁。
- 已知缺口:deploy 的 quiesce 不停这个单元,发车期间它跑旧代码直到 gate 段重启它。
- 耗时:窗内 0.5h。

### H-16 Qdrant server(A1-W1 速赢,`docs/vkpi/qdrant-server-runbook.md`)——可选

- 步骤:按 runbook §2 同机 docker 只绑回环 → 迁两个集合(`vkpi_kol_profile_index_v1`、`via_memory`)→ `.env` 加 `QDRANT_URL` → 窗内重启 web + 车道。**`QDRANT_URL` 是全局开关**,KOL 召回与 VIA 记忆一起切。
- 验收:搜索并发 6 路不再出现 `Storage folder ... already accessed by another instance`;`profile_recall_contract` 无 Errno 30。
- 回退:删 `QDRANT_URL` + 重启(本地文件后端仍在)。
- 耗时:1–2h。

### H-17 排水阻断收窄(BK-09 最小版,A1-W1 `VKPI_DRAIN_BLOCKING_SCOPE`)

- 步骤:prod `.env` 加 `VKPI_DRAIN_BLOCKING_SCOPE=interactive`(探针在远端 `env -i` 下只从 `--env-file` 读到它;写错值会 fail-closed 报错而不是放行)。批量车道行变成「只报数」,交互车道 + provider 预留仍阻断。
- 验收:`train.sh` 的排水等待日志出现 `apify_jobs_active_batch` 非阻断行;一次周末窗内发车在 ≤60 分钟内进入 deploy。
- 回退:删键(回到 `all`)。
- 耗时:5 分钟 + 一次发车观察。

### H-18 迁移 307–310 的发布前置条件与上线后只读核验

- 发布前置条件（2026-09-04 核对）：`train.sh` 要求完整待执行迁移集合与已审阅策略精确匹配。当前 `vkpi-additive-nullable-defaultless-v1` 仅审阅 305–307；308–310 尚不满足该策略，标准班车会阻断，不能仅设置迁移名称或手工先执行 SQL 来绕过。须先完成独立迁移审阅、备份恢复演练及旧应用兼容性验证，并确认生产切换范围。
- 只有完成上述前置条件且实际发布成功后，才做以下只读列核验：
  ```bash
  sudo -u postgres psql -d "$DB" -Atc "SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='token_version'"
  sudo -u postgres psql -d "$DB" -Atc "SELECT column_name FROM information_schema.columns WHERE table_name IN ('vkpi_kol_portal_tokens','apify_jobs') AND column_name IN ('expires_at','payload_purged_at')"
  sudo -u postgres psql -d "$DB" -Atc "SELECT column_name FROM information_schema.columns WHERE table_name='vkpi_dsar_requests' AND column_name IN ('source','public_ref','requester_contact')"
  ```
- 验收:三条各返回预期列;上线当刻**没有**在线用户被踢(307 的 NULL=0 口径);登出后旧 cookie 再访问 `/api/auth/me` 401。
- 310 还须核验 `kol_profile_incremental_refresh` 保持关闭、每日配额表存在，并保留迁移前该任务的配置快照；是否重新启用抓取是单独的运营决定。
- 回退边界：班车在可自动回退阶段恢复应用、环境及服务配置，**不会自动执行 `_down.sql` 或恢复数据库**。308 的已清理数据无法靠 down 恢复；309 down 会修改申请类型并丢弃新增申请字段；310 down 会删除配额账本。新 portal token 产生后的旧版兼容性也必须独立验证，不能把“列可兼容”当成完整应用兼容。
- 上述列核验不代表迁移演练、回退演练或业务验收通过。

---

## 2. 决策表:NDA 内测(甲)vs 独立 beta 实例(乙)

| 维度 | 甲 · NDA 内部化(employee,不授 contacts reveal / `vkpi=admin`) | 乙 · 独立 `viltrox2_beta` 库 + 脱敏种子 + 独立 web/worker 单元 |
|---|---|---|
| 测试者能看到 | 真实池 1802 人、团队收藏 781、发现 / 市场脑全量;邮箱 433 **默认脱敏**(reveal 需显式授权 + 审计);成本 / 健康页对 employee 不可见(`system.usage=none`) | 脱敏池(邮箱 / 电话 / 成本列置空),收藏为空 |
| 隔离强度 | 角色级(读全量,写各自)+ W8 人均额度 | 库级;PgBouncer 多库映射已具备 |
| 人日 | 0(建账号 10 分钟/人) | 2–3(脱敏脚本 + 单元)+ 与 A1 W3 并行 |
| 月成本 | $0 | 同机 ≈€0;独立小机 ≈€5–25(**推断**) |
| 运维 | 单实例、单发车 | 两套单元;蓝绿前发车仍全站停机;只做脱敏快照单向同步,不回流 |
| 搜索空墙(BK-01) | W1 软排除后可缓解,但前端未接前测试者仍可能看到空 | 可清零(beta 库无收藏) |
| 反馈质量 | 高(真实数据) | 中(联系方式类功能测不到) |
| 法务 | **必须 NDA**,只邀可信人 | NDA 可弱化;仍需 BK-08 页面 |
| 退出 | 禁用账号(W4 token_version 立即全设备下线)+ 审计 reveal 日志 | `DROP DATABASE viltrox2_beta` |
| 适用 | ≤10 人、内部化的外聘 / 合作方 | 不特定外部人、>10 人、需要独立发布节奏 |
| 前置(两者都要) | BK-04 人均配额(W8)、BK-02 进门、BK-01 空墙 | 同左 |
| **推荐** | **本周就用甲**(0 人日),配合 W8 上线 | 作为外部扩大时的前置,与 A1 W3 一起做 |

甲的账号建法(owner 操作):设置 → 成员 → 邀请 → 角色保持 `employee`(默认 `vkpi=write`、`kol_ops=read`、`system.*=none`)→
**不要**授联系方式 reveal、**不要**把 `vkpi` 提到 `admin`;W6 上线前把 `kol_ops` 改为 `none`(旧路由裸吐 contact_email,SH-09;W6 上线后改回 `read` 也安全)。
验收:用测试者账号 `POST /api/admin/vkpi/kol-pool/<id>/contacts/reveal` → 403;`kol_ops` 列表 → 403(或上线后脱敏);设置页不显示成本 / 健康区。

---

## 3. 专题:告警 webhook 实发验证(`verify_on_prod`)

前提:H-02 已配 `VKPI_ALERT_WEBHOOK_URL`(+`_KIND`;飞书可选 `_SECRET`)。代码只从**进程环境**读 URL,不写日志、不回显。W8 新增 `scripts/ops/alert_egress_check.py`(零数据库依赖,URL / 密钥永不打印):退出码 0=已发且 2xx,1=发送失败,2=未配置或非 https,3=key 被 `VKPI_ALERT_SILENCE_KEYS` 静默;`--dry-run` 只查配置。

1. 只查配置(不出站):
   ```bash
   ssh viltrox
   sudo -u viltrox bash -c 'set -a; . /opt/viltrox-2.0/.env; set +a; cd /opt/viltrox-2.0/current && PYTHONPATH=backend PYTHONDONTWRITEBYTECODE=1 /opt/viltrox-2.0/.venv/bin/python -B scripts/ops/alert_egress_check.py --dry-run --json'
   ```
   预期 `configured: true`、`kind: feishu|slack|generic`、`signed: true|false`,退出码 0。
2. 实发一条:去掉 `--dry-run`(同 key 6h 内去重;脚本用固定 key `egress-check`,**24h 内第二次实发要等去重窗口过或临时改 `VKPI_ALERT_DEDUPE_HOURS`**)。预期退出码 0,IM 群 ≤5 分钟收到消息「这是 alert_egress_check 发出的测试告警」。
3. 走真实管道(哨兵 → vkpi_alerts → 出站):`sudo systemctl start vkpi-health-sentinel.service`,看 `/var/log/vkpi-health-sentinel/health_sentinel_$(date -u +%Y%m%d).log` 里 `configured=True` 与 `sent`。
- 验收:2 与 3 各到达一条;退出码 2 = 键没进进程环境(检查 `.env` 是否 0600、是否 `set -a` 后再跑);3 = 命中静默清单。
- 回退:删键 + 重启 web 即回到「只落库不出声」。

---

## 4. 专题:nginx `real_ip`(SH-07 的人工半边)

现状:仓内 `deploy/nginx/viltrox-2.0.conf` 无 `set_real_ip_from` / `real_ip_header`(只有 `proxy_set_header X-Real-IP $remote_addr` 与 `X-Forwarded-For`);后端限流 / 锁定按 `cf-connecting-ip` → `x-forwarded-for` 首跳 → `x-real-ip` → `client.host` 的顺序取 IP(`rate_limiter.py:94-106`),前两者可伪造。nginx 不由 deploy 脚本管理,**prod 手改不会被发车覆盖**,但要同步回仓库模板。

1. 定位生效配置:`ssh viltrox 'sudo nginx -T 2>/dev/null | grep -nE "server_name|real_ip|include .*sites"'`,备份:`sudo cp /etc/nginx/sites-available/<file> /etc/nginx/sites-available/<file>.bak.$(date -u +%Y%m%dT%H%M%SZ)`。
2. 在 `http{}`(或该 `server{}`)加:
   ```nginx
   # Cloudflare 回源段:以 https://www.cloudflare.com/ips-v4 与 /ips-v6 当日清单为准,逐行 set_real_ip_from
   set_real_ip_from 173.245.48.0/20;
   # ...(其余 v4/v6 段)
   real_ip_header CF-Connecting-IP;
   real_ip_recursive on;
   ```
   并确认 `proxy_set_header X-Real-IP $remote_addr;`(已有)——代码侧 SH-07 改成只信 `X-Real-IP`(未在树,待车道)。
3. `sudo nginx -t && sudo systemctl reload nginx`。
4. 源站防火墙只放 Cloudflare 段(`5.78.200.75` 直连超时说明大概率已如此,**核实一次** `sudo ufw status` / 云防火墙规则)。
- 验收:`sudo nginx -T | grep -c set_real_ip_from` = 当日清单条数;access log 的 `$remote_addr` 出现真实访客 IP 而非 Cloudflare 段;从外网带伪造 `X-Forwarded-For: 9.9.9.9` 连打登录接口 11 次,第 11 次仍 429(证明桶按真实 IP)。
- 回退:`sudo cp <file>.bak.<stamp> <file> && sudo nginx -t && sudo systemctl reload nginx`。
- 耗时:0.5h;清单每季度复核一次。

---

## 5. 专题:`BOARD_RBAC_ENFORCE`(SH-10,`verify_on_prod`)

现状:缺省 0 = 只记 `board_rbac.would_block` 日志不拦;板块 `none` 仅前端遮挡。

1. 观察(置 1 前,≥24h):
   ```bash
   ssh viltrox 'sudo journalctl -u viltrox-2.0-test.service --since "24 hours ago" --no-pager | grep -o "board_rbac.would_block | path=[^ ]* staff_id=[^ ]* boards=[^ ]*" | sort | uniq -c | sort -rn | head -40'
   ```
   逐行判断是否误杀(owner / manager 出现在列表里 = 误杀,先转 L-ui-facade / 权限车道)。
2. 零误杀后:`.env` 加 `BOARD_RBAC_ENFORCE=1` → 重启 web(§0 标准动作)。
3. 再观察 24h:同一 grep 应为空(真拦时不再记 would_block),并用一个 `board.<x>=none` 的测试账号访问该板块接口 → 403;owner 不受影响。
- 验收:24h 内测试者反馈无「看得见板块却打不开」;哨兵 / 反馈无 403 激增。
- 回退:改回 0 + 重启 web(秒级)。

---

## 6. 建议顺序与登记

顺序:**H-06(关 auto-train,今晚 00:30 EDT 前)** → H-01 → H-04 → H-02(含 §3)→ H-03 → H-14 → H-09/H-10(只读,随手)→ H-17 → 首次窗内发车(建设波 1)→ H-18 → H-13 → H-12 → §5 观察 → H-05 → H-11 → H-07 → H-08(等 W3 合入)→ H-15/H-16(可选)。
非窗内可做:H-06 / H-01 / H-03 核实 / H-04 探针 / H-05 文本 / H-09 / H-10 / H-14 / H-17(只改 .env 不重启)/ §5 第 1 步;其余进维护窗。

| 项 | 完成日期 | 执行人 | 结果 / 数字 | 备注 |
|---|---|---|---|---|
| H-06 auto-train 关 / 窗口 | | | | P0 |
| H-01 甲/乙、英文 | | | | |
| H-02 capabilities 三真 | | | | |
| H-03 timer + R2 对象 + 演练 receipt | | | | |
| H-04 `<BETA_URL>` | | | | |
| H-05 四段文本 + 前端页面 | | | | |
| H-07 小机 | | | | |
| H-08 回归发车 | | | | |
| H-09 两目录文件数 | | | | |
| H-10 kols 邮箱数 | | | | |
| H-11 staging 方案 | | | | |
| H-12 日闸 / enabled 列表 | | | | |
| H-13 演示种子 summary | | | | |
| H-14 HMAC 密钥存在 / dry_run 报数 | | | | |
| H-15 独立 scheduler | | | | 可选 |
| H-16 Qdrant server | | | | 可选 |
| H-17 排水范围 interactive | | | | |
| H-18 三迁移列核验 | | | | |
| §3 webhook 到达时间 | | | | |
| §4 real_ip 条数 | | | | |
| §5 RBAC 置 1 日期 | | | | |
