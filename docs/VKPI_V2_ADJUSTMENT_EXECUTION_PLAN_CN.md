# V-KPI v2.0 调整执行总文档

更新时间：2026-05-08  
适用工程：Viltrox Marketing / V-KPI  
来源文档：`/Users/bibiboer/Downloads/V-KPI-MASTER-PLAN-v2.0.md`  
执行原则：不推倒现有 V-KPI；在现有 Execution 主链路上加入 Product Analysis、行业数据、自建 KOL 池、自动化预埋与可控抓取。

---

## 1. 最终系统定位

V-KPI 不再只是 KPI 页面，也不是单独的 KOL CRM。

最终定位：

```text
V-KPI Marketing Operating System
= Viltrox 内部 Marketing ERP
+ KOL CRM
+ Product Analysis
+ Industry Data / Social Listening
+ Attribution OS
+ KPI Ledger
+ Evidence-first Dashboard
+ Reports / Audit
```

系统不包含：

```text
V-OS
公开 Creator Portal
投稿系统
积分系统
盲盒
对外用户社区
```

---

## 2. 三大业务板块

### 2.1 V-KPI Execution

这是当前已经在做的主系统。

负责：

```text
员工
KOL
项目
消息
样品 / 发货
短链
点击
Shopify / Amazon 归因
成本
KPI
Dashboard
Reports
Evidence
Audit
```

核心链路：

```text
员工
→ KOL 搜索 / Claim
→ Project
→ Message Timeline
→ Terms / Deliverables
→ Samples / Costs
→ Short Links
→ Clicks
→ Shopify / Amazon Orders
→ Attribution Ledger
→ Cost Ledger
→ KPI Ledger
→ Metric Lineage
→ Dashboard / Reports
→ Evidence / Audit
```

### 2.2 Product Analysis

新品上线前的作战入口。

负责：

```text
Launch Brief
Market Pulse
Competitor Watch
KOL Pool 浏览
KOL Recommender
Budget Scenarios
Campaign Plan
Source Evidence
Feedback Learning
```

核心目的：

```text
在真正开始联系 KOL 前，先判断：
1. 这个产品该推给谁
2. 哪个平台优先
3. 哪类内容更容易卖
4. 哪些 KOL 值得进入合作流程
5. 预计成本和风险是什么
```

### 2.3 Industry Data

独立板块，对标 Socialinsider，但增加 Viltrox 独有的 KOL 和销售归因能力。

负责：

```text
行业监控项目
公司账号监控
竞品账号监控
KOL 账号监控
跨平台 KPI
帖子级数据
内容主题 / Pillars
Sentiment
Topic Tracking
自动报告
PDF / CSV / XLSX 导出
```

核心目的：

```text
每天知道：
1. 我们的产品在各平台被怎么提及
2. 竞品账号和竞品产品表现如何
3. 哪些内容正在变热
4. 哪些未联系 KOL 值得进入候选池
5. 公司账号和 KOL 合作账号对销售贡献如何
```

---

## 3. 共享底层能力

三大板块不能各自重复造数据，统一共享底层。

```text
KOL Pool
Crawler / Apify / Official APIs
Feature Store
Outcome Collector
LLM Gateway
Scoring Engine
AB Experiments
Budget Switches
Audience / Network Graph
Training Data Export
Audit
Report Renderer
```

---

## 4. 当前代码接入策略

不重写现有 V-KPI。

当前已有模块继续保留：

```text
backend/app/api/routers/vkpi.py
backend/app/services/vkpi/workflow.py
backend/app/services/vkpi/kol_claims.py
backend/app/services/vkpi/link_center.py
backend/app/services/vkpi/attribution.py
backend/app/services/vkpi/costs.py
backend/app/services/vkpi/kpi_ledger.py
backend/app/services/vkpi/metric_lineage.py
backend/app/services/vkpi/reports.py
backend/app/services/vkpi/exports.py
backend/app/services/vkpi/audit.py
frontend/src/components/vkpi/*
frontend/src/services/vkpi*.ts
```

新增模块只做扩展：

```text
backend/app/services/vkpi/product_analysis.py
backend/app/services/vkpi/industry_data.py
backend/app/services/vkpi/kol_pool.py
backend/app/services/vkpi/feature_store.py
backend/app/services/vkpi/outcome_collector.py
backend/app/services/vkpi/llm_gateway.py
backend/app/services/vkpi/ab_experiments.py
backend/app/services/vkpi/training_data_export.py
backend/app/services/vkpi/platform_crawl_settings.py
backend/app/services/vkpi/audience_graph.py
backend/app/services/vkpi/scoring/
```

### 4.1 新模块调用现有 V-KPI 的契约

新增模块不能绕开现有业务内核，必须通过 adapter 调用当前 V-KPI service。

```text
Product Analysis → 现有 V-KPI
recommendation.claim()
→ 调用 kol_claims lookup / claim 相关 service，完成真实 KOL 主档和 claim 归属

recommendation.create_project()
→ 调用 workflow project create / stage 相关 service，创建真实项目并进入项目跟进

recommendation.create_link()
→ 调用 link_center 创建真实短链，绑定 KOL / Project / Product / Staff

recommendation.feedback()
→ 写 recommendation_feedback，同时调用 outcome_collector 记录 shortlisted / rejected / claimed
```

```text
Industry Data → 现有 V-KPI
industry_account.link_kol()
→ 调用 KOL 主档 upsert / merge / alias 逻辑，避免重复 KOL

snapshot.enrich_with_attribution()
→ 读取 attribution / link / project / cost / KPI 数据，补充 V-KPI attributed GMV / orders / project_count

industry_alert.notify()
→ 调用 alerts / audit，生成真实提醒和审计记录
```

```text
KOL Pool → 现有 V-KPI
kol_pool.import_candidate()
→ 只写候选池，不自动 claim

kol_pool.promote_to_v_kpi()
→ 调用 KOL 主档 upsert，再允许 claim / project

kol_pool.merge_alias()
→ 只合并 alias 和 dedup 关系，不删除历史项目和归因
```

实现要求：

```text
Phase 0A 先建 adapter 函数和契约。
如果真实函数名不一致，adapter 内部处理，不把调用细节散落到页面和其他 service。
所有跨模块动作都写 business audit。
```

---

## 5. 开关策略

所有高成本、高风险、平台依赖能力必须开关化。

### 5.1 管理层开关

新增统一设置：

```text
抓取总开关
平台抓取开关
Apify 开关
官方 API 开关
粉丝图谱开关
互动图谱开关
LLM 推荐解释开关
预算自动分配开关
ML 评分开关
训练数据导出开关
自动报告开关
每日 8 点同步开关
每日优质内容推送开关
```

### 5.2 平台范围

支持平台：

```text
YouTube
Instagram
TikTok
Facebook
Reddit
X
小红书
Bilibili
LinkedIn
Other
```

### 5.3 抓取额度

每个平台都必须可控：

```text
每日账号数量
每账号帖子数量
抓取频率
是否只抓未联系 KOL
是否抓公司账号
是否抓竞品账号
是否抓候选 KOL
是否抓评论
是否抓粉丝
月预算上限
单平台预算上限
失败重试次数
```

默认策略：

```text
公司账号：每天 08:00 中国时间同步一次
竞品账号：每天 08:00 中国时间同步一次
员工候选内容：每天 08:00 中国时间生成每人前 100 条优质内容
粉丝图谱：默认关闭
预算自动分配：默认关闭
ML 真实评分：默认关闭
LLM 解释：默认可开启，但必须记录费用
```

### 5.4 Settings 存储表

开关必须落库，不能只写前端状态。

```text
vkpi_feature_flags
```

字段：

```text
flag_key
enabled
description
updated_by
updated_at
metadata_json
```

```text
vkpi_platform_crawl_settings
```

字段：

```text
platform
crawl_enabled
daily_account_limit
posts_per_account
crawl_comments
crawl_followers
crawl_audience_graph
only_uncontacted_kols
include_company_accounts
include_competitor_accounts
include_candidate_kols
monthly_budget_usd
failure_threshold
last_test_status
last_test_at
updated_by
updated_at
metadata_json
```

```text
vkpi_budget_settings
```

字段：

```text
budget_key
monthly_limit_usd
current_month_spent
alert_threshold_pct
enabled
updated_by
updated_at
metadata_json
```

默认值：

```text
所有付费抓取默认关闭。
粉丝图谱默认关闭。
预算自动分配默认关闭。
ML 评分默认关闭。
LLM 可配置但不读取完整 key。
```

---

## 6. 自学习架构预埋

自学习现在必须预埋，但不急着训练真实模型。

### 6.1 必建 Outcome 标签体系

用于记录推荐后的实际结果。

表：`vkpi_recommendation_outcomes`

关键字段：

```text
recommendation_id
kol_id
launch_id
was_shortlisted
was_rejected
was_claimed
project_created
outreach_sent
reply_received
agreement_reached
content_published
order_attributed
attributed_clicks
attributed_orders
attributed_gmv
attributed_cost
computed_roi
recommended_at
first_action_at
outcome_finalized_at
feature_snapshot
scoring_breakdown
model_version
display_position
display_context
```

规则：

```text
推荐时必须冻结 feature_snapshot。
每个漏斗节点必须单独打时间戳。
训练数据只使用 outcome_finalized_at 之后的数据。
```

### 6.2 Feature Store

文件：`backend/app/services/vkpi/feature_store.py`

接口：

```python
snapshot_features(recommendation_id, kol_id, launch_id) -> dict
get_features_at_time(kol_id, timestamp) -> dict
list_feature_names() -> list[str]
register_feature(name, version, source) -> None
```

Phase 0 实现：

```text
读取当前 KOL Pool / Industry Snapshot / V-KPI 历史数据
生成 JSON snapshot
写入 recommendation outcome
```

后续升级：

```text
时序特征
内容 embedding
历史转化率
平台趋势
相似 KOL 表现
```

### 6.3 Scoring Strategy

目录：`backend/app/services/vkpi/scoring/`

```text
base.py
rule_v0.py
rule_v1.py
vector_llm.py
ml_model.py
```

Phase 0 使用：

```text
rule_v0
```

评分维度：

```text
产品匹配
平台匹配
内容匹配
互动质量
最近活跃
风险
```

后续可升级：

```text
rule_v1
vector_llm
ml_model
```

调用方永远只调用 scoring registry，不直接调用具体模型。

### 6.4 AB Experiments

表：

```text
vkpi_scoring_experiments
vkpi_recommendation_assignments
```

用途：

```text
rule_v0 vs rule_v1
rule_v1 vs vector_llm
rule_v1 vs ml_model
```

Phase 0 默认：

```text
100% rule_v0
```

### 6.5 Behavior Ledger

训练需要记录员工行为上下文。

必须记录：

```text
actor_id
entity_type
entity_id
action_type
timestamp
match_score
display_position
card_strengths
card_concerns
filters_used
view_scope
recommendation_strategy
```

原因：

```text
员工选择某个 KOL，不一定因为 KOL 最好，也可能因为它排在第一个。
训练时必须知道当时展示位置和展示内容。
```

### 6.6 ML Interface

预留接口：

```text
POST /api/internal/vkpi/ml/score
```

Phase 0 返回：

```json
{
  "score": null,
  "fallback": "rule_v0",
  "reason": "ml_not_enabled"
}
```

Phase 5 才接真实模型服务。

### 6.7 Training Data Export

文件：

```text
backend/app/jobs/vkpi/training_data_export.py
```

功能：

```text
recommendations
+ outcomes
+ feature_snapshot
+ scoring_breakdown
+ behavior context
→ parquet/csv/jsonl
```

Phase 0 可导出空数据集，但格式必须固定。

---

## 7. 自建 KOL Pool

### 7.1 数据来源

```text
Apify 历史数据
YouTube Data API
Instagram Apify
TikTok Apify
Facebook Apify
Reddit API / Apify
X API / Apify
小红书抓取
Bilibili 抓取
B&H Photo 种子
Shopify 评论提及
竞品合作 KOL
员工手动录入
CSV 导入
```

### 7.1.1 Apify 历史数据来源和字段映射

Apify 数据来源分三类：

```text
Apify Console 现有 Datasets
Apify API 按 dataset_id 拉取 items
本地 CSV / JSON / JSONL 导入
```

导入入口：

```text
POST /api/admin/vkpi/product-analysis/kol-pool/import
POST /api/admin/vkpi/industry-data/projects/{id}/accounts/import
```

基础字段映射：

```text
apify.username / handle           → kol_pool.handle
apify.url / profileUrl            → kol_pool.profile_url
apify.fullName / name             → kol_pool.display_name
apify.biography / bio             → kol_pool.bio
apify.profilePicUrl / avatar      → kol_pool.avatar_url
apify.followersCount              → kol_pool.followers
apify.followsCount                → kol_pool.following
apify.postsCount                  → kol_pool.posts_count
apify.latestPosts                 → industry_posts / kol_content_snapshots
apify.email / publicEmail         → kol_pool.email
apify.externalUrl                 → kol_pool.other_contacts
raw item                          → raw_platform_data
```

规则：

```text
没有 dataset_id 时支持文件导入。
导入前先 preview 字段映射。
导入后只进入 KOL Pool 或 Industry Data，不自动 claim。
所有导入记录保留 raw payload 和 source_ref。
```

### 7.2 KOL 画像字段

```text
platform
handle
profile_url
display_name
avatar_url
bio
country
language
email
other_contacts
followers
following
posts_count
avg_views
avg_likes
avg_comments
avg_shares
engagement_rate
primary_topic
secondary_topics
content_style
production_quality
audience_estimated
brand_collaborations_observed
viltrox_fit_score
viltrox_fit_reason
potential_concerns
recommended_product_lines
last_seen_at
sync_status
raw_platform_data
```

禁止行为：

```text
没有抓到真实粉丝数时，不显示 0。
没有真实头像时，不显示假头像。
没有联系方式时，显示“未抓到公开联系方式”。
```

### 7.3 去重策略

去重键：

```text
platform + handle
platform_user_id
profile_url normalized
email
cross-platform alias
manual merge
```

目标：

```text
一个真实 KOL 可以有多个平台账号，但 Claim 归属必须统一可见。
```

---

## 8. 粉丝图谱 / 关系图谱

必须做，但默认关闭，并分级启用。

### 8.1 L1 内容相似图谱

低成本，优先做。

依据：

```text
主题
标签
产品线
内容类型
平台
互动率
历史合作品牌
```

### 8.2 L2 互动图谱

中成本，可开关。

依据：

```text
互相提及
评论互动
共同品牌合作
共同内容主题
共同受众语言/地区
```

### 8.3 L3 粉丝重叠图谱

高成本，高合规风险，默认关闭。

原则：

```text
只抓公开可见数据。
优先保存聚合相似度，不保存粉丝个人明细。
必须有审计日志。
必须有平台预算上限。
```

表：

```text
vkpi_kol_network
vkpi_kol_audience_overlap
vkpi_kol_signals
```

---

## 9. Product Analysis 数据结构

Phase 0 至少建骨架。

### 9.1 Launch

表：

```text
vkpi_product_launches
```

字段：

```text
launch_id
name
product_sku
product_name
category
target_market
target_platforms
target_audience
competitor_products
launch_window_start
launch_window_end
budget_range
goals
constraints
created_by
status
metadata_json
```

### 9.2 Market Scan

表：

```text
vkpi_market_scan_runs
vkpi_market_sources
vkpi_market_mentions
vkpi_competitor_products
vkpi_competitor_content
```

Phase 0：

```text
只建表和接口。
没有真实扫描结果时显示空态。
```

### 9.3 Recommendations

表：

```text
vkpi_kol_recommendation_runs
vkpi_kol_recommendations
vkpi_recommendation_explanations
vkpi_recommendation_feedback
```

推荐来源：

```text
KOL Pool
Industry Data
历史 V-KPI 项目
Apify 数据
手动导入
```

推荐后动作：

```text
shortlist
reject
claim
create project
add to campaign
request more evidence
```

---

## 10. Industry Data 数据结构

### 10.1 监控项目

表：

```text
vkpi_industry_projects
```

用途：

```text
公司账号监控
竞品账号监控
新品 launch scan
临时话题追踪
```

### 10.2 监控账号

表：

```text
vkpi_industry_accounts
```

字段：

```text
project_id
platform
handle
display_name
avatar_url
profile_url
account_role
brand_group
linked_kol_pool_id
crawl_enabled
crawl_frequency
last_crawled_at
sync_status
```

### 10.3 每日快照

表：

```text
vkpi_industry_account_snapshots
```

核心 KPI：

```text
followers
followers_growth_24h
followers_growth_30d
followers_growth_pct_30d
posts
posts_30d
avg_posts_per_day
views
views_30d
likes
comments
shares
saves
engagement_total_30d
engagement_rate
avg_engagement_rate_by_followers
avg_engagement_per_day
avg_eng_rate_by_views
avg_eng_rate_by_impressions
avg_eng_rate_by_reach
avg_views
reach_total_30d
impressions_total_30d
reels_views_30d
top_post_views
day_with_most_posts
hour_with_most_posts
day_with_highest_engagement
hour_with_highest_engagement
avg_hashtags_per_post
avg_video_duration_seconds
estimated_organic_value
vkpi_attributed_gmv
vkpi_attributed_orders
vkpi_linked_kol_count
vkpi_project_count
```

说明：

```text
Phase 0A 必须先建齐字段，允许值为空。
未接平台 API 时显示“待同步 / 未配置”，不能显示假 0。
Socialinsider 类 PDF 报告依赖这些字段，不能等 Phase 3 再回头改表。
```

### 10.4 帖子级数据

表：

```text
vkpi_industry_posts
```

字段：

```text
platform_post_id
post_url
thumbnail_url
title
caption
published_at
views
likes
comments
shares
saves
hashtags
mentions
detected_products
content_pillar
sentiment
raw_platform_data
```

---

## 11. 预算自动分配

现在做开关和预算场景，不做最终自动决策。

表：

```text
vkpi_campaign_budget_scenarios
vkpi_campaign_recommendation_sets
```

Phase 0 功能：

```text
管理层可开启/关闭预算优化。
可录入预算上限。
可生成预算场景草案。
不会自动改项目预算。
不会自动给员工下发任务。
```

后续 Phase 4：

```text
Expected Value
Greedy allocation
Diversity constraints
Risk controls
Scenario comparison
```

---

## 12. LLM Gateway

大模型现在可以预埋和少量启用，但必须受控。

表：

```text
vkpi_llm_calls
```

记录：

```text
provider
model
purpose
prompt_hash
input_tokens
output_tokens
cost
latency_ms
status
fallback_used
created_by
created_at
```

LLM 可做：

```text
KOL 画像总结
产品匹配解释
风险说明
联系话术草稿
周报润色
内容主题分类
```

LLM 禁止：

```text
凭空创造数据
替代 evidence
自动决定预算
自动确认 KPI
自动覆盖员工记录
```

---

## 13. API 调整清单

### 13.1 Product Analysis API

```text
GET    /api/admin/vkpi/product-analysis/launches
POST   /api/admin/vkpi/product-analysis/launches
GET    /api/admin/vkpi/product-analysis/launches/{id}
PATCH  /api/admin/vkpi/product-analysis/launches/{id}
DELETE /api/admin/vkpi/product-analysis/launches/{id}

POST   /api/admin/vkpi/product-analysis/scans/run
GET    /api/admin/vkpi/product-analysis/scans

GET    /api/admin/vkpi/product-analysis/kol-pool
POST   /api/admin/vkpi/product-analysis/kol-pool/import
POST   /api/admin/vkpi/product-analysis/recommendations/run
GET    /api/admin/vkpi/product-analysis/recommendations
POST   /api/admin/vkpi/product-analysis/recommendations/{id}/shortlist
POST   /api/admin/vkpi/product-analysis/recommendations/{id}/reject
POST   /api/admin/vkpi/product-analysis/recommendations/{id}/claim
POST   /api/admin/vkpi/product-analysis/recommendations/{id}/create-project
POST   /api/admin/vkpi/product-analysis/recommendations/{id}/feedback

POST   /api/admin/vkpi/product-analysis/budget-scenarios
GET    /api/admin/vkpi/product-analysis/budget-scenarios
```

### 13.2 Industry Data API

```text
GET    /api/admin/vkpi/industry-data/projects
POST   /api/admin/vkpi/industry-data/projects
GET    /api/admin/vkpi/industry-data/projects/{id}
PATCH  /api/admin/vkpi/industry-data/projects/{id}
DELETE /api/admin/vkpi/industry-data/projects/{id}

GET    /api/admin/vkpi/industry-data/projects/{id}/accounts
POST   /api/admin/vkpi/industry-data/projects/{id}/accounts
POST   /api/admin/vkpi/industry-data/projects/{id}/accounts/import
POST   /api/admin/vkpi/industry-data/accounts/{id}/refresh
POST   /api/admin/vkpi/industry-data/accounts/{id}/link-kol

GET    /api/admin/vkpi/industry-data/projects/{id}/cross-platform
GET    /api/admin/vkpi/industry-data/projects/{id}/posts
GET    /api/admin/vkpi/industry-data/accounts/{id}
GET    /api/admin/vkpi/industry-data/accounts/{id}/timeseries
GET    /api/admin/vkpi/industry-data/projects/{id}/alerts
POST   /api/admin/vkpi/industry-data/reports/generate
```

### 13.3 Automation API

```text
GET    /api/admin/vkpi/automation/outcomes/{recommendation_id}
GET    /api/admin/vkpi/automation/experiments
POST   /api/admin/vkpi/automation/experiments
PATCH  /api/admin/vkpi/automation/experiments/{id}/status
GET    /api/admin/vkpi/automation/models
POST   /api/admin/vkpi/automation/models/{version}/activate
GET    /api/admin/vkpi/automation/llm-stats
POST   /api/admin/vkpi/automation/training-data/export
GET    /api/admin/vkpi/automation/training-data/latest
```

### 13.4 Settings API

```text
GET    /api/admin/vkpi/settings/feature-flags
PATCH  /api/admin/vkpi/settings/feature-flags
GET    /api/admin/vkpi/settings/platform-crawl
PATCH  /api/admin/vkpi/settings/platform-crawl
GET    /api/admin/vkpi/settings/budgets
PATCH  /api/admin/vkpi/settings/budgets
```

---

## 14. 前端调整清单

新增一级入口：

```text
产品作战
行业数据
```

### 14.1 产品作战页面

```text
Launch Brief
KOL 推荐
推荐结果
预算场景
Campaign 草案
推荐反馈
```

### 14.2 行业数据页面

```text
监控项目
账号矩阵
跨平台 KPI
帖子分析
竞品趋势
内容主题
自动报告
```

Phase 0B 只实现上面 7 项基础入口。

以下能力保留导航/路由契约，Phase 3 再实装：

```text
Sentiment
Topic Tracking
Query Builder
AI Companion
```

### 14.3 系统设置页面

必须收敛掉废话，只保留可操作设置：

```text
API 是否工作
授权账户
SKU / 产品成本录入
员工授权列表
平台抓取开关
预算优化开关
粉丝图谱开关
LLM / ML 开关
```

Platform API Keys 管理：

```text
YouTube API Key
Apify API Token
OpenAI API Key
Claude API Key
Gemini API Key
X / Reddit / Facebook 相关平台凭据
```

规则：

```text
只能写入、测试、mask 显示。
不能在前端读取完整 key。
保存后立即提供 Test Connection。
显示示例：AIza••••••••••3RA。
本地开发可写入环境变量 / 加密配置；生产必须接 Secret Manager。
所有新增、更新、测试、禁用都写 audit log。
```

### 14.4 空态规则

```text
无真实数据：显示待同步 / 未配置 / 无结果。
禁止假头像。
禁止假 KOL。
禁止假 GMV。
禁止假粉丝数 0。
禁止假视频缩略图。
```

---

## 15. 权限和数据范围

角色：

```text
Operator
Lead
Admin
Finance
Viewer
```

范围：

```text
self
team
all
```

敏感字段：

```text
联系方式
消息全文
现金费用
镜头内部成本
Shopify 订单详情
API Key
导出文件
训练数据
```

规则：

```text
员工只能看自己范围。
管理层可切换员工视角。
Finance 可看成本和导出。
API Key 只能写入、测试、mask 显示。
粉丝图谱和抓取设置只允许管理层操作。
```

---

## 16. 审计要求

必须审计：

```text
KOL claim / release / reassign
recommendation shortlist / reject / claim
project create / stage / delete
message view / contact view
cost add / edit / void
link create / pause / archive
manual attribution
reconciliation
feature flag change
platform crawl setting change
LLM setting change
ML model activate
export download
training data export
```

---

## 17. 成本控制

### 17.1 Apify

控制项：

```text
每日最大 run 数
每平台最大 run 数
每账号最大帖子数
是否抓评论
是否抓粉丝
月预算
失败自动停止阈值
```

### 17.2 LLM

控制项：

```text
每日 token 上限
每模型预算
每功能预算
fallback 模型
是否允许自动摘要
是否允许自动推荐解释
```

### 17.3 图谱

控制项：

```text
L1 内容相似默认开
L2 互动图谱手动开
L3 粉丝重叠默认关
```

---

## 18. 每日 8 点同步设计

时区：Asia/Shanghai

每日任务：

```text
08:00 同步公司账号数据
08:00 同步竞品账号数据
08:00 同步已开启监控 KOL
08:00 为每个员工生成前 100 条优质未联系内容/KOL
08:00 生成异常提醒
08:00 生成行业数据快照
```

员工每日推荐限制：

```text
只推荐未联系过 KOL。
排除已 claim 且未释放 KOL。
排除黑名单。
优先相关产品 / 同级别产品 / 目标受众匹配。
```

---

## 19. Smoke Tests

每轮必须跑：

```text
py_compile
npm run build
V-KPI 14/14 smoke
300 并发 smoke
测试记录残留扫描
```

新增 smoke：

```text
smoke_vkpi_product_launch.py
smoke_vkpi_kol_pool_import.py
smoke_vkpi_recommendation_outcome.py
smoke_vkpi_feature_store.py
smoke_vkpi_scoring_rule_v0.py
smoke_vkpi_industry_project.py
smoke_vkpi_industry_account_snapshot.py
smoke_vkpi_platform_crawl_settings.py
smoke_vkpi_llm_gateway.py
smoke_vkpi_training_export.py
smoke_vkpi_alert_engine.py
smoke_vkpi_pdf_report_render.py
```

说明：

```text
smoke_vkpi_alert_engine.py 在 Phase 2 开始强制执行。
smoke_vkpi_pdf_report_render.py 在 Phase 3 PDF 行业报告模板接入后强制执行。
Phase 0A 先保证新增 Product / Industry / Automation 骨架 smoke 通过。
```

验收标准：

```text
所有接口无 500。
未配置平台显示未配置。
未开启抓取不产生外部费用。
空数据不显示假数据。
推荐到 claim/project 能打 outcome。
设置变更写 audit。
测试数据可清理。
```

---

## 20. 阶段执行计划

### Phase 0A：架构骨架

目标：

```text
建表
注册 API
服务骨架
开关系统
Outcome / Feature / Scoring / LLM / AB / Training 预埋
```

不做：

```text
真实全平台抓取
真实 ML
真实预算自动分配
粉丝全量抓取
```

### Phase 0B：前端入口

目标：

```text
产品作战入口
行业数据入口
设置页开关
真实空态
无假数据
```

### Phase 1：数据接入

目标：

```text
Apify 历史数据导入
KOL Pool 可用
Industry Account Snapshot 可用
每日 8 点同步任务可用
员工每日 100 条候选内容可用
```

### Phase 2：可控抓取和图谱

目标：

```text
YouTube / Instagram / TikTok / Facebook / Reddit / X / 小红书 / Bilibili 分平台开关
L1 内容相似图谱
L2 互动图谱可选
L3 粉丝重叠图谱可选
```

### Phase 3：LLM 推荐解释

目标：

```text
KOL 画像总结
推荐理由
风险解释
联系话术
费用统计
```

### Phase 4：预算场景

目标：

```text
预算场景草案
Campaign Plan
Expected Value
管理层确认后执行
```

### Phase 5：真实 ML

前提：

```text
至少 3-6 个月 outcome 数据
至少 10 个完整 launch
足够推荐结果和后续销售数据
```

目标：

```text
P_reply
P_agree
P_publish
P_order
ROI prediction
AB 灰度
```

---

## 21. 当前第一轮建议

下一轮直接执行 Phase 0A。

### 21.1 Phase 0A Migration 拆分表

Phase 0A 不是 10 张表，而是用 10 个 migration 文件承载 32 张表骨架。

```text
migrations/037_vkpi_product_launches.sql
1. vkpi_product_launches
```

```text
migrations/038_vkpi_market_scan.sql
2. vkpi_market_scan_runs
3. vkpi_market_sources
4. vkpi_market_mentions
5. vkpi_competitor_products
6. vkpi_competitor_content
```

```text
migrations/039_vkpi_kol_pool.sql
7. vkpi_kol_pool
8. vkpi_kol_pool_aliases
9. vkpi_kol_pool_brand_links
10. vkpi_kol_embeddings
11. vkpi_content_pillars
```

```text
migrations/040_vkpi_kol_recommendations.sql
12. vkpi_kol_recommendation_runs
13. vkpi_kol_recommendations
14. vkpi_recommendation_explanations
15. vkpi_recommendation_feedback
```

```text
migrations/041_vkpi_industry_projects.sql
16. vkpi_industry_projects
```

```text
migrations/042_vkpi_industry_accounts.sql
17. vkpi_industry_accounts
```

```text
migrations/043_vkpi_industry_snapshots.sql
18. vkpi_industry_account_snapshots
```

```text
migrations/044_vkpi_industry_posts.sql
19. vkpi_industry_posts
20. vkpi_industry_post_metrics
21. vkpi_industry_post_tags
22. vkpi_industry_reports
```

```text
migrations/045_vkpi_automation_outcomes.sql
23. vkpi_recommendation_outcomes
24. vkpi_scoring_experiments
25. vkpi_recommendation_assignments
26. vkpi_llm_calls
27. vkpi_model_registry
```

```text
migrations/046_vkpi_settings.sql
28. vkpi_feature_flags
29. vkpi_platform_crawl_settings
30. vkpi_budget_settings
31. vkpi_training_exports
32. vkpi_behavior_ledger
```

规则：

```text
所有表 Phase 0A 建骨架。
后续 Phase 1-5 只加字段和索引，不推翻表名。
如果当前 SQLite 本地开发不支持 JSONB / UUID / TIMESTAMPTZ，要在 schema guard 里做兼容映射。
```

文件范围：

```text
migrations/037_vkpi_product_launches.sql
migrations/038_vkpi_market_scan.sql
migrations/039_vkpi_kol_pool.sql
migrations/040_vkpi_kol_recommendations.sql
migrations/041_vkpi_industry_projects.sql
migrations/042_vkpi_industry_accounts.sql
migrations/043_vkpi_industry_snapshots.sql
migrations/044_vkpi_industry_posts.sql
migrations/045_vkpi_automation_outcomes.sql
migrations/046_vkpi_settings.sql
backend/app/services/vkpi/product_analysis.py
backend/app/services/vkpi/industry_data.py
backend/app/services/vkpi/kol_pool.py
backend/app/services/vkpi/feature_store.py
backend/app/services/vkpi/outcome_collector.py
backend/app/services/vkpi/llm_gateway.py
backend/app/services/vkpi/ab_experiments.py
backend/app/services/vkpi/training_data_export.py
backend/app/services/vkpi/platform_crawl_settings.py
backend/app/services/vkpi/audience_graph.py
backend/app/services/vkpi/scoring/base.py
backend/app/services/vkpi/scoring/rule_v0.py
backend/app/api/routers/vkpi.py
scripts/smoke_vkpi_product_industry_phase0.py
```

第一轮验收：

```text
表可创建
API 可返回真实空态
开关可读写
rule_v0 可评分
feature snapshot 可生成
outcome 可记录
LLM gateway 不配置时返回 not_configured
training export 可生成空文件或空结果
py_compile 通过
npm build 不受影响
smoke 通过
300 并发 smoke 通过
测试记录清理为 0
```

---

## 22. 完成度口径

每轮输出以下表：

```text
1. V-KPI Execution 完成度
2. Product Analysis 完成度
3. Industry Data 完成度
4. KOL Pool 完成度
5. Automation / ML 预埋完成度
6. 抓取与成本控制完成度
7. 前端完成度
8. Smoke 完成度
9. 风险剩余
10. 下一轮任务
```

---

## 23. 不可违反规则

```text
不显示假数据。
不显示假头像。
不把未配置平台显示成 0。
不自动烧 Apify / LLM 费用。
不让员工看内部镜头成本。
不让前端读取完整 API Key。
不让员工越权看 team/all 数据。
不把推荐系统结果当成事实，只能作为建议。
不把 LLM 输出当 evidence。
不跳过备份。
不跳过 smoke。
```
