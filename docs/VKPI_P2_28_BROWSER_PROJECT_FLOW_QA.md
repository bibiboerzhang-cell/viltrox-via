# P2.28 Browser Project Flow QA

日期: 2026-05-10

## 范围

P2.28 只验证项目实操链路，不继续拆大文件:

- 登录 Viltrox Marketing。
- 进入 `项目跟进`。
- 新建项目时选择已有 KOL。
- 新建项目时选择主产品，并通过 chip 追加多选产品。
- 从项目列表打开项目详情抽屉。
- 在详情抽屉里确认 4 类附件入口存在。
- 写入一条消息记录，确认详情刷新后可读回。

## 环境

- Frontend: `http://127.0.0.1:5173`
- Backend: `http://127.0.0.1:8102`
- Backend health: `{"status":"ok","service":"admin-web"}`
- Runtime DB: local `127.0.0.1:54329/viltrox2`
- Backup before QA: `/Users/bibiboer/Documents/V-KPI-backups/before-p2.28-browser-project-flow-20260510-225629.tar.gz`

## Browser QA 结果

测试 marker:

```text
vkpi-p2-28-browser-1778425207
```

| 检查项 | 结果 | 证据 |
| --- | --- | --- |
| 登录 / Dashboard | PASS | `Jianbo` 管理层账号进入 Dashboard，无 500、无权限拦截 |
| 项目页进入 | PASS | 左侧 `项目跟进` 可打开，页面显示 `新建项目`、`自动推进流程`、`项目列表` |
| KOL 选择 | PASS | `合作红人` 下拉显示已有红人: `adrisangui`, `seanellisphoto`, `jianbo.zhang` |
| 产品选择 | PASS | 临时成本目录产品后，`主产品` 下拉显示 `P2.28 AF 35mm QA Lens` 和 `P2.28 AF 56mm QA Lens` |
| 多产品选择 | PASS | `可关联产品` chip 显示两个产品，第二个产品可追加选择 |
| 创建项目 | PASS | 浏览器创建 `vkpi-p2-28-browser-1778425207 Browser QA Project` |
| 项目详情 | PASS | 点击项目列表里的项目名称打开 `项目详情` 抽屉 |
| 多产品读回 | PASS | 详情抽屉阶段记录显示 `关联产品：P2.28 AF 35mm QA Lens ... P2.28 AF 56mm QA Lens ...` |
| 附件入口 | PASS | `消息附件 / PDF / 截图`、`内容素材 / 截图 / PDF`、`条款附件 / PDF / 报价单`、`物流凭证 / PDF / 截图` 均可见 |
| 消息写入 | PASS | 浏览器写入 `P2.28 browser QA message with attachment`，详情刷新后消息记录可读回 |
| Console / 500 | PASS | QA 期间未发现前端 console error 或 500 toast |

## 附件自动化边界

浏览器插件当前不能把本地文件直接注入 `<input type="file">`，因此 P2.28 的浏览器 QA 验证到“文件入口可见 + 消息写入读回”。

真实 multipart 上传闭环已由 P2.26 覆盖:

- `scripts/smoke_vkpi_p2_26_project_attachments.py`
- `/api/marketing/evidence/uploads`
- 4 类文件: message / content asset / terms / shipment
- `GET /api/marketing/projects/{id}` 读回上传 URL

## 固化验收

P2.28 新增静态 smoke:

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing
./scripts/run_smoke.sh smoke_vkpi_p2_28_project_flow_frontend.py
```

期望:

```text
VKPI_P2_28_PROJECT_FLOW_FRONTEND_SMOKE_OK
```

该 smoke 固化:

- KOL 下拉优先使用已有红人，保留手动 fallback。
- 产品来源合并成本目录和产品发布。
- 主产品单选 + 多产品 chip 逻辑存在。
- 项目列表 row click 打开项目详情。
- 详情抽屉渲染 `ProjectEvidenceForms`。
- 4 类附件入口和上传 purpose 保留。

## 边界

- 本轮不触发外部平台 API。
- 本轮不新增 schema。
- 本轮不继续 D 系列拆分。
- 本轮不清理现有业务样例数据，只清理 P2.28 临时 marker 数据。
