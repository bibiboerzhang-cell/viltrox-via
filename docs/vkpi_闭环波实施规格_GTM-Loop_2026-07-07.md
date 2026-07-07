# 闭环波实施规格(GTM-Loop = 原 GTM-3+4 合并提前,2026-07-07)

> 依据用户两份闭环裁决:①闭环=预判→行动→执行→记录→对答案→调整(不是预测能力,是"预测后有没有对答案")②闭环对象从 KOL 升级为「产品×市场×渠道×动作×结果」。
> 顺位裁决:**闭环波提前于 GTM-2(Channel Brain)**——理由:GTM-0 三红点里 outcome 裁决 0.3% 是唯一不靠外部数据就能修的;最短闭环路径不需要 Dealer 数据;90 天硬指标(裁决→30%)由本波兑现。

## 〇、现状对账:四本账我们有三本半

| 用户要的账 | 现有实体 | 缺口 |
|---|---|---|
| Prediction Ledger(当时怎么判断) | vkpi_forecast_log 331 行+prediction_ledger 读端+GTM preview 的 forecast 段 | ✅ 基本齐;bet 化格式缺 |
| Action Ledger(实际做了什么) | vkpi_action_inbox 271+action_execution_ledger 34 | 任务≠bet:**缺预期指标/成本/复盘日期** |
| Outcome Ledger(结果怎样) | recommendation_outcomes 778(**KOL-only,裁决 2,finalized 0**) | 🔴 主战场:要 GTM 级+强制裁决流 |
| Learning Memory(下次怎么改) | miss_review 入记忆+recommendation_feedback 权重链+驾照升降 | 结果→推荐权重的自动回流缺 |

结论:不缺账本骨架,缺的是**bet 形动作 + 强制裁决 + GTM 级结果模型 + 权重回流**四个关节。

## 一、bet 合约(每条建议=有预期结果的动作)

action_inbox 建议升级为 bet 七要素(在既有六要素上加预期与复盘钩):

```
why            为什么做(预判引用,带 gtm_plan_id/forecast 段落锚)
what / who     做什么/找谁做
expected       预期结果(量化:回复率≥20% / ≥3 条发布 / 短链点击成本<X)
cost / risk    成本 / 风险
escalate_if    加码线(48h 内容进账号前 25% 分位)
retreat_if     撤退线(回复低于目标 / 评论集中质疑价格兼容)
review_at      复盘日期(7d 默认,类型可调)
```

落点:vkpi_action_inbox.payload 扩 bet 字段(jsonb,零表结构改动)+ materialize 生成时强制填齐;GTM Plan preview 的 action_inbox_items 已有六要素,本波补 expected/review_at 并接真落库(materialize dry_run=False)。

## 二、GTM 级结果账本(迁移 217:vkpi_gtm_outcomes)

数据模型照用户单全收:

```sql
vkpi_gtm_outcomes(
  id, gtm_plan_id text, product_sku, market, segment, channel,
  action_type,          -- kol_outreach / dealer_push / official_post / indie_site_update /
                        -- review_collection / community_test / paid_boost / content_retry /
                        -- landing_page_fix / price_message_test / competitor_response
  content_angle, expected_result jsonb, actual_result jsonb,
  window_7d jsonb, window_14d jsonb, window_28d jsonb,   -- 三窗并行,绝不单窗
  decision text,        -- validated / failed / partial / retry / escalate / retreat / open
  lesson text, next_weight_change jsonb,
  action_inbox_id, kol_pool_id nullable, created_at, decided_at, decided_by
)
```

KOL 只是 `channel='creator'` 的一种;recommendation_outcomes 保留为 KOL 专用明细账,gtm_outcomes 是总账(桥接列 kol_pool_id/action_inbox_id)。

## 三、三窗对答案(7/14/28,硬件周期长绝不单窗)

- **7d 执行效率**:联系了吗/回复了吗/寄样了吗/发布了吗(读外联+履约+漏斗既有表自动回填)
- **14d 内容表现**:播放/评论/保存/点击(evidence+短链自动回填)
- **28d 商业结果**:订单/GMV/ROI/Dealer 反馈(Shopify 归因,本地诚实 pending 等上云)
- 自动回填 job:refresh_gtm_windows()(挂每日;能自动的自动填,填不了的留给人工裁决)

## 四、强制裁决流(治 finalized=0 的核心机制)

- review_at 到期 → Action Inbox 自动生成「裁决任务」置顶(类型 gtm_verdict,**不可跳过**:七天不裁决→升级到晨报头条点名)
- 裁决界面一屏:当时预判/预期 vs 三窗实际(自动回填部分)/一键 decision(验证成立/证伪/部分/重试/加码/撤退)+ lesson 一句话
- 裁决写 gtm_outcomes.decision+lesson;decided 即 finalized

## 五、周对答案报告 + 权重回流

- **周报 job**:每周输出「哪些判断对了/错了/下次怎么改」(吃 gtm_outcomes 按 market/channel/content_angle/action_type 分组胜率;接进晨报+记分卡)
- **next_weight_change 回流**(用户例子照编):某类 KOL 回复率低→下次降权(写 recommendation_feedback 链);地区点击高转化低→标记承接问题(conversion_readiness 吃);内容风格保存率高→入内容处方;Dealer 区域反馈好→区域权重升;产品适合官号教育→改渠道组合默认。v1 先把 weight_change **记录成结构化条目**并接 recommendation_feedback 既有生效链,复杂权重引擎不抢跑
- 样本不足纪律照旧:胜率样本 <5 标 insufficient,绝不小样本改权重(统计功效闸复用)

## 六、验收(= 用户"最短实现路径"逐条)

1. 一个真 SKU 的 GTM Plan materialize 出 10-30 条 bet(预期指标+复盘日期全带)
2. 到期强制裁决任务真出现在 Inbox 置顶;裁决一条→gtm_outcomes 落 decision+lesson,**finalized 从 0 变正数**
3. 三窗自动回填真跑(7d 执行段用既有外联/履约数据当场可验)
4. 首份周对答案报告(对/错/下次怎么改三段)
5. 一条真实 next_weight_change 走通 recommendation_feedback 生效链
6. 90 天目标挂钩:3 个 SKU 跑成 = 增长大脑活了(用户原话为准绳)

## 七、施工切分(4 代理)

- **L1 bet 合约+materialize**:payload bet 字段+preview action 段补齐+materialize 落库(dry_run 双态)
- **L2 迁移 217+裁决流**:gtm_outcomes 表+到期裁决任务生成+裁决端点(POST verdict)
- **L3 三窗回填+周报**:refresh_gtm_windows()+weekly answer job+报告端点/卡片
- **L4 权重回流+裁决界面**:next_weight_change→recommendation_feedback 桥+前端裁决一屏(Inbox 内嵌)+晨报点名
