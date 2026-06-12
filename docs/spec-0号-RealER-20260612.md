# 0号 Real ER 语义件 · 三选项 spec(2026-06-12)

> 纯纸面工序。本文档为唯一产出物;零代码改动、零写库(psql 仅 SELECT)、零 git。
> 法定验收样本:josiah(kol_pool **3462** @josiahlebante14, tiktok)/ frank(kol_pool **3450** @frank_of_all_trades, tiktok)。
> 所有数值均为 2026-06-12 自 viltrox2(127.0.0.1:54329)只读演算,复算口径见附录。

---

## 0. 背景与铁律

- `vkpi_kol_pool.viltrox_fit_score` 现由 **rule_v0**(`backend/app/domains/scoring/rule_v0.py`)经
  `backend/scripts/backfill_fit_scores.py`(marker `rule_v0_backfill_20260612`)回填。
- 0号件目标:给 **Real ER(真实互动率)** 一个有法定语义的独立列,**绝不碰 `viltrox_fit_score` 写点**,
  也不改 `engagement_rate` 既有列;新口径一律落 `real_` 前缀影子列(LLM 衍生物落 `llm_` 前缀,本件不涉及)。

## 1. 现状盘点(前值)

### 1.1 法定样本现库数据

| 字段 | josiah #3462 | frank #3450 |
|---|---|---|
| followers | 516,600 | 1,000,000 |
| avg_views | 18,800,000 | 1,948,966 |
| avg_likes | 2,600,000 | 128,392 |
| avg_comments | 22,314 | 1,684 |
| avg_shares | 86,700 | 16,161 |
| engagement_rate(DB 原值) | **16.91906** | **53.50400** |
| viltrox_fit_score | 95.000 | 95.000 |
| viltrox_fit_reason | [rule_v0_backfill_20260612] 互动率 16.92% | [rule_v0_backfill_20260612] 互动率 53.50% |
| video_evidence_count / has_video_evidence | **0 / false** | **0 / false** |
| raw_platform_data 样本 | 3 条(max_posts=3) | 3 条(max_posts=3) |
| 样本发布期 | 2023-06 ~ 2025-07(置顶爆款偏置) | 2026-01 ~ 2026-06(较新) |

逐条原始样本(raw_platform_data.videos,Apify clockworks~tiktok-scraper,2026-06-03 抓取):

| KOL | 视频 id | 发布日 | views | likes | comments | shares | saves |
|---|---|---|---|---|---|---|---|
| 3462 | 7528386833818979605 | 2025-07-18 | 7,200,000 | 1,100,000 | 5,210 | 91,100 | 100,423 |
| 3462 | 7276458240395791622 | 2023-09-08 | 26,400,000 | 3,700,000 | 6,633 | 99,800 | 258,695 |
| 3462 | 7245252139856219398 | 2023-06-16 | 22,800,000 | 3,000,000 | 55,100 | 69,200 | 254,227 |
| 3450 | 7615817984153521439 | 2026-03-11 | 1,800,000 | 158,800 | 3,259 | 27,600 | 24,097 |
| 3450 | 7598000582879628574 | 2026-01-22 | 4,000,000 | 224,500 | 1,776 | 20,600 | 71,487 |
| 3450 | 7646987859227888926 | 2026-06-03 | 46,900 | 1,877 | 18 | 282 | 744 |

### 1.2 现 ER 口径还原(已逐位核对命中)

`calculate_kpis`(backend/app/domains/industry/snapshot_kpis.py L385)的口径是:

```
engagement_rate = Σ(样本条 likes+comments+shares+saves) / followers
```

- josiah:8,740,388 / 516,600 = **16.91906** ✓(与 DB 完全一致)
- frank:535,040 / 1,000,000 = **0.53504** → ×100 存成 **53.504** ✓

两个结构性缺陷(0号件立项依据):

1. **单位翻转 bug**:`backfill_kol_profile_basics.py` L525 `ratio≤1 则 ×100,>1 则原样存`。
   frank 的 53.504 是"百分数",josiah 的 16.91906 是"倍数"(按 frank 的约定应读作 1691.9%)。
   同列两种单位,任何下游消费都注定读错其一。`backfill_fit_scores.py` L55 再做一次逆变换
   (`>1 则 ÷100`)恰好把两个错误抵消成"看起来都像百分数",纯属侥幸。
2. **分母语义错**:Σ(N 条互动)/followers 随样本条数 N 线性膨胀,既不是 per-view 也不是
   per-post,N=3 与 N=10 不可比;且 josiah 的 3 条样本是置顶爆款(2023~2025),严重高估。

### 1.3 与 rule_v0 的关系(前值锚点)

rule_v0 的 engagement 子分 = `min(25, ER×250)`,即 **ER≥10% 一律截顶 25 分**:

| | josiah | frank |
|---|---|---|
| 进入 rule_v0 的 ER | 0.16919(16.92%) | 0.53504(53.50%) |
| engagement 子分 | 25.0(42.3 截顶) | 25.0(133.8 截顶) |
| fit_score 全分解 | 25(粉丝)+25(互动)+20(播放)+15(平台)+10(题材)=**95** | 同左 =**95** |

结论:现状下两个法定样本 **互动维度完全无区分度**(双双截顶),fit_score 同为 95。
Real ER 的"后值"若能拉开两人差距,即验收通过的核心信号。

---

## 2. 选项A:评论加权 ER(likes+α·comments)/views

**公式**:`real_er_A = mean_over_posts( (likes_i + α·comments_i) / views_i )`,α 为评论权重。

**数据可得性**:
- 现库 per-account 均值字段可直接算近似版:tiktok 95/113 行有 avg_likes+avg_comments+avg_views;
  youtube ~510/549;**instagram 仅 6/354(大缺口)**;media/x/facebook/unknown 全缺。
- per-post 精确版只能取 raw_platform_data.videos(现仅 3 条/人)或 evidence 表。
- **缺**:α 无任何现库依据可标定(无评论质量/转化数据可回归),只能拍。

**与 rule_v0 的关系**:独立影子列 `real_er`(method='comment_weighted_v1'),不写
`viltrox_fit_score`、不改 rule_v0 代码;对照期仅做 what-if 重算供裁决参考。

**josiah & frank 前后值**(per-post 逐条计算后取均值):

| 口径 | josiah #3462 | frank #3450 | 两人区分度 |
|---|---|---|---|
| 前:engagement_rate(DB) | 16.91906(单位污染) | 53.50400 | frank 反而 3.2 倍于 josiah(假信号) |
| 后 α=10 | **15.28%** | **7.03%** | josiah 2.2× frank ✓ |
| 后 α=20 | **16.41%** | **7.90%** | josiah 2.1× frank ✓ |
| 后 α=50 | **19.80%** | **10.54%** | josiah 1.9× frank ✓ |
| what-if rule_v0 互动子分(α=20) | 25.0(仍截顶) | 19.74 | 拉开 5.3 分 |

**评价**:方向正确(分母换成 views 后假信号消失),但 α 是自由参数,选 10/20/50 结论排序不变、
数值漂移 ±30%,作为**法定口径不可裁决**;适合当选项B 的敏感性检验。

---

## 3. 选项B:近10条 evidence 实算 ER

**公式**(pooled,聚合后再除,抗单条小样本噪声):

```
real_er_B = Σ近N条(likes+comments+shares+saves) / Σ近N条 views    (N≤10, is_active=true)
```

**数据可得性(本选项的硬前置)**:
- `vkpi_kol_video_evidence`:**josiah 与 frank 均为 0 行**;tiktok 池仅 18/113 有 evidence。
  字段本身齐备(view_count/like_count/comment_count/share_count,share_count 已有列)。
- 唯一现成替代源 = raw_platform_data.videos,但 `profile_basics_pilot_v1` 抓取参数
  **max_posts=3**,"近10条"名不副实;且 TikTok 返回含置顶视频(josiah 3 条全是
  2023~2025 爆款),**需重抓 resultsPerPage=10 并按 createTimeISO 过滤置顶/陈旧条目**。
- **缺**:saves(collectCount)在 evidence 表无对应列(raw 里有),需评估是否纳入分子。

**与 rule_v0 的关系**:同选项A,独立 `real_er`(method='evidence_pooled_v1')+
`real_er_sample_n` 记录实际条数;evidence 表已有触发器 `trg_sync_kol_video_summary`
自动维护 video_evidence_count,回填 evidence 即顺带修复 has_video_evidence=false 的失真。

**josiah & frank 前后值**(以现有 3 条演算,N=3 注记于 sample_n):

| 口径 | josiah #3462 | frank #3450 |
|---|---|---|
| 前:engagement_rate(DB) | 16.91906 | 53.50400 |
| 后:pooled(L+C+S+Sv)/V | **15.50%** | **9.15%** |
| 参考:per-post 均值 | 16.08% | 8.69% |
| 参考:仅 likes/views | 13.83% | 6.59% |
| what-if rule_v0 互动子分 | 25.0(38.7 截顶) | 22.88 |
| 样本警示 | 3 条全为置顶爆款,真实近况存疑,**必须重抓** | 3 条较新(2026),可信 |

**评价**:语义最贴"真实互动率"——分子分母都来自可审计的单条 evidence,无自由参数;
代价是数据工程前置(evidence 回填 + max_posts=10 重抓 + 置顶过滤)。

---

## 4. 选项C:平台分位归一 ER

**公式**:先算视图 ER(`avg_likes/avg_views`,或选项B 值),再在同平台、非 duplicate 行内取
`real_er_pctl = percent_rank()`,落 0~1 分位。

**数据可得性**:
- tiktok:95 行可算,分布健康(P25=3.94% / P50=6.67% / P75=9.80% / P90=14.90%);
- youtube:507 行可算(但 avg_shares 全缺,分子只能 likes+comments);
- **instagram:仅 5 行可算,分位无统计意义**;media/x/facebook/unknown 完全不可算;
- 新入池账号在平台样本扩充前分位会漂移(冷启动问题),需冻结参考分布或定期重算。

**与 rule_v0 的关系**:独立列 `real_er_pctl`,是**展示/排序层**的衍生列;它消化了 rule_v0
`×250 截顶`造成的高 ER 段无区分度问题,但它本身不是"率",不能替代 real_er 原始值。

**josiah & frank 前后值**(基于 avg_likes/avg_views,tiktok n=95):

| 口径 | josiah #3462 | frank #3450 |
|---|---|---|
| 前:engagement_rate(DB) | 16.91906 | 53.50400 |
| 视图 ER(likes/views) | 13.83% | 6.59% |
| 后:**real_er_pctl** | **0.8723(87 分位)** | **0.4787(48 分位)** |
| what-if 互动子分(pctl×25) | 21.81 | 11.97 |
| 池内排名(95 人) | 第 13 名 | 第 50 名附近 |

**评价**:跨量级可比、天然消截顶,排序产品最好用;但依赖平台样本量(instagram 即不可用),
且数值含义"相对位次"≠"真实互动率",单独作为 0号语义件不达标。

---

## 5. 三选项横评与建议

| 维度 | A 评论加权 | B evidence 实算 | C 平台分位 |
|---|---|---|---|
| 语义贴合"真实互动率" | 中(α 拍脑袋) | **高(无自由参数,可审计到单条)** | 低(是位次不是率) |
| 法定样本现可算 | 可(3 条 raw) | 可演算,**正式需 evidence 回填+重抓** | 可(tiktok n=95) |
| 跨平台覆盖 | ig 大缺口 | 同左,依赖抓取 | ig/x/media 不可用 |
| 区分 josiah/frank | ✓ 2.1× | ✓ 1.7×(pooled) | ✓ 39 个分位点 |
| 抗刷量/爆款偏置 | 弱 | 中(置顶过滤后强) | 强(分位压缩极值) |

**建议:B 为法定主口径,C 作为 B 之上的展示层衍生列;A 降级为 B 的敏感性检验,不入法定。**
理由:0号是"语义件",法定口径必须无自由参数、可审计、单位自洽——只有 B 满足;
C 解决的是消费端(排序/截顶)问题,应消费 B 的产出而非另立数据源;A 的 α 永远吵不出结果。

落列设计(影子列,全部新增、零既有写点触碰):

```
real_er            numeric(8,5)   -- 统一 0~1 小数,杜绝百分数/倍数混存
real_er_method     text           -- 'evidence_pooled_v1'
real_er_sample_n   smallint       -- 实际参与条数(<5 视为低置信)
real_er_window     text           -- 如 'latest10_nonpinned'
real_er_pctl       numeric(6,4)   -- 同平台 percent_rank(选项C 衍生列)
real_er_computed_at timestamptz
```

## 6. 迁移路径(影子列 → 对照期 → 裁决切换)

1. **影子列期(T0,~1 周)**:加 `real_er_*` 列(可空,无默认值,不触发任何读路径);
   evidence 数据工程:① raw_platform_data.videos 物化进 vkpi_kol_video_evidence(saves 列补缺评估)
   ② 重抓 max_posts=10 + 置顶/陈旧过滤(josiah 即典型病例);法定样本 3462/3450 先行,
   验收线:real_er(josiah) > real_er(frank),且两值均落 tiktok P25~P99 带内。
2. **对照期(T0+1~T0+3 周)**:real_er 与 engagement_rate 双轨展示(real_ 列只读消费);
   每周出 delta 报告:符号翻转数(预计 frank 类"假高 ER"批量现形)、子分截顶解除数、
   团队人工抽检 20 账号盲评一致率;期间 viltrox_fit_score 与 rule_v0 **冻结不动**。
3. **裁决切换(T0+3 周后)**:裁决会三选一:
   a) real_er 升格为评分输入(新 scoring 版本号 rule_v1,经 ScoringRegistry 注册,绝不原地改 rule_v0);
   b) 仅展示层切换,评分继续用旧列;c) 退回延长对照。
   任何路径下旧 engagement_rate 列保留只读,单位翻转 bug 以"废弃标注"处理而非原地修数。

---

## 附录:复算口径(全部只读)

```sql
-- 前值
SELECT id, followers, avg_views, avg_likes, avg_comments, avg_shares,
       engagement_rate, viltrox_fit_score, video_evidence_count
FROM vkpi_kol_pool WHERE id IN (3450, 3462);

-- evidence 缺口确认(两人均 0 行)
SELECT kol_pool_id, COUNT(*) FROM vkpi_kol_video_evidence
WHERE kol_pool_id IN (3450,3462) GROUP BY 1;

-- 选项C 分位(tiktok n=95)
WITH base AS (
  SELECT id, avg_likes::numeric/NULLIF(avg_views,0) AS view_er
  FROM vkpi_kol_pool
  WHERE platform='tiktok' AND duplicate_of_id IS NULL
    AND avg_views>0 AND avg_likes IS NOT NULL)
SELECT id, view_er, percent_rank() OVER (ORDER BY view_er) FROM base;
```

选项A/B 的 per-post 数值取自 raw_platform_data.videos 的
playCount/diggCount/commentCount/shareCount/collectCount 逐条代入公式,
原始六条样本已全文列于 §1.1,任何人可手算复核。

— 完(0号 Real ER spec,2026-06-12)
