# GTM-1 实施规格:总脑页面 + 纯读 GTM Plan Preview(2026-07-07)

> 战略见姊妹篇《P2G 战略蓝图 v0.2》。本文=可直接开工的施工规格。红线:零写库、零 LLM、零采集、零复用带副作用的 GET(已证实 marketing-brain/daily 与 market/trends 的 GET 有隐藏写入,一律不碰,全部新建纯读聚合)。

## 一、页面:GTM Command|上市增长指挥图

位置:cockpit 新 nav `gtmCommand`(manager-only,权限走 usePermissions boardLevel)。**不叫分析面板**。五个区块(显示层宪法约束,只吃 public_plan):

1. **主判断(Thesis)**:该不该推/优先押哪个市场/主打人群/走曝光·转化·素材·渠道铺货哪条主线——四行结论+置信徽章+一句 decision_basis_summary
2. **市场预判(Forecast)**:7/14/30 天条件化预判卡(强制模板:预判/依据+置信/触发加码/撤退条件),绝无绝对化表述
3. **增长路线图(Roadmap)**:W1 / W2-4 / M2-3 三段,每段列 KOL/Dealer/官号/自媒体/独立站怎么配合
4. **今日行动(Next Actions)**:找哪类 KOL/推哪些 Dealer/哪些号发什么/哪些失败任务重试/哪些项目观察或暂停——每条带六要素(原因/证据摘要/成本/风险/预计收益/人审按钮占位,v1 按钮 disabled 标"GTM-3 接线")
5. **复盘学习(Learning Digest)**:本轮验证了什么/哪些内容风格有效/哪些渠道不值/下次推荐怎么变

顶部一句话:「GTM Command 把产品、市场、KOL、渠道和历史结果合成作战路线。」外部信号来源在卡片脚注诚实标注(缓存/计划/待接入),不宣传全量实时。

## 二、Endpoint 合约(两个,全新建,纯读)

### A. `GET /api/admin/vkpi/market-brain/summary`
无参。返回五卡数据,前端一次请求:
```
{ weekly_signals:        { items: [{signal, kind, freshness, sample_size, confidence}], sources_note },
  product_opportunities: { items: [{sku, market, persona, content_angle, opportunity_score, basis}] },
  recommended_actions:   { items: [{action, reason, evidence_summary, cost_note, risk, expected_gain, ref}] },
  strategy_defaults:     { simulate_entry: {sku_hint, budget_hint}, note },
  learning_digest:       { validated: [], effective_styles: [], dropped_channels: [], next_change, honesty_note } }
```
数据源映射(全部既有纯读函数,禁带副作用路径):weekly_signals←brand_pulse+category_tracks+market_voice(内部信号,标注"外部雷达待接");product_opportunities←category_tracks top+sku_performance+persona;recommended_actions←action inbox suggested+gifted_funnel 超期+needs-analysis;learning_digest←prediction_ledger+weekly_scorecard+miss_review 只读端。

### B. `GET /api/admin/vkpi/market-brain/gtm-plan/preview?sku=&country=&budget_usd=&goal=&window_days=`
- 参数:sku 必填;country(ISO,v1 只影响受众地域过滤与 Dealer 段占位说明);budget_usd 默认 3000;goal ∈ exposure|conversion|content|channel(v1 影响 budget_mix 模板选择与 success_metrics 侧重);window_days 默认 30
- 返回:`{ public_plan: {...11段}, meta: {generated_at, coverage, data_gaps} }`;**private_evidence 不出现在此端点**(留 `?debug=1` + owner 权限的分支,v1 可不实现)
- 11 段字段定义:
  - `thesis`:{go_nogo, market, persona, mainline, confidence, basis_summary}
  - `forecast`:[{horizon_days, statement, signals_summary, confidence, escalate_if, retreat_if}](条件化模板强制)
  - `market_opportunity`:←category_tracks 该 SKU 焦段/品类赛道行 + industry_benchmark 焦段格局
  - `kol_candidates`:←launch_assembly._candidate_pool + signature/forecast/rate 摘要(风险只出标签不出黑名单)
  - `dealer_targets`:v1 诚实占位 {status:"data_missing", note:"Dealer 表 0 行,GTM-2 导入后激活"}
  - `official_channel_actions`:←employee_channels+channel_metrics 最近快照(哪个号近期什么形式表现好→发什么)
  - `shopify_indie_site_actions`:v1 模板建议(短链/落地页/佣金码占位)+ 本地无订单诚实标注
  - `content_angles`:←persona.promotion_angles + creative_segments 高表现段 + 规则库三模板(awe/身份/before-after)
  - `budget_mix`:三档模板(保守 70/10/20 平衡 50/50 激进 40/60)+ strategy_sim 三方案摘要引用
  - `risks` / `data_gaps`(coverage 审计口径)/ `action_inbox_items`(materialize 预览,不落库)/ `success_metrics`(六段漏斗阈值,规则库口径带 confidence)

## 三、strategy_sim 补参(本波顺带)

`simulate()` 增加 `country`、`goal` 两个可选参(country→候选池受众地域过滤;goal→推荐排序权重:exposure 按触达/conversion 按 CPM 效率/content 按内容产能),向后兼容默认 None。

## 四、前端文件

新建:`cockpit/pages/GtmCommandPage.tsx` + `services/vkpi/gtmCommand-api.ts`。接线(收口者做):navItems +`gtmCommand`(Compass 图标)、CockpitApp lazy+boards+branch、路由注册表 +`vkpi_market_brain`。复用:StrategySimPanel(嵌第 4 卡旁)/IntelligenceCard 样式基因。

## 五、验收标准

1. 真 SKU(AF-85MM-F14-PRO-FE)+US+$3000+conversion → 11 段全出,每段有内容或诚实 data_missing;
2. **零副作用断言**:preview 调用前后,全库行数快照逐表相等(冒烟脚本内置该断言);
3. 响应无 private 字段泄漏(score 明细/原始评论/竞品笔记关键词扫描=0);
4. 预判段每条含 escalate_if+retreat_if 且无绝对化措辞;
5. summary 一次请求 <3s;三闸+千行卫兵+红线照旧。

## 六、风险

- 聚合端点易发胖 → 每段独立 try/except,单段失败诚实 {status:error} 不拖垮整卡
- goal/country v1 只做轻影响,别让参数假装比数据聪明(货不对板宁可 note 说明)
- 旧 vkpi_launch/campaign_plan 骨架有 LLM 路径,v1 只借数据形状不调用

## 七、开发顺序

①gtm_plan_preview 域(纯聚合)→ ②summary 域 → ③两路由 → ④GtmCommandPage 五卡 → ⑤strategy_sim 补参 → ⑥收口接线+零副作用冒烟+提交。预计一个施工波(4 代理:域×2/页/补参+规则库表)。
