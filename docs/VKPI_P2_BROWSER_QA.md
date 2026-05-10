# V-KPI P2 Browser QA Notes

更新时间: 2026-05-10

## 范围

本轮 P2.16 聚焦浏览器真实入口，不继续拆分业务模块。

覆盖路径:

- 登录页
- 登录后 Dashboard 首屏
- 侧边栏入口可见性
- API route acceptance smoke
- v3 release gate

## 已验证

| 项目 | 结果 | 备注 |
| --- | --- | --- |
| 前端 dev server | PASS | `http://127.0.0.1:5173/` |
| 后端 admin API | PASS | `http://127.0.0.1:8102/health` |
| 登录页渲染 | PASS | 显示 `Viltrox Marketing / 登录 / 邮箱 / 密码` |
| 临时 QA 管理员登录 | PASS | 登录后进入 Dashboard |
| Dashboard 首屏 | PASS | 显示侧边栏、指标卡、趋势、漏斗、员工贡献、提醒、周报 |
| 登录后 500 | PASS | 首屏未出现 `500 Internal Server Error` |
| 权限错误 | PASS | 首屏未出现 `当前账号没有 Viltrox Marketing 权限` |

## 本轮修复

登录邮箱输入框从 `type="email"` 改为:

```tsx
type="text"
inputMode="email"
autoCapitalize="none"
```

原因:

- 保留移动端/浏览器邮箱输入体验。
- 避免部分自动化和浏览器环境对 `type=email` 的 `setRangeText` 兼容问题。
- 不改变登录 API、认证状态、localStorage token 逻辑。

## 已知工具限制

Codex in-app browser 本轮在复杂侧边栏点击和截图上出现工具层超时。该问题不等同于 V-KPI 前端故障。

后续如果要做完整可重复 UI E2E，建议单独引入项目内 Playwright/Cypress，并把以下路径自动化:

1. 登录
2. 管理主控
3. 系统设置
4. 数据分析
5. 红人搜索
6. 项目跟进
7. 退出登录

## 当前回归入口

```bash
cd /Users/bibiboer/Documents/V-KPI——marketing

npm --prefix frontend run build
./scripts/run_smoke.sh smoke_vkpi_ui_api_route_acceptance.py
./scripts/run_smoke.sh smoke_vkpi_v3_release_gate.py
./scripts/run_smoke.sh --all
```
