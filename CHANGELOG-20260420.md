# Viltrox 2.0 — 2026-04-20 全量 Patch 已应用

本项目是 `viltrox-2.0-slim-source-config-db-20260419-200212` 的基础上，
应用了 **9 个 patch** 的完整代码库。下载解压即可部署，不需要再 cp/merge。

---

## 应用了什么

### 🔴 P0 修复（3 个）

#### 1. `services/trust.py` 性能修复
**原问题**：每次用户提交视频都会跑一条全表扫描
```sql
SELECT creator_handle, payload_json FROM platform_ingest_events
WHERE source_platform='shopify' AND entity_type='order' AND ingest_status='done'
```
没有 `user_id` 过滤，把所有历史订单 event 拉到 Python 里 loop 字符串匹配。订单越多越慢。

**修法**：
- 改查 `orders` 表（v5 schema 已有 `attribution_user_id` 字段）用索引查询 `O(log N)`
- fallback 保留原 `platform_ingest_events` 路径，但加上 `creator_handle` 索引过滤
- 新加 `_count_paid_shopify_orders()` 辅助函数

**改动**：
- `backend/app/services/trust.py` — 替换了 collect_trust_metrics 里的 paid_orders 计算块
- `backend/app/db/migrations.py` — `init_db()` 末尾自动建 2 个索引：
  - `idx_orders_attr_user_status`
  - `idx_ingest_shopify_by_handle`

---

#### 2. Scheduler 单独 systemd unit
**原问题**：3 个 systemd unit (public/admin/worker) 全都设了 `ENABLE_SCHEDULER=0`，
导致 16 个定时任务（B&H 抓取 / AI 洞察生成 / 集成健康检查 / Trust 阈值扫描…）从没跑过。
这就是 admin 页面 bh_products / ai_insights / orders 一直空的根本原因。

**修法**：
- 新加 `deploy/systemd/viltrox-2.0-scheduler.service` — 独立 unit，`ENABLE_SCHEDULER=1`，单进程（天然 leader）
- 新加 `scripts/start_scheduler.sh` — 启动脚本，单 worker

**部署命令**：
```bash
sudo cp deploy/systemd/viltrox-2.0-scheduler.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now viltrox-2.0-scheduler@$(whoami).service
sudo journalctl -u viltrox-2.0-scheduler@*.service -f    # 看日志
```

---

#### 3. Admin UI 和公开站分离
**原问题**：
- AdminRoute 套在 `AppShell` 里，AppShell 默认 `showFloatingVia=true`，admin 页面右下角有只猫
- AdminOverview 自己又有一层 `admin-v5-shell`，双重 shell 叠加
- 登录未认证时，登录表单嵌在公开站 shell 里（有 topbar、nav、猫），看起来像公开站的一个面板
- `global.css` 8140 行无 scope，admin 和 public 互相污染
- `components/admin.tsx` 和 `components/admin/` 目录同名，随时炸

**修法**：
- `components/admin.tsx` → `components/admin/AdminOverview.tsx`（搬进同名目录内）
- 新加 `components/admin/index.ts` barrel 做 re-export
- 修 11 处相对 import 路径
- 重写 `routes/admin/AdminRoute.tsx`，3 态分离：
  - 未登录 → `.admin-auth-viewport` 全屏居中登录卡（无公开站元素）
  - 登录但非 admin → 权限拒绝卡
  - admin 登录态 → `.admin-root` 独立 shell（只有极简 topbar + AdminOverview）
- 新加 `styles/admin.css`（318 行 scoped）— 所有选择器嵌套在 `.admin-root` 或 `.admin-auth-viewport` 下，主动 `display:none !important` 掉泄漏进来的 `.bw-topnav` / `.shell` / `.floating-via-cat` / `.ui-hero`

**改动**：
- `frontend/src/routes/admin/AdminRoute.tsx` — 重写
- `frontend/src/components/admin/AdminOverview.tsx` — 从原 `admin.tsx` 搬来
- `frontend/src/components/admin/index.ts` — 新建
- `frontend/src/styles/admin.css` — 新建

---

### 🟠 P1 修复（5 个）

#### 4. `.gitignore` 加好了（为 git init 做准备）
**原问题**：项目从没 git 化，靠 `v2_addon/` `v2_final/` `v3/` 这种文件夹备份。

**修法**：加一个完整的 `.gitignore`，覆盖：
- Python 缓存 (`__pycache__/`, `*.pyc`)
- Node (`node_modules/`, `dist/`)
- Secrets (`.env`, `*.pem`, `*.key`)
- SQLite DB (`*.db`, `*.sqlite`)
- Runtime/cache (`runtime/`, `.cache/`)
- IDE (`.vscode/`, `.idea/`)
- **历史备份目录** (`v2_addon/`, `*_final/`, `backup_*/`, `*.before-*`)

**下一步（你手动做）**：
```bash
cd /opt/viltrox-2.0   # 或你的实际路径
git init
git add .
git commit -m "initial snapshot with 2026-04-20 patches"
```

---

#### 5. `.env` 去重 ADMIN_PASSWORD
**原问题**：同一个变量定义 7 次，只有最后一个生效，但 7 行明文都在文件里。

**修法**：保留最后一条（实际生效的那条），删除前 6 条重复。

**改动**：`.env` — 7 条 → 1 条 ADMIN_PASSWORD

---

#### 6. Postgres `STRING_AGG(DISTINCT)` 兼容
**原问题**：`db/connection.py` 把 `GROUP_CONCAT(DISTINCT x)` 翻译成
`STRING_AGG(DISTINCT x, ',')`，Postgres 拒绝这种语法（必须带 `ORDER BY`）。

**修法**：改一行正则，加上 `ORDER BY`：
```python
# 之前:  r"STRING_AGG(DISTINCT \1, ',')"
# 之后:  r"STRING_AGG(DISTINCT \1, ',' ORDER BY \1)"
```

**改动**：`backend/app/db/connection.py` 第 303 行

---

#### 7. httpx 12s → 3s
**原问题**：`services/via/knowledge_seed.py` 里用 12 秒超时 fetch 外部页面，
如果目标站慢，整个 VIA 首次启动 / 新用户冷启动会卡 12 秒。

**修法**：改成 3 秒。

**改动**：`backend/app/services/via/knowledge_seed.py` 第 581 行

---

#### 8. 前端 `Promise.all` → 全量 `.catch()` 兜底
**原问题**：Admin 7 个 snapshot fetcher（Operations / Via / Analytics / Runtime …）
每个都用 `Promise.all` 并发 8-10 个 API 请求。一个失败整个 tab 空白。
scheduler 没跑时某些端点返回 500，Operations tab 就彻底打不开。

**修法**：给每个 `Promise.all` 块里所有 `apiFetch(...)` 调用都加 `.catch(e => {...})`
兜底，单个失败只 `console.warn`，返回 `{}` 不阻塞其他请求。

**改动**：`frontend/src/services/admin.service.ts` — 50 处 `.catch`，覆盖 10 个 snapshot 函数

---

### 🟢 P3 修复（1 个）

#### 9. Systemd unit 绝对路径修复
**原问题**：4 个 systemd unit 文件里写死了 macOS 本地路径
`/Users/jianbozhang/Downloads/viltrox-app-test/viltrox-2.0`，上服务器跑直接报路径不存在。

**修法**：统一改成 `/opt/viltrox-2.0`（部署建议路径）。

**改动**：
- `deploy/systemd/viltrox-2.0-public.service`
- `deploy/systemd/viltrox-2.0-admin.service`
- `deploy/systemd/viltrox-2.0-worker.service`
- `deploy/systemd/viltrox-2.0-scheduler.service` ← 新建

如果你服务器实际在其他路径（比如 `/home/viltrox/app`），跑一次：
```bash
sudo sed -i 's|/opt/viltrox-2.0|/你的实际路径|g' /etc/systemd/system/viltrox-2.0-*.service
sudo systemctl daemon-reload
```

---

## 部署到服务器（步骤清单）

### 第 1 步：上传 + 解压
```bash
# Mac 本地
scp viltrox-2.0-full-patched-20260420.zip viltrox:/tmp/

# 服务器
ssh viltrox
cd /opt   # 或你打算放的位置
sudo rm -rf viltrox-2.0.before-patch
sudo mv /home/viltrox/app viltrox-2.0.before-patch  # 备份旧 1.0
sudo unzip /tmp/viltrox-2.0-full-patched-20260420.zip
sudo mv viltrox-2.0-full-patched-20260420 viltrox-2.0
sudo chown -R viltrox:viltrox viltrox-2.0          # 或你的 service 用户
```

### 第 2 步：Python 环境
```bash
cd /opt/viltrox-2.0/backend
python3 -m venv venv
./venv/bin/pip install -r ../requirements.txt
```

### 第 3 步：前端构建
```bash
cd /opt/viltrox-2.0/frontend
npm install
npm run build
# dist/ 目录会生成
```

### 第 4 步：.env（敏感信息要手动加）
包里的 `.env` 是清理过的模板，**以下变量你必须补**：
- `SHOPIFY_API_KEY` / `SHOPIFY_API_SECRET`（Shopify dashboard 给）
- `SHOPIFY_WEBHOOK_SECRET`（店铺后台配 webhook 时给）
- `POSTGRES_URL`（Postgres 连接串）
- `REDIS_URL`（Redis 连接串）

### 第 5 步：装 systemd unit
```bash
sudo cp /opt/viltrox-2.0/deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now viltrox-2.0-public@$(whoami).service
sudo systemctl enable --now viltrox-2.0-admin@$(whoami).service
sudo systemctl enable --now viltrox-2.0-worker@$(whoami).service
sudo systemctl enable --now viltrox-2.0-scheduler@$(whoami).service   # 🆕
```

### 第 6 步：验证
```bash
# 看 4 个服务状态
sudo systemctl status viltrox-2.0-*@*.service --no-pager

# 看 scheduler 定时任务注册了没
sudo journalctl -u viltrox-2.0-scheduler@*.service -n 20 | grep register

# 看索引建了没
sqlite3 submissions.db ".indexes orders"
# 应该有 idx_orders_attr_user_status
```

### 第 7 步：Git 化（强烈建议现在做）
```bash
cd /opt/viltrox-2.0
git init
git add .
git commit -m "initial snapshot - 2026-04-20 patches applied"

# 如果 GitHub 有 private repo,推上去
git remote add origin git@github.com:你的用户/viltrox-2.0.git
git push -u origin main
```

---

## 验证清单

应用完所有 patch 后，挨个勾：

### 后端
- [ ] `sudo systemctl status viltrox-2.0-scheduler@*` → `active (running)`
- [ ] `journalctl -u viltrox-2.0-scheduler@*.service` 能看到 `scheduler.bh_daily_snapshot_registered`
- [ ] `sqlite3 submissions.db "PRAGMA index_list('orders');"` 看到 `idx_orders_attr_user_status`
- [ ] `grep -c ADMIN_PASSWORD .env` 返回 `1`
- [ ] 提交一个视频，日志无 "trust metrics took >1s" 警告

### 前端
- [ ] 公开首页 `/` 不出现 admin 风格
- [ ] Admin 未登录 `/admin` → 独立居中登录卡，无浮动猫
- [ ] Admin 登录后 → 极简 topbar，无 BwTopNav，无 FloatingViaCat
- [ ] 网络请求面板：即使某个 `/api/admin/xxx` 500 了，tab 其他部分仍有数据

---

## 下一轮再说的 bug（未覆盖）

- #6 pbkdf2 100k → 600k iterations（要写登录时旧 hash 升级兼容逻辑）
- #9 `repositories/insights.py` 的 `{cutoff}` SQL 注入风险（要先确认 days 参数链路）
- #11 `global.css` 整体拆分 scope（本轮只隔离了 admin）
- #12 11 个集成的 health-check 实现（一个一个补）
- #17 多 worker 的 scheduler leader lock（目前用单独 systemd unit 绕开，以后要真加锁）
- #21 `SHOPIFY_WEBHOOK_SECRET` 要你去 Shopify 店铺后台配完 webhook 才能填
- #20 Shopify secret 泄露 → 记得去 Shopify dev dashboard 点"轮换"

---

## 回滚办法

如果部署出问题想退回：

```bash
sudo systemctl stop viltrox-2.0-*@*.service
sudo rm -rf /opt/viltrox-2.0
sudo mv /opt/viltrox-2.0.before-patch /opt/viltrox-2.0    # 用之前备份的旧 1.0
sudo systemctl start viltrox-2.0-*@*.service
```

旧数据库如果跑过迁移也不用回滚——加的 2 个索引是 `IF NOT EXISTS`，
就算 SQL 层旧代码也能正常工作。

---

## 总结

**总改动文件数**：
- 新建：6 个
  - `deploy/systemd/viltrox-2.0-scheduler.service`
  - `scripts/start_scheduler.sh`
  - `frontend/src/components/admin/index.ts`
  - `frontend/src/components/admin/AdminOverview.tsx`（从 admin.tsx 搬）
  - `frontend/src/styles/admin.css`
  - `.gitignore`

- 修改：8 个
  - `backend/app/services/trust.py`（性能修复）
  - `backend/app/db/migrations.py`（注入索引 migration）
  - `backend/app/db/connection.py`（STRING_AGG + ORDER BY）
  - `backend/app/services/via/knowledge_seed.py`（httpx timeout）
  - `frontend/src/services/admin.service.ts`（50 处 .catch 兜底）
  - `frontend/src/routes/admin/AdminRoute.tsx`（重写 3 态分离）
  - `.env`（去重 ADMIN_PASSWORD）
  - 4 个 `deploy/systemd/*.service`（路径标准化）

- 删除：1 个
  - `frontend/src/components/admin.tsx` （搬进 `components/admin/AdminOverview.tsx`）

**包里 NOT 改的部分**：本 patch 只动上述文件，其他 470 多个文件保持原样。

---

## 2026-04-21 补丁：清理僵尸文件

删掉了 3 个没人用的文件：

1. **`frontend/src/routes/PublicHomeRoute.tsx`** — 老版本公开站首页组件
   - 0 处引用（router 用的是 `routes/public/IndexRoute.tsx`，不是这个）
   - 用的是旧 AppShell + CreatorWelcome + Via 猫的架构，跟当前黑白 BwTopNav 风格不一致
   - 留着会误导以后的人以为是"可以切回来的老版本"

2. **`frontend/src/pages.tsx`** — 182 行的老路由 hub 组件
   - 0 处引用（被 react-router v6 后的 `app/router.tsx` 完全取代）

3. **`frontend/src/pages/`** — 空目录
   - 和同名的 `pages.tsx` 构成命名冲突地雷（跟之前 `admin.tsx` vs `admin/` 一样的问题）

### 保留但可以以后清理的死代码

`frontend/src/components/creator.tsx` 里有 7 个 export 确认 0 处引用：
`SubmissionComposer`, `SystemProgressCard`, `RewardsPanel`, `LeaderboardPanel`,
`CreatorWelcome`, `PointsRulesCard`, `VideoSourceLink`

保留原因：这是组件库文件，里面 `MonoUploadComposer` / `MonoProgressCard` / `AccountHub`
还在用。删个别 export 不如等 Vite tree-shaking 自然剔除。等以后重构 `creator.tsx`
时一起清。

---

## 2026-04-21 批次 1: Admin UI 框架重构

### 新增文件
- `frontend/src/components/admin/Icons.tsx` - 集中封装 30+ 个 Lucide React icon
- `frontend/src/components/admin/AdminShell.tsx` - 新的外层壳(顶栏 + sidebar + main outlet)
- `frontend/src/routes/admin/AdminLoginRoute.tsx` - 独立 `/admin/login` 路由

### 修改文件
- `frontend/src/styles/admin.css` - 整个重写(670+ 行,完整 design tokens)
- `frontend/src/routes/admin/AdminRoute.tsx` - 简化为纯守门员
- `frontend/src/app/router.tsx` - 注册 `/admin/login` 路由
- `frontend/src/components/admin/index.ts` - barrel 新增 AdminShell / Icons 导出

### 能看到什么
1. 访问 `/admin` 未登录 → 自动跳 `/admin/login`
2. `/admin/login` 全屏黑色居中登录卡,SF Pro 字体
3. admin 登录后 → 外层顶栏 (V-OS Admin + 全局搜索 + 公开站链接 + 头像)
4. 左侧 sidebar 9 个 tab 带 Lucide icon
5. 右侧内容暂时还是旧 AdminOverview(内容,未重写),但外层壳已新

### 还不能看到(批次 2-5 做)
- Overview / Operations / Creators 等具体 tab 的新设计
- 全局搜索真正搜东西 (现在只是 UI)
- Lifecycle stage chip 在 Creators tab
- 筛选面板折叠
- 移动端抽屉 + 底部 tab bar

### 批次 1 不改后端, 不动任何 tab 的内容逻辑
只是把 **shell** 换了。AdminOverview 的功能原封不动工作。

---

## 2026-04-21 批次 2-5: Admin UI 全量重写完成

### 新增目录与文件

**components/admin/shared_v2/** — 新设计系统共享组件 (11 files)
- PageHeader.tsx, KPIGrid.tsx, States.tsx, Creator.tsx, LifecycleRow.tsx,
  Filters.tsx, DataTable.tsx, Tags.tsx, Viz.tsx, useAdminSnapshot.ts, index.ts

**components/admin/tabs_v2/** — 9 个 tab 组件 (10 files)
- OverviewTab.tsx      批次 2: 核心 KPI + 最近提交
- OperationsTab.tsx    批次 2: review / verify / users / redemptions
- CreatorsTab.tsx      批次 3: HERO — VID/VIP/Lifecycle/筛选/Tag/详情
- ProductsTab.tsx      批次 3: 产品目录 + Top 5
- AnalyticsTab.tsx     批次 4: 月度排行榜 + learning stats
- StudentTab.tsx       批次 4: 学校 / 批次 / 名册
- ViaTab.tsx           批次 4: 提案 / 策略 / 评估
- CommandTab.tsx       批次 5: commerce + brand matrix + market
- RuntimeTab.tsx       批次 5: integrations / trust / staff / system
- index.ts

**routes/admin/AdminRoute.tsx** — 完全重写为 tab 路由器
- 使用 react-router nested <Routes>
- 9 tab 对应 9 路径
- 不再依赖 AdminOverview.tsx (保留为孤儿备用)

**components/admin/AdminShell.tsx** — 增加 mobile 支持
- 汉堡菜单 → 抽屉
- 5 个主 tab 底部 tab bar (mobile)
- 全局搜索默认跳 /admin/creators?q=...

**styles/admin.css** — 补完 mobile 响应式
- 抽屉 (position: fixed + transform)
- 抽屉遮罩
- 底部 tab bar (iOS-style, env safe-area-inset)

### 验证
- ✅ 36 个 admin 文件相对 import 全部通过
- ✅ 227 个后端 Python py_compile 全绿
- ✅ 新旧路由并存: /admin (主), /admin/* (tab), /admin/login (独立)

### 视觉效果

**桌面** (≥769px):
- 顶栏 44px: logo + 搜索框 + 公开站 + 语言 + 头像
- 左侧 sidebar 150px: 9 tab + Lucide icon + 未读 badge
- 主区: 9 个精细化 tab 页 (SF Pro 字体, VID 主标识, 金属 VIP 色等)

**移动** (≤768px):
- 顶栏: hamburger + logo + 搜索 + 头像
- sidebar 变抽屉 (点汉堡打开, 背景遮罩)
- 底部 5 tab (Overview/Operations/Creators/Analytics/Runtime) — 黑色 iOS 风
- 主区自动留 bottom padding (56px + safe-area)

### 没动
- 后端 API 一行没改
- 旧 AdminOverview.tsx 文件保留, 但 AdminRoute 不再引用
- 旧 components/admin/tabs/ 目录 (更旧的实验) 保留, 但无人引用

### 部署说明

解压后覆盖本地 src/ 目录, 然后 `cd frontend && npm run build`.
不需要重启后端, 不需要跑数据迁移.
