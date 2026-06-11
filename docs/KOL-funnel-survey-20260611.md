#### ONE_PAGER
# V-KPI KOL 四环漏斗一页纸(2026-06-11,只读盘点综合)

**总判:漏斗断在第 1→2 跳(Pool→收藏),且第 2→3 跳被旁路。** 当前真实数据流是 Pool(1123)→[跳过收藏]→Projects(721 在役/302 已发布)→Dashboard(部分回显)。"收藏"环既无持久层也无消费方,是四环中唯一整环缺失。

| 环 | 现状 | 断点(断在哪一跳) | 关键证据(文件:行号) |
|---|---|---|---|
| **1 Pool** | 健康。vkpi_kol_pool 1123 行,smart search 链路冻结前已闭环;冻结仅 ~2 天(06-09 起),解冻成本低 | 无断点;欠账为结构性死代码(死 Sidebar、GEN2 占位、白名单拦截) | migrations/003_postgres_baseline.sql:84;V615ReplicaApp.tsx:11,94,939-953;git afc88ffc/2736f49e |
| **2 收藏 My KOL** | **整环缺失**。前端"我的列表"=纯内存 useState(new Set()),不写 localStorage、不调端点,刷新即丢;DB 全库无收藏表/列;MY KOL 页实际读 kols 主表 17 条全表(无收藏过滤),与 Pool 1123 条零数据通路 | **跳 1(Pool→收藏)完全断**:无持久层。最近通路 promote(linked_main_kol_id)仅 1/1123 linked 且前端按钮 disabled,语义=入役主表非个人收藏,不可复用 | KOLPoolPage.tsx:57,98-104;KOLDetailDrawer.tsx:1141-1148(title 自述"尚未接入后端保存");claim_listing.py:66-139(无收藏过滤);vkpi_kol_pool.py:1216-1243(promote);SQL: pool_linked=1/1123, kols=17 |
| **3 Projects** | 在役链路真实(2184 assignments/721 KOL),但 AddKolModal 选择器走 /kol-pool/available=**全池**,仅排除"已在本项目",标题"从 MY KOL Pool 添加"名不副实;"已关注"筛选因端点不返回 active_claim_id 恒为死筛选;写入侧 add_project_kols 不校验收藏可绕过 | **跳 2(收藏→Projects)被旁路**:选择器直连 Pool。且反向不完整:release claim 不感知在役 assignment(只改 claims.status+清 assigned_staff_id),在役 KOL 可沦为无人跟进孤儿;2183/2184 assignment 的 pool 行从未收藏→直接切"仅 My KOL"会清空选择器,**必须先 backfill** | workflow_projects.py:576-634(全池 SQL)、:637-679(无收藏校验);ProjectDetailModals.tsx:555(标题);kolModels.ts:52 vs workflow_projects.py:613-626(死筛选);claim_lifecycle.py:74-106(release 不查 assignments);SQL: 2183/2184 未收藏 |
| **4 Dashboard** | 大半为真:KPI 6 卡中 4 张走 /api/admin/vkpi/dashboard 真数据,GMV/ROI 诚实 pending;右栏 5 块全真;仅 Upcoming Events/Revenue Donut 硬编码空数组 | **跳 3(→回显)半通**:① 无四环聚合端点(漏斗为纯前端拼装,MyKolPage 自注"等待 grouped endpoint");② 收藏环计数算不出——claims FK 挂 kols(id) 非池,7 条 claims 0 条可 join 到池;③ stage 枚举坑:discovery(23)/discovered(170) 双拼写并存、DB 无 'reviewed' 行,roster_detail pending 桶漏 23 行 | summary.py:316-342,101-143;V615ReplicaApp.tsx:970-971;MyKolPage.tsx:439-455,608;SQL: 池1123→在役721(64.2%)→已发布302(41.9%) 一条 CTE 已验证可出 |

**修复主线**:先立收藏持久层(新表,不复用 promote/claims——前者语义混淆、后者 FK 错挂 kols),再 backfill 721 个在役 KOL 入收藏,然后才能切选择器"仅 My KOL"并补写入侧校验;Dashboard 漏斗聚合端点可并行先行(收藏数在 backfill 前诚实显示近零)。取消收藏走软禁止(409+force),绝不自动解除 assignment(会毁 Dashboard 度量与历史);同时盖住 DELETE /kols 与删 pool 行的两个 CASCADE 旁路。

#### CONSTRUCTION_CARDS

C1: C1 收藏持久层:新表 vkpi_kol_pool_favorites(迁移) [闸:迁移幂等可重放;psql \d 显示表+唯一索引+两 FK;vkpi_kol_pool 仍 50 列不变;回滚脚本就位] 依赖:无(序列起点)
  scope: 新建迁移:vkpi_kol_pool_favorites(id, kol_pool_id FK→vkpi_kol_pool(id) ON DELETE CASCADE, staff_id, created_at, UNIQUE(kol_pool_id, staff_id))。决策定稿:不复用 promote/linked_main_kol_id(语义=入役主表)也不复用 vkpi_kol_claims(FK 挂 kols(id),7 条 claims 0 条可 join 池)。现有表零改动。
  files: backend/migrations/00X_kol_pool_favorites.sql

C2: C2 收藏端点:favorite / unfavorite / list [闸:curl 三连:favorite→list 含该 id→unfavorite 后消失;跨 staff 不可见;重复收藏返回幂等 200/409 而非 500] 依赖:C1
  scope: POST /api/admin/vkpi/kol-pool/{id}/favorite、DELETE 同路径、GET /kol-pool/favorites(返回 pool 行+staff_scope 隔离);domain 层新建 pool_favorites.py,复用 claim_audit.log_kol_audit 落审计。
  files: backend/app/api/routers/vkpi_kol_pool.py; backend/app/domains/kol/pool_favorites.py

C3: C3 Pool 前端收藏接线(替换内存 Set) [闸:收藏→硬刷新→星标与『我的列表』过滤仍在;Network 面板可见端点调用;无 localStorage 残留方案] 依赖:C2
  scope: KOLPoolPage myList 从 useState(new Set()) 改为 API 驱动(初始 GET favorites,toggle 调 C2 端点+乐观更新);Drawer 星标按钮接真端点并删除『尚未接入后端保存』title;FilterBar『只显示我的列表』读服务端集合;KOLTable 星标随之。
  files: frontend/src/components/vkpi/v615-replica/KOLPoolPage.tsx; frontend/src/components/vkpi/v615-replica/components/KOLDetailDrawer.tsx; frontend/src/components/vkpi/v615-replica/components/FilterBar.tsx; frontend/src/components/vkpi/v615-replica/components/KOLTable.tsx

C4: C4 My KOL 页改读收藏集 [闸:My KOL 列表条数=当前 staff 收藏数(不再是 17 条主表);零收藏时空态正确;Pool 收藏后刷新 My KOL 即出现] 依赖:C2(可与 C3 并行)
  scope: MyKolPage 数据源从『kolOptions(kols 主表 17 条全表)+projects 推导』切到收藏集(favorites join pool 行);删除『当前用现有 KOL/project 数据过渡』自述与等待 grouped endpoint 空态;viltroxOnly 过滤口径随收藏集一并重定义(对齐 3-④,避免返工)。
  files: frontend/src/components/vkpi/pages/myKol/MyKolPage.tsx; frontend/src/services/vkpi/dashboard-api.ts

C5: C5 Backfill:721 个在役 KOL 批量入收藏 [闸:SELECT COUNT(DISTINCT kol_pool_id) FROM vkpi_kol_pool_favorites ≥ 721;抽样 20 条 active assignment 的 pool 行均已收藏;脚本可重放不重复插入] 依赖:C1
  scope: 一次性脚本:对 vkpi_project_kol_assignments(stage NOT IN churned/cancelled)涉及的全部 kol_pool_id 写入 favorites,归属取 assignment→project 负责人(无则默认负责人)。这是 C6 的硬前置:当前 2183/2184 assignment 的 pool 行从未收藏,直接切『仅 My KOL』选择器近空、导入全败。
  files: backend/scripts/backfill_pool_favorites.py

C6: C6 选择器切『仅 My KOL』+ 修活死筛选 [闸:available 仅返回收藏 KOL 且非空(≥721);『已关注』筛选不再恒判未关注;导入名单回归通过;scope 缺省时行为向后兼容] 依赖:C2 + C5
  scope: 后端:workflow_projects.list_available_kols 签名加 scope:str='my_kol',filters 追加 EXISTS(favorites),SELECT 补 active_claim_id/favorite 标志(修死筛选);路由 scope Query 透传。前端:projects-api 加 scope 参数,ProjectsPage 选择器(:249)与导入(:312)两调用点传参(共用端点一改两通);弹窗标题『从 MY KOL Pool 添加』终于名副其实,已关注筛选接真数据或删除。
  files: backend/app/domains/projects/workflow_projects.py; backend/app/api/routers/vkpi_kol_pool.py; frontend/src/services/vkpi/projects-api.ts; frontend/src/components/vkpi/pages/ProjectsPage.tsx; frontend/src/components/vkpi/pages/projects/ProjectDetailModals.tsx

C7: C7 写入侧防绕过:add_project_kols 收藏校验 [闸:直接 POST 未收藏 id → 4xx 含明确错误;已收藏 id 正常入役;既有项目流程回归不受影响] 依赖:C6
  scope: workflow_projects.add_project_kols 在插入前(约 :668)对每个 kol_pool_id 加收藏校验——只改 available 端点时,直接 POST /api/marketing/projects/{id}/kols 仍可塞任意 Pool KOL。
  files: backend/app/domains/projects/workflow_projects.py

C8: C8 取消收藏/删除的在役软禁止(409+force) [闸:对在役 KOL release/unfavorite/delete → 409 含项目数与清单;force=true 放行且审计落库;vkpi_project_kol_assignments 行前后零变更] 依赖:C1(语义定稿后即可做,可与 C6 并行)
  scope: 三处盖口:① claim_lifecycle.release()(:87 前)经 linked_main_kol_id 反查 pool,存在 stage_status='active' assignment 则 409 返回项目清单,body force=true 放行;② unfavorite 端点(C2)同口径在役检查;③ DELETE /api/marketing/kols/{id}(kol_ops.py:561-588)同检查,堵 FK CASCADE 连删 claims/断链 pool 的旁路。前端 MyKolMatrix 捕获 409 弹确认。绝不自动解除 assignment。审计沿用 claim_audit。
  files: backend/app/domains/kol/claim_lifecycle.py; backend/app/domains/kol/kol_ops.py; backend/app/domains/kol/pool_favorites.py; frontend/src/components/vkpi/pages/channels/MyKolMatrix.tsx

C9: C9 Dashboard 四环漏斗聚合端点(可并行先行) [闸:GET /api/admin/vkpi/dashboard 返回 funnel{1123, claimed, 721, 302, 64.2%, 41.9%} 与手工 SQL 一致;pending 桶含 discovery 23 行;窗口参数不影响漏斗总量口径] 依赖:无硬依赖(claimed 数字在 C5 后才有意义,验收建议排 C5 后)
  scope: summary.py evidence_metrics 加 funnel 块:pool_total / claimed(favorites 计数) / in_service / published + 两级 pct,一条 CTE(已验证可执行:1123→721 64.2%→302 41.9%)。SQL 必须兼容 stage 双拼写 discovery/discovered 与 content_posted(DB 无 reviewed 行);顺手修 roster_detail pending 桶漏掉 discovery 23 行。无硬前置——C5 前 claimed 诚实显示近零。
  files: backend/app/domains/dashboard/summary.py

C10: C10 前端漏斗卡接线 + 空壳清理 [闸:首页漏斗四数与 SQL/端点逐一相等;MyKolPage 漏斗与 Dashboard 同源同数(消灭双口径);无任何卡片回退到 data/metrics.ts 假数字] 依赖:C9(完整验收需 C5)
  scope: normalizers 解析 funnel 块;首页新增最小卡片集:My KOL 收藏卡/在役卡/已发布卡+转化条(Pool 总量卡已现成);MyKolPage 漏斗(:439-455)从前端拼装改读后端 funnel,补 measured 独立环口径决策;顺手删 V615ReplicaApp:970-971 的 Upcoming Events/Revenue Donut 硬编码空数组(或接真源)。
  files: frontend/src/components/vkpi/v615-replica/normalizers.ts; frontend/src/components/vkpi/v615-replica/DashboardReplicaPage.tsx; frontend/src/components/vkpi/v615-replica/V615ReplicaApp.tsx; frontend/src/components/vkpi/pages/myKol/MyKolPage.tsx

#### UNFREEZE
KOL Pool 解冻评估:冻结期仅约 2 天(06-09 最后实改 afc88ffc/2736f49e,06-10~11 火力全在 worker/Projects P5 波),smart search 主链路(统一入口→会话轮询→失败重试→终态回写)冻结前已闭环,解冻无大欠账,成本低。欠账以结构性死代码而非 TODO 堆积存在:① V615Sidebar.tsx 整文件死代码+假徽章(1023/49/7/12),V615ReplicaApp.tsx:11 死导入;② 8 个 GEN2 导航全落『此页面尚未接入』占位页(:939-953),activeNav 白名单(:94)只放行 5 个 key;③ 05-15 stash wip-three-tier-matrix 悬置。解冻第一刀:删 V615Sidebar 死文件+GEN2 徽章收敛为真假两档(零风险纯减法)。Campaigns GEN2 判定『后端真、旧壳真、v615 壳假、数据空』(vkpi_campaigns 0 行),收口三件事=白名单放行+CampaignsPage 挂入 v615 分支+ActiveCampaignsCard 数据源对齐,验收需至少一条真活动走通。TaskProgressBoard 的 vkpi:llm-activity 监听满足整组删除三条件(生产者 emitLlmActivity 全前端 0 调用点、合同提取已改 worker 队列回显、无其他 dispatcher),删除同时消除 clientActivities 无 TTL 的永久挂起隐患;唯一保留理由是近期计划重新引入同步 LLM 即时调用,当前无此计划即删。

#### MYKOL_TOP5
My KOL 优化前五(按价值排序):① 收藏机制持久化——四环漏斗 Pool→My KOL 断环本体,先于其余一切,对应施工卡 C1-C4(KOLPoolPage.tsx:57 内存 Set / vkpi_kol_pool.py 40 端点无 favorite / DB 无收藏表),跨层改动;② 漏斗阶段真实化+measured 独立环——employeeFunnelStage 只取最近项目 stage 致 multi-project KOL 失真,measured 被 published 吞掉(MyKolPage.tsx:55-76,448-451 死键),依赖第四环口径对齐,对应 C9/C10 的口径决策;③ 团队矩阵假数据剥离——硬编码 4 张人卡+假焦点+静态『同步 5 分钟前』(MyKolPage.tsx:36-41,136-145,701),不依赖漏斗可独立先做,与 8b13e9b9 honesty 波同方向,真实 staff 数据已有来源只差去壳;④ viltroxOnly 开关语义修复——开关只透传内容层、左侧列表过滤不受影响(MyKolPage.tsx:428,604,469-473),小改但建议与①一起定义过滤口径(已并入 C4 范围);⑤ 列表性能+硬编码默认选中——全量渲染无分页、contentReadyDefaultKolIds 硬编码 5 个 id(MyKolPage.tsx:589-598,179-184),①接通后列表自然收缩到收藏集、优先级随之下降;若①短期不做则此条升至第三位(全池 1123 条灌入是现实卡顿源)。

#### RING34 RAW(探查原文)
v615 Dashboard 首页 KPI 6 卡中 4 张(Active Roster/Active 30D/Exposure/Engagement)已走真实端点 /api/admin/vkpi/dashboard 的 evidence_metrics(底表 vkpi_kol_pool + vkpi_kol_video_evidence + vkpi_channel_post_metrics),GMV/ROI 两卡是诚实 pending 占位;右栏 Active Campaigns(starred projects)、日历(recent-content)、Signals(market-intelligence cards)、AI Today(copilot-brief)、Top Movers(kol-pool workspace)均为真数据;Upcoming Events 与 Revenue Donut 在 V615ReplicaApp.tsx:970-971 硬编码空数组,无数据源。四环漏斗:池子数(1123)、在役数(721)、已发布数(302)与两级转化率(64.2%/41.9%)用现表一条 SQL 即可算;唯一断环是"收藏(My KOL)"——vkpi_kol_claims.kol_id 外键挂 kols(id) 而非 vkpi_kol_pool,7 条 claims 0 条可 join 到池(linked_main_kol_id 仅 1 行非空),且 active claim 只有 1 条;此外缺一个一次性返回四环计数的聚合端点(MyKolPage.tsx:608 自注"等待后端 grouped endpoint",当前漏斗为纯前端拼装)。
  • 【真数据】KPI 卡1 Active Roster:dashboard.summary.evidence_metrics.active_roster_by_scope → build_dashboard_kpi → v_dashboard_account_pool 视图(基于 vkpi_kol_po
    ev: frontend/src/components/vkpi/v615-replica/normalizers.ts:316-318 → api.ts:37 (/api/admin/vkpi/dashboard?window_days=30) 
  • 【真数据】KPI 卡2 Active 30D:近30天有 publish_date 的 evidence 去重账号 + 官方渠道有发帖/正增量;卡3 Total Exposure = SUM(view_count);卡4 Engagement Rate = (likes+comments)/view
    ev: normalizers.ts:321-333 → summary.py:251-313 (_build_evidence_active_30d_summary, 表 vkpi_kol_video_evidence + vkpi_channe
  • 【pending 占位非假数】KPI 卡5 GMV、卡6 ROI 固定传 null + '待 Shopify 订单接入',MetricCard 渲染为 -- 与琥珀色待接入徽章;data/metrics.ts:14-55 里的硬编码假数字(1041/301/367M/3.94%)被 normaliz
    ev: normalizers.ts:336-344 (gmv/roi metricData(null)), normalizers.ts:347-352 (覆盖逻辑), data/metrics.ts:6-85, components/Metri
  • 【真数据】右栏 Active Campaigns 用 starred projects(starred=true)优先,空则不显示 fallback;7 天日历=recent-content;Signals=market-intelligence cards v0;AI Today=copilot-
    ev: api.ts:38-45(各端点), normalizers.ts:674-697 (normalizeV615Dashboard), normalizers.ts:480-525 (starred), 527-543 (calendar)
  • 【mock/空壳】Upcoming Events 浮动卡与 Revenue by Source 圆环卡:父组件硬编码空数组,Revenue 卡因 length>0 条件永不渲染,Upcoming Events 渲染空卡;二者无任何后端数据源
    ev: frontend/src/components/vkpi/v615-replica/V615ReplicaApp.tsx:970-971 (upcomingEvents: [], revenueBySource: []), Dashboar
  • 【漏斗·已能算】池子数=1123(端点现成:/api/admin/vkpi/kol-pool/summary 的 total;dashboard 端点 roster_detail.total_pool 也已返回);在役数=721、已发布数=302、转化率 64.2%/41.9% 一条 SQL 可出
    ev: SQL 验证(viltrox2@54329, 2026-06-11): SELECT COUNT(*) FROM vkpi_kol_pool → 1123; COUNT(DISTINCT kol_pool_id) FROM vkpi_pro
  • 【漏斗·断环1】收藏(My KOL)环:vkpi_kol_claims.kol_id 外键指向 kols(id) 而非 vkpi_kol_pool;7 条 claims(active 仅 1)经 linked_main_kol_id 反查 0 条可 join 到池(池内 linked_main_ko
    ev: SQL: \d vkpi_kol_claims (FK kol_id REFERENCES kols(id)); LEFT JOIN vkpi_kol_pool p ON p.linked_main_kol_id=c.kol_id → jo
    gap: 需 (a) claims 增挂 kol_pool_id 或批量回填 linked_main_kol_id,或 (b) 改用其他'收藏'语义(当前 MyKolPage 用项目 assignments 凑 claimed 漏斗,纯前端计算 My
  • 【漏斗·断环2】无四环聚合端点:没有任何端点一次返回 池子/收藏/在役/已发布+转化率;roster_detail.partnership_4tier(summary.py:101-143)是同底表的近似分桶但无'在役/已发布'单值;active_campaigns.published_kol_co
    ev: summary.py:101-143 (partnership_4tier SQL, 同表 vkpi_project_kol_assignments), summary.py:362-440 (_build_active_campaigns
    gap: 建议在 /api/admin/vkpi/dashboard 的 evidence_metrics 里加 funnel 块(pool_total/claimed/in_service/published+pct),后端一条 CTE 即可(已验
  • 【漏斗·口径坑】stage 枚举不一致:DB 实际无 'reviewed' 行(content_posted 696 行),而 roster_detail SQL 按 ('content_posted','reviewed') 过滤;'discovery'(23行) 与 'discovered'(1
    ev: SQL: SELECT stage, COUNT(*) FROM vkpi_project_kol_assignments GROUP BY stage → device_sent 843/content_posted 696/contac
    gap: roster_detail 的 pending 桶漏掉 stage='discovery' 的 23 行;建议统一枚举或 SQL 兼容
  • 【最小卡片集】① Pool 总量卡:数据现成(dashboard 端点 roster_detail.total_pool 或 /kol-pool/summary.total),零后端改动;② My KOL 收藏卡:GET /claims?status=active 可直接计数(现值 1),但与池无 
    ev: ①summary.py:79-80,223 + pool.py:499; ②vkpi_kol_links.py:173-179 + SQL claims_active=1; ③④SQL COUNT(DISTINCT kol_pool_id)
