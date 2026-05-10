# V-KPI P2.20 Browser QA

日期: 2026-05-10

## 范围

本轮只做浏览器真实 QA，不做新功能拆分。

验证链路:

- 登录 Viltrox Marketing
- 进入项目跟进
- 新建项目时选择已有 KOL
- 新建项目时选择产品发布 SKU
- 多产品选择入口可操作
- 进入项目详情
- 项目详情附件上传入口可见
- 全程确认无 500

## 环境

- Frontend: `http://127.0.0.1:5173`
- Backend: `http://127.0.0.1:8102`
- Runtime env: `scripts/runtime_env.sh`
- Database: local `127.0.0.1:54329/viltrox2`
- Backup before QA: `/Users/bibiboer/Documents/V-KPI-backups/before-p2.20-browser-qa-20260510-211302.tar.gz`

注意: `.env` 中的默认 `DATABASE_URL` 可能不是当前运行库；本轮所有 DB 命令均先 `source scripts/runtime_env.sh`，避免误连旧库。

## 结果

| 项目 | 结果 | 证据 |
| --- | --- | --- |
| 登录 | PASS | `jianboz@viltrox.com` 本地管理员账号可进入 Dashboard，无 500 |
| Dashboard | PASS | 页面展示核心指标、左侧导航、退出入口，无权限错误 |
| 项目页 | PASS | `项目跟进` 页面可打开，表单和项目流程区可见 |
| KOL 选择 | PASS | `合作红人` 下拉显示已有红人: `adrisangui`, `seanellisphoto`, `jianbo.zhang` |
| 产品选择 | PASS | 临时种子产品后，`主产品` 下拉显示产品发布 SKU |
| 多产品入口 | PASS | `可关联产品` chip 区显示两个产品，第二个产品按钮可切换为 active |
| 创建项目 | PASS | 使用 label-based 选择器创建项目后，DB 持久化 `product_sku=P2-20-QA-SKU`, `product_name=P2.20 QA Lens` |
| 项目详情 | PASS | 项目详情 Drawer 可打开，无 500 |
| 附件上传入口 | PASS | `消息附件 / PDF / 截图`, `内容素材 / 截图 / PDF`, `条款附件 / PDF / 报价单`, `物流凭证 / PDF / 截图` 均可见 |
| 测试数据清理 | PASS | 2 个 P2.20 测试项目和 2 个 P2.20 测试产品已清理 |

## 发现

1. 浏览器插件在本机输入长文本时触发虚拟剪贴板限制，导致自动化无法完成第二次多产品项目提交；页面本身没有 500，DOM 已确认多产品 chip 可选中。
2. 如果自动化脚本用原始 `select.nth(...)`，可能误选顶部数据范围等非项目表单控件；真实 QA 应使用 label-based 选择器，例如 `合作红人`、`主产品`。
3. 从 KOL profile 进入项目详情时，会出现 KOL drawer 与 Project detail drawer 叠层显示；当前不阻塞业务，但属于后续 UX 债务。
4. 初始库中 `vkpi_product_launches` 为 0，因此产品下拉为空是数据状态，不是 UI 失败；种子产品后入口正常显示。

## 验收口径

P2.20 不新增 smoke。验收依赖:

- 浏览器链路结果记录在本文件
- `npm run build` PASS
- `./scripts/run_smoke.sh --all` PASS
- diff secret scan PASS
- 测试数据清理完成

## 后续边界

不建议继续为了 P2.20 拆 UI 或重写 Project detail。下一步应转到 P2.21 的真实 crawler 小样本扩展，或专门开一轮轻量 UX 修复处理 drawer 叠层。
