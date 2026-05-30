# KOL Pool Page — Claude Code Briefing

## 任务目标
在不动现有任何页面的前提下,新增一个 KOL Pool 真页面,接真后端,跟现有 
DataAnalysisPage 等并行运行。同事用 3-7 天验证后,再考虑是否删除旧页面。

## 项目状态(2026-05-25 / commit 8bb0943)
- 后端 P4 域迁移已完成,backend/app/domains 是真业务主层
- KOL Pool 相关后端 endpoint 全部就位(见下方)
- 旧 services/vkpi 已退成 313 行 shim,router 不再依赖
- 800 行守卫 0 违规
- 当前重点:UI 大改,不是后端继续清理

## Mockup 输入(两份 HTML 在桌面)
- ~/Desktop/kol_pool_v6.15.6.html         单页源版本(参考交互细节)
- ~/Desktop/vkpi_v6.15.7_integrated.html  已嵌入大框架的版本(参考最终形态)

两份 mockup 都是同一个 KOL Pool 页面的不同表达,以 v6.15.7_integrated 为准,
v6.15.6 仅作交互细节参考。

## 可用后端 endpoints(P4 已就位,无需新增)
- GET  /api/admin/vkpi/kol-pool              列表
- GET  /api/admin/vkpi/kol-pool/{id}         详情(已返回 refresh / freshness)
- GET  /api/admin/vkpi/search?q=...          自然语言搜索
- POST /api/admin/vkpi/kol-pool/import       一键导入
- POST /api/admin/vkpi/kol-pool/{id}/link    转入主库

## 关联 domain 层(已稳定,前端通过 API 间接调用)
- backend/app/domains/kol/pool.py
- backend/app/domains/kol/eleven_dimensions.py
- backend/app/domains/kol/intelligence_card.py
- backend/app/domains/recommendations
- backend/app/domains/sync/refresh_tier

## 数据契约(关键 — mockup 用了 3 个后端暂时没有的字段)
mockup 引入的字段:
- candidate_kind: existing_fresh | existing_stale | existing_low_confidence 
                | new_promoted | new_validated | new_discovered
- refresh_state:  fresh | stale | warming | queued
- validation_score: 0-100

后端现状:
- refresh_state 在 vkpi_kol_pool.py 返回(字段名 refresh / freshness)
- candidate_kind / validation_score 后端暂无

处理方案:
- 在 frontend/src/domains/kol/searchModels.ts 定义 TypeScript 契约
- candidate_kind 前端从后端字段推导:
    fresh + 在主库  → existing_fresh
    stale + 在主库  → existing_stale  
    缺数据 + 在主库 → existing_low_confidence
    新发现未入库    → new_discovered
    cheap_validate 通过 → new_validated
    人工标记升级    → new_promoted
- validation_score 暂用占位值,等后端补字段后接

## 严格约束
1. 不动 DataAnalysisPage / DashboardPremium / 任何现有页面文件
2. 不删除、不重命名任何现有文件
3. 不接 mock data,直接接真后端
4. 路由 key 用 'kol-pool',GlassSidebar 加导航项,标签后加 "(新)" 角标
5. 0 行后端代码改动
6. 单页 cutover 模式 — 这个页面跑通同事用一周后才考虑删旧的

## 参考模板
frontend/src/components/vkpi/pages/DataAnalysisPage.tsx
是离 KOL Pool 最近的页面,学习它:
- 怎么用 GlassSidebar / GlassTopBar 这套 chrome
- 怎么定义 page props / state
- 怎么调 API(看 services/http 的 apiFetch 用法)
- 怎么用 frontend/src/domains/kol/api.ts 已有的 client

## 文件预期产出
frontend/src/
├── domains/kol/
│   └── searchModels.ts                              # 新增 TS 契约
├── components/vkpi/
│   ├── pages/
│   │   ├── KOLPoolPage.tsx                          # 新增主页面
│   │   └── kol-pool/                                # 新增子目录
│   │       ├── KPIBar.tsx
│   │       ├── FilterBar.tsx
│   │       ├── SearchProgressBar.tsx
│   │       ├── KOLTable.tsx
│   │       ├── KOLDetailDrawer.tsx
│   │       ├── ContactModal.tsx
│   │       ├── CandidateKindChip.tsx
│   │       ├── RefreshStateStripe.tsx
│   │       ├── TrendPulseBar.tsx
│   │       ├── MarketCoverageCard.tsx
│   │       └── candidateKind.ts                     # 推导逻辑 + 标签信息
│   ├── styles/
│   │   └── kol-pool.css                             # .kp-table / .geo-bar-* 规则
│   └── vkpiTypes.ts                                 # 加 'kol-pool' 到 VkpiPageKey
└── (主 App / Router 文件)                            # 加路由分支

## 验收标准
1. npm run typecheck → 0 errors
2. npm run build → 通过
3. 浏览器手测:
   - Sidebar 点 "KOL Pool (新)" → 跳转到新页面
   - 页面渲染真 KOL 数据(不是 mock)
   - 候选类型 chip / 国家筛选 / 排序 / 我的列表 toggle 都生效
   - 点 KOL 行打开 detail drawer
   - 自然语言搜索框输入 → 调 /api/admin/vkpi/search
4. 现有页面(DataAnalysisPage / DashboardPremium 等)行为不变

## 后续计划(本次任务不做,只供你了解全局)
- 阶段 2:Mission Control V2(新管理主控页),同样模式
- 阶段 3:后端补 candidate_kind / validation_score 字段,前端去掉推导逻辑
- 阶段 4:同事用 1-2 周稳定后,删除被替代的旧页面
