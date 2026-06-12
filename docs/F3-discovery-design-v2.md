# F3 全网发现 · 设计稿 v2(过闸版,2026-06-12)

> 纯纸面零施工。本版在 v1 骨架(docs/F3-discovery-design.md)上补齐七要件至闸A(资金)+闸C(语义)可过水准。
> 全部代码锚点为当日只读审计实测;演算数字为当日 psql 只读 SELECT(viltrox2)。
> 红线复述:viltrox_fit_score 唯一写点 pool.py:838 / 禁碰 /enrich 与 run_kol_pool_gemini_single / 主列表默认排序 COALESCE(fit,0) DESC / 13 区块不简化 / rule_v0+rubric 冻结 / **新 LLM 产物一律 llm_ 前缀+未定标标签**。F3/F4 全链不触碰任何 fit 字段。

## 0 审计结论(当日只读,引用为后文依据)

| # | 结论 | 证据 |
|---|---|---|
| 0.1 | **全仓零 grounding 实现**:grep `grounding\|google_search\|googleSearch` 全仓(backend/frontend/shared/scripts)唯一命中 `provider_preflight.py:71`——那是 Google Programmable Search(CSE)的源登记项,`live_probe="not_implemented"`,与 Gemini grounding 非同物 | backend/app/domains/market/provider_preflight.py:69-80 |
| 0.2 | `_call_google` 请求体只有 `contents` + `generationConfig{maxOutputTokens,temperature}`,**无 tools 通道**;且 provider caller 签名被 `_PROVIDER_CALLERS` 钉死为 `(prompt, max_output_tokens)`,purpose 不进 caller | backend/app/platform/llm_gateway.py:351-367、424-428 |
| 0.3 | **"全网发现"已有一条腿在跑(非 grounding)**:`profile_discovery.discover_new_creators` 走 Apify 平台搜索 actor(streamers/youtube-scraper、clockworks/free-tiktok-scraper 等),由 `/kol-smart-search` 的 `include_new_discovery+execute_new_discovery` 双开关触发,结果落 search session items(item_type=new_creator),**不落任何暂存表** | backend/app/domains/kol/profile_discovery.py:808-895;backend/app/services/intelligence/account_scan_service.py:356-396;backend/app/api/routers/vkpi_kol_pool.py:511-540 |
| 0.4 | `llm_gateway.invoke` 预算检查 **telemetry-only**(docstring 原文 "no longer prevent an explicitly triggered provider call")——硬停必须做在调用方 | llm_gateway.py:458-473 |
| 0.5 | **通用 `single_call` 闸已饱和**:实测 cap $0.50 / current_spend $58.2676(analysis_worker 等共用累计)。任何依赖通用 scope 判定的预检,F3 第一天就永久"不许"——F3 必须看**专属 scope** | psql 只读 vkpi_provider_budget_caps,2026-06-12 |
| 0.6 | **记账盲点在档**:`cost_cents` 整数地板除,亚美分调用记 0,`current_spend` 不累计(P5-handover §telemetry 盲点);且 grounding 查询费**不在 token usage 里**,沿用 `_estimate_cost_cents` 必然记 0 | docs/P5-handover.md:70;llm_gateway.py:138-140 |
| 0.7 | 暂存表与专属预算 scope 均不存在:`to_regclass('vkpi_kol_discovery_staging')`=NULL;caps 表无 `%discovery%` scope。migration 头=110 | psql 只读,2026-06-12 |
| 0.8 | 库内召回 `recall_kol_profiles` **无 platforms 参数**(planner 已抽取 platforms 但只喂给 discovery 腿)——F2 平台过滤是真缺口 | vkpi_kol_pool.py:489-505 |
| 0.9 | 池 1128 行(YT 549/IG 354/TT 113/media 81/unknown 14/x 9);favorites 772 行/15 staff;search sessions 26 个 | psql 只读,2026-06-12 |

---

## 要件 1 · 双轨入口形态(库内泛搜 + 全网发现)

**形态**:一个输入框(A1 统一智能输入,SmartKolInputPanel),问句进来走 F1 意图跳(找人? y/n + 提取产品/题材)→ 追问条平台多选 chips → 两腿并发:

- **腿一·库内泛搜(F2,即时同步)**:既有 `kol_smart_query_planner.plan_text_query` + `recall_kol_profiles`,creator_quota/reviewer_quota 既有 15/15 收敛为**库内合计 15**(quota 参数已在端点,纯传参);缺口=召回侧补 `platforms` 过滤参数(审计 0.8),planner 的 platforms 产物从"只喂 discovery"改为双喂。
- **腿二·全网发现(F3,同步单调用,15s 软时限)**:Gemini grounding 单调用(要件 2)→ 候选过卫生闸 → 落暂存区(要件 5)→ 回显 15 条。失败/超时/预算拒绝=琥珀诚实条标注原因,**绝不静默降级为编造**。
- **既有 Apify 平台搜索腿的归位(审计 0.3,v1 漏项)**:`discover_new_creators` 不废除、不与 grounding 混淆——定位为"平台内容关键词搜索"(找**视频**反推作者),grounding 定位为"泛行业语义发现"(直接找**人**)。两者产物统一改投暂存区(source 枚举区分,要件 5/7),session items 的 new_creator 旁路在 F3 开闸后收编,**避免两套全网结果两套真相**。

平台 chips 白名单与后端一致:youtube/instagram/tiktok(`SUPPORTED_DISCOVERY_PLATFORMS` 另含 douyin,F3 默认不开);F1 草图里的 [FB] **后端无支持,chips 不出 FB**(闸C:不许展示做不到的选项)。

**验收标准**
- [ ] 同一问句一次提交,返回体里库内/全网两腿状态各自独立可见(任一腿失败不拖垮另一腿)
- [ ] 库内腿带 platforms 约束的召回结果,平台分布 100% 落在所选 chips 内
- [ ] 全网腿被预算/配置拒绝时,UI 出现诚实条且文案含具体原因(blocked_by_budget / not_configured / timeout)
- [ ] chips 集合与后端白名单字面一致,无 FB

---

## 要件 2 · Gemini grounding 调用设计

### 2.1 API 形态
- 端点:既有 `generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`(PROVIDER_CONFIG.google 原样,model=gemini-flash-latest)
- 请求体增量:`"tools": [{"google_search": {}}]`;其余 generationConfig 不动
- 响应增量:`candidates[0].groundingMetadata` —— `webSearchQueries[]`(实际发起的搜索词)、`groundingChunks[]`(uri/title 引证)、`groundingSupports[]`(文段↔引证映射)。**候选卡的 profile_url 优先取 groundingChunks 真实 uri,模型正文里的裸链接只作兜底**(防幻觉链接)。
- 提示词契约:要求结构化 JSON 输出(name/platform/handle/profile_url/followers_estimate/reason),≤15 条。**已知风险预登记**:gemini-flash 动态思考吃 maxOutputTokens 致 JSON 截断(P6-1 在案,复盘已实证)——F3 提示词按短输出设计(15 条 ≈ 1.5-2k tok),实测时记录是否截断;若截断,方案候选与 P6-1 同族(thinkingConfig / 截断重试),不在 F3 内私修平台层。

### 2.2 网关接入点(两案候裁,任选其一过闸后施工)
| | 甲 · invoke 透传 provider_options | 乙 · 独立伪 provider "google_grounded" |
|---|---|---|
| 改动面 | `invoke()` 加可选参 `provider_options`,三个 caller 签名同扩 `(prompt, max_tokens, options)` | `_PROVIDER_CALLERS` 注册第 4 项 `_call_google_grounded`(~40 行,复制 `_call_google` 加 tools+groundingMetadata 解析);**不进 PROVIDER_ORDER 默认链** |
| 共享路径风险 | 触三 caller 签名(共享默认被动),需 purpose 白名单防误传 | **零触既有三 caller**,其他 purpose 永不可能落到 grounded 路径 |
| 失败语义 | fallback 链可能滑向无 grounding 的 google → 静默编造风险 | F3 指定 `preferred_provider="google_grounded"`,失败即失败(fail-closed),不滑链 |
| 成本记账 | `_estimate_cost_cents` 仍不识查询费,要另开口子 | 查询费估算**局部化**在新 caller 内(token 费 + grounded query 数 × 单价) |
| **判定** | | **推荐乙**:对齐 v1 "不动共享默认"意图 + fail-closed + 记账局部化 |

账本不绕行:无论甲乙,调用必须走 `llm_gateway.invoke`(purpose=`vkpi_kol_discovery`,cost_tag=专属 scope),享受既有 record_call 留痕。**禁止仓内任何直连 HTTP 旁路**(探针除外,见 2.4)。

### 2.3 每次成本实测口径(定义,候"测"回填)
- 牌价:grounding = 模型 token 费($0.07/M in + $0.30/M out,PROVIDER_CONFIG 实价)+ grounded 查询费(**牌价 $35/千次**)。Apify 案教训:牌价不作数,实测落档。
- **牌价的口径歧义即实测第一问**:计费单位是"每个 grounded 请求"还是"每条 webSearchQueries"?一次请求可发 N 条搜索词。两种读法成本差 N 倍。实测必须同时记录 `webSearchQueries` 条数与账单侧实扣。
- 上界演算(最坏读法):10 条查询 × $0.035 + token 费 < $0.36 < $0.50 单次硬停——探针在最坏口径下仍在闸内。
- 实测产出五元组,回填本节:
  `实测:$____ /次(账单口径)· ____ 候选 · ____ webSearchQueries · ____ ms · JSON 截断 y/n(候"测")`

### 2.4 探针形态(零代码、零写库、单次)
本仓无 grounding 能力(审计 0.1/0.2),而过闸又要实测——**探针不进产品代码**:一条 curl 直打 generateContent(GEMINI_API_KEY 取自 .env,体=固定问句+tools),stdout 留存 usageMetadata + groundingMetadata,美元数对账 Google 计费台。单次预算 <$0.5,候"测"字执行。探针记录追加入本档 2.3,不碰仓、不碰库。

**验收标准**
- [ ] 2.3 五元组全部回填且来源为真实账单/响应体,非牌价推算
- [ ] 接入案(甲/乙)有裁决记录;乙案下 grep PROVIDER_ORDER 不含 google_grounded
- [ ] 施工后任一次 F3 调用在 vkpi_llm_calls 账本可查(purpose=vkpi_kol_discovery)
- [ ] 候选卡 profile_url 与 groundingChunks uri 的对应率在首批实测中抽查 ≥10 条留痕

---

## 要件 3 · 15+15 结果版式

**复用面(零新発明)**:库内 15 沿用 RecallMiniItem 卡 + 三列 grid(SmartKolInputPanel.tsx:889 `grid gap-2 md:grid-cols-2 xl:grid-cols-3`)原样;琥珀诚实条(:885,P0-B 范式)升级为**双腿状态条**。

**版式**:同屏上下两区(移动端纵排):
- 区一「库内 15」:既有卡,含 V6 Fit、可开 Drawer;
- 区二「全网 15」:新 ExternalCandidateCard —— 名/平台徽章/粉丝(空="—",诚实空值)/profile_url 外链/`llm_reason`(一句话理由,**卡上贴「LLM·未定标」标签**——红线:llm_ 前缀+标签)/quality_flags 留痕角标/撞库徽章。
- **外部卡无任何分数**:不显示、不计算、不参与排序语义;区二排序=grounding 返回序,标注"来源排序,非评分"(闸C:不发明分数,不复用 fit 视觉语言)。
- 撞库命中的卡置灰不可勾,显示「已在库」并直链该 KOL Drawer(dedup_hit_kol_pool_id 软引用)。
- 勾选只存在于区二;单次提交勾选上限 15。
- 诚实条文案分层(闸C 文案层级):第一层=两腿各命中数;第二层=未启用/被拒原因(预算/配置/超时);沿用现条款"批量自动分析按闸关闭(E4)"不动。

**验收标准**
- [ ] 外部卡不出现 fit/score 任何字样或同视觉元素;LLM 理由字段渲染处带「LLM·未定标」标签
- [ ] followers 缺失渲染为"—"而非 0(钱口径诚实语义同族裁决)
- [ ] 撞库卡不可勾且可跳 Drawer;非撞库卡可勾,勾选数上限 15 有前后端双重约束
- [ ] 诚实条在"全网腿 0 命中""全网腿被闸""全网腿超时"三态下文案可区分(浏览器 QA 三截图)

---

## 要件 4 · 勾选 → My KOL 落库链(复用 favorites)

**链条(五跳,全部复用既有件)**:
```
勾选(区二外部卡,staff 身份)
→ ① staging.status: pending→approved(记 created_by_staff_id;唯一新端点)
→ ② F4 建档:复用 A1 新人管线 url_deep_crawl(execute=true)
     → 管2 write_kol_profile_basics(profile_basics.py:211)
     → 卫生闸自动承袭:_normalise_profile_data 调 _garbage_handle_rule(登记表管2 在案)
→ ③ 建档成功返 kol_pool_id → staging.promoted_kol_pool_id 记录(软引用),status→promoted
→ ④ 自动收藏:pool_favorites.add_favorite(kol_pool_id, staff=勾选人, note='F3 discovery')
     (vkpi_kol_pool_favorites,migration 107;实测 772 行/15 staff 在役)
→ ⑤ My KOL 可见(C4 收藏分区;C4-full 后即主体)
```
- ④ 的语义裁定(闸C):**勾选=该 staff 的跟进意愿**,自动收藏成立;若裁为"建档≠收藏",则 ④ 改为建档完成 toast 内一键收藏——两形态都只用既有 add_favorite,无新表。默认按自动收藏设计。
- **失败路径诚实**:②建档失败(垃圾 handle 被管2 拒收/抓取失败)→ staging.status 回 pending + quality_flags_json 追加失败痕,UI 显示失败原因,**不产生半截 pool 行,不产生收藏**。
- **硬前置申报(B2 勘误 #2 / E5)**:"建档→全量同步"中全量同步不存在(候选池 max_posts=3 抓死 url_deep_crawl.py:919 + worker 无 account-sync job)。F4 首版交付口径=「建档+代表作 ≤3 条」,全量同步候 E5;此降级在建档完成文案中如实标注。排期不变:F4 尾巴依赖 E5。
- 独占体制(B 案·项目维)与本链无冲突:收藏≠在役 assignment,不触发独占校验。

**验收标准**
- [ ] 勾选→My KOL 全链一次走通的浏览器 QA 记录(staging 行、pool 行、favorites 行三处 id 互证)
- [ ] 建档失败样本(垃圾 handle)实测:pool 零新行、favorites 零新行、staging 留痕含 rule 名
- [ ] favorites 写入复用 add_favorite 原函数(diff 中 pool_favorites.py 零改动)
- [ ] My KOL 列表中 F3 来源行与 C5 backfill 行外观无二(note 字段可溯源即可)

---

## 要件 5 · 暂存区零外键设计

**原则**:暂存区与 kol_pool **零外键直连**——暂存区绝不污染主池;一切关联用软引用列(裸 BIGINT,无 FK 约束),主池删行/合并(109 duplicate_of)永不级联牵暂存。

```sql
-- migration 111(编号占位,随 apply 窗分配;三段式=up + down + psql 逐执/幂等复验/schema_migrations 记录,沿 107-110 波次范式)
CREATE TABLE vkpi_kol_discovery_staging (
  id                    BIGSERIAL PRIMARY KEY,
  discovery_uid         TEXT UNIQUE NOT NULL,      -- sha256(source|platform|lower(handle 或 profile_url)),幂等防重投
  source                TEXT NOT NULL,             -- 'gemini_grounding' | 'platform_search' | 'market_signal'(要件 7)
  query_text            TEXT,
  platform              TEXT,                      -- 白名单枚举,入列校验
  handle                TEXT,
  display_name          TEXT,
  profile_url           TEXT,
  followers             BIGINT,                    -- 可空;空即空,不填 0
  llm_reason            TEXT,                      -- 一句话理由(LLM 产物,红线:llm_ 前缀;v1 的 one_line_reason 名违例,本版改正)
  quality_flags_json    TEXT,                      -- 校验留痕(过/拒+规则名+失败痕)
  status                TEXT NOT NULL DEFAULT 'pending',  -- pending→approved→promoted | rejected | archived
  dedup_hit_kol_pool_id BIGINT,                    -- 撞库命中软引用,无 FK
  promoted_kol_pool_id  BIGINT,                    -- 建档产物软引用,无 FK(v2 新增,链条③需要)
  created_by_staff_id   BIGINT,                    -- 软引用,无 FK
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_kds_status   ON vkpi_kol_discovery_staging (status);
CREATE INDEX idx_kds_platform_handle ON vkpi_kol_discovery_staging (platform, lower(handle));
```

- **入口闸=卫生闸(入列校验,先于落行)**:最低粉丝 ≥5,000(平台可配;followers 为空不卡门,卡门交给人工审)/平台白名单 youtube/instagram/tiktok/机构号过滤(official/brand/shop/store + 粉丝关注比异常)/**`_looks_like_garbage_handle` 必经**(pool_common.py:663,与管1-管3 同一个函数——"一个卫生标准"原则;22/22 回归在案)。校验不过=不落行,拒收计数与规则名回 session diagnostics 留痕。
- **撞库查重(双查)**:handle/channel_id 对 vkpi_kol_pool(1128 行,platform+lower(handle) 索引路径)+ 本暂存表自身;命中→照常落行但 dedup_hit_kol_pool_id 置值,UI 置灰(要件 3)。
- 状态机单向:pending→approved→promoted 为主干;rejected(人工拒)/archived(保留期满)为出口;**任何状态都不反向回 pool 写任何字段**。
- 保洁条款:promoted/rejected 行 90 天转 archived(纸面约定,清理任务候后批,不随 F3 首版)。
- 写入路径登记(staging 自身):本表唯一写点=要件 7 的 `staging_intake` 单一入口,三 source 共用,登记于下表。

**验收标准**
- [ ] `\d vkpi_kol_discovery_staging` 零 FOREIGN KEY 输出(apply 后实查)
- [ ] down 迁移单独演练通过(建→灌 3 行→down→up 幂等复验)
- [ ] 同一候选重复投递(同 discovery_uid)二次入列被 UNIQUE 拒并幂等返回既有行
- [ ] 垃圾 handle 样本集(在案 22 例)对入列校验 22/22 拒收,真 handle 对照组零误伤

---

## 要件 6 · 成本护栏(单次 <$0.5,候"测"字的实测方案)

**Scope 提案(沿 106 范式:DB 种子 caps + 调用方 cost_tag)**:
```sql
-- migration 112(占位,随窗;与 106 同构)
('single_call_kol_discovery', 0.50, 0, 0.80, 1.00, 'block_kol_discovery', …),
('cron:vkpi_kol_discovery',  10.00, 0, 0.80, 1.00, 'block_kol_discovery', …)  -- 月度,软警 0.8
```
数字为提案,实测后可调。

**三个实测落档的工程事实决定护栏形态(v1 未覆盖,本版补强)**:
1. **网关不拦(审计 0.4)**:`invoke` 预算为 telemetry-only。⇒ 硬停做在 **F3 调用方**:调用前 `llm_gateway.budget_preflight(purpose='vkpi_kol_discovery', cost_tag='single_call_kol_discovery')`,**专属 scope 判不许即拒发**(与复盘的 record-only 不同:F3 是探索型支出,裁为 fail-closed),UI 走诚实条。
2. **通用 single_call 已饱和(审计 0.5,$58.27/$0.50)**:F3 预检**只裁于专属 scope 的判定**,不得把通用 scope 的"不许"当 F3 的"不许"——否则第一天即永久封死。预检返回体里通用 scope 状态照记照报(telemetry 不丢),只是不作为 F3 的拦截依据。
3. **记账盲点(审计 0.6)**:token 费亚美分地板除记 0 + grounding 查询费不在 token usage。⇒ F3 记账契约:每次调用后按「token 费 + webSearchQueries 条数 × 实测单价」算 USD 浮点,经 budget_guard.record_cost 入专属 scope(绕开 cost_cents 地板除),grounded query 数与估算美元同写 record_call metadata。平台级 microcents 改造仍归 P6,F3 不私改共享层,只保证**自己的 scope 看得见自己的钱**。

**实测方案(候"测"字,单次 <$0.5)**:即要件 2.4 探针——1 次真实 grounded 调用,产出 2.3 五元组 + 计费口径裁定(per-request vs per-query);最坏口径上界 $0.36(2.3 演算)。实测后:① 回填 2.3;② 据实扣单价复核 $0.50/$10 两数,需调则改本节提案数字再过闸A 确认;③ 单价常数落配置的方式随施工 commit。

**验收标准**
- [ ] 探针实付金额经 Google 计费台核对并 <$0.50,记录在本档 2.3
- [ ] 施工后:人为把专属 scope current_spend 置满(沙箱),F3 调用被调用方拒发且 UI 出诚实条(fail-closed 实证)
- [ ] 施工后:连续 3 次 F3 调用,专属 scope current_spend 增量 >0(记账盲点绕开实证)
- [ ] 月度 scope 0.8 软警触发时有 warning 日志且不拦(软警语义)

---

## 要件 7 · Intelligence 创作者腿合并点

**裁定在案(既裁并入)**:一条管线,多个进水口。本版把合并点钉到唯一函数:

```
staging_intake.submit_candidates(source, items, *, staff=None, query_text="")
  = vkpi_kol_discovery_staging 的唯一写点
  = 入列校验(要件 5 卫生闸)+ 撞库双查 + discovery_uid 幂等,对所有 source 一视同仁
```
- **进水口一** `gemini_grounding`:F3 主腿(要件 2)。
- **进水口二** `platform_search`:既有 Apify 腿收编(审计 0.3)——`discover_new_creators` 的 new_creators 产物改投 staging,session items 旁路在收编 commit 中标注过渡;**收编前该腿不许再直造 new_creator 旁路数据语义**(防两套真相)。
- **进水口三** `market_signal`:Intelligence 体系(vkpi_market_sources/mentions 链)将来识别出"创作者"实体时,只需构造 items 调 submit_candidates——**审批/建档/卫生/撞库/My KOL 全复用,Intelligence 侧零新表零新闸**。当前 signal_taxonomy 尚无 creator 类目(grep 零命中),本进水口为**接口契约预留**,不随 F3 首版交付任何 Intelligence 侧代码。
- 池写入路径登记表(KOL-Pool-requirements §登记制)对账:staging 非池本体,但**管4 行按 v1 第 5 节先登记**:

> 管4 | F4 暂存区→建档 | profile_basics.py:211(经管2 write_kol_profile_basics,自动承袭 _garbage_handle_rule) | 闸接入点=staging 入列校验(要件 5)+ 管2 既有闸,双层 | 接入 commit:候"施工"

**验收标准**
- [ ] 施工后 grep:vkpi_kol_discovery_staging 的 INSERT 仅存在于 staging_intake 一处
- [ ] 三 source 枚举入列走同一校验路径(单测:同一垃圾 handle 三个 source 三拒)
- [ ] 登记表管4 行在 KOL-Pool-requirements.md 落档并指回本档
- [ ] platform_search 收编后,session 流程的全网结果与 staging 行数对账一致(无旁路漏记)

---

## 过闸所需的两个字

| 字 | 对应动作 | 边界 |
|---|---|---|
| **测** | 执行要件 2.4/6 探针:仓外单条 curl 真实 grounded 调用一次,预算 <$0.5;产出 2.3 五元组 + 计费口径裁定,回填本档 2.3 与要件 6 数字复核 | 零仓内代码、零写库、单次;只动本档 |
| **施工** | F3 开工:migration 111/112 入 apply 窗(三段式)、网关乙案(或裁甲)、staging_intake、F1 chips+F2 platforms 传参、15+15 版式、F4 链④自动收藏、platform_search 收编;管4 行接入 commit 回填登记表 | 候闸A(本档要件 6 数字)+闸C(本档要件 3/4 语义裁定)双过后;F4 全量同步尾巴依赖 E5,排期不变 |

二字独立:可先"测"后凭实测数字再裁"施工";直接"施工"则牌价口径风险自担(不建议,Apify 案在前)。
