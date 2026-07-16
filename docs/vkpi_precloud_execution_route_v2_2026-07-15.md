# V-KPI 上云前执行路线 v2（2026-07-15）

> 本文件覆盖旧报告中已经失效的运行态判断。它只陈述本轮现场核验后的事实，并把下一轮拆成可并行、可验收、可回滚的执行单元。

## 1. 当前结论

### 已完成并有运行证据

- 仓库仍在 `codex/dashboard-real`，HEAD 为 `fe3871c438ff9de8884589e052d8dd8b82b94b83`。
- 主 PostgreSQL 已执行 migration `260_vkpi_dealer_map_management.sql`；隔离库完成 260 的前进、回滚、再前进演练。
- Dealer 已支持多厂商品牌关系、自由新增、编辑、单独发布/撤下地图、Viltrox 内部部署状态、活动状态、公开网站和社媒字段。
- 本地真实数据为 7 家 Dealer、5 个已发布地图点、4 个州、1 个国家；地图点可点击并显示联系方式、网站、发布、授权、部署和活动的分层事实。
- Nikon、Canon、Sony 等 13 个制造商入口和 21 个零售商入口已登记，共 34 个来源；34/34 完成离线格式映射，但 0/34 已启用联网导入、0 个直接导入。
- 3 个 Apify Worker（1 交互、2 批量）和 1 个 Redis Worker 在线，SHA 一致；PostgreSQL `idle in transaction=0`。
- `scripts/verify.sh` 完整退出码为 0；前端 123/123 测试文件、965/965 测试通过，TypeScript、生产构建、chunk 图和千行门禁通过。
- 重启后本地发布验收 48/48 必需接口、21/21 页面族通过；数据态为 real 40、empty 7、pending 0、degraded 1，P95 765.8ms。
- Dealer 只读压力烟测在 1/4/8/16 并发下均为 0 错误；16 并发为 352.2 req/s、P95 65.19ms。该数字只代表 Dealer 读接口，不代表整站可承载用户数。
- Intelligent 固定意图查询、本地历史、服务端顾问会话和当前员工私有记忆路径可用；外部模型仍明确关闭，没有伪装成 LLM 成功。

### 仍不能声称

- 34 个入口不是 34 家门店，也不是美国全量 Dealer；目前仅 7 家业务记录、5 个地图点。
- Nikon/Sony/Canon 品牌关系只表示人工登记，不能推断成 Viltrox 授权、实时库存、在售或本地影响力。
- Viltrox `not_deployed/planned/deployed/paused` 是内部部署字段，不替代品牌授权、库存或销售证据。
- Event Radar 当前接口可用但真实 upcoming 数据为空；Dealer 活动也仍为 7 条 unknown，尚未形成自动监测。
- 外部 LLM 目前没有通过“精确模型签名 + 可用性探测 + 任务评估 + 预算授权”四道门，不能作为上线可用能力。
- Shopify 未授权，真实库存、成本、GMV、ROI 和销售归因未接；业务闭环不能打 4.5。
- 本轮没有 commit、push 或云部署；工作树仍很脏，不能把整个工作树直接打包上线。

## 2. 当前分项评分（5 分制）

| 维度 | 当前 | 到 4.5 的硬门槛 |
|---|---:|---|
| 本地工程能力 | 4.2 | 冻结可发布范围、不可变 RC、完整云前预演、日志泄漏门禁 |
| 本地运行可靠性 | 4.0 | staging 迁移/回滚、备份恢复、Redis 持久化、72 小时稳定运行 |
| Dealer 产品能力 | 3.6 | 来源级合规复核、分批实体导入、坐标/联系方式 QA、增量复查 |
| AI 工程骨架 | 3.4 | 精确模型 readiness、任务评估、可观测 fallback、成本和延迟 SLO |
| 每用户记忆/学习 | 2.8 | 候选→人工确认→版本化记忆→撤回/过期→离线评估闭环 |
| 真实业务闭环 | 1.0 | Shopify/库存/成本/归因授权，加上人工 finalized outcome 与反馈 |

结论：当前可以准备“AI-off、真实空态、受控测试站”，不能把总系统或真实业务宣称为 4.5。4.5 必须由下面的门禁证明，不用文案提分。

## 3. 超级并行执行拓扑

下一轮固定为六条泳道；每条泳道有独立所有权，禁止多人同时改同一文件。只有 Release Lane 可以合并、迁移和重启。

| 泳道 | 优先级 | 范围 | 主要产物 | 依赖 |
|---|---|---|---|---|
| A. Release/可靠性 | P0 | RC 冻结、staging clone、迁移、备份恢复、原子发布/回滚 | release manifest、restore receipt、cloud preflight | 无，先启动 |
| B. 性能/容量 | P0 | KOL N+1、轮询、ROI/GMV、无界响应 | 查询预算测试、基准前后对照 | 固定 RC 基线 |
| C. LLM/智能体 | P0 | 精确模型 readiness、fallback、评估、预算与可观测性 | model evidence、task canary、AI-off/AI-on 双门禁 | 不依赖 Dealer |
| D. Dealer/Event 数据 | P1 | 34 来源合规复核、批次候选、人工 QA、地图和活动关联 | source snapshot、candidate batch、QA ledger | 不允许直接进业务表 |
| E. KOL/学习 | P1 | 分阶段结果、个人记忆、反馈/训练导出 | stage SLA、memory audit、point-in-time export | LLM 可保持关闭 |
| F. 业务接口 | P1 | Shopify、库存、成本、归因的 Settings 接入口和状态机 | connector contract、empty/degraded UI、audit log | 凭证后置 |

并发规则：

1. A 先建立不可变基线，B/C/D/E/F 才能用同一 SHA 做性能和行为比较。
2. B/C/D/E/F 并行开发，各自只交小批 diff 和证据，不直接部署。
3. A 每 2～4 小时收一次候选，跑 migration rehearsal、完整 verify、48 项验收、浏览器门禁。
4. 任一泳道出现数据越权、真实性降级、迁移不可回滚或 5xx，即停止合并；其它泳道继续，不全盘等待。
5. 不以“测试通过”代替云运行；云上必须重复接口、浏览器、Worker、数据库、队列和日志证据。

## 4. 精准实施顺序

### P0-A：先把本地候选变成可发布 RC（0.5～1 天）

1. 按功能域列出当前 795 个 tracked 修改与 508 个 untracked 文件，只选择 Dealer/KOL URL/LLM fail-closed/reliability 的必需闭包。
2. 为选中闭包生成 release manifest：路径、SHA256、migration 范围、环境变量清单、构建产物、回滚脚本。
3. 从主库备份恢复到 staging clone，演练 `259→260`、`260→259`、`259→260`，核对 Dealer 7/5、品牌关系和 publication 状态不丢失。
4. 核验 PostgreSQL 备份可恢复、对象存储/R2 配置备份、Redis AOF/快照、非 root 服务身份、只读环境文件和 secret 注入。
5. 形成不可变 RC；RC 之后的变更另开批次，禁止继续向同一候选叠加。

验收：`verify.sh=0`；staging restore 成功；迁移三段演练成功；回滚后业务记录不丢；RC manifest 可重算一致。

### P0-B：修性能真实瓶颈（1～2 天，可与 C 并行）

按附件复核的 ROI 顺序执行：

1. KOL detail 缓存批量读取：49 次 SQL / 38 次连接降到总 SQL ≤12、独立连接 0、19 视频热态 P95 ≤50ms。
2. 视频状态改为一个批量端点；每轮最多 1 个状态请求，只在 ready 数变化或终态时重拉 detail，轮询退避 5～10 秒。
3. 高价值 KOL 榜单改成批量 CTE；50 条查询数 ≤4，并与旧实现逐字段一致。
4. GMV 点击/销售预聚合后 JOIN；EXPLAIN 不再有按 link 重复的销售 SubPlan，补真值部分索引。
5. Project/My KOL 首屏 summary + cursor 分页，首包 ≤150KB；禁止无界 `SELECT *`。
6. 将 `table_exists()` 收敛为迁移后的 schema capabilities；必备表不在请求热路径重复探测。

每一步先写查询次数/响应体预算测试，再改实现；性能基准必须在同一数据库快照和同一 RC 上做前后对照。

### P0-C：把 AI 从“骨架可用”推到“可控可用”（1～2 天）

1. 每个任务绑定保存 provider、精确 model id/version、prompt schema、输出 schema、预算和超时；禁止只按“GPT/Claude/Gemini”家族名宣称就绪。
2. 先做低成本探测，再跑任务级 golden set：KOL 摘要、视频时间戳、受众解释、推荐原因、Marketing Advisor 各自独立评估。
3. 评估必须记录 groundedness、格式通过率、拒答、延迟、费用和 P95；未过阈值的任务维持 `operator_disabled/model_not_ready`。
4. AI-on 与 AI-off 两套 UI/接口门禁同时保留。外部模型失败时回到本地结构化数据或明确空态，不返回伪 AI 结论。
5. 引入 circuit breaker、重试预算、幂等 job key、dead-letter 和可恢复断点；Provider 429/5xx 不允许拖死交互车道。
6. 智能体只生成建议/草稿；发货、预算、外发、发布和写业务记录继续人工确认。

到 4.5 的门槛：生产任务绑定覆盖率 100%；每个绑定至少一份可复现评估；AI-on canary 连续 24 小时无静默降级；AI-off 全功能可浏览；费用和失败率可观测。

### P1-D：Dealer/Event 不再手工反复扩代码（2～5 天，按批处理）

1. 以 34 个来源为唯一清单，逐来源完成条款/robots、快照日期、解析器 fixture 和 publisher identity 复核。
2. 每次只开 1～3 个来源，数据先进 candidate staging；名称、地址、电话、网站、坐标、品牌关系、活动页逐字段记录 provenance。
3. 身份去重使用 stable org/location key；地址和坐标冲突进入人工队列，不自动覆盖现有业务行。
4. 通过人工 QA 后才晋级 `vkpi_dealers`；默认草稿，显式发布后才上图。
5. Viltrox 部署和授权永远独立。只有公司确认后才把部署从 `not_deployed` 改为 `planned/deployed`。
6. Dealer 活动以 `dealer_id` 关联 Event Radar；活动过期自动转历史，不删除证据。

批次验收：每批 100 家抽检 ≥20%；地址/州/坐标完整率 ≥98%；重复率 <1%；网站可达率、联系方式来源覆盖率单独报告；未核验行不上图。

### P1-E：KOL 分阶段结果与个人学习（1～2 天）

1. 搜索后先返回基础身份、平台、粉丝/公开数据和 provenance；随后依次补视频、评论、受众、AI、QA，前端显示每阶段进度和 ETA。
2. 15 个 KOL 不再等待全部完成才展示；交互车道优先，批量车道后台续跑，失败可单阶段重试。
3. 每用户记忆只接受候选；用户确认后才生效，支持来源、版本、过期、暂停、撤回和审计。
4. 学习数据使用 point-in-time export，训练/验证按时间切分；没有 finalized outcome、非演示反馈和 actual 时，不训练效果模型。
5. 达到至少 5 条 finalized outcome、5 条非演示反馈、5 条带 actual prediction eval 后，才启动离线基线模型；先 shadow eval，再考虑在线建议。

### P1-F：真实业务接口只把代码准备好（1 天）

在 Settings 保留 Shopify、Dealer 权限/部署、真实库存、成本、销售归因入口；每个连接器必须有：

- `not_configured / pending_authorization / connected / degraded / revoked` 状态机；
- 凭证只存 secret 管理系统，前端永不回显；
- webhook 签名、幂等、回放保护、增量游标和审计日志；
- 未授权时只显示空态/待接入，GMV/ROI 不得生成估算值；
- 授权后先 shadow sync 和 reconciliation，再切换为业务真值。

## 5. 云端发布顺序

1. 新建 staging，不覆盖现网；安装 PostgreSQL client/backup 工具、Redis、反向代理、进程管理、日志采集和监控 agent，应用运行在非 root 用户下。
2. 注入 secrets 和只读 release env；同步不可变 RC，不传本地 `.env`、数据库或缓存目录。
3. 恢复 staging clone，跑 migration 260；启动 backend、frontend、3-lane Worker 和 Redis Worker。
4. 重跑 `verify.sh` 严格运行态、48 项只读验收、21 页面浏览器点击、console/network gate、匿名 403、docs/openapi 404。
5. 跑 1/4/8/16/32 并发阶梯和 30 分钟 soak；分别测 Dealer 读、KOL list/detail、Dashboard、GTM，不把单端点 RPS当整站用户数。
6. 连续观察 72 小时：5xx、P95、队列等待、Worker heartbeat、DB pool、idle transaction、Redis backlog、LLM 费用/失败、R2 错误。
7. 达标后再切流；发布失败执行原子回滚，数据库只按已演练脚本退回，不手工改表。

云端 GO 条件：

- release SHA、client/server/worker SHA、migration 260 全一致；
- 48/48、21/21、浏览器 0 blocking error；
- 关键 GET P95 <1s，GTM Summary <2s，Preview <1.5s；
- 交互任务 P95 开跑等待 <60s，批量队列没有无界增长；
- 72 小时无数据丢失、无 secret 泄漏、无持续 5xx；
- AI 未就绪时明确关闭，业务未授权时保持空态。

## 6. 本轮证据

- 本地最终验收：`runtime/ops/dealer-map-local-acceptance-final-20260715.json`
- Dealer 压力烟测：`runtime/ops/dealer-read-load-smoke-20260715.json`
- 数据库备份：`runtime/backups/viltrox2-pre-dealer260-20260715T181742Z.dump`
- 备份 SHA256：`38f9fe3c81c665abe70dc50dacebeae468bc4cfd289f668dd4396e004cfe9cb1`
- Dealer 来源说明：`docs/vkpi/us-dealer-source-registry.md`
- migration：`migrations/260_vkpi_dealer_map_management.sql`

## 7. 下一轮固定交付物

1. 只包含选中功能闭包的 RC manifest；不提交整包脏工作树。
2. staging clone 的 migration/rollback/restore receipts。
3. 六条泳道各自的 before/after 基准和失败清单。
4. 云端 48/48、21/21、浏览器 console/network、Worker/DB/Redis 证据包。
5. 一份 GO/NO-GO 表：工程、可靠性、AI、业务真实性分别评分，不合并粉饰。
