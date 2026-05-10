# V-KPI P2.22 Drawer UX Fix

日期: 2026-05-10

## 范围

本轮只修 P2.20 浏览器 QA 发现的 drawer 叠层问题，不做大拆分。

问题:

- 从 KOL Profile 的项目历史点击“打开项目详情”时，KOL Profile drawer 与 Project Detail drawer 会同时留在右侧。
- 同类问题也可能发生在 Staff Profile -> Project Detail。

## 修复

文件:

- `frontend/src/components/vkpi/VkpiDashboard.tsx`
- `scripts/smoke_vkpi_p2_22_drawer_ux_frontend.py`

改动:

- 新增 `closeKolProfileDrawer()`。
- 新增 `closeStaffProfileDrawer()`。
- `handleSelectProject()` 打开项目详情前先关闭 KOL/Staff profile drawer。
- KOL/Staff drawer 的 `onClose` 复用同一个 helper，避免关闭逻辑分叉。

## 验收

- `smoke_vkpi_p2_22_drawer_ux_frontend.py` 验证打开项目详情前会关闭 profile drawer。
- `npm run build` 必须通过。
- `./scripts/run_smoke.sh --all` 必须通过。
- 浏览器复核项目详情打开后不再保留 KOL Profile drawer。

## 边界

本轮不改:

- Project Detail drawer 内容结构。
- KOL Profile drawer 数据接口。
- CSS drawer 宽度/层级。
- 其他页面的 Account drawer / Filter drawer。

下一步可以进入 P2.23: Settings、数据分析、红人搜索浏览器真实 QA。
