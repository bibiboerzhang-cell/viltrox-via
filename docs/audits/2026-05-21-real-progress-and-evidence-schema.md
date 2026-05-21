# V-KPI Real Progress Audit + Evidence Schema

**Date:** 2026-05-21  
**Anchor commit:** P5.1 commit containing this audit  
**Triggered by:** 看完 clean code zip + 对照 5/19 评估 + 校准 P 阶段路线

## 1. 两份独立评估的对照

- **5/19 评估** (`docs/audits/2026-05-19-code-intelligence-assessment.html`): 综合 64/100
- **5/21 看代码后评估**: 代码层 90% / 业务闭环 30% / UI 可见 50%,综合 ~60-70 分(口径不同)

两份独立做,数字一致。当前真实瓶颈不是代码不够,是业务闭环和反馈数据。

## 2. 5/19 评估的可复用部分

**保留**:四个产品收敛方向 — IntelligenceCard / Mission Lite / Evidence Drawer / Feedback Loop  
**作废**:5/19 评估里 P15/P16/P17 的具体编号 — 不能直接照执行,数据底座未到位

## 3. P25 澄清

之前误以为 P25 是"自治商业体",**错的**。5/19 评估明确写:

> P25 = Content Brain v1 / 视频深度
> Gemini 粗筛 + Claude Vision 精筛 + 评论 sentiment
> 完成指标:内容建议采纳率 >=30%,成本 <=$1500/月

P25 已有代码底座(gemini_video.py / claude_vision.py / account_dossier.py),缺的是 KOL Top N pipeline。本轮 P7 就是 P25 的最小落地。

## 4. 关键执行决策

### 决策 1: Evidence Drawer schema 提前钉死
P5.2 / P6 / P7 都要写 evidence,schema 不先定会三次返工。Schema 见本文档第 5 节。

### 决策 2: 记忆卡作为 enrichment 层,不绑定搜索入口
确认于 2026-05-21-deploy-delta.md。Annotate_platform_items 模式 ✅

### 决策 3: 11 维 confidence 必填,不凑默认值
每个维度 `{value, confidence, source, evidence_count, last_updated_at}` 内嵌。
Confidence=0 的维度 UI 直接灰显或不显示。

### 决策 4: P13 是被低估的事
关闭 review backlog(8 人对 50-100 条历史推荐人工 accept/reject)是反馈闭环的种子。
不做 P13,P10 学习层永远没数据。**排在 P5.2 之后立即做**。

### 决策 5: 产品 persona 数据(P3.3a)由 Jianbo + Iain 半天填
11 个核心 SKU 优先,几百个长尾 SKU 增量补。

### 决策 6: Top N 视频筛选用"GPT 细筛 → 失败降级到规则"
单次 GPT 调用 $0.01-0.05,失败 fallback 到 views + Viltrox 关键词排序。

## 5. Evidence Drawer Schema v0

所有智能输出(推荐、风险、机会、任务、卡片)必须挂 evidence。Evidence 字段如下:

```json
{
  "evidence_id": "ev_<source>_<source_id>_<timestamp>",
  "source": "cooperation_history | competitor_signal | brand_signal | video_analysis | excel_legacy | platform_api | rule_engine | llm_inference",
  "source_table": "vkpi_legacy_cooperations_staging",
  "source_id": 1234,
  "source_url": "https://... (可选,平台原始链接)",
  "captured_at": "2026-05-21T08:00:00Z",
  "freshness_hours": 4,
  "confidence": 0.82,
  "confidence_method": "rule_v0 | gpt_inference | gemini_video | claude_synth | manual_input",
  "reasoning": "KOL 历史合作过 35mm LAB 系列 3 次,2 次 ROI > 2.0",
  "raw_data_ref": "vkpi_kol_video_analysis:567",
  "rebuttal_supported": true
}
```

### 字段规范

| 字段 | 必填 | 类型 | 说明 |
|---|:-:|---|---|
| evidence_id | ✓ | string | 全局唯一,格式 `ev_{source}_{source_id}_{ts}` |
| source | ✓ | enum | 8 种来源之一,不能随便起新名字 |
| source_table | ✓ | string | 真实表名,便于 SQL 追溯 |
| source_id | ✓ | int/string | 真实 row id |
| source_url | — | string | 平台原链接(若适用) |
| captured_at | ✓ | ISO8601 | 这条 evidence 采集时间 |
| freshness_hours | ✓ | int | 距当前时间小时数,UI 显示"X 小时前" |
| confidence | ✓ | float 0-1 | 置信度,0=不可用,1=完全确信 |
| confidence_method | ✓ | enum | 怎么算出 confidence 的 |
| reasoning | ✓ | string | 中文一句话,**不要技术词**(不要写"待接"、"v0") |
| raw_data_ref | — | string | 引用关联表 row(如视频分析、合作记录) |
| rebuttal_supported | ✓ | bool | 员工能否反馈"我不同意" |

### 8 种 source 类型说明

| source | 来自 | 典型例子 |
|---|---|---|
| cooperation_history | vkpi_legacy_cooperations_staging | "2024 年合作过 AF-35MM-F12-LAB,ROI 2.3" |
| competitor_signal | vkpi_competitor_relation | "近 30 天发布 Sigma 35 Art 评测 5 次" |
| brand_signal | vkpi_brand_signal | "近 90 天提到 Viltrox 7 次,正面率 86%" |
| video_analysis | vkpi_kol_video_analysis | "Gemini 识别 production_quality=professional" |
| excel_legacy | vkpi_legacy_kol_profiles_staging | "联系状态:已对接,邮箱:xxx" |
| platform_api | scan_kol_account 返回 | "YouTube 460K 粉丝,近 7 天涨粉 2.3%" |
| rule_engine | eleven_dimensions.py 计算 | "block3_business confidence=0.71" |
| llm_inference | LLM Gateway 输出 | "Claude 综合判断 cooperation_priority=P1" |

### 强制约束

1. **所有智能输出必须挂 ≥1 条 evidence**,无 evidence = 不显示
2. **reasoning 字段禁用开发词**:不能出现 "待接"、"v0"、"rule dimensions"、"deep scan" 等
3. **confidence 不能凑齐**:数据缺失就是 0,不写默认 0.5 凑数
4. **freshness_hours > 168 (一周) 的 evidence UI 必须标"陈旧"**

### Schema 实施在哪些 P 阶段

| P 阶段 | 怎么用 Evidence Schema |
|---|---|
| P5.2 11 维 backfill | 每个 dimension 的 `evidence_count` 字段对应这里的 evidence 数 |
| P6 Product Fit | 每个 SKU × KOL 适配分数挂 evidence(历史合作 + 竞品 + 关键词) |
| P7 视频 Top N 分析 | 每条视频分析结果就是一个 evidence(source=video_analysis) |
| P8 Evidence Drawer | UI 实现,按本 schema 渲染 |
| P9 IntelligenceCard | card.evidence: Evidence[] 数组 |

## 6. 修正后的执行顺序

| # | 动作 | 工程量 | LLM 成本 |
|---|---|---|---|
| 0 | 本文档 + 5/19 评估归档 commit | 10 min | $0 |
| 1 | P5.1 push 远程(已 local 完成) | 5 min | $0 |
| 2 | P5.2A seed vkpi_kol_profile_deep | 1h | $0 |
| 3 | P5.2B dry-run + 全量 backfill | 1-2h | $0 |
| 4 | **P13 启动**:让 Jianbo / 1-2 员工对 50-100 条历史推荐人工 accept/reject | 3-5 天 | $0 |
| 5 | P6 Product Fit 规则版 | 1-2 天 | $0 |
| 6 | P7 视频 Top N 分析 pipeline + vkpi_kol_video_analysis 表 | 2-3 天 | $1-3 试水 |
| 7 | P8 Evidence Drawer 标准化实施 | 1-2 天 | $0 |
| 8 | P9 IntelligenceCard v0 | 1-2 天 | $0 |
| 9 | P10 Mission Lite | 1-2 天 | $0 |

**P5.2 + P13 并行**:P5.2 是工程任务给 Codex 跑,P13 是人脑任务给你/员工做。两件不冲突。

## 7. 不做清单(本轮)

- ❌ 不做 IntelligenceCard / Mission Lite 的空壳 UI(必须等 P6/P7 数据稳)
- ❌ 不做 1023 KOL 全量 Gemini 深扫(P25 第一波只跑 30 个历史档案 KOL)
- ❌ 不做 LightGBM(P13 反馈数据没到 200 条之前不开始)
- ❌ 不做服务器迁移(Hetzner CCX33 当前无压力)
- ❌ 不动 14→8 项导航精简(等 P9 IntelligenceCard 完成后顺手做)
- ❌ 不清本地 2.6G 媒体(R2 双写稳定 7 天后再清)

## 8. 下一次 audit 触发条件

下次 audit 应该在以下情况之一发生时写:
- P5.2 全量 backfill 跑完,有真实覆盖率数据
- P13 收满 50 条反馈
- P7 第一次 Gemini 视频深扫完成($0.50 试水后)
- 任何重大方向修正
