import { KpiCard } from "./MarketVoicePage.modules";

// 战略台 · 板块页范式改版 —— 模块口径注册表 + 响应类型 + KPI 带。
//   金样板 = MarketVoicePage.modules / GtmCommandBoardPage.modules 同构:
//   MODULE_SOURCES 一份注册表(label=真实表名/端点,rows=口径;行数为
//   2026-07-12 只读 PG 实测),SrcChip 与溯源弹窗共用,零第二份口径。
//   卡面零介绍性文案(验收纪律):旧四面板的方法论句/口径脚注全部收进本注册表。
//   数据:page 层注入(benchmark / tracks / performance 三纯读 GET),本文件零直连网络。
// 红线:纯读展示;不触 viltrox_fit_score / rule_v0;绝不调用 marketing-brain/daily
//   与 market/trends 的 GET(总脑纯读红线,两端点有隐藏写入 —— 本页全族绕开);
//   机会分公式/权重原文不上前端(显示层宪法,口径只留一句人话);
//   颜色全 token 零写死色;数字全真源,空态诚实短句;时间戳一律绝对时间。

export type Row = Record<string, any>;

/* ============ 响应类型(与旧四面板同形;后端 /strategy/* 四纯读端点) ============ */

export type ExampleItem = {
  evidence_id?: number | null; title?: string; content_url?: string; platform?: string;
  posted_at?: string; view_count?: number | null; kol_name?: string; matched_via?: string;
};
export type BrandItem = {
  key?: string; brand?: string; brand_type?: string; category?: string;
  videos?: number; kol_count?: number; total_views?: number | null; avg_views?: number | null;
  views_known?: number; trend?: string; momentum_pct?: number;
  share_of_voice?: number | null; rank?: number | null; brand_count_ranked?: number;
  engagement?: { avg_rate?: number | null; sample?: number; confidence?: string };
  focals?: { focal?: string; mm?: number; video_count?: number }[];
  top_examples?: ExampleItem[];
};
export type H2HRow = { metric?: string; label?: string; viltrox?: number | null; competitor?: number | null };
export type H2HItem = { key?: string; brand?: string; rows?: H2HRow[]; verdict?: string };
export type GridCell = {
  focal?: string; mm?: number; competitor_videos?: number; competitor_brands?: string[];
  viltrox_videos?: number; in_catalog?: boolean; sku_count?: number; official_sku_count?: number;
  flagship?: string | null; sku_weak?: boolean; voice_weak?: boolean;
};
export type BenchResp = {
  status?: string; reason?: string; window_days?: number;
  viltrox?: BrandItem; competitors?: BrandItem[]; brand_count_ranked?: number;
  head_to_head?: H2HItem[];
  focal_grid?: { status?: string; reason?: string; cells?: GridCell[]; opportunities?: GridCell[]; voice_weak_cells?: GridCell[] };
  basis?: { videos_scanned?: number; brand_hit_videos?: number; deep_analyzed_in_window?: number };
  confidence?: { level?: string; reason?: string };
};

export type Quote = { text?: string; author?: string; platform?: string; at?: string; likes?: number; source?: string };
export type Demand = {
  total?: number; norm?: number;
  comment_mentions?: number; comment_recent?: number; comment_prev?: number;
  comment_trend?: string; comment_mom_pct?: number;
  evidence_mentions?: number; evidence_recent?: number; evidence_prev?: number;
  evidence_trend?: string; evidence_mom_pct?: number;
  wish_count?: number; wish_quotes?: Quote[]; voice_quotes?: Quote[];
};
export type Coverage = { sku_count?: number; our_voice_videos?: number; points?: number; norm?: number };
export type Competitors = {
  status?: string; reason?: string; total_mentions?: number; brand_count?: number;
  top_brand?: string | null; top_share?: number | null; hhi?: number | null;
  monopoly?: boolean; openness?: number; sample_sufficient?: boolean;
  example?: { brand?: string; title?: string; view_count?: number | null; kol_name?: string; content_url?: string } | null;
};
export type Opportunity = { score?: number; demand_norm?: number; weakness?: number; openness?: number; confidence?: string; basis?: string };
export type TrackItem = {
  track_id?: string; dimension?: string; key?: string; label?: string; mm?: number; in_catalog?: boolean;
  demand?: Demand; coverage?: Coverage; competitors?: Competitors; opportunity?: Opportunity; confidence?: string;
};
export type OppItem = {
  track_id?: string; dimension?: string; label?: string; opportunity?: Opportunity;
  demand?: Demand; coverage?: Coverage; competitors?: Competitors;
};
export type NoGoItem = { track_id?: string; dimension?: string; label?: string; reason?: string; demand_total?: number; opportunity_score?: number };
export type TracksResp = {
  status?: string; reason?: string; method?: string;
  sources?: { voice_docs?: number; evidence_rows?: number; catalog_skus?: number };
  category_tracks?: TrackItem[]; focal_tracks?: TrackItem[];
  opportunities?: OppItem[]; no_go?: NoGoItem[];
  mount_signals?: { mount?: string; wish_count?: number; quotes?: Quote[] }[];
};

export type BetItem = {
  bet_id?: number; bet_uid?: string; hypothesis?: string; probability?: number | null;
  risk_level?: string | null; outcome?: string; lesson?: string | null;
  review_at?: string | null; created_at?: string | null; age_days?: number | null; review_overdue?: boolean;
};
export type BetsBlock = {
  status?: string; reason?: string | null; won?: number; lost?: number; open?: number; void?: number;
  total?: number; settled?: number; hit_rate?: number | null; confidence?: string;
  oldest_open?: BetItem | null; bets?: BetItem[];
};
export type PredGroup = {
  action_type?: string; label?: string; status?: string; hit_rate?: number | null;
  sample_count?: number; confidence?: string; hits?: number; misses?: number;
  pending_count?: number; window?: number;
};
export type PredictionsBlock = {
  status?: string; reason?: string | null; groups?: PredGroup[]; groups_total?: number;
  groups_with_sample?: number; judged_total?: number; pending_total?: number;
  backlog_top?: { action_type?: string; label?: string; pending_count?: number } | null;
};
export type LoopStep = { step_index?: number; step_name?: string; status?: string; finished_at?: string | null };
export type PlanVsActualSample = {
  post_id?: number; project_id?: number; project_name?: string | null; kol_pool_id?: number;
  post_status?: string; platform?: string | null; content_url?: string | null;
  planned?: { window_id?: number | null; starts_at?: string | null; ends_at?: string | null; scan_count?: number | null; baseline?: string };
  actual?: { published_at?: string | null; view_count?: number; like_count?: number; comment_count?: number };
  published_within_window?: boolean | null; match_confidence?: number | null;
};
export type FulfillmentBlock = {
  status?: string; reason?: string | null; loops_completed?: number;
  first_loop?: { run_id?: number; workflow_name?: string; status?: string; created_at?: string | null; steps?: LoopStep[] } | null;
  windows?: { total?: number; matched?: number; scanning?: number };
  posts?: { confirmed?: number; candidates?: number };
  plan_vs_actual?: { samples?: PlanVsActualSample[]; sample_pool?: number; max_actual_views?: number; planned_baseline_reason?: string };
  confidence?: string;
};
export type LessonItem = { text?: string; source?: string; ref?: string; context?: string; at?: string | null };
export type PerfResp = {
  status?: string; method?: string; generated_at?: string;
  scoreboard?: { bets?: BetsBlock; predictions?: PredictionsBlock; fulfillment?: FulfillmentBlock };
  lessons?: { status?: string; reason?: string | null; items?: LessonItem[] };
  honesty_note?: { items?: string[]; note?: string };
};

/* ============ 数字格式化(旧四面板同款,集中一份) ============ */

export function fmtNum(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(Number(n))) return "—";
  const v = Number(n);
  if (v >= 1_000_000) return (Math.round(v / 100_000) / 10).toFixed(1) + "M";
  if (v >= 1_000) return (Math.round(v / 100) / 10).toFixed(1) + "K";
  return String(Math.round(v));
}

export function fmtPct(r: number | null | undefined): string {
  return typeof r === "number" && Number.isFinite(r) ? (Math.round(r * 1000) / 10).toFixed(1) + "%" : "—";
}

export const CONF_LABEL: Record<string, string> = {
  none: "无样本", insufficient: "样本不足", low: "低置信", medium: "中置信", high: "高置信",
};

export function ConfChip({ level, reason, extra }: { level?: string; reason?: string; extra?: string }) {
  const key = String(level || "none");
  const cls = key === "high" || key === "medium"
    ? "border-good bg-good-soft text-good"
    : key === "low"
      ? "border-line bg-card text-ink-2"
      : "border-warn bg-warn-soft text-warn";
  return (
    <span className={`flex-none rounded-md border px-1.5 py-0.5 text-[9px] tabular-nums ${cls}`} title={reason || undefined}>
      {(CONF_LABEL[key] || key) + (extra ? ` · ${extra}` : "")}
    </span>
  );
}

/* ============ 模块口径注册表(SrcChip label=真源;行数=2026-07-12 只读 PG 实测) ============ */

export const MODULE_SOURCES: Record<string, { label: string; rows: Array<[string, string]> }> = {
  kpiS: {
    label: "strategy/* 四纯读聚合",
    rows: [
      ["方法", "纯 SQL/规则聚合库内真数据 · 零采集零写库 · 同输入同输出"],
      ["声量份额", "industry-benchmark:视频×品牌口径(一条视频同品牌只记 1 次),与品牌脉搏同源"],
      ["机会赛道", "category-tracks opportunities:需求高 × 我方弱 × 竞品未垄断"],
      ["押注命中", "vkpi_bet_ledger 2 行(1 won / 1 open,2026-07-12 实测)· 分母=已结算注"],
      ["待对答案", "预测台账 vkpi_prediction_runs 434 行:有预测无结果的积压条数"],
      ["趋势线", "战略域无按日时序端点 —— 四卡虚线如实待接,不编序列"],
    ],
  },
  rank: {
    label: "vkpi_kol_video_evidence 2,899 行 · 深析产物",
    rows: [
      ["口径", "声量 = 90 天窗口内提及视频 × 品牌去重,与品牌脉搏完全同口径"],
      ["样本", "2026-07-12 实测:扫描 1,462 条证据 · 品牌命中 568 · 窗口内深析 145"],
      ["词表", "品牌词表 18 家(证据标题/描述命中,零模型调用)"],
      ["红线", "差距句为规则模板生成 · 不参与 Fit 评分"],
    ],
  },
  h2h: {
    label: "industry-benchmark · head_to_head",
    rows: [
      ["三行对比", "声量(提及视频)/ 覆盖 KOL(独立人数)/ 均播放(有播放数的提及视频)"],
      ["质量侧写", "该品牌被提及视频的均互动率;样本 <5 后端标低置信,如实展示"],
      ["例证", "按播放排序 top3,直跳原帖外链"],
      ["结论句", "规则模板一句话差距,非模型生成"],
    ],
  },
  focal: {
    label: "focal_matrix 词表 · vkpi_products 369 行",
    rows: [
      ["口径", "焦段格局 = 竞品声量 vs 我方声量 vs 官方 SKU 覆盖(词表正则 6–800mm)"],
      ["红格", "竞品有声量而我方该焦段零官方 SKU(SKU 空档)"],
      ["黄格", "有货无声:有 SKU 但零内容声量"],
    ],
  },
  matrix: {
    label: "vkpi_comments 875 行 · 证据 · vkpi_products",
    rows: [
      ["需求", "评论+意向近 60 天 + 证据标题近 180 天(2026-07-12 实测:声音 797 条 · 证据 1,644 条)"],
      ["覆盖", "目录 SKU(369)+ 我方内容声量"],
      ["竞品", "词表命中密度 + 垄断判定(top 份额 ≥60% 且样本 ≥5)"],
      ["机会分", "规则打分 v0 · 权重待校准;公式与常量在接口 basis 字段,不上门面"],
      ["方法", "纯词表/规则聚合 · 零模型调用 · 不参与 Fit 评分"],
    ],
  },
  oppTop: {
    label: "category-tracks · opportunities",
    rows: [
      ["口径", "需求高 × 我方弱 × 竞品未垄断,跨品类/焦段两维排序"],
      ["证据", "每条机会带愿望原声 / 覆盖红格 / 竞品例证,点行在机会矩阵展开"],
    ],
  },
  noGo: {
    label: "category-tracks · no_go + mount_signals",
    rows: [
      ["不进", "需求低或竞品垄断,理由随行(含份额与样本阈值)"],
      ["卡口愿望", "目录卡口口径未建,只报声量不硬算覆盖(辅助信号)"],
    ],
  },
  sim: {
    label: "/strategy/simulate · 决定性模拟",
    rows: [
      ["口径", "选 SKU + 预算 → 三策略并排(头部集中 / 长尾铺量 / 混合 50/50)"],
      ["引擎", "报价 rate_card + 播放预测逐人求和(p10/p50/p90)+ 组合去重触达"],
      ["候选", "vkpi_kol_pool 1,254 行(2026-07-12 实测)"],
      ["方法", "零模型调用决定性模拟 · 同输入同输出 · 缺数据诚实空态 · 不参与 Fit 评分"],
    ],
  },
  bets: {
    label: "vkpi_bet_ledger 2 行(2026-07-12 实测)",
    rows: [
      ["口径", "hypothesis → won/lost/open 结算 + 最老 open 注账龄与约定复盘日"],
      ["实测", "1 won / 1 open · 已结算 1 注(样本极小,置信如实标)"],
    ],
  },
  preds: {
    label: "vkpi_prediction_runs 434 行 · evals 0 行",
    rows: [
      ["口径", "各动作组命中率 = 已裁决命中 / 已裁决样本;条色按 ≥70% / 40–70% / <40% 分档"],
      ["积压", "待对答案 = 有预测无结果;大头组随行透出"],
      ["实测", "vkpi_prediction_evals 0 行(2026-07-12)—— 裁决走台账回填,如实展示"],
    ],
  },
  ful: {
    label: "观察窗口 102 · 内容帖 23 · workflow 21(2026-07-12 实测)",
    rows: [
      ["口径", "计划 = 真实观察窗口(不编计划日期);实际 = 发布时点 + 回填播放"],
      ["闭环", "签收 → 开窗 → 内容匹配 → 复盘 真实步骤留痕(vkpi_workflow_runs/steps)"],
      ["样例", "planned vs actual 逐条:窗口内/外如实记录,不粉饰"],
    ],
  },
  lessons: {
    label: "复盘留痕 · 数据荒诚实条",
    rows: [
      ["教训", "只读真实留痕 top5(来源 + ref + 时间),绝不现编"],
      ["数据荒", "哪本账还空着直说(逐条来自后端 honesty_note)"],
      ["时间", "generated_at 存 UTC · 按浏览器时区显示"],
    ],
  },
};

export const PROV_TITLES: Record<string, string> = {
  kpiS: "战略总览",
  rank: "声量份额排名",
  h2h: "Viltrox vs 竞品",
  focal: "焦段格局",
  matrix: "机会矩阵",
  oppTop: "Top 机会赛道",
  noGo: "不进清单",
  sim: "策略模拟器",
  bets: "押注台账",
  preds: "预测命中率",
  ful: "履约对账",
  lessons: "教训与数据荒",
};

/* ============ KPI 带四卡(现值全真;战略域无时序端点 → 四卡诚实虚线零药丸) ============ */

export function StrategyKpiBand({
  bench,
  tracks,
  perf,
}: {
  bench: BenchResp | null;
  tracks: TracksResp | null;
  perf: PerfResp | null;
}) {
  // K1 声量份额:benchmark ok 才有真值;no_data/no_signal/未就绪 → 诚实 pending
  const benchOk = bench != null && String(bench.status || "") === "ok";
  const sov = benchOk && typeof bench!.viltrox?.share_of_voice === "number"
    ? Math.round(bench!.viltrox!.share_of_voice! * 1000) / 10
    : null;
  const benchNote = bench == null
    ? "对照聚合读取中…"
    : bench.status === "no_data_in_window"
      ? "窗口内暂无入库视频证据"
      : bench.status === "no_brand_signal"
        ? "窗口内视频未命中品牌词表"
        : "对照数据未就绪";

  // K2 机会赛道:tracks ready 才有真值(0 也如实 0)
  const tracksOk = tracks != null && String(tracks.status || "") === "ready";
  const oppCount = tracksOk ? (Array.isArray(tracks!.opportunities) ? tracks!.opportunities!.length : 0) : null;
  const tracksNote = tracks == null ? "赛道聚合读取中…" : String(tracks.reason || "窗口内暂无声量数据");

  // K3 押注命中:分母=已结算注;0 注结算 → 诚实 pending 不编率
  const betsBlock = perf?.scoreboard?.bets;
  const betsOk = perf != null && String(perf.status || "") === "ok" && String(betsBlock?.status || "") === "ok";
  const settled = betsOk ? Number(betsBlock!.settled) || 0 : 0;
  const hitPct = betsOk && settled > 0 && typeof betsBlock!.hit_rate === "number"
    ? Math.round(betsBlock!.hit_rate! * 1000) / 10
    : null;
  const betsNote = perf == null ? "表现聚合读取中…" : !betsOk ? String(betsBlock?.reason || "押注账不可用") : "尚无已结算押注";

  // K4 待对答案:预测台账积压(warn 语义)
  const predsBlock = perf?.scoreboard?.predictions;
  const predsOk = perf != null && String(perf.status || "") === "ok" && String(predsBlock?.status || "") === "ok";
  const pendingTotal = predsOk ? Number(predsBlock!.pending_total) || 0 : null;
  const predsNote = perf == null ? "表现聚合读取中…" : String(predsBlock?.reason || "预测台账不可用");

  return (
    <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      <KpiCard label="声量份额" value={sov != null ? sov : "—"} unit="%" pending={sov == null} pendingNote={benchNote} seriesColor="var(--ds-accent)" />
      <KpiCard label="机会赛道" value={oppCount ?? "—"} unit="条" pending={oppCount == null} pendingNote={tracksNote} seriesColor="var(--ds-accent-2)" />
      <KpiCard label="押注命中" value={hitPct != null ? hitPct : "—"} unit="%" pending={hitPct == null} pendingNote={betsNote} seriesColor="var(--ds-good)" />
      <KpiCard
        label="待对答案"
        value={pendingTotal ?? "—"}
        unit="条"
        tone="warn"
        pending={pendingTotal == null}
        pendingNote={predsNote}
        seriesColor="var(--ds-warn)"
      />
    </div>
  );
}
