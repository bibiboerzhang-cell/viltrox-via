##### ONE-PAGER
# KOL Pool B2 差量报告(战役计划第〇段验收件)
2026-06-11 · V-KPI · Pool 1123 行 · 深析覆盖 205/1123 · B1 减法(75d6cc84)已验收落地

## TL;DR
主链路(输入→列表→详情→深析入队→任务看板)全真且健康;病灶收敛为:**2 假按钮、6 死区(其中 3 个"待接入"的后端端点+前端 API 包装已齐只差接线)、新鲜度整面"载入即死"、a+ 半落码 / a++ 伪增量、D 类五项无一完整存活**。

## 一、四盲区结论
1. **交互真伪**:12 类真按钮全接真端点(A1 三分支/深析入队/detail-bundle/筛选 chips/看板闭环)。假按钮 2、化妆延迟 2、死区 6(5 处 disabled+title 诚实,1 处无说明)。**关键差量:promote/link/import 三个"待接入"死区,后端 vkpi_kol_pool.py:1216/1322/1273 与前端 kolPool-api.ts:929/906/880 双端已存在,纯缺接线**。
2. **性能**:主表自虚拟化健康(DOM≤34 行,KOLTable.tsx:20-34)。带病:首屏 12 并行 API(9 个 dashboard 接口与 Pool 页无关,useV615Runtime.ts:154-202 不看 activeNav);detail-bundle 走 SELECT *(pool.py:607)拖 raw_platform_data;整页打进 1 个 488KB chunk 且被 modulepreload(index.html:21),无路由级分割;骨架屏全仓 0;增长红线 1123/1200/2000 静默截断无告警(useV615Runtime.ts:55、pool.py:346)。
3. **新鲜度**:列表与 Drawer"载入即死"。KOL_POOL_REFRESH_MS 死常量零使用(useV615Runtime.ts:11);refresh_state 色条枚举零交集恒透明(DB 真值 synced=959/imported=125/needs_human_review=34/no_results=5 vs 前端 fresh/stale/warming/queued,kolPoolRuntime.ts:216+refreshStateInfo.ts:4-9)→34 条人工复核积压不可见;stale 判定恒 false(kolPoolRuntime.ts:149);后端 freshness 整套已落码(refresh_tier.py:446-458)但前端零消费,且 vkpi_kol_refresh_tier 仅 25 行、last_refresh_at 全 NULL;时间戳在行映射层全丢(kolPoolRuntime.ts:138-228);深析无 created_at 角标、入队后按钮死锁"分析中…"(KOLDetailDrawer.tsx:590-597)。**数据实况:last_seen_at 全池停摆于 2026-06-04(整 7 天),77% 行 updated_at 落 7-14 天前**——UI 却标榜"真实 API"。
4. **数据链路**:a+ 半落码、a++ 伪增量(明确答案见第五节)。

## 二、假按钮/死区清单
| # | 类型 | 位置 | 判定 |
|---|---|---|---|
| 1 | 假按钮 | Drawer"复制邮箱" KOLDetailDrawer.tsx:911-914 | 有 title 无 onClick,点击无声失败(全页唯一) |
| 2 | 假交互 | FilterBar 搜索模式三 chips :76-84 | 仅改标签,唯一消费点是文案 KOLPoolPage.tsx:280 |
| 3 | 死区·无说明 | new_promoted"待接入写入" Drawer:709-714 | 无 title;promote 双端已齐(:1216/api.ts:929) |
| 4 | 死区·有说明 | "入主表·待接入" Drawer:1156-1160 | title 已过时:link/main-candidates API 双端已齐(:1322/:1014) |
| 5 | 死区·有说明 | FilterBar"一键导入" :86-90 | import 端点在(:1273);<xl 屏完全不可见 |
| 6 | 死区+死端输入 | ContactModal 发送:155-159/保存:210-214 | 声明属实(pool 无联系人写端点);3 输入框无提交路径关 modal 即丢 |
| 7 | 小死区 | TaskProgressBoard 无 session 行 :127-131 | disabled 且 title:"" |
| 8 | 化妆延迟×2 | FilterBar:22-26(300ms)/ContactModal:34-42(700ms) | 假 spinner,功能真 |
| 9 | 半落码 | "加入本地列表" Drawer:1141-1148 | 内存 Set 刷新即丢,无 localStorage,title 诚实 |

## 三、性能基线数字(批6 锚点)
- chunk-s_fdbVlH.js = **499,532B / 141,780B gzip**(modulepreload);全站 JS 2.06MB;最大 CSS 334,086B
- workspace items = **1,730kB 未压缩**(1123 行 × 均值 1577B;gzip 估 200-300KB)
- raw_platform_data 全表 **63MB,均值 57kB,最大 474kB**(id=3421);抽屉典型 150-250kB,最坏 700kB+
- 首屏 **12 并行 API + 2.5s 轮询(24 次/分钟常驻)**;骨架屏 **0**;增长红线 **1123/1200/2000**
- llm_deep ready 615 条/205 KOL;video final_v1 缓存 413 条均值 27kB;KolPoolAllModal 硬截断 160 行

## 四、机器词清单(11 处用户可见 + 1 健康对照)
1. upgrade_window 原值×3:KOLTable.tsx:138、KOLDetailDrawer.tsx:985、:738
2. Drawer:llm_v6_fit(:492)、conf 0.82(:500)、evidence #N(:601/:605)、final_v1(:612)、validation_score+原始时间串(:704-706)、severity/status 原值(:127/:183)
3. 视频面板:表名 vkpi_analysis_cache(KOLVideoAnalysisPanel.tsx:359)、video_analysis_final_v1 空态(:365/:367/:381)、issue 原值(:137/:141)
4. SmartKolInputPanel:status 直出(:276)、rule_v0(:326/:332)、media_resolve_failed(:414)、成功横幅 kol_pool_id/evidence_id/job_id 串(:448-453)、raw boolean(:450/:928)、queued(:914)、英文计数 chip(:921-924)
5. TaskProgressBoard error_category 半映射(:88-91)
6. KPIBar avg_views/reach 字段名(:37-38);KOLPoolPage.tsx:283 kindFilter 未知值直出
- 健康对照:task.kind 后端已中文映射(queue_view.py:106-139)——可作集中字典化范本

## 五、a+/a++ 明确答案
**a+ = 半落码**。onboarding 链存在但"全量"不可能:断点1 = url_deep_crawl.py:919(候选池在进 onboarding 前已按 max_posts 抓死,默认 3、硬上限 12 见 :2055-2061;前端固定传 3,SmartKolInputPanel.tsx:725-734);断点2 = apify_jobs_worker.py:3001-3013(分发表无任何 account-sync job 类型,final_v1 后唯一派生是本地萃取 account_dossier_extract :1161/:2189)。DB 铁证:新人 5657/5658(posts 122/294)走完流程 evidence=1 条。
**a++ = 假 since(伪增量)**。游标字段 vkpi_kol_pool.last_video_at(date 粒度,453/1123 有值);全部 crawler 签名无 since/after/cursor;provider 整列表重拉后本地按 posted_at>cutoff 截断(url_deep_crawl.py:1447-1482,严格大于→同日多发误跳),content_url 去重(video_evidence.py:232)。DB:kol 3972 run#18 候选 11 条全被本地跳过——配额照烧。daily light refresh 更是零游标覆盖重抓(daily_sync.py:266/273),且不推进 last_video_at,两套机制互不联动。

## 六、D 类生死判(摘要,详见 d_verdicts)
D1 半落码→入队第3(硬前置 email 0/1123;loyalty 量纲 bug 第一段顺手修)| D2 半落码→入队第1(60 行成品 UI 只差后端 v6_breakdown)| D3 未落码→销账删 V615Topbar.tsx(需求并入新鲜度段)| D4 半落码→入队排末 | D6 B1 验收销账+残骸三行(V615ReplicaApp.tsx:767-770)顺手清,真徽章入队第2(unread 数据源现成)。

##### a+ 答案
判定:半落码。执行链有同步式 onboarding(_execute_new_creator_video_flow url_deep_crawl.py:864:建档 :930→本视频 evidence :939→入析 :954→onboarding_body{mode:account_deep, rep_limit:3, history_video_limit:max_posts, materialize:true} :964-991),但'账号全量'结构性不可能。断点1 = url_deep_crawl.py:919:候选池=crawl['videos_items'],在进 onboarding 前已按 max_posts 抓死(默认 3,无 materialize 标志硬上限 12,:2055-2061;_profile_representative_video_metadata :1399-1431 只读 crawl 不补抓;前端固定传 max_posts:3 且从不传 history_video_limit/materialize,SmartKolInputPanel.tsx:725-734)。断点2 = apify_jobs_worker.py:3001-3013:worker 分发表仅 5 种 job+默认视频分析,final_v1 完成后唯一自动派生是本地萃取 account_dossier_extract(:972-1030,调用点 :1161/:2189,模块自述 never calls providers);SQL 证实 apify_jobs 全表不存在任何 account-sync 类 job_type。DB 铁证:新人 kol 5657/5658(posts_count 122/294)流程跑完 evidence_rows=1;run id=19 onboarding candidate_count=2/queued=0/materialized=0。附注:URL 分支后端只 dry-run(vkpi_kol_pool.py:469-476),真执行靠前端二跳 execute=true(:762)——无人点执行连建档都不发生,'自动'语义打折。

##### a++ 答案
判定:假 since,伪增量。游标字段 = vkpi_kol_pool.last_video_at(date 粒度,453/1123 行有值;读 _profile_incremental_state url_deep_crawl.py:1616-1653,写经 _latest_video_date :1973→profile_basics.py:26/38/51 白名单落库)。provider 侧零游标:五平台 crawler 签名仅 max_posts/max_results(youtube_crawler.py:249/292、instagram_crawler.py:164/200、bilibili_crawler.py:118/137、twitch_crawler.py:146/166、reddit_crawler.py:558/570),grep onlyPostsNewerThan/publishedAfter/sinceDate 在 crawler 层零命中。实际机制=重拉最新 N 条→本地 _filter_incremental_profile_videos 按 posted_at>cutoff 截断(url_deep_crawl.py:1447-1482;严格大于+date 粒度→同日多发视频误跳)→evidence 按 content_url 去重(video_evidence.py:232)。DB 实证:kol 3972 两次 execute(run#1 06-04 / run#18 06-10),第二次 incremental 模式候选 11 条 skipped_by_incremental=11、materialized=0——provider/Apify 配额照烧,只省本地 LLM 与写入。daily_incremental_sync 更是零游标:run_kol_pool_light_refresh(daily_sync.py:266)max_posts 夹 1-3(:273),enrich_item(pool.py:700)每次整体覆盖重抓(:741/:743/:764)且不推进 last_video_at——两套'增量'机制互不联动。

##### D 类生死判
- D1a 产品 chip 选择器(读 recommended_product_lines): 【已落码】 ContactModal.tsx:15,86-115 chip+自定义+空态全在;数据通路 kolPoolRuntime.ts:157-160,212 双源归并。DB:recommended_product_lines_json 非空 0/1123,raw_platform_data 含该键仅 10/1123
    归宿: 销账(代码侧无需改);数据补齐并入 D1 入队段(第3段)
- D1b 选品自动重生成主题: 【已落码】 ContactModal.tsx:29-33 切 chip 即 setSubject,:106 自定义逐字符同步;lib/email.ts:10-18 按产品名分支;正文不自动覆盖与 :28 注释设计一致
    归宿: 销账
- D1c AI 写信按钮: 【半落码】 ContactModal.tsx:132-142 按钮在,但 :135 title 自认'本地模板重写: 不调用 LLM / Gemini',:36 setTimeout 700ms 假延迟;全链无任何 LLM 调用点——智能件缺席,交互件已诚实标注
    归宿: 入队第3段随 D1;或产品拍板总册降级为模板方案后销账(二选一)
- D1d 正文按 KOL 信号五分支: 【半落码】 lib/email.ts:27-53 五信号分支代码全在;但 geo_match 恒 undefined(kolPoolRuntime.ts:184-227 输出无该键)、loyalty 错接 v6_fit 量纲 bug(kolPoolRuntime.ts:208,0-100 对阈值 0.85 恒 true)、DB b
    归宿: 入队:量纲一行 bug + geo_match 透传可第一段顺手修;数据补齐随 D1 第3段
- D1 总判:邮件触达流: 【半落码】 UI 四件全在但运行时不可达:DB email 非空 0/1123(全空串)→hasEmail 恒 false(ContactModal.tsx:14)→邮件 tab(:65-74)对全部 1123 行零曝光;发送 :155-159 与保存 :210-214 均 disabled 待接入
    归宿: 入队第3段;硬前置=email 采集(0/1123),采集无解则整段冻结
- D2 'Why V6 Fit = N?' 四 bullet 解释区: 【半落码】 UI 完整成品约 60 行(KOLDetailDrawer.tsx:627-631 读 v6_breakdown||score_breakdown,:725-786 八维定义+top3 偏离+风险条);但 backend/app 全树 grep v6_breakdown 零命中、kolPoolRuntime.ts 固定
    归宿: 入队排第1段:后端 detail 序列化输出 v6_breakdown 或 normalizer 从 viltrox_fit_reason 推导(守红线:fit_score 唯一写点 pool.py:838,解释面只读不写)
- D3 数据新鲜度 pill(TopBar+sync 弹层): 【未落码】 V615Topbar.tsx:18-51 组件在但全仓零 import(实际 header 是 V615ReplicaApp.tsx:801-862 另一套);'2 分钟前'与 6 数据源全硬编码(:26,:34-39);'立即重新同步'按钮无 onClick(:46-48)——孤儿假数据死文件,误挂载即复活假功能
    归宿: 销账:删 V615Topbar.tsx;新鲜度真需求并入盲区3(freshness 契约)修复段,detail.freshness/refresh 通路已有
- D4 lux 视觉基线(Sora/Inter/渐变数字/动效): 【半落码】 Sora 全树 0 命中;Inter 无 @font-face 且 mockup.css:4 栈序排 -apple-system 后(macOS 实渲 SF Pro);渐变数字 bg-clip-text 0 处;AnimatedNumber.tsx:10-19 完整实现但全仓零引用=死件;framer-motion 2
    归宿: 入队排末(第4位):Inter webfont+KPI 接 AnimatedNumber 两小件可顺手提前,渐变数字一个 utility class;不值独立段
- D6 侧栏真数据徽章(B1 删假后现状): 【半落码】 B1 减法干净落地:navItems.ts:9-22 共 14 项 badge 全 null,唯一 :15 events 'New' 静态——与预期完全一致;但 V615ReplicaApp.tsx:767-773 仍留 badge==='49'/'7'/'GEN2' 三行永不命中的配色死条件;'真数据'徽章(告警/新
    归宿: B1 验收销账+残骸三行第一段顺手清;真徽章入队排第2(成本最低、数据源现成)

##### 入队排序建议
第二段消化顺序(仅 D 类入队项,按 ROI/依赖排序):
① D2 Why V6 Fit——ROI 最高:60 行成品 UI 只差后端在 pool item/detail 序列化输出 v6_breakdown(或 normalizer 从 viltrox_fit_reason/score 推导);严守红线 fit_score 唯一写点 pool.py:838,解释面纯只读。
② D6 真数据徽章——成本最低:runtimeNotifications unread 计数(V615ReplicaApp.tsx:846)现成可挂;前置动作:第一段顺手清 :767-770 三行 GEN2/49/7 死条件残骸。
③ D1 邮件流整段——硬前置:email 采集 0/1123,采集方案未定前整段冻结;可剥离先行项(随第一段顺手修):loyalty 量纲 bug(kolPoolRuntime.ts:208)、geo_match 透传;D1c AI 写信 vs 模板降级需产品拍板后再动。
④ D4 lux 打磨——排末;两小件(Inter webfont+栈序、KPI 数字接 AnimatedNumber)可在任意段顺手捎带,渐变数字一个 utility class,不值独立排期。
不入队:D3(销账删 V615Topbar.tsx 孤儿假数据文件),其新鲜度诉求并入盲区3 freshness 契约修复段(refresh_state 枚举映射+detail_bundle 内联 freshness+last_refresh_at 回填)统一消化,避免两处各修一半。
