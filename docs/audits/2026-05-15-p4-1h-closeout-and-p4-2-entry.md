# P4.1H Closeout + P4.2 Entry Gate

时间: 2026-05-15 08:42 Asia/Shanghai  
工作目录: `/Users/bibiboer/Documents/V-KPI——marketing`  
分支: `codex/vkpi-cleanup-d7`

## 1. 本轮目标

P4.1H 是 P4.1 的收口报告,不做功能开发。

目标:

- 汇总 P4.1A-G 的核验结果。
- 确认当前工作树脏改仍可解释。
- 标出 P4.2 前必须知道的风险。
- 给出下一步进入 P4.2 的明确顺序。

## 2. 备份

本轮开工前备份:

`/Users/bibiboer/Documents/V-KPI-backups/before-p4-1h-closeout-20260515-084145.tar.gz`

## 3. P4.1 执行状态

| 模块 | 状态 | 证据 |
|---|---:|---|
| P4.1A Dirty worktree classification | 完成 | `docs/audits/2026-05-15-p4-1-dirty-worktree-closeout.md` |
| P4.1B Classification correction | 完成 | docs/audits 25->26, smoke scripts 8->11 已修正 |
| P4.1C Docs / agent low-risk review | 完成 | `docs/audits/2026-05-15-p4-1c-low-risk-batch-review.md` |
| P4.1D Backend governance verification | 完成 | `docs/audits/2026-05-15-p4-1d-backend-governance-verification.md` |
| P4.1E Frontend / media UX verification | 完成 | `docs/audits/2026-05-15-p4-1e-frontend-media-ux-verification.md` |
| P4.1F Smoke script batch verification | 完成 | `docs/audits/2026-05-15-p4-1f-smoke-script-batch-verification.md` |
| P4.1G Unit test batch verification | 完成 | `docs/audits/2026-05-15-p4-1g-unit-test-batch-verification.md` |

判定: P4.1 已完成当前范围的核验与收口。

## 4. 当前验证摘要

### 后端治理

- Targeted backend tests: `36 passed, 65 warnings`。
- 覆盖权限 scope、audit/firewall decorator、成本、KOL pool、KOL lifecycle、项目 lifecycle。

### 前端 / 媒体 UX

- `npm run build`: PASS。
- 4 个媒体相关 smoke: PASS。
- 浏览器 QA 已验证:
  - 前后端 build hash 一致: `20cd80db`。
  - Data Analysis 可打开账号详情。
  - 头像代理可加载。
  - 内容列表显示真实加载数量。
  - `打开平台` 链接存在。
  - 单帖详情抽屉可打开。
  - 视频元素 metadata 可用,`duration=77.530023`, `1280x720`, `readyState=4`。
  - console error/warning = 0。

### Smoke 脚本

- P4 smoke 批次: `PASS=10 / FAIL=0 / TOTAL=10`。
- 已清理旧 `p4-step22-*` / `p4-step23-*` marker 残留。
- 复扫 13 张相关表,marker 残留为 0。

### 单元测试

- Targeted pytest: `49 passed, 101 warnings in 2.26s`。
- `tests/test_vkpi_metric_lineage.py` 当前按生产定义保留,未为测试改业务公式。

## 5. 当前工作树状态

当前脏改仍为 43 条:

```text
27 M
16 ??
```

这 43 条不是新增失控,而是 P4.1 已分类的批次:

- Backend governance batch
- Frontend / media UX batch
- Smoke scripts batch
- Unit tests batch
- Docs / audits / agent package batch

本轮未 staging,未 commit。

## 6. 剩余风险

### R1. Warning 债务仍在

单元测试有 101 warnings,主要是:

- `datetime.utcnow()` deprecation。
- `asyncio.iscoroutinefunction()` deprecation。
- 第三方 `google/genai` deprecation。

处理建议: 放到单独 P4.x 技术债小轮,不要夹在当前 P4.1 收口里扩大改动面。

### R2. Browser QA 未覆盖写成本动作

P4.1E 没有点击:

- `运行单帖分析`
- `刷新该账号`
- `关闭账号抓取`

原因: 这些是会消耗 LLM/API 或改变配置的动作。需要独立 live-action QA,带预算与回滚。

### R3. Settings gate 状态仍需解释

浏览器中曾出现账号级开关已开、平台/API gate 仍阻塞的文案。这是配置状态或展示解释问题,不是本轮 smoke 失败。

建议: P4.2 或 P4.3 单独核对 `platform_crawl_settings` / `last_test_status` / API readiness 文案。

### R4. Data Analysis 仍不是 Socialinsider 级

当前 P4.1 验证的是团队可用基础路径:

- 真实数据可读。
- 媒体可打开。
- 关键链接可达。
- 假按钮减少。

但不是完整 Socialinsider 级:

- 全量趋势图 / compare / metric picker / topic tracking 仍需 P5 或后续专项。

### R5. 未提交改动需要单独 commit 策略

43 条脏改已经可解释,但还未进入 git 历史。继续开发前建议先做 commit 分批或至少再打一份纯净包。

## 7. P4.2 进入条件

建议 P4.2 开始前满足以下条件:

1. 当前 P4.1 报告已读完并接受。
2. 决定是否先 commit P4.1 的 43 条改动。
3. 若不 commit,至少保留当前备份链。
4. 明确 P4.2 单轮只做一个模块。

## 8. 推荐 P4.2 顺序

下一步建议进入 P4.2: Mutation Safety Audit 动态核对。

范围:

- 只读扫描所有 POST/PATCH/DELETE endpoint。
- 输出 endpoint -> permission -> audit -> confirm -> rollback 矩阵。
- 不修功能代码。

理由:

- P4.1 已证明当前按钮/媒体/单元测试基本路径能跑。
- 下一层不是继续加 UI,而是把写操作风险摸清楚。
- 这能直接回答哪些按钮是真危险、哪些只是静态误报。

P4.2 验收:

- 生成 `docs/audits/2026-05-15-p4-2-mutation-safety-matrix.md`。
- 至少覆盖所有 `backend/app/api/routers/**/*.py` 的写 endpoint。
- 输出 P0/P1/P2 风险清单。
- 不改业务代码。

## 9. 结论

P4.1 可收口。

本阶段成果不是新增功能,而是把当前 P4 工作树从“很多脏改”变成“每个批次都有解释、有测试、有报告、有备份”。

下一阶段应做 P4.2 Mutation Safety Audit,不要直接继续加功能。
