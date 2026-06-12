# KOL Pool 需求总册 · 2026-06-11 重编
(来源:5月原型会话 / 6月施工计划 / 审计+四环survey / 裁决记录)

══ A · 已建成并验真(成品资产,只许接线不许重做)══
 A1 统一智能输入框:URL/ID/文字自动分流——视频URL→已在库"只析
    此视频"/新人"建档并析";账号URL→execute auto+增量since;
    文字→语义召回(TikTok 分支按裁决=显示风险提示,不灰掉)
 A2 Pool 总数大窗:1123 全量列表+搜索+平台/已分析筛选 chips+
    窗内可发起分析
 A3 13 区块详情 Drawer【成品铁律:1:1 保留】:头部+bio+顶部4指标
    (Real ER/HHI/Loyalty/Trend)+11维雷达+联系&代表作+视频深析+
    设备&升级机会+地理分布·Reach+V6 Fit 10项breakdown+Viltrox
    适配+推荐产品线+风险点+合作历史
 A4 搜索会话 hub(ledger tier):session 表+orchestrator,
    入队→泳道→轮询契约
 A5 智能输入的泳道联动(终态回填/partial/ETA)

══ B · 进行中(本周战役)══
 B1 解冻减法仪式:删 V615Sidebar 死文件+GEN2 假徽章收敛+休眠监听
    (⚠️ GEN2 假徽章删=对;原型 Discover/Signals/Agents 真数据
    徽章是想要项,删假≠放弃真,真徽章挂 D6)
 B2 差量诊断四盲区:交互健康/性能基线/数据新鲜度/机器词
    (+本令 a+/a++ 两问)
 B3 四环漏斗 C1-C10【已批】:107 收藏表→三端点→Pool/Drawer
    收藏入口→My KOL 改读收藏→backfill 721(C6 铁前置)→
    Projects 选择器切 My KOL+修活"已关注"死筛选→写入侧防绕过→
    取消收藏在役软禁止(409+清单+force)→Dashboard 四环聚合
    (顺修 stage 双拼写)

══ C · 已批排队(P5 后裁决在档)══
 C1 My KOL 优化前五:收藏持久化(=B3)/漏斗阶段真实化+measured
    独立环/团队矩阵假数据剥离(可独立先做)/viltroxOnly 开关
    语义/列表性能+硬编码默认选中
 C2 批6 UI(Pool 切面):层级重排(智能输入+最近任务上黄金位,
    六统计卡折叠摘要条)/默认列砍至5列其余进 Drawer/骨架屏/
    机器词白名单/英雄区不均匀化
 C3 实时规范:数据新鲜度时间戳 pill(TopBar+sync 弹层)/
    载入即死字段接轮询或时间戳
 C4 连通:Pool↔Projects 双向跳转(项目里 KOL→Drawer/Drawer
    合作历史→项目详情)/任务条目→所属 KOL

══ D · 原型遗产·状态待核(差量诊断顺手查,核完销账或入队)══
 D1 ContactModal 邮件流 V6.15.4:产品 chip 选择器(读
    recommended_product_lines)/选品自动重生成主题/AI 写信按钮
    重写主题+正文/正文按 KOL 信号分支(合作史/竞品/loyalty/
    geo/trend)/规则:切品只动主题,正文重写须显式点 AI
 D2 "Why V6 Fit = N?"四 bullet 解释区(纯前端规则,读 v6_breakdown)
 D3 数据新鲜度 pill(=C3 前端件)
 D4 lux 视觉基线全量对齐:Sora/Inter/渐变数字/微动效/入场动画
 D5 原型 7 模式按钮=纯演示,不迁移
 D6 侧栏真数据徽章(Discover/Signals/Agents)——等数据侧支撑

══ E · 数据侧依赖(Pool 的血,Codex 回归单)══
 E1 深析覆盖 148/1122 → 600+(铺量 wave)
 E2 V6 Fit 10 因子列不落库——"—"是诚实空值,breakdown 全亮需
    因子持久化(评分语义冻结,只读不改算法)
 E3 llm_v6_fit 改名/贴"LLM 分·未定标"标签(P0 旧账,闸C)
 E4 auto-fanout 批量分析:闸住,待小批成本验证+真实并发数确认

══ F · 双轨发现(新需求 2026-06-11,设计先行)══
 F1 对话式分流:问句输入("我们有个xxx镜头想找KOL")→识别找人
    意图→追问平台(TK/IG/FB/YT 多选 chips)→带平台约束召回。
    单发输入框升级为可追问
 F2 库内召回 15:既有语义召回+平台过滤,按 V6 Fit 排序
 F3 全网发现 15【闸A+闸C,设计稿先行零施工】:Gemini Google
    Search grounding 泛行业搜索→候选卡(名/平台/粉丝/链接/
    一句话理由)→落"外部候选暂存区",绝不直接入 kol_pool。
    设计稿要件:grounding 单次成本实测/每问预算上限/候选质量
    校验(最低粉丝/平台白名单/机构号过滤)/暂存区表结构
    (一张表,migration 三段式)——设计稿过闸后才施工
 F4 暂存区→建档:人工勾选→复用 A1 新人管线(析代表视频→建档
    →全量同步);建档前 handle/channel_id 撞库查重
 旅程合龙:问→选平台→30 候选(15库内+15全网)→勾选→My KOL
    →进项目——F 是旅程前半,B3 漏斗是后半,缺一不成"聪明可用"

══ 红线(全程不变量)══
 viltrox_fit_score 唯一写点 pool.py:838 / 禁碰 /enrich 与
 run_kol_pool_gemini_single / 主列表默认排序 COALESCE(fit,0) DESC
 / 13 区块不简化 / rule_v0+rubric 冻结 / 新 LLM 产物一律 llm_
 前缀+未定标标签

---
# 对照打勾记录(原文不动,进度与勘误追加于此)

## 2026-06-11 第〇段验收
- [x] **B1 减法仪式** — commit `75d6cc84`:V615Sidebar 删/GEN2+Beta 假徽章删(Events New 保留)/休眠监听整组删。待 Jianbo 扫 Pool 回"无恙"=冻结解除
- [x] **B2 差量诊断** — 报告 `docs/KOL-Pool-delta-report-20260611.md`(`a52a8bcc`):四盲区+a+/a+++D 类生死判
- [x] **D 类销账/入队完毕,无悬案**:D1 入队第3(硬前置 email 0/1123)/ D2 入队①(ROI 最高)/ D3 销账(删 V615Topbar.tsx 孤儿)/ D4 排末 / D5 本就不迁移 / D6 B1 验收销账+真徽章入队②

## B2 勘误(总册 vs 实测,三处)
1. **A1 星号**:"账号URL→execute auto+增量since"——交互链路验真 ✅,但 **增量 since 实测为伪**(a++:游标字段 last_video_at 不进 provider,整列表重拉本地截断,配额照烧);且 "execute auto" 后端只 dry-run,真执行靠前端二跳 execute=true。A1 成品定性不变,**增量语义降级为"待真游标"**,归 E 类数据侧。
2. **F4 依赖缺口**:F4 写"复用 A1 新人管线(析代表视频→建档→**全量同步**)"——a+ 实测**全量同步不存在**(候选池 max_posts=3 抓死 + worker 无 account-sync job 类型)。F 段设计稿必须把"全量同步补建"列为 F4 前置,或降级为"代表作 N 条"。
3. **E1 数字更新**:深析覆盖 148/1122 → **实测 205/1123**(2026-06-11)。

## 2026-06-12 裁决确认 + E 节新增(a+/a++ 正式移交 Codex)
- **D 类裁决确认**(用户回执):D2 入队①(后端序列化,第一段顺手)/ D6 真徽章入队② / D1 email 无解则冻结(loyalty+geo 两 bug 第一段修)/ D3 销账删孤儿 / D4 排末。三个一行级修复(复制邮箱/D6 残骸/loyalty 量纲)捎进 C3 commit。
- **E5(新增)账号全量同步补建**:新人 onboarding 候选池 max_posts=3 抓死(url_deep_crawl.py:919)+ worker 无 account-sync job 类型(apify_jobs_worker.py:3001)→ 补建"建档后全量(或大 N)同步"管线。**Stage1 ingest 管线已有 path contract 可复用**。F4 的硬前置。
- **E6(新增)真 since 游标改造**:现为伪增量(游标 last_video_at 不进 provider,整列表重拉本地截断,配额照烧;daily light refresh 另一套零游标互不联动)→ provider 侧传真游标(YouTube publishedAfter 等)+ 统一游标推进。**Stage1 ingest 管线已有 path contract 可复用**。light refresh 开闸前置。
- **停摆诊断(2026-06-12)**:last_seen_at 停摆 06-04 根因=双重主动闸——①全系统 ENABLE_SCHEDULER=0(admin 实测 env,worker 默认 0),APScheduler 未启动,morning_sync 等 cron 全停;②应用层守卫 allow_qualified_kol_refresh 默认 false,即便调度器开着 kol_pool_light 也会 skipped(daily_sync.py:553-560 QUALIFIED_KOL_REFRESH_GUARD)。**非故障,light refresh 本就处于停闸省钱态**;E6 落地前不开闸。

## 2026-06-12 全量自走令执行(d1-d6 + 备稿)
- [x] **d1** Drawer 复制邮箱接真(`65b6b2e0`)| **d2** D6 残骸三行清除(`f7db19af`)| **d3** loyalty 量纲+geo_match(`9c3b1a47`)| **d4** D2 'Why V6 Fit' 点亮——真断点为前端 normalizer 滤键,后端投影早已在 pool.py:611(`a54081a8`,零 SQL 写入证明在 commit)| **d5** D3 销账删 V615Topbar(`e34442b4`)| **d6** ContactModal 草稿暂存(`ae69f01c`)。dist=ae69f01c
- [x] **B 心跳盘点** + **C 周脉冲报价** → docs/KOL-Pool-heartbeat-20260612.md(13 job 全死着=ENABLE_SCHEDULER=0;qualified 真实作用面 25 行非 960,报价 <$1.2/周 待裁)
- [x] **A 补全扫** workflow 满配在跑 → docs/KOL-Pool-fullscan-20260612.md(完成即归档)
- 备稿(未 commit,等闸):**E** C2 三端点+域+3 测试全过 | **F** C3 收藏接线(Pool 拉取/乐观 toggle/Drawer 文案,tsc 绿;C4 My KOL 主切换待 107+C5 数据后一刀落,避免空集回退反复)| **G** C5 脚本+dry-run 人审清单 docs/C5-backfill-dryrun-20260612.md(**781 对/721 KOL**,staff 40 名下 4 条 CODEX-VERIFY 测试项目建议人审剔除)

## 程序法(2026-06-12 立)
- **tsc 红禁 commit**:体检不过不许落刀;amend 仅限未 push 的紧急修正且必须回执自报(d4 首例已自报,记录在案不追责,下不为例)。

## 2026-06-12 三拍落锤执行记录
- [x] 拍3 **C5=777 落锤**(`8bba657b`):剔除 4041/4042/4027/**4026(复核发现同系列漏网)**/3620;777 对/720 KOL 与裁决吻合;执行候 "apply"
- [x] 即刻件 **d7**(`6911ea0d`)llm_deep 历史 615 条放出 + **d8**(`f0c4aaa0`)evidence/llm 两计数水管(已分析 chip 复活;后端读侧,激活候下次 HUP);dist=f0c4aaa0
- [ ] 拍4 **25 行实测**:首跑为**空转**(requested=0,默认 tier 闸=hot 而 25 行全 warm)——**零 provider 调用/零花费/零写入,指纹未动**;带 kol_tiers=[hot,warm] 重跑被守卫拦(判一次性授权已耗),候一字令"**重跑**"
- [ ] ④ V6 Fit 智能层第 0 号报价:单行成本依赖上述实测,同候"重跑";排序降级一行提案已呈(见对话)
- [x] ⑤ 程序法 tsc 红禁 commit(`b119c8dc`)

## 程序法第三条(2026-06-12 立,与 3b 同族教训)
- **条件随字走**:放行字附带条件的,执行回执必须对「该字全部附带条件」逐条对账,缺一条不算执行完毕;多道指令叠发时条件不被新令冲掉。条件复述与边界复述同级。

## 2026-06-12 A 补分补课记录
- 补课 b(头排点名核):90+ 共 6 行已逐个人眼核,judgment 见对话回执(petermckinnon/thecamerastoretv/brandonli 等相机圈真顶流,头排成立);**报裁两点:① brandonli 与 brandonliunscripted 为同一人重复档案,头排占两席(数据治理,非评分错);② TikTok 两行输入量纲可疑(ER 53.5%、avg_views 18.8M>粉丝 36 倍,疑单爆款拉伸)——标记不回滚,E6 刷新自愈**。随机 5 行 sane。
- 补课 a(零漂移补写):370 行薄数据(avg_views 与 ER 双缺)reason 追加「·基于有限数据」;**写后指纹 1123/39823.6700/1123 分毫不差(score 零触碰自证)**;幂等复跑 0。

## 程序法第四条(2026-06-12 立,第四例 footgun)
- **拦截波及同批**:守卫拦截发生后,同批命令逐条标注已执行/未执行,恢复时逐条复核,不许假设(本例:sed 改参随拦截静默未执行,致 tier 闸复踩)。

## 2026-06-12 普查 + 脉冲对账
- **重复档案普查(只读,双路)**:路A 字段同值 2 对(brandonli/brandonliunscripted、stefanolombardoyt/stefanolombardo)+ 路B 同平台前缀 2 对(danieljm.visuals/danieljmvisuals、matthewstorerphotography/matthewstorer)= **真重复 4 对 ≤10 → 候裁主从标记单**。跨平台同名 ~10 组为合法多平台行,不动。**新发现·池污染群(P6 专项候裁)**:LLM 失败输出当 handle 入库 5 行(id 3323-3328)+ 垃圾 handle("u"/products×3/contact/camera/cameras/hipster)+ media 池卡口拆行(-fe/-z)与宣发项目行(3311/3312)≈ 20+ 行,性质=非 KOL 行入池,非重复。
- **TikTok 量纲(裁决②)**:智能层 0 号案卷**首例田野证据**——TT 视图数不受粉丝约束(算法分发),YT 语义公式系统性高估全部 TT 行;Real ER de-inflation 因子即为此病设计;**josiah(3462)/frank(3450)为 0 号法定验收样本**。
- **25 行脉冲实测(漂移第三起,最终令)**:23 行实跑(YT13/IG7/TT3,media 2 行不可 enrich 自动排除)/210.8s/0 错误;**Apify 实测 $0.0206**(YT 官方配额 $0)。指纹 39823.6700→**39814.3900**(Δ-9.28 全额归因 23 行 enrich 重算,逐行前后账在档:有升有降——juanografoo 21.9→66.9 补到真数据,jasonvong 86.2→57.5 真 ER 校准;原 11 行之一 teleginivan 85→67.5 = 条件 e 预期覆盖)。**as-of 戳自动跳 2026-06-12**(d14 活了)。skipped:id 3341 handle="u" no_results(污染群又一证)。
- **双报价(实测落档)**:① 周脉冲 ≈ **$0.02/次、<$0.10/周**(qualified 25 行口径;下次跑带 stale_before=7d);② 方案B 全池 provider 刷新参考:~9.2s/行,1112 行 ≈ **<$1、串行 ~2.9h**(YT 527 行免费,IG+TT ~400 行 ×$0.002)。

## 2026-06-12 五件裁决执行
- [x] **① 周脉冲钉死**(`a70910b2`):crontab 每周一 08:30 直调 `weekly_pulse.py`(allow 标志,ENABLE_SCHEDULER 保持 0,13 死 job 不借道);stale_before=7d;每次自动出漂移账(前后指纹+逐行归因+成本)→ `docs/KOL-Pool-pulse-log.md`;E6 落地即停
- [~] **② YT 先遣波执行中**(漂移#4):527 行基线快照在档(/tmp/ytwave_before.txt,跑前指纹 39814.3900);配额预检=主路径 2-4u/行≈2k 总量/10k 日配额(5× 裕量),search fallback 雪崩由 error_stop=10 兜底;可中断让行;完毕出全套对账。**IG/TT 波(~$0.8)押后 0 号 Real ER 落地**
- [ ] **③ d14 语义修复**(下个 d 窗口):max() 虚报→分布诚实形「最新 X · 中位 Y · N% 行 ≥7 天未更」,hover 给分布(用户自首规格错,记案)
- [ ] **④ 主从标记单**(下窗口):4 对;主行判定=有 FK 引用者为主,均无则低 id;存储=migration 109(搭 107/108 同一 apply 窗);列表默认滤从行,Drawer 留「关联档案」链
- [x] **⑤a 围堵验证:不成立,重大发现**——污染 18 行中 1 行已在 qualified 脉冲集(3341"u",已实跑 no_results);**17 个 assignment 对挂污染行 → C5 按 777 执行会收污染行进 My KOL**;**C5 修订候裁**(建议:执行前剔除污染 id 集,777→修订数随裁决定)
- [x] **⑤b 根因堵口**(`e3a1e872`):口子=5/19 legacy 批次 `_normalize_item` 对 handle 零校验;已加 `_looks_like_garbage_handle`(LLM 标记/失败叙述/超长/单字符拒收,中日文正常 handle 断言不误伤)=F4 入库验证先于流量的田野证据落地
