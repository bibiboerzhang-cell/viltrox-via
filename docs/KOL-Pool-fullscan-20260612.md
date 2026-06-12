# KOL Pool 全量扫描作战图(fullscan 2026-06-12)

口径:5 路扫描(架构/血缘/API/金字塔/UI)合成;DB 实测 viltrox2(只读)。红线不动:fit_score 唯一写点 pool.py:838、13 区块不简化、rule_v0+rubric 冻结。已知项(假按钮/性能/新鲜度/机器词/loyalty 量纲,见 docs/KOL-Pool-delta-report-20260611.md)只入第六节排序表不展开。
**合成期新发现(修正既有口径)**:vkpi_kol_pool.py 实为 **44 端点非 41** —— 多出 C2 收藏三端点 POST/DELETE `/kol-pool/{id}/favorite` + GET `/kol-pool/favorites`(vkpi_kol_pool.py:1380/1403/1423 → pool_favorites.py),代码注释自标"四环漏斗第一段;依赖 migration 107,apply 前勿激活";DB 实测 `to_regclass('vkpi_kol_pool_favorites') IS NULL`(表不存在,调用即 500),前端 grep favorite 零命中 → myList 议题的正解不是 localStorage,而是 apply migration 107 + 前端接线。

---

## 一、架构解剖图

**前端数据主干(单挂载点)**:V615ReplicaApp(990 行 shell)→ useV615Runtime(useV615Runtime.ts:61)拉 getKolPoolWorkspace(limit=1200, sortBy=fit),失败 fallback listKolPool 分页 500×4 cap2000(useV615Runtime.ts:41-59/114-115)→ toV615KolPoolRows 行映射(kolPoolRuntime.ts:138-228,**唯一字段合同层**)→ props 注入 KOLPoolPage(KOLPoolPage.tsx:45)。SWR 缓存:IndexedDB "vkpi-v615-runtime-cache"+localStorage 镜像(resourceCache.ts:3/52),键 v615.kol_pool.rows.v1 / v615.dashboard.bundle.v1。KOLPoolPage 之下 KPIBar/MarketCoverageCard/FilterBar/KOLTable/KolPoolAllModal/ContactModal 全纯 props 零 fetch;自带网络仅二:SmartKolInputPanel(smartKolSearch+2.5s 轮询+history+advance-job,SmartKolInputPanel.tsx:4-15)与 KOLDetailDrawer(父层拉 detail-bundle 失败 fallback getKolPoolItem,KOLPoolPage.tsx:114-122;drawer 内 bundle 未 ready 自拉 dims11+llm,KOLDetailDrawer.tsx:549-572)。TaskProgressBoard 挂 App 侧边栏(V615ReplicaApp.tsx:779)轮询 /task-queue/compact(2.5s+visibility 暂停,TaskProgressBoard.tsx:199-232)。跨组件总线:localStorage "vkpi:pendingKolSearchSessionId"+CustomEvent "vkpi:open-kol-search-session"(TaskProgressBoard.tsx:108-109 写→V615ReplicaApp.tsx:104→SmartKolInputPanel.tsx:620-630 消费)——三处裸字符串。

**后端分层**:vkpi_kol_pool.py(1376 行、44 端点)import 13 kol 域模块+sync/tasks/intelligence/evidence/audit/access(:27-48)。读:pool.list_pool/workspace/summary/get_item/detail_bundle(detail_bundle 聚合 dims11+llm+analysis_cache 纯只读,pool.py:616-700)。写:import_items/enrich_item/batch_enrich,fit 唯一写点 pool.py:838(rule_v0 :811),daily_sync.py:14 复用同写点。**胖 router**:smart_kol_search 编排全在端点函数(vkpi_kol_pool.py:447-560,URL→url_deep_crawl / text→planner→recall→sessions→discovery,quota 15+15 硬编码);_attach_*_session(:101-142)为 router 私有不可复用。url_deep_crawl.py(2330 行)跨模块 import 私有函数 `_enqueue_final_v1_video_analysis`(url_deep_crawl.py:35)且直接入队 dossier(:1867)。sync 域:refresh_tier(:309/428/376)、daily_sync(:532/:266)、apify_batch_refresh/cron/guard/sentinel;kol 域另有 15 个下游消费文件。

**状态管理**:runtime 返回 currentUser/notifications/reminders/kolPoolRows/loading/error/dashboardRuntime(useV615Runtime.ts:209-220);localStorage 全清单:vkpi-dashboard-state-v1(storageKey.ts:4)、vkpi:pendingKolSearchSessionId(两文件重复定义)、v615.kol_pool.rows.v1/dashboard.bundle.v1;KOLPoolPage 17 个局部 useState;**myList 纯内存 Set(KOLPoolPage.tsx:57)零持久化**——后端归宿已建(pool_favorites)只差 migration 107+接线。

**任务系统双轨**:A 轨 apify_jobs 表 DB 轮询(FOR UPDATE SKIP LOCKED,apify_jobs_worker.py:2965-2996,有 retry/advisory lock/LLM 并发槽);B 轨 in-process job_queue(main.py:441,/refresh→_maybe_enqueue_refresh router:143-216→enqueue.py:327→job_execution_ledger,**重启即丢 in-flight**)。job_type 六类:session_advance、smart_search_profile_advance(1 done/3 failed)、account_dossier_extract(128 done)、project_contract_extract、project_retrospective_aggregate、default 'video'(434 done/297 failed/15 blocked)。可见性唯一桥:queue_view 双源 UNION(queue_view.py:264-275/338-363)→/task-queue/compact→三泳道;worker 完成 _sync_search_session_job 回写 session 闭环。**三点同步陷阱**:新 job_type 必须同时改 worker if 链(apify_jobs_worker.py:3001-3013)+queue_view 子串推断(:106-167)+前端硬编码三泳道(TaskProgressBoard.tsx:236-244),漏一处即落 default 'video' 被 block 或归错泳道。

**改 X 必动 Y 速查**:① 后端 sync_status 枚举/fit 阈值 → kolPoolRuntime.ts:149-153 candidate_kind 推导+KPIBar/FilterBar/Drawer/kindCounts(KOLPoolPage.tsx:172-181)整体漂移无报错;② 后端列名/detail_bundle 形状 → kolPoolRuntime.ts:138-228 全段(列表/详情/邮件信号三处共用,调用点 useV615Runtime.ts:118+KOLPoolPage.tsx:115/121);③ video_analysis_enqueue 私有函数签名 → url_deep_crawl URL 深爬链静默断(url_deep_crawl.py:35);④ 任务可见性字段 → 两轨同查(queue_view.py:264-363);⑤ 跳转信物字符串 → TaskProgressBoard.tsx:8/SmartKolInputPanel.tsx:21/V615ReplicaApp.tsx:104 三处同改。

---

## 二、血缘大表(UI←runtime←API←DB,DB 实测 1123 行)

| 信息位 | UI←runtime←API←DB 链路 | 写入者 | 覆盖率 | 判定 |
|---|---|---|---|---|
| V6 Fit | V6FitBar+Drawer:652/1018 ←kolPoolRuntime.ts:148,206 ←viltrox_fit_score(pool.py:267/605) | **唯一写点 pool.py:838**(仅手动 enrich) | **11/1123=1.0%**(SQL 复核) | 饿死A;排序键退化 updated_at(pool_common.py:577) |
| Real ER 去水 | Drawer:791-802 ←:146(real_engagement_rate 键 0%→fallback engagement_rate),:196-198(er_calibration 恒0) | enrich pool.py:804/857+import :189/211 | 主值 53.2%;去水值 0% | **全池假去水文案** |
| Audience/HHI | Drawer:804-818 ←:201 恒 null;hhi 从不赋值 | 无 | 0% | 死区 |
| Loyalty Depth | Drawer:586,821-832 ←:208(**=fit 别名**);loyalty_signals 无赋值 | 无 | 同 fit 1%/信号 0% | 假数据;Why 速读(:729-730)连带空 |
| Trend(卡/列/区块) | KPIBar:37+KOLTable:125+Drawer:833-846,1062-1075 ←:156,204-205 | 无 | 0% | 死区,TrendDot 恒灰 |
| 11 维雷达 | Drawer:143-173,850-895 ←dims11(pool.py:629/router:1043)←vkpi_kol_profile_deep.dimensions_11_json | seed 脚本:92+backfill eleven_dimensions.py:564→623(计算 :311-450 纯规则) | **1023/1123=91.1%** | 最满区块但 4 维退化:Fit 维 60% 空、Audience 维写死 82(Drawer:155)、Risk 恒满(competitor_relation 0 行)、Brand 恒 0 |
| V6 公式/Why 速读 | Drawer:726-786,1018-1060 ←v6_breakdown(仅详情 pool.py:611←:81-143) | 读侧投影,9 槽乘数硬编码 1.0(:119-131),真分项 components(:133)UI 不读 | fit 非空才渲染=1% | 假公式视觉噪音 |
| 设备&升级 | KOLTable:131-138+Drawer:940-988 ←:173-177,220-226 兜底"待接入/待评估" | 无 | ≈0.5% | 兜底字符串恒 truthy→100% 渲染 100% 占位 |
| 地理 Reach | Drawer:990-1016 ←:144,202-203(单国 share=100% 合成)+:155(avg_views 假 reach) | country: legacy_kol_commit.py:354+导入 | country 87.8%/真分布 0% | 伪分布 |
| 推荐产品线 | Drawer:1088-1098 ←:157-160,212 ←recommended_product_lines_json | **全后端零写点**(仅 sku_fit.py:92 等 SELECT) | 0%(SQL '[]' 100%) | 饿死A |
| 风险点 | Drawer:1100-1111 ←:161-164,213 ←potential_concerns_json | legacy_kol_commit.py:360 唯一 | 50.9% 有值 | 4 种机器枚举词(contact_missing 347/no_coop 300/missing_profile 36/watchlist 7) |
| 品牌合作/友商/已用Viltrox | Drawer:1113-1134+KOLTable:76+Drawer:672-680 ←:165-172,214 ←brand_collaborations_json | 零写点 | **0/1123**(SQL 复核) | 饿死A;徽章永不出现;11 维 Brand 连带恒 0(eleven_dimensions.py:379-382) |
| 联系方式 | Drawer:905-914,:1154 ←:191/:180 ←email/other_contacts/profile_url | email 写点存在(pool.py:204/legacy:355)从未灌入 | **email 0/1123**;profile_url 96.3% | 邀请按钮恒退化"添加联系方式";Comm 维仅 0.25 置信 |
| 代表作+视频深析 | Drawer:927-939 ←:103-129,178-179 ←get_item pool.py:612←:511-602(JOIN media_cache) ←vkpi_kol_video_evidence | video_evidence.py:191+workflow_evidence.py:472 | 381/1123=33.9%(1068 条) | 覆盖型;列表 payload 不含 |
| LLM 深度判断 | Drawer:463-526 ←detail-bundle/router:1069 ←vkpi_kol_llm_deep_analysis_results | final_v1_extract.py:392+account_dossier_extract.py:367 | 205/1123=18.3%(615 条) | 覆盖型+**憋死:只露 primary 1 条** |
| 列表杂项 | followers 84.2%;real_followers_pct 0%(:147);industry_label 恒兜底(content_style/primary_topic 100% 空,:217);refresh_state←sync_status(synced 959/imported 125/review 34/no_results 5);candidate_kind 前端合成(:149-153,linked 99.9% NULL→全池 new_*);weekly_views_delta 恒 null(:218) | 各注 | — | 混合 |

**饿死字段清单(空置率>80%,按写入者归因)**——A 有列无数据:①viltrox_fit_score/_reason 99.0%(写点仅 enrich 触发 11 次)②email+other_contacts_json 100%(写点存在未灌)③recommended_product_lines_json 100%(零写点)④brand_collaborations_json 100%(零写点)⑤content_style/primary_topic 100%(无写点)⑥linked_main_kol_id 99.9%(pool_main_linking.py 写点存在仅 1 行)。B 无列无 API 纯 UI 期待(100% 前端合成):real_engagement_rate、real_followers_pct、er_calibration、trend_score/resonance/hits、audience_type、hhi、loyalty_signals、device_primary/camera_body、lenses、upgrade_window、weekly_views_delta、source_query、discovered_at、validation_score、industry_tier、geo_match。C 卫星表覆盖型:llm_deep 81.7% 缺、competitor_relation 100% 缺(0 行→Risk 恒满)、video_evidence 66.1% 缺。非饿死参照:dims11 91.1%、display_name 99.3%、profile_url 96.3%、country 87.8%、followers 84.2%。

---

## 三、API 台账(44 端点修正版,vkpi_kol_pool.py)

零鉴权裸奔 0(全挂 require_tab);**读权做写/花钱 6 处**;完全孤儿 10+3;wrapper 孤儿 9。格式:#行号 路径 [scope] → 前端消费 | 状态。

| # | 端点(行号) | scope | 前端 | 状态 |
|---|---|---|---|---|
| 1 | GET /kol-pool(:216) | read(:243-252 refresh_if_stale 可入队!) | listKolPool→useV615Runtime.ts:46 等 | 在用·降级裸奔 |
| 2 | GET /kol-pool/summary(:258) | read | getKolPoolSummary→dashboard 3 处 | 在用 |
| 3 | GET /kol-pool/workspace(:266) | read | getKolPoolWorkspace→useV615Runtime.ts:55 首屏唯一源 | 在用 |
| 4 | POST /kol-search-sessions(:292) | **read 却写库**(:295) | 零调用 | 孤儿+读权写 |
| 5 | GET /kol-search-sessions(:311) | read | 零调用(被 #6 取代) | 孤儿 |
| 6 | GET /kol-search-history(:325) | read | listKolSearchHistory→SmartKolInputPanel.tsx:541 | 在用 |
| 7 | GET /kol-search-sessions/{id}(:346) | read | getKolSearchSession→轮询 :596/640 | 在用 |
| 8 | POST …/items/{iid}/profile-crawl(:361) | write | 零调用 | 孤儿 |
| 9 | POST …/advance(:384) | write | 零调用(同步版被 #10 取代) | 孤儿 |
| 10 | POST …/advance-job(:405) | write | 仅服务端内部复用(:664) | 前端孤儿 |
| 11 | POST …/advance-job/cancel(:426) | write | 零调用——看板无取消按钮 | 孤儿(delta 假按钮镜像) |
| 12 | POST /kol-smart-search(:447) | **read 却 LLM+embedding 花钱**(:450,489-491) | smartKolSearch→SmartKolInputPanel.tsx:693 | 在用·降级裸奔 |
| 13 | POST /kol-smart-search/profile-advance-job(:561) | write | →SmartKolInputPanel.tsx:755 | 在用 |
| 14 | GET /kol-recall(:701) | read 但 create_session 写会话(:735-752) | wrapper(kolPool-api.ts:642)组件零调用 | wrapper 孤儿+读权写 |
| 15 | POST /kol-url-deep-crawl(:762) | **read 却 execute=true 真抓取**(:765) | deepCrawlKolUrl→SmartKolInputPanel.tsx:727 生产在用 | 在用·降级裸奔 |
| 16 | GET /kol-pool/available(:796) | read(唯一显式 ScopeDenied→403 :813-816) | projects-api.ts:274 经 main.py:586-587 前缀重写 | 在用·别名 |
| 17 | GET /kol-pool/competitors/dashboard(:819) | read | →dashboard 2 处 | 在用 |
| 18 | POST /kol-pool/batch-enrich(:836) | write+audit | →DiscoverPageLayout.tsx:254(v615 主页未接) | 在用 |
| 19 | GET /kol-pool/{id}(:869) | read 但 refresh_if_stale 默认 True(:873) | getKolPoolItem→KOLPoolPage.tsx:120(显式 false)等 | 在用·降级裸奔 |
| 20 | GET …/detail-bundle(:893) | read | →KOLPoolPage.tsx:114 抽屉主源 | 在用 |
| 21 | GET …/account-dossier(:908) | read | 零 wrapper 零调用 | 孤儿 |
| 22 | POST …/account-dossier-extract-job(:929) | write | 零调用(与 #21 成对闲置) | 孤儿 |
| 23 | POST …/refresh(:948) | write | wrapper(kolPool-api.ts:764)零调用——无手动刷新入口 | wrapper 孤儿(delta 新鲜度镜像) |
| 24 | POST …/enqueue-video-analysis(:970) | write | →KOLDetailDrawer.tsx:606 | 在用 |
| 25 | POST /kol-pool/enqueue-video-analysis-batch(:994) | write | 零调用 | 孤儿 |
| 26 | GET …/main-candidates(:1014) | read | wrapper(:917)零调用(被 #38 取代) | wrapper 孤儿 |
| 27 | GET …/competitors(:1027) | read | →DiscoverPage.tsx:253 | 在用 |
| 28 | GET …/dimensions11(:1043) | read | →KOLDetailDrawer.tsx:554 等 | 在用 |
| 29 | GET …/llm-deep-analysis(:1069) | read | →KOLDetailDrawer.tsx:555 | 在用 |
| 30 | GET /task-queue(:1080) | read | wrapper(tasks-api.ts:145)零调用 | wrapper 孤儿 |
| 31 | GET /task-queue/compact(:1096) | read | →TaskProgressBoard.tsx:189(2.5s) | 在用 |
| 32 | GET …/intelligence-card(:1110) | read | →KolPoolV2/Discover/NaturalSearch 3 处 | 在用 |
| 33 | GET …/evidence-summary(:1127) | read | →KolPoolV2Page.tsx:267 | 在用 |
| 34 | GET …/ai-brief(:1148) | read | wrapper(:793)零调用 | wrapper 孤儿 |
| 35 | GET …/gemini-preflight(:1169) | read | wrapper(:802)全前端零引用 | wrapper 孤儿 |
| 36 | GET …/gemini-go-no-go(:1188) | read | wrapper(:811)零引用 | wrapper 孤儿 |
| 37 | GET /kol-pool-dimensions11/preview(:1205) | read | 零调用(调试残留) | 孤儿 |
| 38 | POST …/promote(:1216) | write+audit | →KolPoolV2/DiscoverPage | 在用 |
| 39 | POST …/enrich(:1245) | write+audit(max_posts 钳 1-50 :1261) | →DiscoverPageLayout.tsx:253 | 在用 |
| 40 | POST /kol-pool/import(:1273) | write+audit 但 **firewall_check 三参全空**(:1274-1279) | wrapper(:880)零调用 | wrapper 孤儿+防火墙空转 |
| 41 | POST …/link(:1322) | write+audit 但 **路由层裸 SQL UPDATE**(:1366-1370) | wrapper(:906)零调用(被 promote 取代) | wrapper 孤儿 |
| 42 | **POST …/favorite(:1380)** | write+audit | 前端零引用 | **新登记:表缺失(migration 107 未 apply)调用即 500** |
| 43 | **DELETE …/favorite(:1403)** | write+audit | 同上 | 同上 |
| 44 | **GET /kol-pool/favorites(:1423)** | read | 同上 | 同上;myList 正解归宿 |

**发现 A** 读权写/花钱 6 处(:295/:450/:765/:716+737/:243-252/:873),其中 smart-search 与 url-deep-crawl(execute)是真实花钱生产路径。**发现 B** 完全孤儿 10 个(:292/311/361/384/405/426/908/929/994/1205)+收藏 3 个待激活。**发现 C** wrapper 孤儿 9 个(kolPool-api.ts:642/764/793/802/811/880/906/917+tasks-api.ts:145),仅 barrel 再导出。**发现 D** 反向孤儿 0;两处别名:analysis-cache 实由 vkpi_projects.py:26 提供(KOLVideoAnalysisPanel.tsx:326-327 消费)、/api/marketing 前缀经 main.py:586-587 重写。

---

## 四、金字塔结论(批 6 砍列/重排依据)

**根因一句话**:列表 API 只选 38 列(pool_common.py:19-58,无 raw_platform_data/llm 字段),而 v6.15 视觉稿假设的 trend/设备/真实%/品牌/受众六组键在全池 raw_platform_data 中 **0/1123** → 列表 14 信息位 5 个恒空、Drawer 16 区块 5 个恒占位、KPIBar 6 卡 3 卡死;真资产(雷达 91%、llm 615 条/205 人、evidence 1068 条/381 人)被压在 Drawer 中下部且只露 1/N。

1. **砍列**:Trend 列(KOLTable.tsx:124-129)、设备列(:131-138)、真实%二行(:93-95)、er_calibration(kolPoolRuntime.ts:198 恒 0 假校准)、AudienceTypeChip 五个 0% 信息位;保留 KOL·平台 100%/粉丝 84%/Real ER 主值 53%/Geo 88%;weekly_views_delta 死代码顺手清。
2. **替补升位**:has_video_evidence/video_evidence_count 两现成表列加进 KOL_POOL_LIST_COLUMNS + llm 覆盖 EXISTS 子查询 → 列表一眼辨"已深析 205/有 evidence 381";同一条水管自动复活 KolPoolAllModal.tsx:33 的"已分析"chip(其判断的 4 个键现在数据流中根本不存在,恒计 0)。
3. **排序真相**:默认 sortBy='v6_fit'(KOLPoolPage.tsx:50)对 1112 行并列 0(:195)= DB 返回序;6 项排序菜单中 trend 恒 null、loyalty/upgrade 是 fit 别名,实际可用仅 fit(1%)/real_er(53%)/followers(84%);llm_v6_fit(609 条/205 人)可作"LLM 参考分"独立列,守红线不回写。
4. **Drawer 密度梯队**:T1>80%(雷达 91%/主页 96%/Geo 88% 伪分布/Bio 82%)→T2 30-55%(Real ER 53/风险点 51/代表作 34)→T3 18%(LLM 深析/视频面板)→T4 1%(V6 头数/Why/公式/适配判断)→T5 0%(设备/品牌史/产品线/Trend 命中/HHI+Loyalty 卡)。重排:雷达+LLM 上移至 Bio 后;T5 守红线不删但合并统一"待接入"折叠态;公式降为雷达展开项。
5. **深析憋死三连**:①615 条 llm 结果 UI 只读 primary_result(KOLDetailDrawer.tsx:464-465),items+summary(含模型分歧离散度)整体丢弃,单 KOL 最多 18 条只见 1 条——数据已在 detail_bundle,加"共 N 条"展开零后端改动;②列表 205 已深析零标识→重复入队风险(防线仅 Drawer 内 :611-612);③detail_bundle video_limit 默认 3(pool.py:612/623),82 个重点 KOL 的第 4+ 条 evidence(共 624 条)及挂载 final_v1 缓存(413 条 ready)无 UI 入口,API 已支持 ≤10。
6. **黄金位倒挂**:937 行 SmartKolInputPanel(召回 19 次/URL 20 次,两位数用量)常驻 hero 第二位,1123 行主列表默认收起(inlineListOpen=false KOLPoolPage.tsx:60)+modal 截断 160 行(KolPoolAllModal.tsx:78);MarketCoverageCard 与 KPI 第 6 卡同源重复(avg_views 聚合)。重排:表格升默认主体、SmartKolInput 折叠单行命令条、MarketCoverage 并入。
7. **KPIBar/FilterBar**:6 卡中"本周高 Trend"死卡(KPIBar.tsx:37)、"月度估算 Reach"伪指标(:38)、kind 分类塌缩 1 vs 1122(linked 1/1123+fit≥80 11/1123);换卡候选"已深析 205/有 evidence 381/画像完整度 by_data_status(pool.py:394-415 已算好前端未消费)"。FilterBar 8 控件 4 死筛选+3 死排序(audienceType :172-183/trendLevel :194-197/已用Viltrox/友商 checkbox 勾选必 0 行),数据未接前置灰加 tooltip;健康控件仅 country 筛选+搜索框,应前置。

---

## 五、UI 工整化清单(lux)

1. **TaskProgressBoard 整卡三重越轨**(最伤眼):私有色板 #5DCAA5/#7F77DD/#FAC775 系(TaskProgressBoard.tsx:10-41,112-117)、GitHub 风 bg-[#0d1117](:258,周边全是 bg-white/[0.014-0.025])、text-white/N 灰阶 vs 全页 slate(:137-325 十余处);另 px-[9px](:160)、tracking-[0.04em](:167)。换白名单色+slate 阶,纯 class 替换。
2. **字号阶梯越轨 ~47 处**:text-[9.5px]×19(KOLPoolPage.tsx:215-291、KOLDetailDrawer.tsx:500/703、FilterBar.tsx:121/136、SmartKolInputPanel.tsx 6 处、KolPoolAllModal.tsx:175/179 等)+text-[10.5px]×21+极值 7.5/8/8.5/11.5/15/18px 散落(KOLDetailDrawer.tsx:333-347/653-654、KPIBar.tsx:71-72 等)。归并 9/10/11 + 展示数字专档。
3. **同语义双色**:选中/激活在大窗 cyan(KolPoolAllModal.tsx:124/161)vs 表格+FilterBar 紫(mockup.css:225-228、FilterBar.tsx:115-117)。统一紫,cyan 留给 SmartKolInput 入口语义。
4. **V6 Fit 色阶两套阈值**:V6FitBar.tsx:18 四档 vs KOLDetailDrawer.tsx:655/1056 三档(缺 <40 灰)。抽 v6FitColor(score) 共享(只动颜色不碰数据链路红线)。
5. **控件高度 6 档**(24/30/32/34/36/40px:FilterBar.tsx:62/67、KOLDetailDrawer.tsx:1144-1160、SmartKolInputPanel.tsx:429/830/836/902、KolPoolAllModal.tsx:107/122)→ 收敛主 36/次 28。
6. **圆角三档+padding 五种**(KOLPoolPage.tsx:209 rounded-2xl p-3.5 / :245 lg / :305 xl px-4 py-3;FilterBar:43、TaskProgressBoard:258 等)→ 页级 rounded-xl+p-3,模态 rounded-2xl。
7. **模态分叉**:ContactModal.tsx:44-46 手写遮罩(rgba 0.6+blur4+z-60)vs CenterModal.tsx:26-27(black/75+blur-md+9999 portal);关闭 X 13/14 混用(ContactModal:61、Drawer:659 vs 其余 14);面板底色 4 种(#0a1020 主流 vs #0b1324/#070b14/#0d1117)。统一 CenterModal+X14+#0a1020。
8. **标题/小标字阶**:面板标题 12/13/14px 三档+text-sm 异写法(KOLPoolPage:214、Drawer:646、KolPoolAllModal:92、ContactModal:57、TaskProgressBoard:263、SmartKolInputPanel:797);抽屉小标三种(标准 10px slate-500 ×10 处 vs 9px slate-400 ×4 vs 8px)。统一 13px 标题/10px 小标。
9. **SmartKolInputPanel 边框透明度 8 档**(cyan /10/12/15/18/20/22/25/45,:271-:902)→ 三档语义:静 /15、hover /25、focus /45。
10. **内联 hex ~60 处无语义色层**(KOLTable.tsx:78-144、KOLDetailDrawer 20+ 处、FilterBar:36-213、KPIBar:33-38;色值白名单内但阈值→色映射 5 个文件各写一遍)→ lib/colors.ts 语义常量+阈值函数,量大但机械。
11. **lux 基线三缺口实证**:Inter 字体栈第三位且 webfont 全仓未加载(mockup.css:4,grep 0 hits,macOS 实渲 SF Pro);AnimatedNumber.tsx 存在 0 引用(KPIBar.tsx:71 静态);bg-clip-text 渐变数字 0 处;补:bespoke 阴影两套(KOLPoolPage:209 vs TaskProgressBoard:258)、hero 渐变 rgba 直写 class(KOLPoolPage:209 全切面唯一)。
12. **徽章 pill 几何三种并存**(KOLPoolPage:215/222-226/279-286+SmartKolInputPanel:798/807-810+Drawer:673/677)→ 两档:状态徽章 rounded-full px-2 py-0.5 text-[10px] / 内联 tag rounded px-1.5 py-0.5 text-[9px]。

---

## 六、行动项总排序(价值×(6−工作量),60 项)

见结构化 ranked_items 同序。Top10 速览:①V6 Fit 1% 覆盖根治(25)②llm 615 条展开(20)③深析徽章入列表 payload(20)④砍 5 个 0% 列(20)⑤饿死清单落决策(20)⑥TaskProgressBoard 色板归位(20)⑦假按钮诚实化(delta,20)⑧Loyalty 卡=fit 本体(20)⑨⑩smart-search/url-deep-crawl 升 write(20×2)。段分布:第〇小修 6 项/第一漏斗 19 项/第二体验 24 项/第三发现 2 项/Codex-E类 9 项。


---
# 全项重排(价值×(6−工作量) 降序)

| # | score | v | e | 段 | 项 | 证据 |
|---|---|---|---|---|---|---|
| 1 | 25 | 5 | 1 | 第〇小修 | V6 Fit 99.0% NULL:默认排序与三处 UI 全退化——批量 enrich 补分或默认排序降级 | pool.py:838 唯一写点仅手动 enrich 触发;SQL 复核 11/1123;kolPoolRuntime.ts:148,206-209 三处共用;pool_commo |
| 2 | 20 | 5 | 2 | 第二体验 | llm_deep 615 条只露 primary 1 条——面板尾部加'共 N 条'展开(零后端改动) | KOLDetailDrawer.tsx:464-465 丢弃 items+summary;llm_deep_analysis.py:105-165 已返回最多 50 条;SQL 6 |
| 3 | 20 | 5 | 2 | 第二体验 | 替补升位:has_video_evidence/video_evidence_count+llm 计数加进列表 payload | pool_common.py:19-58 不含两现成表列;SQL evidence 381/llm 205 覆盖;同水管复活'已分析'chip |
| 4 | 20 | 5 | 2 | 第二体验 | 砍列:Trend/设备/真实%/er_calibration/AudienceChip 五个 0% 信息位(Codex 可执行) | KOLTable.tsx:93-95,124-138;kolPoolRuntime.ts:147,156,198,205,221,225;SQL 六组 raw 键 0/1123;w |
| 5 | 20 | 5 | 2 | 第二体验 | 饿死字段清单落决策:A 类 6 列补管道或撤 UI、B 类 18 个期待字段冻结、C 类 3 卫星表扩面 | SQL 汇总:fit 99%/email 100%/rec_lines 100%/brand 100%/content_style 100%/linked 99.9%;raw 键  |
| 6 | 20 | 5 | 2 | 第一漏斗 | TaskProgressBoard 整卡色板/底色/灰阶三重越轨归位(纯 class 替换) | TaskProgressBoard.tsx:10-41,112-117 私有色板;:258 bg-[#0d1117];:137-325 text-white/N vs 全页 sla |
| 7 | 20 | 4 | 1 | 第一漏斗 | 假按钮诚实化(入主表·待接入等,delta 已列,只入表) | docs/KOL-Pool-delta-report-20260611.md;KOLDetailDrawer.tsx:689-715 新发现操作条因 linked 99.9% NU |
| 8 | 20 | 4 | 1 | 第一漏斗 | Loyalty Depth 卡显示的是 V6 Fit 本体,loyalty_signals 恒空——撤卡或换真源 | kolPoolRuntime.ts:208 loyalty_score=fit 别名;KOLDetailDrawer.tsx:586,821-832;Why 速读 :729-730 |
| 9 | 20 | 4 | 1 | 第一漏斗 | POST /kol-smart-search 读权花钱(LLM+embedding)升 write | vkpi_kol_pool.py:450 scope=read;:489-491 LLM 规划+OpenAI embedding;:516-524 可触发真实平台发现 |
| 10 | 20 | 4 | 1 | 第一漏斗 | POST /kol-url-deep-crawl execute=true 读权真抓取升 write(生产路径在用) | vkpi_kol_pool.py:765 scope=read;SmartKolInputPanel.tsx:727 正以 execute=true 调用 |
| 11 | 20 | 4 | 1 | 第一漏斗 | 选中/激活态 cyan vs purple 双色统一为紫 | KolPoolAllModal.tsx:124,161 cyan vs mockup.css:225-228 紫 + FilterBar.tsx:115-117 紫 |
| 12 | 16 | 4 | 2 | 第一漏斗 | candidate_kind 前端推导:fit≥80 阈值+sync_status 枚举硬编码收敛(后端枚举改动即整体漂移) | kolPoolRuntime.ts:149-153;消费方 KPIBar/FilterBar/Drawer/KOLPoolPage.tsx:172-181;DB 真值 synced |
| 13 | 16 | 4 | 2 | Codex-E类 | job_type 三点同步陷阱收敛为单一 JOB_TYPES 注册表+handler dict | apify_jobs_worker.py:3001-3013 裸字符串 if 链;queue_view.py:106-167 子串 haystack;TaskProgressBoa |
| 14 | 16 | 4 | 2 | 第一漏斗 | 其余 4 处读权写收敛(POST sessions/kol-recall create_session/两个 GET refresh 入队) | vkpi_kol_pool.py:295、716+737、243-252、873(refresh_if_stale 默认 True) |
| 15 | 16 | 4 | 2 | 第一漏斗 | Real ER 假去水文案:real_engagement_rate 键全池 0%,er_calibration 恒 0 | kolPoolRuntime.ts:146,196-198;KOLDetailDrawer.tsx:791-802;SQL raw NOT LIKE '%real_engageme |
| 16 | 16 | 4 | 2 | 第一漏斗 | V6 公式 Breakdown 乘数 9 槽硬编码 1.0——读 components 真分项或撤公式区 | pool.py:119-143 写死 1.0/competitor_decay 0.0,真分项 :133 UI 不读;KOLDetailDrawer.tsx:1018-1060 十 |
| 17 | 16 | 4 | 2 | 第一漏斗 | 设备&升级区块恒占位词('待接入'兜底恒 truthy → 100% 渲染 100% 占位) | kolPoolRuntime.ts:220-226 兜底;KOLDetailDrawer.tsx:940-988 以兜底值为渲染条件;SQL device 键 99.5% 缺;KO |
| 18 | 16 | 4 | 2 | 第二体验 | KPIBar 3 死/伪卡换真数据卡(已深析 205/evidence 381/画像完整度) | KPIBar.tsx:37 Trend 死卡/:38 avg_views 伪 reach;by_data_status 后端已算好前端未消费(pool.py:394-415);ki |
| 19 | 16 | 4 | 2 | 第二体验 | '已分析'chip 复活:列表 205 个已深析 KOL 零标识(防重复入队) | KolPoolAllModal.tsx:33 判断的 4 个键在 kolPoolRuntime.ts:184-227 与 pool_common.py:19-58 均不存在恒计 0 |
| 20 | 16 | 4 | 2 | 第一漏斗 | 字号阶梯收敛:9.5px×19+10.5px×21+6 种散档约 47 处(sed 级) | KOLPoolPage.tsx:215-291、KOLDetailDrawer.tsx:333-347/500/703、SmartKolInputPanel.tsx 多处、KPIB |
| 21 | 16 | 4 | 2 | 第二体验 | 圆角三档+卡片 padding 五种收敛(页级 xl+p-3/模态 2xl) | KOLPoolPage.tsx:209/245/305;FilterBar.tsx:43;TaskProgressBoard.tsx:258;ContactModal.tsx:50 |
| 22 | 15 | 5 | 3 | 第二体验 | 黄金位倒挂重排:表格升默认主体、SmartKolInput 折叠单行、MarketCoverage 并入 | KOLPoolPage.tsx:60 inlineListOpen=false 默认收起/:206-307 渲染序;KolPoolAllModal.tsx:78 截断 160;SQ |
| 23 | 15 | 5 | 3 | 第二体验 | Drawer 16 区块按密度 T1-T5 重排:雷达+LLM 上移,T5 五块合并'待接入'折叠(守 13 区块红线) | SQL 密度梯队 91%→0% 见作战图四-4;KOLDetailDrawer.tsx:850-895 雷达/:463-525 LLM/:940-1133 T5 各块行号 |
| 24 | 15 | 5 | 3 | 第二体验 | 11 维雷达 4 个退化维修复(Fit 60% 空/Audience 写死 82/Risk 恒满/Brand 恒 0) | eleven_dimensions.py:311-450 计算源;KOLDetailDrawer.tsx:155 写死 82;vkpi_competitor_relation 0  |
| 25 | 15 | 5 | 3 | 第二体验 | 品牌合作历史零写点:衍生链(徽章/友商/11 维 Brand)全失效——补写点或撤链 | SQL 复核 brand_collaborations_json 非空 0/1123;grep 全后端仅 SELECT;kolPoolRuntime.ts:165-172;KOLD |
| 26 | 15 | 3 | 1 | 第一漏斗 | 抽屉打开三路并发重复请求:effect 加 detailLoading 守卫(1 行级) | KOLPoolPage.tsx:108-116 先置 null 再 await;KOLDetailDrawer.tsx:549-572 只判 bundleRecord.status |
| 27 | 15 | 3 | 1 | 第二体验 | FilterBar 4 死筛选+3 死排序置灰加'数据待接入'tooltip(Codex 可执行) | FilterBar.tsx:172-183 audienceType/:194-197 trendLevel;已用Viltrox/友商 checkbox 勾选必 0 行(SQL 0 |
| 28 | 15 | 3 | 1 | 第一漏斗 | Trend 卡/列/区块零来源死 UI 诚实化(卡恒'—'、TrendDot 恒灰、命中区永不渲染) | kolPoolRuntime.ts:156,204-205;KOLTable.tsx:125;KOLDetailDrawer.tsx:833-846,1062-1075;SQL r |
| 29 | 15 | 3 | 1 | 第一漏斗 | Audience·HHI 卡零来源(audience_type 恒 null、hhi 从不赋值恒'—') | kolPoolRuntime.ts:201;KOLDetailDrawer.tsx:804-818 直接读未赋值字段;SQL raw audience_type 键 0/1123 |
| 30 | 15 | 3 | 1 | 第〇小修 | potential_concerns 4 种机器枚举词人话化(delta 机器词清单关联,只入表) | legacy_kol_commit.py:360 唯一写点;KOLDetailDrawer.tsx:175-184 硬编码翻译;SQL 有值 50.9%:contact_missi |
| 31 | 15 | 3 | 1 | 第〇小修 | 列表剩余兜底字段清理:industry_label 恒'真实 KOL Pool'、industry_tier 恒'—'、真实% 恒'—' | kolPoolRuntime.ts:145,147,216-218;SQL content_style/primary_topic 100% 空、real_followers_pc |
| 32 | 15 | 3 | 1 | 第一漏斗 | V6 Fit 色阶两套阈值抽 v6FitColor(score) 共享函数(只动颜色不碰数据红线) | V6FitBar.tsx:18 四档 vs KOLDetailDrawer.tsx:655,1056 三档缺 <40 灰档 |
| 33 | 15 | 3 | 1 | 第一漏斗 | SmartKolInputPanel 同色边框透明度 8 档收敛 3 档语义(静/15 hover/25 focus/45) | SmartKolInputPanel.tsx:271,301,329,429,792,807,830,836 cyan /10-/45 八档;emerald/violet 同样任意 |
| 34 | 15 | 3 | 1 | Codex-E类 | url_deep_crawl 跨模块 import 私有函数 _enqueue_final_v1_video_analysis 公开包装 | url_deep_crawl.py:35 跨文件引用下划线私有;改名即静默断 URL 深爬视频入队链 |
| 35 | 15 | 3 | 1 | Codex-E类 | loyalty/upgrade=v6_fit 假差异排序项处理(量纲 bug delta 已列,只入表) | kolPoolRuntime.ts:206-209 三字段同源同值;排序菜单 loyalty/upgrade 为假选项;合同层唯一翻译点 useV615Runtime.ts:118 |
| 36 | 15 | 3 | 1 | Codex-E类 | import 端点 firewall_check 三参全空形同虚设——补参或移除注释自欺 | vkpi_kol_pool.py:1274-1279 platform=''/feature_flag=''/require_budget=False,注释自认'暂时不用' |
| 37 | 12 | 4 | 3 | Codex-E类 | 双任务队列双轨语义文档化+可见性改动两轨同查(认知红线) | main.py:441 in-process job_queue(重启丢 in-flight)vs apify_jobs_worker.py:2965-2996 DB 轮询(有 r |
| 38 | 12 | 4 | 3 | 第二体验 | 默认排序键 v6_fit 99% 随机:llm_v6_fit 作'LLM 参考分'独立列(守红线不回写) | KOLPoolPage.tsx:50 默认 sortBy/:195 (b.v6_fit//0) 1112 行并列;SQL llm has_fit 609 条/205 KOL;红线  |
| 39 | 12 | 4 | 3 | 第二体验 | 地理 Reach 单国伪分布(share=100%)+avg_views 假 reach 诚实化 | kolPoolRuntime.ts:155,202-203 前端合成;KOLDetailDrawer.tsx:990-1016 占比条恒单条;真实粉丝地理分布无任何来源 |
| 40 | 12 | 4 | 3 | 第二体验 | 推荐产品线零写点:补 sku_fit 写管道或撤区块 | SQL recommended_product_lines_json '[]' 100%;grep 全后端无 INSERT/UPDATE,仅 sku_fit.py:92/produ |
| 41 | 12 | 4 | 3 | 第二体验 | email 全池 0/1123:联系方式采集链路立项(邀请按钮恒退化) | SQL 复核 email 非空 0、other_contacts_json '[]' 100%;写点 pool.py:204/legacy_kol_commit.py:355 从未 |
| 42 | 12 | 4 | 3 | 第二体验 | 交互控件高度 6 档(24-40px)收敛两档(主 36/次 28) | FilterBar.tsx:62,67;KOLDetailDrawer.tsx:1144-1160;SmartKolInputPanel.tsx:429,830,836,902;K |
| 43 | 12 | 4 | 3 | 第二体验 | lux 基线补全:self-host Inter 提栈首+KPI/V6 大数字接 AnimatedNumber+渐变数字+阴影 token | mockup.css:4 Inter 第三位且全仓无 @font-face(grep 0);AnimatedNumber.tsx 0 引用(KPIBar.tsx:71 静态);bg |
| 44 | 12 | 3 | 2 | 第二体验 | detail_bundle video_limit=3 截断:前端'查看更多'解锁 82 个重点 KOL 的 624 条 evidence | pool.py:612 limit=3/:623 钳 1-10 API 已支持;SQL >3 条 evidence 82 人;vkpi_analysis_cache 413 条 r |
| 45 | 12 | 3 | 2 | 第一漏斗 | myList 接已建收藏三端点:apply migration 107+前端接线(合成期修正:非 localStorage) | KOLPoolPage.tsx:57 纯内存 Set;vkpi_kol_pool.py:1380/1403/1423 C2 三端点已建(注释自标'四环漏斗第一段');DB 实测 v |
| 46 | 12 | 3 | 2 | 第二体验 | TaskProgressBoard 接 advance-job/cancel 取消按钮(孤儿端点与缺操作互为镜像) | vkpi_kol_pool.py:426 端点零调用;delta 报告'假按钮/缺操作'主题后端反向印证 |
| 47 | 12 | 3 | 2 | 第二体验 | 手动刷新入口接 POST /{id}/refresh(wrapper 孤儿,delta 新鲜度清单镜像) | kolPool-api.ts:764 refreshKolPoolItem 有包装零调用;vkpi_kol_pool.py:948;详情页/表格无刷新入口 |
| 48 | 12 | 3 | 2 | 第〇小修 | LLM 深析面板覆盖 18.3%:81.7% 行整块不渲染——加空态引导入队 | SQL 205/1123;KOLDetailDrawer.tsx:463-526 status!=='ready' 直接 return null;写入者 final_v1_extr |
| 49 | 12 | 3 | 2 | 第〇小修 | 代表作覆盖 33.9%:66.1% 行空态/补采策略 | SQL 381/1123(has_video_evidence=false 66.1%);pool.py:511-602 仅详情序列化;列表 payload 不含(pool_com |
| 50 | 12 | 3 | 2 | 第二体验 | 首屏 workspace limit=1200 性能(delta 已列,只入表) | useV615Runtime.ts:41-59,114-115 1200 行全量+fallback 500×4 cap2000;docs/KOL-Pool-delta-report |
| 51 | 12 | 3 | 2 | 第二体验 | 模态系统分叉统一:ContactModal 改走 CenterModal+X 统一 14+面板底色统一 #0a1020 | ContactModal.tsx:44-46,61 vs CenterModal.tsx:26-27;底色越轨 KolPoolAllModal.tsx:89/#0b1324、KOL |
| 52 | 12 | 3 | 2 | 第一漏斗 | 标题 12/13/14px 三档+小标三种统一(13px 标题/10px slate-500 小标) | KOLPoolPage.tsx:214;KOLDetailDrawer.tsx:646,653,767-1115;KolPoolAllModal.tsx:92 text-sm 异写 |
| 53 | 12 | 3 | 2 | 第一漏斗 | 徽章/pill 几何三种并存收敛两档 | KOLPoolPage.tsx:215,222-226,279-286;SmartKolInputPanel.tsx:798,807-810;KOLDetailDrawer.tsx |
| 54 | 10 | 2 | 1 | Codex-E类 | 跨组件跳转信物魔法字符串(localStorage 键+事件名)收敛共享常量 | TaskProgressBoard.tsx:8,108-109 与 SmartKolInputPanel.tsx:21,620-630 重复定义;事件名散落 V615Replica |
| 55 | 10 | 2 | 1 | 第〇小修 | 跨路由别名台账登记(analysis-cache 在 projects 路由/marketing 前缀重写) | kolPool-api.ts:686→vkpi_projects.py:26(KOLVideoAnalysisPanel.tsx:326-327 消费);projects-api. |
| 56 | 9 | 3 | 3 | 第三发现 | smart_kol_search 胖 router 下沉 kol 域 orchestrator(结构性,排后) | vkpi_kol_pool.py:447-560 编排+quota 15+15 业务参数全在端点;:101-142 私有 _attach_* 无法被 worker/cron 复用; |
| 57 | 9 | 3 | 3 | 第二体验 | 完全孤儿端点 10 个下线/补 UI 决策(收藏 3 端点另案待 migration 107) | vkpi_kol_pool.py:292,311,361,384,405,426,908,929,994,1205;account-dossier 对(908/929)整链闲置;d |
| 58 | 8 | 2 | 2 | 第二体验 | wrapper 孤儿 9 个死代码清理 | kolPool-api.ts:642,764,793,802,811,880,906,917+tasks-api.ts:145;仅 barrel 再导出无组件消费 |
| 59 | 8 | 2 | 2 | Codex-E类 | link 端点路由层裸 SQL 下沉 domains 层 | vkpi_kol_pool.py:1366-1370 UPDATE vkpi_kol_pool 直写路由层,违反分层惯例;且 wrapper 孤儿(kolPool-api.ts:9 |
| 60 | 6 | 3 | 4 | 第三发现 | 内联 hex ~60 处建 lib/colors.ts 语义色层(量大机械,消除未来分叉的根) | KOLTable.tsx:78-144;KOLDetailDrawer.tsx 20+ 处;FilterBar.tsx:36-213;KPIBar.tsx:33-38;阈值→色映射 |
