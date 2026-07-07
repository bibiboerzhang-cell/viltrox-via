// S4 实际战略表现看板:「我们的战略判断到底准不准」—— plan vs actual 三账对齐。
// 数据:GET /api/admin/vkpi/strategy/performance —— 纯 SQL/规则聚合已有真实数据,零 LLM。
// 三账记分牌:🎲 押注账(won/lost/open + 最老 open 账龄) / 🎯 预测军团(组命中率精选
// + 待对答案积压) / 📦 履约战果(完成 loop + 观察窗口→发布→实际播放 planned vs actual)。
// + 📖 已沉淀教训 top5(只读真实留痕) + 数据荒诚实条(哪本账还空着直说)。
// 诚实态:单账 status!=="ok" 如实展示 reason;接口失败整块安静缺席(非阻塞增益块)。
// 红线:纯展示,绝不渲染/触碰 viltrox_fit_score 与 rule_v0;不编造市场数据。
import React from "react";
import { AlertTriangle, BookOpen, Dices, PackageCheck, Target } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import { SectionFold } from "./SectionFold";

const e = React.createElement;

type Basis = Record<string, unknown>;
type BetItem = {
  bet_id?: number; bet_uid?: string; hypothesis?: string; probability?: number | null;
  risk_level?: string | null; outcome?: string; lesson?: string | null;
  review_at?: string | null; created_at?: string | null; age_days?: number | null; review_overdue?: boolean;
};
type BetsBlock = {
  status?: string; reason?: string | null; won?: number; lost?: number; open?: number; void?: number;
  total?: number; settled?: number; hit_rate?: number | null; confidence?: string;
  oldest_open?: BetItem | null; bets?: BetItem[]; basis?: Basis;
};
type PredGroup = {
  action_type?: string; label?: string; status?: string; hit_rate?: number | null;
  sample_count?: number; confidence?: string; hits?: number; misses?: number;
  pending_count?: number; window?: number;
};
type PredictionsBlock = {
  status?: string; reason?: string | null; groups?: PredGroup[]; groups_total?: number;
  groups_with_sample?: number; judged_total?: number; pending_total?: number;
  backlog_top?: { action_type?: string; label?: string; pending_count?: number } | null; basis?: Basis;
};
type LoopStep = { step_index?: number; step_name?: string; status?: string; finished_at?: string | null };
type PlanVsActualSample = {
  post_id?: number; project_id?: number; project_name?: string | null; kol_pool_id?: number;
  post_status?: string; platform?: string | null; content_url?: string | null;
  planned?: { window_id?: number | null; starts_at?: string | null; ends_at?: string | null; scan_count?: number | null; baseline?: string };
  actual?: { published_at?: string | null; view_count?: number; like_count?: number; comment_count?: number };
  published_within_window?: boolean | null; match_confidence?: number | null;
};
type FulfillmentBlock = {
  status?: string; reason?: string | null; loops_completed?: number;
  first_loop?: { run_id?: number; workflow_name?: string; status?: string; created_at?: string | null; steps?: LoopStep[] } | null;
  windows?: { total?: number; matched?: number; scanning?: number };
  posts?: { confirmed?: number; candidates?: number };
  plan_vs_actual?: { samples?: PlanVsActualSample[]; sample_pool?: number; max_actual_views?: number; planned_baseline_reason?: string };
  confidence?: string; basis?: Basis;
};
type LessonItem = { text?: string; source?: string; ref?: string; context?: string; at?: string | null };
type PerformanceResp = {
  status?: string; method?: string; generated_at?: string;
  scoreboard?: { bets?: BetsBlock; predictions?: PredictionsBlock; fulfillment?: FulfillmentBlock };
  lessons?: { status?: string; reason?: string | null; items?: LessonItem[] };
  honesty_note?: { items?: string[]; note?: string };
  note?: string;
};

function fmtPct(r: number | null | undefined): string {
  return typeof r === "number" && Number.isFinite(r) ? (Math.round(r * 1000) / 10).toFixed(1) + "%" : "—";
}
function fmtNum(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(Number(n))) return "—";
  const v = Number(n);
  if (v >= 1_000_000) return (Math.round(v / 100_000) / 10).toFixed(1) + "M";
  if (v >= 1_000) return (Math.round(v / 100) / 10).toFixed(1) + "K";
  return String(v);
}
function fmtDate(s: string | null | undefined): string {
  return s ? String(s).slice(0, 10) : "—";
}

const CONFIDENCE_LABEL: Record<string, string> = {
  none: "无样本", insufficient: "样本不足", low: "低置信", medium: "中置信", high: "高置信",
};
const OUTCOME_STYLE: Record<string, string> = {
  won: "bg-emerald-500/10 text-emerald-200 border-emerald-300/20",
  lost: "bg-rose-500/10 text-rose-200 border-rose-300/20",
  open: "bg-sky-500/10 text-sky-200 border-sky-300/20",
  void: "bg-slate-500/10 text-slate-400 border-white/[0.08]",
};
const OUTCOME_LABEL: Record<string, string> = { won: "押对", lost: "押错", open: "未结算", void: "作废" };

// 块标题行(与 Signature/PredictionLedger 同款排版语言)
function BlockTitle(icon: React.ReactNode, text: string, extra?: React.ReactNode) {
  return e("div", { className: "flex items-center gap-1.5 mt-2 mb-1" },
    icon,
    e("span", { className: "text-[10px] font-medium text-slate-300" }, text),
    extra || null,
  );
}

// 诚实空态/错误行(reason 直接来自后端,不本地编造)
function EmptyLine(reason: string) {
  return e("div", { className: "text-[10px] leading-relaxed text-amber-300/90" }, reason || "暂无数据");
}

function ConfidenceBadge(conf: string | undefined, extra?: string) {
  const c = String(conf || "none");
  const label = (CONFIDENCE_LABEL[c] || c) + (extra ? ` · ${extra}` : "");
  const cls = c === "insufficient" || c === "none"
    ? "bg-amber-500/10 text-amber-200 border border-amber-300/20"
    : c === "high" || c === "medium"
      ? "bg-emerald-500/10 text-emerald-200 border border-emerald-300/10"
      : "bg-slate-500/10 text-slate-300 border border-white/[0.06]";
  return e("span", { className: "shrink-0 rounded px-1.5 py-0.5 text-[8.5px] tabular-nums " + cls }, label);
}

// ── 🎲 账① 押注记分牌 ──
function BetsBlockView(bets: BetsBlock) {
  if (String(bets.status || "") !== "ok") return EmptyLine(String(bets.reason || "押注账不可用"));
  const oldest = bets.oldest_open;
  const counts: Array<[string, number]> = [
    ["won", Number(bets.won) || 0], ["lost", Number(bets.lost) || 0],
    ["open", Number(bets.open) || 0], ["void", Number(bets.void) || 0],
  ];
  return e(React.Fragment, null,
    e("div", { className: "flex flex-wrap items-center gap-1.5" },
      counts.filter(([k, n]) => n > 0 || k !== "void").map(([k, n]) =>
        e("span", { key: k, className: "rounded border px-2 py-0.5 text-[10px] tabular-nums " + (OUTCOME_STYLE[k] || "") },
          `${OUTCOME_LABEL[k] || k} ${n}`)),
      typeof bets.hit_rate === "number" && e("span", { className: "text-[9px] tabular-nums text-slate-500" },
        `已结算命中 ${fmtPct(bets.hit_rate)}(${Number(bets.settled) || 0} 注)`),
      ConfidenceBadge(bets.confidence, `已结算 ${Number(bets.settled) || 0}`),
    ),
    oldest && e("div", { className: "mt-1 rounded border border-sky-300/10 bg-sky-400/[0.05] px-2 py-1" },
      e("div", { className: "flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[9px]" },
        e("span", { className: "text-sky-200 font-medium" }, "最老 open 注"),
        e("span", { className: "tabular-nums text-slate-400" }, `账龄 ${oldest.age_days ?? "—"} 天`),
        oldest.review_overdue
          ? e("span", { className: "rounded bg-rose-500/10 px-1.5 py-0.5 text-rose-200" }, `复盘已过期(约定 ${fmtDate(oldest.review_at)})`)
          : e("span", { className: "text-slate-500" }, `约定复盘 ${fmtDate(oldest.review_at)}`),
        typeof oldest.probability === "number" && e("span", { className: "tabular-nums text-slate-500" }, `押 p=${oldest.probability}`),
      ),
      e("div", { className: "mt-0.5 text-[9.5px] leading-relaxed text-slate-300", title: oldest.hypothesis }, oldest.hypothesis || "—"),
    ),
  );
}

// ── 🎯 账② 预测军团 ──
function PredGroupRow(g: PredGroup, i: number) {
  const sample = Number(g.sample_count) || 0;
  const rate = typeof g.hit_rate === "number" ? g.hit_rate : null;
  const pending = Number(g.pending_count) || 0;
  const widthPct = sample > 0 && rate != null ? Math.max(3, Math.round(rate * 100)) : 0;
  const barColor = sample <= 0 || rate == null
    ? "rgba(148,163,184,0.25)"
    : rate >= 0.7
      ? "linear-gradient(90deg, rgba(52,211,153,0.35), rgba(52,211,153,0.8))"
      : rate >= 0.4
        ? "linear-gradient(90deg, rgba(251,191,36,0.35), rgba(251,191,36,0.8))"
        : "linear-gradient(90deg, rgba(251,113,133,0.35), rgba(251,113,133,0.8))";
  return e("div", { key: g.action_type || i, className: "flex items-center gap-2" },
    e("span", { className: "w-[110px] shrink-0 truncate text-[10px] text-slate-300", title: `${g.label || ""}(${g.action_type || ""})` },
      g.label || g.action_type || "—"),
    e("div", { className: "relative h-[7px] flex-1 overflow-hidden rounded-full bg-white/[0.05]" },
      widthPct > 0 && e("div", { className: "absolute inset-y-0 left-0 rounded-full", style: { width: widthPct + "%", background: barColor } }),
    ),
    e("span", { className: "w-[44px] shrink-0 text-right text-[10px] font-medium tabular-nums " + (sample > 0 ? "text-slate-200" : "text-slate-600") }, fmtPct(rate)),
    ConfidenceBadge(g.confidence, `样本 ${sample}`),
    pending > 0 && e("span", { className: "shrink-0 text-[8.5px] tabular-nums text-slate-500" }, `积压 ${fmtNum(pending)}`),
  );
}

function PredictionsBlockView(preds: PredictionsBlock) {
  if (String(preds.status || "") !== "ok") return EmptyLine(String(preds.reason || "预测台账不可用"));
  const groups = Array.isArray(preds.groups) ? preds.groups : [];
  const backlog = preds.backlog_top;
  return e(React.Fragment, null,
    groups.length === 0
      ? EmptyLine("台账无任何分组(诚实空账)")
      : e("div", { className: "space-y-1" }, groups.map((g, i) => PredGroupRow(g, i))),
    (Number(preds.pending_total) || 0) > 0 && e("div", { className: "mt-1 text-[9px] leading-relaxed text-amber-300/90" },
      `待对答案积压 ${fmtNum(preds.pending_total)} 条(有预测无结果)` +
      (backlog ? `,大头在「${backlog.label || backlog.action_type}」${fmtNum(backlog.pending_count)} 条` : "") +
      `;已裁决合计 ${fmtNum(preds.judged_total)} 条。`),
  );
}

// ── 📦 账③ 履约战果 ──
function SampleRow(s: PlanVsActualSample, i: number) {
  const planned = s.planned || {};
  const actual = s.actual || {};
  const views = Number(actual.view_count) || 0;
  const within = s.published_within_window;
  return e("div", { key: s.post_id || i, className: "rounded border border-white/[0.05] bg-black/20 px-2 py-1.5" },
    e("div", { className: "flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[9.5px]" },
      e("span", { className: "font-medium text-slate-200" }, s.project_name || `项目 #${s.project_id ?? "—"}`),
      e("span", { className: "text-slate-500" }, `KOL #${s.kol_pool_id ?? "—"}`),
      e("span", { className: "rounded bg-slate-500/10 px-1.5 py-0.5 text-[8.5px] text-slate-300" },
        s.post_status === "retrospective_ready" ? "人工确认·已复盘" : s.post_status === "matched" ? "窗口匹配" : String(s.post_status || "—")),
      s.content_url && e("a", { href: s.content_url, target: "_blank", rel: "noreferrer", className: "text-[8.5px] text-cyan-300/80 hover:underline" }, "内容链接"),
    ),
    e("div", { className: "mt-0.5 grid grid-cols-2 gap-x-3 text-[9px] tabular-nums" },
      e("div", { className: "text-slate-500" },
        e("span", { className: "text-slate-400" }, "计划:"),
        planned.window_id != null
          ? ` 观察窗口 ${fmtDate(planned.starts_at)} ~ ${fmtDate(planned.ends_at)}(扫 ${Number(planned.scan_count) || 0} 次)`
          : " 无观察窗口(人工手录)"),
      e("div", { className: "text-slate-500" },
        e("span", { className: "text-slate-400" }, "实际:"),
        ` 发布 ${fmtDate(actual.published_at)} · 播放 ${views > 0 ? fmtNum(views) : "0(未回填)"}`),
    ),
    within != null && e("div", { className: "mt-0.5 text-[8.5px] " + (within ? "text-emerald-300/80" : "text-amber-300/80") },
      within ? "发布落在计划窗口内" : "发布时点在计划窗口之外(如实记录,不粉饰)"),
  );
}

function FulfillmentBlockView(ful: FulfillmentBlock) {
  if (String(ful.status || "") !== "ok") return EmptyLine(String(ful.reason || "履约账不可用"));
  const loop = ful.first_loop;
  const windows = ful.windows || {};
  const posts = ful.posts || {};
  const samples = Array.isArray(ful.plan_vs_actual?.samples) ? ful.plan_vs_actual!.samples! : [];
  return e(React.Fragment, null,
    e("div", { className: "flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[9.5px] tabular-nums text-slate-400" },
      e("span", null, "完成 loop ", e("span", { className: "font-medium text-emerald-300" }, String(Number(ful.loops_completed) || 0))),
      e("span", null, `观察窗口 ${Number(windows.total) || 0}(匹配 ${Number(windows.matched) || 0} / 扫描中 ${Number(windows.scanning) || 0})`),
      e("span", null, `内容帖 确认 ${Number(posts.confirmed) || 0} / 候选 ${Number(posts.candidates) || 0}`),
      ConfidenceBadge(ful.confidence, `确认 ${Number(posts.confirmed) || 0}`),
    ),
    loop && e("div", { className: "mt-1 flex flex-wrap items-center gap-1 text-[8.5px]" },
      e("span", { className: "text-slate-500" }, `首条真实闭环 run #${loop.run_id}(${fmtDate(loop.created_at)}):`),
      (loop.steps || []).map((st, i) => e("span", {
        key: i,
        className: "rounded border px-1.5 py-0.5 tabular-nums " + (String(st.status) === "done"
          ? "border-emerald-300/20 bg-emerald-500/10 text-emerald-200"
          : "border-amber-300/20 bg-amber-500/10 text-amber-200"),
      }, `${st.step_name}${String(st.status) === "done" ? " ✓" : ` (${st.status})`}`)),
    ),
    samples.length > 0 && e("div", { className: "mt-1.5 space-y-1.5" },
      e("div", { className: "text-[9px] text-slate-500" }, "planned vs actual 样例(计划=真实观察窗口,不编计划日期):"),
      samples.slice(0, 3).map((s, i) => SampleRow(s, i)),
    ),
  );
}

export function StrategyPerformancePanel({ apiToken }: { apiToken: string }) {
  const [data, setData] = React.useState<PerformanceResp | null>(null);

  // 只读拉取(后端纯聚合已有数据,读得起);失败静默不渲染(非阻塞增益块)。
  React.useEffect(() => {
    setData(null);
    if (!apiToken) return;
    let cancelled = false;
    void apiFetch<PerformanceResp>("/api/admin/vkpi/strategy/performance", {}, apiToken)
      .then((payload) => { if (!cancelled) setData(payload && typeof payload === "object" ? payload : null); })
      .catch(() => { if (!cancelled) setData(null); });
    return () => { cancelled = true; };
  }, [apiToken]);

  if (!apiToken || !data) return null;
  if (String(data.status || "") !== "ok") return null; // 聚合失败:安静缺席,不甩后端报错

  const sb = data.scoreboard || {};
  const bets = sb.bets || {};
  const preds = sb.predictions || {};
  const ful = sb.fulfillment || {};
  const lessons = data.lessons || {};
  const lessonItems = Array.isArray(lessons.items) ? lessons.items : [];
  const honesty = Array.isArray(data.honesty_note?.items) ? data.honesty_note!.items! : [];
  const settledBets = (Number(bets.won) || 0) + (Number(bets.lost) || 0);

  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    e(SectionFold, {
      id: "strategy-performance",
      header: e(React.Fragment, null,
        e(Target, { size: 11, className: "text-emerald-300" }),
        e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "战略表现 · 我们的判断准不准"),
        e("span", { className: "rounded bg-emerald-500/10 px-1.5 py-0.5 text-[9px] text-emerald-200" },
          `${settledBets} 注已结算 · ${Number(ful.loops_completed) || 0} loop`),
      ),
    },
      // ── 🎲 账① 押注账(hypothesis → won/lost/open + 账龄)──
      BlockTitle(e(Dices, { size: 10, className: "text-sky-300" }), "押注账 · 猜未来押对了吗"),
      BetsBlockView(bets),

      // ── 🎯 账② 预测军团(组命中率精选 + 待对答案积压)──
      BlockTitle(e(Target, { size: 10, className: "text-cyan-300" }), "预测军团 · 组命中率精选"),
      PredictionsBlockView(preds),

      // ── 📦 账③ 履约战果(loop + planned vs actual)──
      BlockTitle(e(PackageCheck, { size: 10, className: "text-emerald-300" }), "履约战果 · 计划 vs 实际"),
      FulfillmentBlockView(ful),

      // ── 📖 已沉淀教训 top5(只读真实留痕,绝不现编)──
      BlockTitle(e(BookOpen, { size: 10, className: "text-amber-300" }), "已沉淀教训 TOP5"),
      String(lessons.status || "") !== "ok" || lessonItems.length === 0
        ? EmptyLine(String(lessons.reason || "还没沉淀出教训"))
        : e("div", { className: "space-y-1" }, lessonItems.map((item, i) =>
            e("div", { key: i, className: "rounded border border-white/[0.05] bg-black/20 px-2 py-1" },
              e("div", { className: "text-[9.5px] leading-relaxed text-slate-200" }, `${i + 1}. ${item.text || ""}`),
              e("div", { className: "mt-0.5 flex flex-wrap items-center gap-x-2 text-[8.5px] text-slate-600" },
                e("span", null, `来源 ${item.source || "—"}(${item.ref || "—"})`),
                item.context && e("span", null, item.context),
                item.at && e("span", { className: "tabular-nums" }, fmtDate(item.at)),
              ),
            ))),

      // ── 数据荒诚实条 ──
      honesty.length > 0 && e("div", { className: "mt-2 rounded border border-amber-300/15 bg-amber-400/[0.05] px-2 py-1.5" },
        e("div", { className: "flex items-center gap-1.5" },
          e(AlertTriangle, { size: 10, className: "text-amber-300" }),
          e("span", { className: "text-[9px] font-medium text-amber-200" }, "数据荒诚实条 · 哪本账还空着"),
        ),
        e("div", { className: "mt-0.5 space-y-0.5" }, honesty.map((line, i) =>
          e("div", { key: i, className: "text-[9px] leading-relaxed text-amber-200/80" }, "· " + line))),
      ),

      e("div", { className: "mt-1.5 text-[9px] text-slate-600" },
        "口径:plan vs actual 三账对齐(押注账本 / 预测台账 / 履约窗口→发布→播放),纯聚合库内真实数据,零 LLM 零外部行情;数字全带 basis 可追溯;本板永不影响任何评分。"),
      data.generated_at && e("div", { className: "mt-0.5 text-[8.5px] text-slate-700 tabular-nums" },
        "生成于 " + String(data.generated_at).slice(0, 19).replace("T", " ") + " UTC"),
    ),
  );
}
