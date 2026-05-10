# P2.26 Project Attachment QA

更新时间: 2026-05-10

## 范围

P2.26 只补项目详情附件闭环，不继续拆大文件:

- 消息附件: 本地文件上传后以 `evidence_url` 写入项目消息。
- 内容素材: 本地文件上传后写入 `vkpi_content_assets`，项目详情刷新后仍可见。
- 合作条款: 本地文件上传后 URL 进入条款备注和 deliverable evidence。
- 物流凭证: 本地文件上传后以 `evidence_url` 写入 shipment，并在详情抽屉展示。

## 修复点

- `backend/app/services/vkpi/workflow_evidence.py`
  - `add_project_content()` 现在会把 `asset_url` / `thumbnail_url` 同步写入 `vkpi_content_assets`。
  - 避免同一 `post_id + asset_url` 重复插入。
  - 写入 `content_asset_add` 业务审计。

- `frontend/src/components/vkpi/drawers/ProjectDetailDrawer.tsx`
  - `样品 / 物流` 行展示 `evidence_url`，员工和管理层都能看到物流凭证链接。

- `scripts/smoke_vkpi_p2_26_project_attachments.py`
  - 真实 multipart 上传 4 个本地小文件。
  - 走 `/api/marketing/evidence/uploads`、`/projects/{id}/messages`、`/content`、`/terms`、`/shipments`。
  - 验证 `GET /api/marketing/projects/{id}` 能读回 4 类上传 URL。
  - 清理数据库 marker 数据和本地上传文件。

## 验证

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing

PYTHONPATH=backend .venv/bin/python -m py_compile \
  backend/app/services/vkpi/workflow_evidence.py \
  scripts/smoke_vkpi_p2_26_project_attachments.py

./scripts/run_smoke.sh smoke_vkpi_p2_26_project_attachments.py
npm run build
./scripts/run_smoke.sh --all
```

期望:

- `VKPI_P2_26_PROJECT_ATTACHMENTS_SMOKE_OK`
- 全量 smoke 新增 1 条后通过。
- 前端 build 通过。

## 边界

- 本轮不触发外部平台 API。
- 本轮不改通用 message/content evidence API，因为现有 `/api/marketing/content` 已经支持 `asset_url`。
- 浏览器文件选择器不作为自动化强制项；真实文件上传由 smoke 通过 multipart 覆盖。
