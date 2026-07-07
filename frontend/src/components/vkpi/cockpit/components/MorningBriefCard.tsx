// 件 B 夜班经理晨报卡:「昨晚我做了 X 件」— Dashboard 右侧栏。
// 数据:GET /api/admin/vkpi/morning-brief(默认窗口 16h=昨天16:00→今晨8:00 本地锚点),
// 后端纯 SQL 聚合已有任务/告警/记账数据,零 LLM,卡片加载即算(按需,无 cron)。
// 结构:headline 大字 + 分组小计 chips(抓取/深析/评论/新KOL/告警/AI花费)
//      + 异常红条(窗口内失败任务 TOP3 带原因 + danger 告警)。
// 诚实态:各段 status==="empty" 如实展示 0/reason;接口失败整卡降级为「--」,绝不编造数字。
// 红线:纯展示,零外部动作;绝不渲染/触碰 viltrox_fit_score 与 rule_v0。
// U2:卡头加新鲜度呼吸灯(generated_at,title 给具体时间)+ 计数 chips 旁挂 DeltaBadge
//     (较上次访问 ↑↓,localStorage 基线,变化时 300ms 高亮一次)+ 加载态换骨架屏。
import React from "react";
import { AlertTriangle, Loader2, Sunrise } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import { DeltaBadge } from "./ui/DeltaBadge";
import { FreshnessDot } from "./ui/FreshnessDot";
import { SkeletonBlock } from "./ui/SkeletonBlock";

const e = React.createElement;

type Section = {
  key?: string;
  label?: string;
  status?: string;
  reason?: string;
  // scraping
  done?: number; failed?: number; total?: number;
  avg_duration_seconds?: number | null; timed_sample?: number;
  by_type?: { job_type?: string; status?: string; count?: number }[];
  // deep_analysis
  final_v1?: number;
  by_method?: { derive_method?: string; count?: number }[];
  // comments
  new_comments?: number; posts_covered?: number; collection_runs?: number;
  // kol_pool
  new_count?: number;
  examples?: { handle?: string; display_name?: string; platform?: string; followers?: number | null;
    severity?: string; title?: string; created_at?: string }[];
  // alerts
  new_total?: number; open_now?: number;
  by_severity?: Record<string, number>;
  // budget
  ai_total_usd?: number; ai_calls?: number; business_cost_cents?: number; business_entries?: number;
  by_provider?: { provider?: string; cost_usd?: number; calls?: number }[];
  // anomalies
  failed_total?: number; danger_alerts?: number;
  failed_top?: { job_id?: number; job_type?: string; category?: string | null; reason?: string; failed_at?: string | null }[];
};

type BriefResp = {
  status?: string;
  reason?: string;
  headline?: string;
  window?: { hours?: number; start_local?: string; end_local?: string; timezone?: string };
  totals?: {
    scrape_done?: number; deep_done?: number; comments_new?: number;
    kol_new?: number; alerts_new?: number; failed_jobs?: number;
  };
  sections?: Section[];
  generated_at?: string;
};

function fmtUsd(n: number | null | undefined): string {
  if (typeof n !== "number" || !Number.isFinite(n)) return "--";
  return "$" + (Math.round(n * 10000) / 10000).toString();
}

// 分组小计 chip:数值为 0 也如实显示(诚实 0,不隐藏)。
function StatChip(label: string, value: string | number, tone: "ok" | "muted" | "warn", key: string) {
  const toneCls =
    tone === "warn"
      ? "border-rose-400/20 bg-rose-500/10 text-rose-200"
      : tone === "ok"
        ? "border-white/[0.08] bg-white/[0.04] text-slate-200"
        : "border-white/[0.05] bg-black/20 text-slate-500";
  return e(
    "span",
    { key, className: "rounded border px-1.5 py-0.5 text-[9.5px] tabular-nums " + toneCls },
    label + " ",
    e("span", { className: "font-semibold" }, String(value)),
  );
}

interface MorningBriefCardProps {
  apiToken?: string;
}

export function MorningBriefCard({ apiToken = "" }: MorningBriefCardProps) {
  const [data, setData] = React.useState<BriefResp | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [errored, setErrored] = React.useState(false);

  // 卡片加载即算(后端纯 SQL 聚合,读得起);失败降级不编数字。
  React.useEffect(() => {
    if (!apiToken) {
      setData(null);
      setErrored(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setErrored(false);
    void apiFetch<BriefResp>("/api/admin/vkpi/morning-brief", {}, apiToken)
      .then((payload) => {
        if (cancelled) return;
        if (payload && typeof payload === "object" && String(payload.status || "") !== "error") {
          setData(payload);
        } else {
          setErrored(true);
          setData(null);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setErrored(true);
          setData(null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken]);

  if (!apiToken) return null;

  const totals = data?.totals || {};
  const sections = Array.isArray(data?.sections) ? data!.sections! : [];
  const byKey: Record<string, Section> = {};
  for (const s of sections) if (s && s.key) byKey[s.key] = s;
  const anomalies = byKey["anomalies"] || null;
  const budget = byKey["budget"] || null;
  const alerts = byKey["alerts"] || null;
  const failedTop = anomalies && Array.isArray(anomalies.failed_top) ? anomalies.failed_top : [];
  const failedTotal = Number(anomalies?.failed_total) || 0;
  const dangerAlerts = Number(anomalies?.danger_alerts) || 0;
  const hasAnomaly = failedTotal > 0 || dangerAlerts > 0;
  const windowLabel = data?.window
    ? `窗口 ${Number(data.window.hours) || 16}h · 至今晨8点(${String(data.window.timezone || "本地")})`
    : "";

  return e(
    "div",
    { className: "rounded-xl border border-white/[0.08] bg-white/[0.02] p-4 backdrop-blur-xl" },
    // ── 卡头 ──
    e(
      "div",
      { className: "mb-2 flex items-center justify-between" },
      e(
        "div",
        { className: "flex items-center gap-2" },
        e(Sunrise, { size: 14, className: hasAnomaly ? "text-amber-300" : "text-emerald-300" }),
        e("h3", { className: "text-sm font-semibold text-white" }, "夜班晨报"),
        // U2 新鲜度呼吸灯:这份晨报算出来的时刻(绿≤24h/黄≤72h/红=stale)
        data && data.generated_at
          ? e(FreshnessDot, { key: "fresh", ts: data.generated_at, label: "晨报数据" })
          : null,
      ),
      loading ? e(Loader2, { size: 12, className: "animate-spin text-slate-500" }) : null,
    ),

    // ── headline:昨晚完成 X 件(接口失败/未回来 → 「--」,绝不编造)──
    // U2:加载期用骨架屏替代「…」文字(shimmer,reduced-motion 降级静态)
    errored || (!loading && !data)
      ? e("p", { className: "text-[11px] text-slate-500" }, "-- 晨报读取失败,稍后重试")
      : loading && !data
        ? e(SkeletonBlock, { className: "h-3.5 w-3/4 rounded" })
        : e(
            "p",
            { className: "text-[11.5px] font-medium leading-relaxed text-slate-100" },
            data?.headline || "--",
          ),

    // ── 分组小计 chips(真实计数,0 也如实显示)──
    // U2:每个计数 chip 旁挂 DeltaBadge(较上次访问 ↑↓;无基线/持平安静缺席;
    //     基线存 localStorage,变化时 300ms 高亮一次;告警以「降」为好消息)
    data &&
      e(
        "div",
        { className: "mt-2 flex flex-wrap items-center gap-1" },
        StatChip("抓取", Number(totals.scrape_done) || 0, (Number(totals.scrape_done) || 0) > 0 ? "ok" : "muted", "c-scrape"),
        e(DeltaBadge, { key: "d-scrape", id: "brief.scrape", value: Number(totals.scrape_done) || 0 }),
        StatChip("深析", Number(totals.deep_done) || 0, (Number(totals.deep_done) || 0) > 0 ? "ok" : "muted", "c-deep"),
        e(DeltaBadge, { key: "d-deep", id: "brief.deep", value: Number(totals.deep_done) || 0 }),
        StatChip("评论", Number(totals.comments_new) || 0, (Number(totals.comments_new) || 0) > 0 ? "ok" : "muted", "c-comments"),
        e(DeltaBadge, { key: "d-comments", id: "brief.comments", value: Number(totals.comments_new) || 0 }),
        StatChip("新KOL", Number(totals.kol_new) || 0, (Number(totals.kol_new) || 0) > 0 ? "ok" : "muted", "c-kol"),
        e(DeltaBadge, { key: "d-kol", id: "brief.kol", value: Number(totals.kol_new) || 0 }),
        StatChip("新告警", Number(totals.alerts_new) || 0, (Number(totals.alerts_new) || 0) > 0 ? "warn" : "muted", "c-alerts"),
        e(DeltaBadge, { key: "d-alerts", id: "brief.alerts", value: Number(totals.alerts_new) || 0, good: "down" }),
        budget && budget.status === "ready"
          ? StatChip("AI花费", fmtUsd(budget.ai_total_usd), "ok", "c-budget")
          : StatChip("AI花费", "$0", "muted", "c-budget"),
      ),

    // ── 告警快照小注(open 为当前快照,不限窗)──
    data && alerts && typeof alerts.open_now === "number"
      ? e("div", { className: "mt-1 text-[9px] text-slate-600" }, `当前未解决告警 ${alerts.open_now} 条(快照,不限窗口)`)
      : null,

    // ── 异常红条:失败任务 TOP3 带原因 + danger 告警(纯展示,不提供任何执行按钮)──
    data && hasAnomaly
      ? e(
          "div",
          { className: "mt-2 rounded border border-rose-400/20 bg-rose-500/[0.07] px-2 py-1.5" },
          e(
            "div",
            { className: "flex items-center gap-1.5" },
            e(AlertTriangle, { size: 11, className: "text-rose-300" }),
            e(
              "span",
              { className: "text-[10px] font-medium text-rose-200" },
              `异常:失败任务 ${failedTotal} 个` + (dangerAlerts > 0 ? ` · danger 告警 ${dangerAlerts} 条` : ""),
            ),
          ),
          e(
            "div",
            { className: "mt-1 space-y-0.5" },
            failedTop.slice(0, 3).map((f, i) =>
              e(
                "div",
                { key: f.job_id || i, className: "truncate text-[9px] leading-relaxed text-rose-200/80", title: f.reason || "" },
                `#${f.job_id ?? "?"} ${f.job_type || "job"}` + (f.category ? `[${f.category}]` : "") + ":" + (f.reason || "(无错误文本)"),
              ),
            ),
          ),
        )
      : data && anomalies && anomalies.status === "empty"
        ? e("div", { className: "mt-2 text-[9px] text-emerald-300/70" }, "窗口内无失败任务、无 danger 告警")
        : null,

    // ── 脚注:窗口与口径 ──
    data &&
      e(
        "p",
        { className: "mt-2 text-[9px] leading-relaxed text-slate-600" },
        (windowLabel ? windowLabel + " · " : "") + "纯读聚合已有数据,按需计算;不参与任何评分",
      ),
  );
}
