// CB1 官号内容计划器面板(Owned Media Planner):官号不是 dashboard,是渠道动作。
// 数据:GET /api/admin/vkpi/channel/official-plan?sku=&category= —— 纯读聚合
//   (每帖最新快照去重 + 标题词表回打内容形式 + 发帖 UTC 小时分时段),
//   零采集 / 零 LLM / 零重析 / 零写库。
// 展示:大盘最灵形式 headline + 每个官号一张建议卡(该发什么形式 · 最佳时段 ·
//   配合哪个 SKU),带 Sparkline 播放趋势与 FreshnessDot 新鲜度。
// 诚实态:官号 0 / 帖子 0 时后端 status=data_missing/empty,本面板原样展示 note,
//   绝不本地编数;每个数字带后端 basis;时段口径为 UTC(前端不硬编码本地时段名)。
// 红线:纯展示,绝不渲染/触碰 viltrox_fit_score 与 rule_v0;接口失败整块安静缺席。
import React from "react";
import { Megaphone } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import { FreshnessDot } from "./ui/FreshnessDot";
import { Sparkline } from "./ui/Sparkline";
import { SkeletonBlock } from "./ui/SkeletonBlock";

const e = React.createElement;

type FormBucket = {
  form?: string; form_label?: string; count?: number; share?: number;
  avg_views?: number; avg_engagement_rate?: number; low_sample?: boolean;
};
type DaypartBucket = {
  bucket?: string; label?: string; count?: number; share?: number; avg_views?: number;
};
type ChannelPlan = {
  channel_id?: number; platform?: string; handle?: string; display_name?: string;
  url?: string | null; followers?: number; followers_basis?: string;
  posts_analyzed?: number; avg_views?: number; avg_engagement_rate?: number;
  best_form?: FormBucket | null; best_daypart?: DaypartBucket | null;
  relevance_hits?: number; recommendation?: string; spark?: number[];
  latest_captured_at?: string | null; note?: string | null; priority_rank?: number;
};
type PlanResp = {
  status?: string; reason?: string; method?: string; time_basis?: string;
  focus?: { sku?: string | null; category?: string | null; sku_resolved?: boolean;
    resolved_product?: { marketing_name?: string; model_name?: string } | null; note?: string | null };
  focused?: boolean; official_count?: number; posts_analyzed?: number;
  latest_snapshot_date?: string | null;
  overall_best_form?: FormBucket | null;
  channels?: ChannelPlan[]; basis?: string; note?: string | null;
};

// 内容形式视觉体系(建议徽章配色):发布=emerald/促销=rose/教程=cyan/社区=violet/样片=amber/规格=slate
const FORM_STYLE: Record<string, { badge: string }> = {
  product_launch: { badge: "bg-emerald-500/15 text-emerald-200 border-emerald-300/20" },
  promo_offer: { badge: "bg-rose-500/15 text-rose-200 border-rose-300/20" },
  tutorial_tips: { badge: "bg-cyan-500/15 text-cyan-200 border-cyan-300/20" },
  community_ugc: { badge: "bg-violet-500/15 text-violet-200 border-violet-300/20" },
  sample_footage: { badge: "bg-amber-500/12 text-amber-200 border-amber-300/20" },
  spec_showcase: { badge: "bg-slate-500/15 text-slate-300 border-slate-300/15" },
};

function fmtNum(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(Number(n))) return "—";
  const v = Number(n);
  if (v >= 1_000_000) return (Math.round(v / 100_000) / 10).toFixed(1) + "M";
  if (v >= 1_000) return (Math.round(v / 100) / 10).toFixed(1) + "K";
  return String(v);
}

function fmtRate(r: number | null | undefined): string {
  return typeof r === "number" && Number.isFinite(r) ? (Math.round(r * 1000) / 10).toFixed(1) + "%" : "—";
}

// 内容形式徽章(统一;title 带样本量,建议可解释)
function FormBadge(bucket: FormBucket | null | undefined) {
  if (!bucket || !bucket.form) return null;
  const style = FORM_STYLE[String(bucket.form)] || FORM_STYLE.spec_showcase;
  const low = bucket.low_sample ? " ·样本少" : "";
  return e("span", {
    className: `inline-flex items-center rounded border px-1.5 py-0.5 text-[9px] ${style.badge}`,
    title: `平均播放 ${fmtNum(bucket.avg_views)} · 互动率 ${fmtRate(bucket.avg_engagement_rate)} · 样本 ${bucket.count || 0} 帖`,
  }, String(bucket.form_label || bucket.form) + low);
}

// 单官号建议卡
function ChannelCard(c: ChannelPlan, focused: boolean) {
  const spark = Array.isArray(c.spark) ? c.spark : [];
  return e("div", {
    key: c.channel_id,
    className: "rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-2",
  },
    // 头行:排名 + 平台 + handle + 新鲜度 + 相关命中
    e("div", { className: "flex items-center gap-1.5" },
      e("span", { className: "text-[9px] tabular-nums text-slate-500" }, `#${c.priority_rank ?? "—"}`),
      c.platform && e("span", { className: "rounded bg-white/[0.05] px-1.5 py-0.5 text-[9px] text-slate-300" }, String(c.platform)),
      e("span", { className: "truncate text-[11px] font-medium text-slate-200" }, String(c.handle || "")),
      e(FreshnessDot, { ts: c.latest_captured_at || undefined, label: "最新快照" }),
      focused && (c.relevance_hits ?? 0) > 0 && e("span", {
        className: "ml-auto rounded bg-emerald-500/10 px-1 py-0.5 text-[8.5px] text-emerald-200/90",
        title: "标题命中聚焦 SKU/品类 token 的帖数",
      }, `相关 ${c.relevance_hits}`),
    ),
    // 建议行(核心动作)
    e("div", { className: "mt-1 text-[10.5px] leading-relaxed text-slate-300" }, String(c.recommendation || "")),
    // 徽章行:最灵形式 + 最佳时段
    e("div", { className: "mt-1 flex flex-wrap items-center gap-1.5" },
      FormBadge(c.best_form),
      c.best_daypart && c.best_daypart.label && e("span", {
        className: "rounded border border-white/[0.06] bg-white/[0.03] px-1.5 py-0.5 text-[9px] text-slate-300",
        title: `该时段平均播放 ${fmtNum(c.best_daypart.avg_views)} · 样本 ${c.best_daypart.count || 0} 帖`,
      }, "🕑 " + String(c.best_daypart.label)),
    ),
    // 数据脚:帖数 · 均播放 · 互动率 · 粉丝 + sparkline
    e("div", { className: "mt-1.5 flex items-center gap-2 text-[9px] tabular-nums text-slate-500" },
      e("span", null, `${c.posts_analyzed || 0} 帖`),
      e("span", null, `均播放 ${fmtNum(c.avg_views)}`),
      e("span", null, `互动 ${fmtRate(c.avg_engagement_rate)}`),
      e("span", null, `粉丝 ${fmtNum(c.followers)}`),
      spark.length >= 2 && e("span", { className: "ml-auto text-slate-500" },
        e(Sparkline, { data: spark, width: 56, height: 14, title: "近帖播放趋势(旧→新)" })),
    ),
    c.note && e("div", { className: "mt-1 text-[9px] text-amber-300/80" }, String(c.note)),
  );
}

export function OfficialPlannerPanel(
  { apiToken, sku, category }: { apiToken?: string; sku?: string; category?: string },
) {
  const [data, setData] = React.useState<PlanResp | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [expanded, setExpanded] = React.useState(false);

  React.useEffect(() => {
    setData(null);
    if (!apiToken) return;
    let cancelled = false;
    setLoading(true);
    const qs = new URLSearchParams();
    if (sku) qs.set("sku", sku);
    if (category) qs.set("category", category);
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    void apiFetch<PlanResp>(`/api/admin/vkpi/channel/official-plan${suffix}`, {}, apiToken)
      .then((payload) => { if (!cancelled) setData(payload && typeof payload === "object" ? payload : null); })
      .catch(() => { if (!cancelled) setData(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [apiToken, sku, category]);

  if (!apiToken) return null;
  if (loading && !data) {
    return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" }, e(SkeletonBlock, { lines: 4 }));
  }
  if (!data || String(data.status || "") === "error") return null; // 聚合失败:安静缺席

  const focus = data.focus || {};
  const focused = Boolean(data.focused);

  // 数据缺失诚实态:官号 0 / 帖子 0 → 展示后端 note,不编数。
  if (data.status === "data_missing" || (Array.isArray(data.channels) && data.channels.length === 0)) {
    return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
      e("div", { className: "flex items-center gap-1.5" },
        e(Megaphone, { size: 11, className: "text-cyan-300" }),
        e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "官号内容计划器"),
      ),
      e("div", { className: "mt-1 text-[10px] text-amber-300/90" }, String(data.note || data.reason || "官号数据缺失")),
    );
  }

  const channels = Array.isArray(data.channels) ? data.channels : [];
  const shown = expanded ? channels : channels.slice(0, 6);
  const overall = data.overall_best_form || null;
  const focusName = focus.resolved_product?.marketing_name || focus.resolved_product?.model_name || focus.sku;

  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    // 卡头:标题 + 新鲜度 + 规模
    e("div", { className: "flex items-center gap-1.5" },
      e(Megaphone, { size: 11, className: "text-cyan-300" }),
      e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "官号内容计划器 · Owned Media Planner"),
      e(FreshnessDot, { ts: data.latest_snapshot_date || undefined, label: "最新快照" }),
      e("span", { className: "ml-auto text-[9px] tabular-nums text-slate-500" },
        `${data.official_count || 0} 官号 · ${data.posts_analyzed || 0} 帖`),
    ),

    // 聚焦条(有 SKU/品类时)
    (focus.sku || focus.category) && e("div", { className: "mt-1 flex flex-wrap items-center gap-1.5 text-[9px]" },
      e("span", { className: "text-slate-500" }, "聚焦:"),
      focusName && e("span", { className: "rounded bg-cyan-500/10 px-1.5 py-0.5 text-cyan-200/90" }, String(focusName)),
      focus.category && e("span", { className: "rounded bg-white/[0.05] px-1.5 py-0.5 text-slate-300" }, String(focus.category)),
      focus.sku && focus.sku_resolved === false && e("span", { className: "text-amber-300/80" }, "(未在库内命中,按字面匹配)"),
    ),

    // 大盘最灵形式 headline
    overall && overall.form && e("div", { className: "mt-2 flex items-center gap-1.5 text-[10px]" },
      e("span", { className: "text-slate-500" }, "大盘最灵形式:"),
      FormBadge(overall),
      e("span", { className: "text-[9px] tabular-nums text-slate-500" },
        `均播放 ${fmtNum(overall.avg_views)} · ${overall.count || 0} 帖`),
    ),

    // 官号建议卡列表
    e("div", { className: "mt-2 space-y-1.5" }, shown.map((c) => ChannelCard(c, focused))),

    // 展开/收起
    channels.length > 6 && e("button", {
      type: "button",
      onClick: () => setExpanded((v) => !v),
      className: "mt-1.5 text-[9px] text-slate-500 hover:text-slate-300 underline decoration-dotted underline-offset-2",
    }, expanded ? "收起" : `展开全部 ${channels.length} 官号`),

    // 口径脚注(诚实 basis)
    e("div", { className: "mt-1.5 text-[9px] leading-relaxed text-slate-600" },
      String(data.basis || "") + " " + String(data.time_basis || "") +
      " 纯读展示,不参与 V6 Fit 评分,不触发任何采集/重析。"),
  );
}
