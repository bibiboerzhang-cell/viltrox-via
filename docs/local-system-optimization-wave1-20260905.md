# 系统优化第一批：本地实施回执

日期：2026-09-05。对应系统级优化蓝图的首批稳定性与使用状态改进。

## 结论

四项本地代码改进已落地，定向离线回归通过。它们解决的是来源失败被当成无结果、任务状态误读、维护事务异常连锁失败，以及 LLM 缓存上下文与校验问题；不代表完整蓝图完成，也不代表当前运行服务或云端已经更新。

- 分支：`codex/optimization-v1-local`。
- 起点及当前 HEAD：`ff823eac77261ec1b61c0772b2da6978bee47b99`；本轮改动尚未提交。
- 开始时工作树干净；本轮只改应用源码、对应测试和本回执。
- 未改业务数据库、未启用抓取、未调用真实模型或供应商、未重启服务、未部署云端。

## 这一批让哪些步骤更可靠

| 使用步骤 / 蓝图映射 | 本地实现 | 能解决什么 | 仍需验证或补齐 |
| --- | --- | --- | --- |
| 看市场信号 / S03、S09 部分 | 三个既有内部来源分别计算完整、部分、失败状态；部分结果保留；失败不显示假零；不缓存部分摘要并更新缓存版本 | 读取失败不再被解释成市场没有需求；用户看到核对来源、重新读取的下一步 | 未新增外部市场源；未完成全系统证据身份统一；旧接口没有来源字段时仍兼容顶层状态 |
| 批准与执行行动 / S08、S09 部分 | 区分批准、入队、执行回执、失败和未知；未知结果保留并暂禁重复执行；缺失费用不显示为实际零费用；读取失败暂停操作 | “点了批准”或“任务排队”不会直接被表示为业务完成 | 未新增后台任务终态接口；防重复标记是当前组件内状态，不是持久化的业务幂等记录 |
| 每日 KOL 维护 / S02、S05 部分 | 槽位绑定或释放异常后恢复事务；恢复失败停止后续派发；提交结果未知时保留配额占用并标记不可直接重试 | 一次数据库异常不再污染后续连接；未知提交不因盲目重试导致重复抓取 | 本轮用假连接建模事务错误；未在业务 PostgreSQL 实测；没有启用每日付费维护或提高原每日 5 个任务上限 |
| LLM 读取与复用 / S06 部分 | 缓存键绑定调用方提供的身份、权限、范围、策略和版本上下文；命中重查模型准入；回退模型不写入主模型缓存；JSON 合同及期限通过后才记成功命中 | 降低错误上下文复用和无效缓存被算作成功调用的风险 | 仍须补齐调用方缺失的 ACL／数据版本；未实现整任务预算、缓存击穿保护或未知费用对账 |

Action Inbox 额外覆盖：并行读取不让旧 approved 覆盖 executing；切换列表数量不清除未知执行保护；人工对账表单即使已打开，在加载中、不可用或读取失败时也不能提交。

新 LLM 缓存键会产生冷缓存，且回退模型结果暂不缓存，因此不能宣称本次一定降费或提速。端到端速度与真实费用需在固定数据、环境和批准的调用范围内另测。

## 本轮最终验证

| 验证范围 | 结果 | 证据边界 |
| --- | --- | --- |
| 后端合并回归 | **383 passed，1 skipped，1 warning**；7.61 秒 | 市场信号、缓存、每日维护、调度回执、LLM Gateway、复杂度与文件长度约束；临时测试库／假供应商，不是业务库验收 |
| 前端合并回归 | **9 个文件、148 passed**；2.03 秒 | 组件与 API 合同测试；不是浏览器实际操作或视觉验收 |
| Python 编译检查 | **3378 个文件通过** | 内存编译，不运行应用 |
| 仓库 hardening 严格检查 | **0 errors，0 warnings** | 静态检查，不是安全渗透结论 |
| silent exception 基线 | **0** | 未放宽既有基线 |
| i18n 严格检查 | **0 个新增缺口** | 368 文件，78／1237 zh／en entries |
| 前后端共享合同、跨看板目录 | **通过** | 使用只读 `--check`；目录 182 个模块 |
| TypeScript、差异空白检查 | **通过** | 全部代理停止修改后执行 `tsc --noEmit`、`git diff --check` |
| 独立目录前端生产构建 | **通过**；3197 个模块，4.94 秒 | 产物仅位于临时目录；未覆盖活动 `frontend/dist`，未启动预览服务 |
| 构建产物分包检查 | **通过**；136 个 JS 文件，1208 条静态依赖边，无环，最大 307.2 KiB | 低于原 600 KiB 限制；不是网络延迟或真实首屏速度测量 |

后端跳过项为需显式启用的 PostgreSQL 实测分支。警告来自既有 `google.genai` 对 Python 私有类型的弃用引用，不是本轮测试失败。

后端合并命令（不将不同轮次的重叠用例重复相加）：

```sh
VKPI_PYTEST_ALLOW_LIVE_SERVICES=0 PYTHONPATH=.:scripts:backend .venv/bin/python -B -m pytest -q \
  tests/test_market_brain_signal_state.py tests/test_market_brain_summary_read_cache.py \
  tests/test_vkpi_strategy_read_cache.py tests/test_inventory_refresh_recovery_contract.py \
  tests/test_kol_search_inventory_refresh.py tests/test_kol_inventory_scan_stability.py \
  tests/test_scheduler_result_contract.py tests/test_cc_ratchet.py tests/test_line_soft_ratchet.py \
  tests/test_llm_gateway_*.py
```

前端合并范围：`marketSignalState.test.ts`、`gtmCommand-api.test.ts`、`GtmCommandBoardPage.signal-state.test.tsx`、`GtmCommandBoardPage.smoke.test.tsx`、`crossBoardModules.smoke.test.tsx`、`ActionInboxPanel.test.tsx`、`actionInboxStatus.test.ts`、`actionInbox-api.test.ts`、`cockpit/lib/i18n.test.ts`。

构建产物：`/private/tmp/vkpi-wave1-build.2CPj7a`，可被系统清理，不是发布包。构建标识显式设为 `uncommitted-wave1-ff823eac`，不伪装成已提交的 SHA。构建时仅出现“输出目录在项目外，不执行清空”的提示；未使用 `--emptyOutDir`。

**未完成的验收：**完整 `scripts/verify.sh`、浏览器流程、业务数据库迁移、真实供应商和连续定时任务观察。本回执不是发布许可或系统评分。

## 当前运行版本：本轮只读实测

2026-09-05 05:09:55 UTC，读取本地 `/health`：

- 状态 `degraded`，`sha_aligned=false`。
- 服务 `87192cf675da055287502140dc90e512e9b61b32`。
- 前端 `f845fb07a346e85f0e4c741563702b3c3a39af84`。
- Apify Worker 在线 16／16、心跳新鲜，但 SHA 为 `ec804ed656ec999162dfdce8dc5440acab7c7afe`，未与服务对齐。
- Redis Worker 在线 1，其 SHA 与旧服务相同。

这次读取不能证明之后的持续健康；本轮未重启，所以本次修复尚未加载到该旧运行栈。云端本轮未复核，不复用历史健康结果作为当前证明。

## 下一批顺序与停止条件

1. **先做运行验收准备（S01）**：本轮独立目录构建已通过；下一步冻结最终候选并做无真实副作用的浏览器流程验收，不覆盖旧服务正在使用的前端产物。
2. **再受控加载本地候选**：沿用发布检查点处理迁移历史、备份恢复、维护排水及会话兼容。旧 supervisor 会补起服务、调度器和 Worker，直接重启不是单纯换代码；在维护及迁移边界明确前，不直接重启、不删除历史台账、不放宽守卫。
3. **然后补业务引导（S07／S08）**：优先现有内部数据，形成单产品族、单市场的机会简报与营销项目；每条建议带证据缺口、负责人、批准要求、交付和下一步。没有真实资料时保留待补证据，不自动外发。
4. **最后验收日常自动化与速度（S05／S10）**：平台逐条确认授权、预算、完成回执和停止条件后，再小范围连续观察；分别记录首次结果、后台完成、错误率、数据新鲜度与费用，不以排队成功代替抓取成功。

不同时扩大所有平台、不承诺未经测量的延迟或营销效果。80 分目标必须等统一评分口径和运行证据后再评价。

## 复核入口

- [原系统级优化蓝图](/Users/bibiboer/.codex/visualizations/2026/08/29/01a04b63-d437-7470-afe9-753668884274/vkpi-system-optimization-blueprint-20260905.md)
- [发布对齐检查点](/Users/bibiboer/Documents/V-KPI——marketing/docs/release-alignment-checkpoint-20260905.md)
- [市场来源状态](/Users/bibiboer/Documents/V-KPI——marketing/backend/app/domains/market_brain/summary_signal_state.py)
- [每日维护槽位恢复](/Users/bibiboer/Documents/V-KPI——marketing/backend/app/domains/kol/search_inventory_slots.py)
- [LLM 缓存实现](/Users/bibiboer/Documents/V-KPI——marketing/backend/app/platform/llm_gateway_result_cache.py)
- [行动流程提示](/Users/bibiboer/Documents/V-KPI——marketing/frontend/src/components/vkpi/cockpit/components/ActionInboxProgress.tsx)
