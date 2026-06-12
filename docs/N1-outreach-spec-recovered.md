# N1 沟通历史 CRM · 骨架 spec(recovered 2026-06-12)
> **骨架版 · 细节以五月原文为准**(原文寻回后逐节替换)。裁决:N1=生,复活施工排 Pool 切面(第二段窗口后,与 G3 协作痕迹同族)。

## 组件六件(前端)
1. `OutreachTimelinePanel` — 时间线主面板(按日分组)
2. `OutreachRecordCard` — 单条记录卡
3. `OutreachCreateDrawer` — 新建记录抽屉
4. `OutreachEditDrawer` — 编辑抽屉
5. `OutreachAttachmentList` — 附件列表
6. `outreachTypes` — 类型定义

## API 三端点(后端)
1. `GET /kol-pool/{kol_id}/outreach/timeline` — 含 summary:`last_outreach_at` · `by_direction` · `by_status` · `is_active_conversation`
2. `GET /outreach/by-project/{project_id}`
3. CRUD(create/update/delete 记录与附件)

## 数据形
- `direction` = `outbound | inbound | internal_note`
- `status` = `no_response | replied_yes | agreed | …`(全集以原文为准)
- 附件(复用 evidence uploads 形)
- migration 正反向(up/down 三段式,编号随 apply 窗分配)
- **增量条款(2026-06-12 裁决)**:record schema 加 `quote_amount` + `currency` + `quote_date`(均可空)——**喂智能层 3 号 CPM 锚**

## 注入制(铁律)
- **只读注入宿主 Drawer,不改宿主文件**(13 区块红线同源);注入指南单独成档
- UI:Drawer 新增「沟通历史」tab,按日分组

## 依赖节 · 四借力(既有资产,复活非从零)
1. `vkpi_staff_outreach_digests`(migration 033)
2. `daily_outreach_digest_only` automation(SettingsPage:342 真调用在用)
3. `vkpi_outreach_suggestions` 表
4. `kol_outreach` 权限 key(staffPermissionTemplates.ts:41)
