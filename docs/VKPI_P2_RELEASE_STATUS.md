# V-KPI P2 Release Status

更新时间: 2026-05-10

## 当前定位

V-KPI 当前是 Viltrox Marketing 内部系统的 v3/P2 硬化版本，重点能力已经从“功能补齐”转向“可交付、可回归、可接手”。

本版本覆盖:

- Marketing dashboard / lineage / drilldown
- KOL / Project / Link / Cost / KPI ledger
- Industry data / platform crawler registry
- Comments / sentiment / pillars / weekly reports
- Provider settings / platform crawl budget gates
- UI-facing API route acceptance gate
- Browser login compatibility QA
- v3 release gate

## 本轮收口状态

| Round | 内容 | 验收 |
| --- | --- | --- |
| P2.12 | YouTube + Apify live provider gate acceptance | `smoke_vkpi_live_gate_acceptance.py` |
| P2.13 | 前端关键 API 路由验收 | `smoke_vkpi_ui_api_route_acceptance.py` |
| P2.14 | 将 P2.13 并入 release gate | `smoke_vkpi_v3_release_gate.py` |
| P2.15 | 纯净交付包 + 状态文档 | 本文件 + clean archive |
| P2.16 | 浏览器登录兼容 QA | `docs/VKPI_P2_BROWSER_QA.md` + browser login pass |
| P2.17 | 更新交付状态 + 纯净包刷新 | 本文件 + clean archive |
| P2.18 | 真实 crawler 小样本校准 | `docs/VKPI_P2_18_LIVE_CALIBRATION.md` + live guard |
| P2.19 | 业务录入体验收口 | `smoke_vkpi_p2_19_business_input_frontend.py` |
| P2.20 | 浏览器真实 QA: 项目创建 / KOL 选择 / 产品选择 / 项目详情附件入口 | `docs/VKPI_P2_20_BROWSER_QA.md` + browser QA pass |
| P2.21 | TikTok / Bilibili / Xiaohongshu 真实 crawler 小样本校准 | `docs/VKPI_P2_21_LIVE_CRAWLER_SAMPLES.md` + live mapping guard pass |

最新验证:

- `npm run build`: PASS
- `./scripts/run_smoke.sh --all`: PASS, 70/70
- `VKPI_P2_13_PROBE=1 ./scripts/run_smoke.sh smoke_vkpi_ui_api_route_acceptance.py`: PASS
- Browser QA 登录: PASS, Dashboard 可进入，无 500/权限拦截
- P2.18 live crawler calibration: Instagram PASS, YouTube PASS
- P2.19 business input frontend: PASS, 项目创建可合并产品成本目录 + 产品发布；项目详情消息/内容/条款/物流支持附件上传元数据
- P2.20 browser QA: PASS, 项目创建可选择已有 KOL + 产品发布 SKU；多产品 chip 可选；项目详情 4 类附件入口可见；无 500
- P2.21 live crawler samples: PASS, TikTok / Bilibili / Xiaohongshu 单账号真实请求均返回 `ok/synced` 且 KPI 可映射；未打开预算 gate
- 密钥扫描: 未发现新 diff 中包含明文 provider key

## 本地服务入口

后端:

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing
./scripts/start_admin.sh
curl -sS http://127.0.0.1:8102/health
```

前端:

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing/frontend
npm ci
npm run build
npm run dev
```

默认端口:

- Backend admin API: `http://127.0.0.1:8102`
- Frontend dev: `http://127.0.0.1:5173`

## 核心回归命令

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing

# 单项 route acceptance
./scripts/run_smoke.sh smoke_vkpi_ui_api_route_acceptance.py

# 小范围 provider probe
VKPI_P2_13_PROBE=1 ./scripts/run_smoke.sh smoke_vkpi_ui_api_route_acceptance.py

# v3 release gate
./scripts/run_smoke.sh smoke_vkpi_v3_release_gate.py

# 全量
./scripts/run_smoke.sh --all
```

## API 验收边界

`smoke_vkpi_ui_api_route_acceptance.py` 覆盖以下风险:

- Dashboard / Project / Link / Cost / KPI / KOL 等前端依赖路由返回 500
- Settings provider / budget / crawl / control-status 路由返回 404 或 500
- 非管理账号读取管理设置未被 403 拦截
- Provider status 或 probe 响应泄露完整 API key
- 后端进程 stale 导致新 route 未注册

默认不触发真实外部平台抓取。需要小范围健康探测时显式设置 `VKPI_P2_13_PROBE=1`。

## 当前架构边界

后端核心路径:

- `backend/app/api/routers/vkpi_*.py`: V-KPI API routers
- `backend/app/services/vkpi/*.py`: V-KPI domain services
- `backend/app/services/vkpi/industry_crawlers/*.py`: platform crawler adapters
- `scripts/smoke_vkpi_*.py`: smoke regression suite

前端核心路径:

- `frontend/src/components/vkpi/pages/`: V-KPI page shells
- `frontend/src/components/vkpi/panels/`: reusable panels
- `frontend/src/services/vkpi.ui-api.ts`: frontend API client

## 不再继续拆分的原则

当前不建议继续无边界拆分。后续只在满足以下条件时拆:

- 有明确 smoke 覆盖
- 单轮只动一个模块
- 保持 public wrapper 或 route 行为不变
- 拆完必须 `npm run build` 或 `py_compile` + `./scripts/run_smoke.sh --all`

## 下一步建议

优先级从高到低:

1. P2.22: 轻量 UX 修复，优先处理 KOL drawer 与 Project detail drawer 叠层。
2. P2.23: Settings、数据分析、红人搜索做浏览器真实 QA 补充。
3. P2.24: 预算与抓取闭环，从 Settings 开关/预算到数据分析刷新状态。
4. D 系列继续拆分暂缓，除非某个文件已经明确阻塞开发。
