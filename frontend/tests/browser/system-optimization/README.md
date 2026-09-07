# 系统优化浏览器验收夹具（仅合成数据）

直接渲染当前源码里的 `ActionInboxPanel`、`GtmKpiBand`、`SignalsBody`、`CampaignPlanReadiness`、`CampaignPlanPresentation`、`StaffProfileDrawer` 和 `KolProfileDrawer`，复用现有 global/tokens/type-scale/theme-alias/ds-viz 样式。本目录的控制栏与假数据不是生产功能，也不是新的业务页面。

此夹具只能证明组件在指定合成输入下的显示与交互。截图、构建成功或假接口调用成功，均不证明上线、真实账号权限、平台连接、数据完整性、真实费用、订单归因或营销闭环。

## 隔离合同

- 固定 `http://127.0.0.1:4178`、严格端口。端口被占用时失败退出，不自动换端口；不绑定公网，不读取原生产 Vite 配置，不设置代理。
- 独立 `envDir` 仅指向本目录，前缀不是 `VITE_`；不读取生产 `.env`、登录状态或真实 token。固定 token 字符串明确为合成标记，假实现拒绝其他 token。
- 仅此配置下，将 `services/http`、`lib/api`、`services/vkpi/actionInbox-api` 映射到本目录假实现。支持的行动操作严格验证合成 ID、状态与参数；未列出的接口一律抛错，不回退真实实现。
- 通用 `apiFetch`/URL 构造全部拒绝。浏览器启动入口在加载真实组件前封锁 fetch/XHR/WebSocket/EventSource/Worker/sendBeacon/service-worker 注册，CSP 也禁止连接。不能访问 8102、供应商或任何业务 API。
- 假审批、执行、对账仅在浏览器内存中变化，需要人工点击；执行确认弹窗额外标明“合成数据夹具”。没有持久化、外部抓取、付费调用或业务数据库写入。
- 未知调用会增加界面“拒绝未知调用”计数；不要以移除隔离换取页面显示。正常场景若出现拒绝，应先核查依赖变化。
- 静态服务拒绝 `/api`、`/health`、`/graphql` 和非 GET/HEAD 请求。仅本地静态模块/样式/脚本资源属于预期网络请求。

## 启动（由验收人员显式运行）

夹具不会自行启动服务。无需安装依赖，使用仓库现有 Node/Vite。推荐构建后预览，避免开发 HMR 与 `connect-src 'none'` 的控制台冲突。

```bash
cd "/Users/bibiboer/Documents/V-KPI——marketing/frontend"
fixture_build_dir="$(mktemp -d /private/tmp/vkpi-ui-fixture.XXXXXX)"
node node_modules/vite/bin/vite.js build --config tests/browser/system-optimization/vite.config.ts --configLoader runner --outDir "$fixture_build_dir"
node node_modules/vite/bin/vite.js preview --config tests/browser/system-optimization/vite.config.ts --configLoader runner --outDir "$fixture_build_dir" --host 127.0.0.1 --port 4178 --strictPort
```

仅在前一个 build 命令成功后启动 preview。随后打开 `http://127.0.0.1:4178/`。不要使用生产 `npm run dev`/`npm run build`、`frontend/dist`、真实登录或任何供应商凭据。

输出只允许本目录 `.build` 或通过上述 `mktemp` 创建的 `/private/tmp/vkpi-ui-fixture.*`；其他输出路径会被配置拒绝。构建缓存若产生，只在本目录 `.vite-cache`，已通过本目录 `.gitignore` 排除。

停止：在该 preview 的前台终端按 **Ctrl+C**。不要使用宽泛 kill 命令；若端口已被其他进程占用，先确认归属，不停止不相关服务。临时构建目录可保留给本次验收，不执行自动清理。

## 场景与验收步骤

| 场景 | 人工操作 | 必须观察到的边界 |
| --- | --- | --- |
| 来源全部失败 | 选择场景 | 读取失败，不显示“市场无需求”；信号总数待核验而非假 0 |
| 仅部分来源结果 | 选择场景 | 保留一条明确合成的信号，显示覆盖不完整，不能称完整判断 |
| 完整读取后为空 | 选择场景 | 三来源均读取成功但窗口无信号；可为 0，仍标注全部合成 |
| 建议待批 | 点击“通过” | 仅批准，显示“尚未执行”，无自动执行调用 |
| 批准未执行 | 选择场景、不点击执行 | 显示批准与下一步，执行调用数保持 0 |
| 模拟入队 | 点击“执行”并确认合成提示，打开台账 | 只显示任务已提交、结果待核验；0 是回执估算，不证明免费或业务完成 |
| 模拟响应丢失 | 点击“执行”并确认，再刷新 | 结果未知，旧 approved 不恢复执行按钮；没有自动重试 |
| 读取失败暂停 | 打开人工对账，填原因和任意合成证据；点夹具“模拟读取失败”，再点组件刷新 | 保留旧记录，显示操作暂停，已打开表单的提交按钮不可用；“恢复读取”后需手动刷新 |
| 计划缺证据 | 选择场景 | 显示合成缺口与下一步；`executable=false`、未请求批准，不声称项目已创建或可执行 |
| 规划草案结构化审阅 | 选择 `plan_review`，按需展开原始 JSON | 真实准备检查与规划展示全宽呈现；合成预算 1,000.01、分配合计 750.01、未分配 250.00，币种不推断；模型费用未知，逐人理由缺失如实提示，没有执行/批准按钮 |
| KPI 与消息证据待核 | 选择 `kpi_truth`，滚动抽屉内的证据列表；点击“切换合成工作量为已核实 0” | 工作量 `null` 显示待核验，不能退回旧 `kpi_credit=987654`；来源/分组/汇总的 `null` 保持待核验，合成真 0 显示 0；手工 inbound 消息必须显示“对方回复（手工记录）”“收发未核验” |

每次切换或“重置合成场景”会重建合成内存状态并重新挂载行动组件。这是验收夹具的重置动作，不代表生产中跨刷新持久化。KPI 的待办数仅来自合成 suggested 窗口，不代表所有已批准/运行中任务。

`plan_review` 独立显示完整合成规划，不同时挂载 KPI、信号和行动收件箱；不触发行动假接口。原始 JSON 默认折叠，结构化摘要、预算、节奏、候选和依据风险不折叠。固定 1440×1400 截图从页顶开始保留合成标识，runner 检查关键预算块完整位于视口；较下方的依据风险、JSON 详情和隔离回执可能需要滚动。移动宽度的可读性仍须人工核验，不由桌面截图推定。

`kpi_truth` 是一个场景，内含工作量未知 → 真实 0 的两步合成对照。不挂载行动收件箱，不传 KOL token（不启用 TwinCard 查询），不提供头像或外链。真实抽屉只接收合成 props；生产样式仅在此场景挂载，夹具作用域把固定抽屉并排放置，保留独立内容滚动。关闭按钮固定留在当前夹具，不作为关闭行为验收。切换场景或重置会回到 `workload_score=null`。两张截图均保留页顶合成标识：未知工作量与手工消息、零工作量与 KOL KPI 四行证据；这不是生产抽屉定位/完整响应式的验收。

验收建议记录：源码 SHA/dirty 状态、所选场景、窗口尺寸、操作步骤、实际显示、是否通过、Console/Network 是否出现非预期请求。截图必须保留页顶“合成数据”标识；不得作为线上已发布证据。

## 不启动服务的检查

```bash
node node_modules/typescript/bin/tsc --noEmit -p tests/browser/system-optimization/tsconfig.json
```

此目录不修改生产组件或原 Vite 配置。浏览器操作和完整页面观感仍需启动后人工验收，不能由类型检查代替。

## 独立合成浏览器回归

在上述 4178 预览已由验收人员启动后，从仓库根目录显式运行：

```bash
node frontend/tests/browser/system-optimization/run-browser-regression.mjs --run
```

runner 复用仓库 CDP pipe 与 deadline 实现，只启动自己的临时 headless Chrome/profile（不连接用户浏览器），不注入任何真实认证。固定总期限 120 秒，通过 DOM 选择场景、点击按钮、填写合成对账原因，并且仅确认带“合成数据夹具”前缀的弹窗。检查实际矩形、visibility 和 opacity，禁用按钮的正常半透明仍算可见。

页面请求只允许 4178 的根文档及构建静态资源，其他请求在发送前拒绝。回执包含原有 10 场景加 `kpi_truth`（共 11 场景）的 DOM、普通 Console/Network 记录和 6 张保留合成标识的截图：原有 `01-source-failure`、`06-queue-not-completion`、`08-read-failure-pauses-form`、`10-plan-review-presentation`，新增 `11-kpi-truth-unknown` 与 `11-kpi-truth`。新增场景先检查工作量待核且旧积分不出现，逐条检查 Staff 来源/分组、KOL 来源/汇总的 null/0，再检查手工消息可见；切换 0 后检查工作量为 0 且 KOL KPI 四行全部位于截图范围。该场景要求连假行动接口调用也为 0。文件保存在运行时打印的 `/private/tmp/vkpi-synthetic-browser-*` 下。无 `--run` 时只显示用法。

完成或失败后只终止 runner 自己启动的 Chrome，保留精确 profile/回执/截图路径。4178 预览仍由原启动者负责停止。此结果属于合成浏览器功能验收，不是生产发布或真实业务验收。
