# 系统优化第二批：规划引导与隔离验收

日期：2026-09-05。延续第一批本地优化，不是云端发布回执。

## 本次完成的代码调整

1. **营销计划不再只有一份 JSON 和绿色 ok。** 新增准备检查，明确缺规划条件、缺判断依据或草稿可供人工审阅；展示资料缺口、原因和下一步。生成成功不代表项目创建、批准或执行。
2. **准备状态由服务端决定。** 输入缺口优先于证据缺口；失败、未知或畸形来源不会提升为就绪；无顶层 status 的真实 KOL 返回按既有 projection 合同核对。模型只接收上下文副本，不能覆盖准备状态或原地改变规划范围。
3. **只修精确的副作用。** `campaign_plan` 读取市场简报时显式关闭过期信号写回。现有 KOL schema 初始化、缓存以及运行账本路径仍在，不能称整个规划零写；本轮未实际执行这些业务库路径。
4. **缺条件的草稿不进入新合同下的可用评审。** 新准备合同必须完整，且为供人审草稿，才能进入已有人工复核流程。历史没有该合同的账本仍保留兼容，不重新打分或改写历史。
5. **修正 Skill Studio 异步结果归属。** 切换技能或账号后，旧运行响应不能覆盖新页面；运行中切换待评／已评，完成后的列表刷新使用当前筛选，不重跑已提交任务。

计划原有四段模板保留。预算算法、默认四周节奏、模型费用记录和合作简报的缺档案处理不在本次修复范围；没有新增项目实体、对外消息、自动投放、付费调用或平台凭据。

## 准备状态合同

| 状态 | 含义 | 用户下一步 |
| --- | --- | --- |
| `needs_inputs` | 预算缺失／不大于零，或目标不支持；同时保留证据缺口 | 确认规划预算与目标，再生成 |
| `needs_evidence` | 候选材料或本草案使用的市场材料缺失、未知或不可用 | 先在已有 KOL／市场页面核对来源、时间和范围；不自动抓取 |
| `draft_for_review` | 有材料可供人工阅读，不证明匹配或资源已验证 | 负责人核对范围、候选档期、资源、预算和交付条件 |

三种状态均固定 `executable=false`、`approval_status=not_requested`、`claim_status=descriptive_only`。不存在本次模型输出即获准执行的分支。字段缺失或互相矛盾时，前端显示待核验，不推定已准备完成。

## 合并验证结果

以下为第一批与第二批合并后的同一轮结果，不与之前的 383／148 等重叠次数相加。

| 验证 | 结果 | 边界 |
| --- | --- | --- |
| 后端定向合并回归 | **577 passed，3 skipped，2 warnings**，9.44 秒 | 3 项为未启用的真实 PostgreSQL 分支；警告来自既有 Google 类型和 Pydantic Config 弃用 |
| 前端定向合并回归 | **11 文件、176 passed**，2.09 秒 | 含准备检查、旧响应隔离、当前筛选刷新及第一批状态回归 |
| TypeScript / i18n | **通过**；0 个新增词条缺口 | 英文字典 1261 条 |
| Python 内存编译 | **3380 文件通过** | 不启动应用 |
| hardening / silent exception | **0 errors、0 warnings / 0** | 未放宽原基线 |
| 共享合同 / 跨看板目录 | **通过**；目录 182 模块 | 只读检查 |
| 独立目录生产构建 | **通过**；3198 模块，5.11 秒 | 未覆盖活动 `frontend/dist` |
| JS 分包检查 | **通过**；136 文件、1211 条静态依赖边；无环；最大 307.3 KiB | 低于原 600 KiB 限制，不是实际首屏速度测量 |

临时生产构建：`/private/tmp/vkpi-wave2-build.RMAyut`；标识 `uncommitted-wave2-ff823eac`，明确包含未提交改动，不冒充发布 SHA。

完整 canonical gate、真实应用浏览器全流程、业务库迁移／恢复、真实模型／平台及连续每日任务观察均未完成。不能由上述测试推定系统整体已稳定或已经达到某个评分。

## 浏览器合成验收

新增可复用夹具，直接渲染当前真实的市场信号、KPI、行动收件箱和准备检查组件。仅使用合成数据与内存假接口，页面显著标注合成；没有真实登录、业务 API 代理或外部连接。

固定静态预览地址为 `127.0.0.1:4178`。这是独立验收服务，不是旧 8102 应用的重启或版本切换。

最终独立 Chrome 验收开始于 **2026-09-05 05:49:46.625 UTC**：**9/9 场景通过**，耗时 5.804 秒，窗口 1440×1400。这是固定合成脚本的运行时长，不是实际系统响应速度或生产性能指标。

| 场景 | 浏览器实际结果 |
| --- | --- |
| 来源全部失败 | 信号数为待核验而非假 0，明确提示读取失败 |
| 仅部分来源结果 | 保留合成信号，同时提示覆盖不完整 |
| 完整读取后为空 | 显示 0，并说明对应窗口暂无信号 |
| 建议审批 | 批准后显示尚未执行，执行调用数保持 0 |
| 已批准但未执行 | 不自动执行 |
| 执行返回入队 | 仅显示任务已提交、结果待核验，实际费用待对账 |
| 执行响应丢失后刷新 | 保持未知结果，不恢复执行按钮、不自动重试 |
| 读取失败时已有对账表单 | 保留记录并暂停操作，提交按钮禁用，没有对账调用 |
| 计划缺证据 | 显示缺口和下一步，不称项目已创建或可执行 |

最终 Console 记录 **0**，Network **4 条且均为 200**（仅本地文档、2 个 JS、1 个 CSS），未知请求拦截 **0**，仅接受 **2 次明确标注合成的确认弹窗**。未访问真实业务 API 或供应商。主代理逐张复核了三张最终截图，合成标识、关键状态和暂停表单均可见。

过程中发现并修正的是**夹具自身配置问题**：缺少 `data-style` 和真实 `LazyMotion/domMax` 容器、缺 favicon 造成 404；并纠正测试对禁用按钮正常半透明的误判，行动容器仍要求完成显现。最终保留零 Console 错误要求，没有通过忽略错误、改生产样式或放宽接口隔离来通过验收。

自有 Chrome 已退出；主代理随后以 Ctrl+C 停止自有 4178 预览，进程退出后用 `lsof` 确认该端口无监听。未停止旧业务服务。临时构建、Chrome profile 和验收回执保留，不自动删除；它们不是仓库内持久发布物。

- [夹具运行与验收说明](/Users/bibiboer/Documents/V-KPI——marketing/frontend/tests/browser/system-optimization/README.md)
- [可复跑脚本](/Users/bibiboer/Documents/V-KPI——marketing/frontend/tests/browser/system-optimization/run-browser-regression.mjs)
- [最终机器回执](/private/tmp/vkpi-synthetic-browser-QX70pj/receipt.json)
- [来源失败截图](/private/tmp/vkpi-synthetic-browser-QX70pj/01-source-failure.png) · [入队未完成截图](/private/tmp/vkpi-synthetic-browser-QX70pj/06-queue-not-completion.png) · [读取失败暂停截图](/private/tmp/vkpi-synthetic-browser-QX70pj/08-read-failure-pauses-form.png)

以上不涵盖完整应用路由、真实账号、真实项目落库、供应商执行和业务效果；不能替代受控加载新版本后的真实应用验收。

## 当前本地运行状态与切换条件

本轮在 **2026-09-05 05:30:37 UTC** 只读获取 `/health`：仍为 `degraded`，服务 `87192cf6`、前端 `f845fb07`，`sha_aligned=false`；Apify Worker 16 个在线、心跳新鲜但 SHA 不一致，Redis Worker 1 个在线且与旧服务一致。最大已应用迁移字段为 309。此为时间点检查，不是持续可用证明。

当前源码 HEAD 仍是 `ff823eac77261ec1b61c0772b2da6978bee47b99`，分支 `codex/optimization-v1-local`，两批改动未提交、未加载旧运行栈。云端本轮未访问或部署。

下一次本地切换不能直接运行 `train.sh`：

- 该脚本要求干净候选，还会访问云端、覆盖前端并重启；`SKIP_RESTART` 不是纯只读模式。
- supervisor 会自动补起服务、调度器和 Worker，必须定义停派发与排水顺序。
- 应用启动本身会进入迁移；围栏不能当作“绝不迁移”。当前源码发布策略对 308–310 仍未审定。
- 先前检查点记录的本地 081／083 历史台账差异需审定兼容口径；本轮未重新读取业务库台账，不能删历史或伪造缺失迁移。
- 需要新的目标绑定备份／恢复证据、实时任务排水结果，以及旧会话兼容和失败后的恢复安排。

因此，先完成候选和验收准备，再确认本地维护窗口与精确迁移范围后受控加载；不自行启用每日付费维护、不直接迁移或恢复历史任务。

## 代码与证据入口

- [准备合同](/Users/bibiboer/Documents/V-KPI——marketing/backend/app/domains/marketing_brain/skills/campaign_plan_readiness.py)
- [营销规划集成](/Users/bibiboer/Documents/V-KPI——marketing/backend/app/domains/marketing_brain/skills/campaign_plan.py)
- [准备检查组件](/Users/bibiboer/Documents/V-KPI——marketing/frontend/src/components/vkpi/pages/CampaignPlanReadiness.tsx)
- [Skill Studio](/Users/bibiboer/Documents/V-KPI——marketing/frontend/src/components/vkpi/pages/SkillStudioPage.tsx)
- [第一批实施回执](/Users/bibiboer/Documents/V-KPI——marketing/docs/local-system-optimization-wave1-20260905.md)
- [既有发布检查点](/Users/bibiboer/Documents/V-KPI——marketing/docs/release-alignment-checkpoint-20260905.md)
