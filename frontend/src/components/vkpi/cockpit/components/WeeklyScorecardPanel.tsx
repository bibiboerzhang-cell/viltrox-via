// L 轨道收官件 A:周度记分卡面板 —— 「周度命中率自动出」。
// 数据:GET /api/admin/vkpi/learning/weekly-scorecard?weeks=8 —— prediction_ledger 同款
// 裁决口径按 ISO 周分桶(纯聚合已有数据,零新采集/零 LLM/零写库)。
// 三块:🔴 pending 积压红条(776 条待对答案=头号信号,顶部显著)+ 最老 top5 催办名单
//      / 📈 本周 vs 上周动量 / 📊 各组周曲线迷你条(样本荒周诚实降透明度)。
// 诚实态:某周 0 判定不画柱;整组无裁决如实写"窗口内无裁决";接口失败整块安静缺席。
// 红线:纯展示,绝不渲染/触碰 viltrox_fit_score 与 rule_v0;命中率只用于催办,不改评分。
import React from "react";
import { CalendarDays, Hourglass } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import { SectionFold } from "./SectionFold";

const e = React.createElement;

type WeekCell = {
  week?: string; week_start?: string; judged?: number; hits?: number;
  hit_rate?: number | null; sparse?: boolean;
};
type Momentum = {
  this_week?: WeekCell | null; last_week?: WeekCell | null;
  delta_pp?: number | null; direction?: string; low_sample?: boolean;
};
type Group = {
  action_type?: string; label?: string; status?: string; reason?: string; note?: string;
  judged_time_basis?: string; judged_total_all_time?: number; pending_count?: number;
  weekly?: WeekCell[]; in_range_judged?: number; in_range_hits?: number;
  in_range_hit_rate?: number | null; sparse_weeks?: number; momentum?: Momentum;
};
type ChaseItem = {
  action_type?: string; label?: string; ref?: string; detail?: string;
  pending_since?: string | null; age_days?: number | null;
};
type Backlog = {
  pending_total?: number; judged_total_all_time?: number; pending_share?: number | null;
  headline?: string; by_group?: { action_type?: string; label?: string; pending?: number }[];
  chase_top5?: ChaseItem[];
};
type ScorecardResp = {
  status?: string; reason?: string; weeks?: number; generated_at?: string;
  min_sample_per_week?: number; ledger_import?: string;
  week_axis?: { week?: string; week_start?: string }[];
  groups?: Group[]; overall?: Group; momentum?: Momentum; pending_backlog?: Backlog;
};

function fmtPct(rate: number | null | undefined): string {
  return typeof rate === "number" ? (Math.round(rate * 1000) / 10).toFixed(1) + "%" : "—";
}

// 动量方向 → 展示文案与色(判不出方向的诚实态一并覆盖,不硬画箭头)
function momentumBits(m: Momentum | undefined): { text: string; cls: string } {
  const dir = String(m?.direction || "");
  const delta = typeof m?.delta_pp === "number" ? m!.delta_pp : null;
  const deltaText = delta == null ? "" : (delta > 0 ? " +" : " ") + delta + "pp";
  if (dir === "up") return { text: "↑ 上行" + deltaText, cls: "text-emerald-300" };
  if (dir === "down") return { text: "↓ 下行" + deltaText, cls: "text-rose-300" };
  if (dir === "flat") return { text: "→ 持平" + deltaText, cls: "text-slate-400" };
  if (dir === "stalled") return { text: "本周 0 裁决(对答案停摆)", cls: "text-amber-300" };
  if (dir === "new") return { text: "上周无样本,本周开张", cls: "text-sky-300" };
  return { text: "两周皆无裁决", cls: "text-slate-600" };
}

// 单周迷你柱:高度=命中率;0 判定不画柱只留底线;样本荒周降透明度(诚实态)。
function WeekBar(cell: WeekCell, i: number) {
  const judged = Number(cell.judged) || 0;
  const rate = typeof cell.hit_rate === "number" ? cell.hit_rate : null;
  const heightPct = judged > 0 ? Math.max(12, Math.round((rate || 0) * 100)) : 0;
  const color = rate == null ? "" : rate >= 0.6 ? "rgba(52,211,153,0.75)" : rate >= 0.3 ? "rgba(251,191,36,0.75)" : "rgba(251,113,133,0.75)";
  const title = `${cell.week || ""}:判定 ${judged} / 命中 ${Number(cell.hits) || 0} / 命中率 ${fmtPct(rate)}` +
    (cell.sparse ? " · 样本荒(<5)" : "") + (judged === 0 ? " · 本周无裁决" : "");
  return e("div", { key: cell.week || i, className: "relative flex-1 self-stretch", title },
    e("div", { className: "absolute inset-x-[15%] bottom-0 h-[2px] rounded-full bg-white/[0.07]" }),
    judged > 0 && e("div", {
      className: "absolute inset-x-[15%] bottom-0 rounded-t-sm" + (cell.sparse ? " opacity-50" : ""),
      style: { height: heightPct + "%", background: color },
    }),
  );
}

// 单组一行:标签 + 周曲线迷你条 + 范围内命中率/判定数 + 动量。
function GroupRow(group: Group, i: number) {
  const weekly = Array.isArray(group.weekly) ? group.weekly : [];
  const inRange = Number(group.in_range_judged) || 0;
  const mom = momentumBits(group.momentum);
  return e("div", { key: group.action_type || i, className: "rounded border border-white/[0.05] bg-black/20 px-2 py-1.5" },
    e("div", { className: "flex items-center gap-2" },
      e("span", {
        className: "w-[108px] shrink-0 truncate text-[10px] text-slate-300",
        title: `${group.label || ""}(${group.action_type || ""})· 裁决时间口径:${group.judged_time_basis || "—"}`,
      }, group.label || group.action_type || "—"),
      e("div", { className: "flex h-[22px] flex-1 items-end gap-[2px]" }, weekly.map((w, j) => WeekBar(w, j))),
      e("span", { className: "w-[44px] shrink-0 text-right text-[10px] font-medium tabular-nums " + (inRange > 0 ? "text-slate-200" : "text-slate-600") },
        fmtPct(group.in_range_hit_rate)),
      e("span", { className: "w-[46px] shrink-0 text-right text-[9px] tabular-nums text-slate-500" }, `判定 ${inRange}`),
    ),
    e("div", { className: "mt-0.5 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[8.5px]" },
      e("span", { className: mom.cls }, mom.text),
      (Number(group.sparse_weeks) || 0) > 0 && e("span", { className: "text-amber-300/80" }, `样本荒 ${group.sparse_weeks} 周`),
      (Number(group.pending_count) || 0) > 0 && e("span", { className: "text-rose-300/90" }, `待对答案 ${group.pending_count}`),
      inRange === 0 && e("span", { className: "text-slate-600" },
        group.status === "error" ? "该组聚合失败(诚实缺席)" : group.status === "stale" ? "有历史裁决但不在本窗口" : "窗口内无裁决"),
      group.note && e("span", { className: "text-slate-600", title: String(group.note) }, "口径备注"),
    ),
  );
}

export function WeeklyScorecardPanel({ apiToken }: { apiToken: string }) {
  const [data, setData] = React.useState<ScorecardResp | null>(null);

  // 只读拉取(后端纯聚合已有数据,读得起);失败静默不渲染(非阻塞增益块)。
  React.useEffect(() => {
    setData(null);
    if (!apiToken) return;
    let cancelled = false;
    void apiFetch<ScorecardResp>("/api/admin/vkpi/learning/weekly-scorecard?weeks=8", {}, apiToken)
      .then((payload) => { if (!cancelled) setData(payload && typeof payload === "object" ? payload : null); })
      .catch(() => { if (!cancelled) setData(null); });
    return () => { cancelled = true; };
  }, [apiToken]);

  if (!apiToken || !data) return null;
  if (String(data.status || "") !== "ok") return null; // 聚合失败:安静缺席,不甩后端报错

  const groups = Array.isArray(data.groups) ? data.groups : [];
  const backlog = data.pending_backlog || {};
  const chase = Array.isArray(backlog.chase_top5) ? backlog.chase_top5 : [];
  const pendingTotal = Number(backlog.pending_total) || 0;
  const share = typeof backlog.pending_share === "number" ? backlog.pending_share : null;
  const overall = data.overall || {};
  const overallMom = momentumBits(data.momentum || overall.momentum);
  const axis = Array.isArray(data.week_axis) ? data.week_axis : [];
  const firstWeek = axis.length > 0 ? String(axis[0].week || "") : "";
  const lastWeek = axis.length > 0 ? String(axis[axis.length - 1].week || "") : "";

  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    e(SectionFold, {
      id: "weekly-scorecard",
      header: e(React.Fragment, null,
        e(CalendarDays, { size: 11, className: "text-emerald-300" }),
        e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "周度记分卡 · 命中率自动出"),
        e("span", { className: "rounded bg-emerald-500/10 px-1.5 py-0.5 text-[9px] text-emerald-200" },
          `${firstWeek} → ${lastWeek}`),
        pendingTotal > 0 && e("span", { className: "rounded bg-rose-500/15 px-1.5 py-0.5 text-[9px] font-medium text-rose-200" },
          `${pendingTotal} 待对答案`),
      ),
    },
      // ── 🔴 头号信号:pending 积压红条(显著呈现,曲线不缺算法,缺的是对答案)──
      pendingTotal > 0 && e("div", { className: "mb-2 rounded border border-rose-400/20 bg-rose-500/[0.06] px-2 py-1.5" },
        e("div", { className: "flex items-center gap-2" },
          e(Hourglass, { size: 11, className: "shrink-0 text-rose-300" }),
          e("span", { className: "text-[13px] font-semibold tabular-nums text-rose-200" }, pendingTotal),
          e("span", { className: "text-[10px] text-rose-100/90" }, "条预测待对答案"),
          share != null && e("span", { className: "text-[9px] tabular-nums text-rose-300/80" },
            `占全部预测 ${fmtPct(share)}`),
        ),
        e("div", { className: "relative mt-1 h-[7px] overflow-hidden rounded-full bg-white/[0.05]", title: String(backlog.headline || "") },
          share != null && e("div", {
            className: "absolute inset-y-0 left-0 rounded-full",
            style: { width: Math.max(2, Math.round(share * 100)) + "%", background: "linear-gradient(90deg, rgba(251,113,133,0.45), rgba(244,63,94,0.85))" },
          }),
        ),
        e("div", { className: "mt-1 text-[9px] leading-relaxed text-rose-100/80" }, String(backlog.headline || "")),
        // 谁该被催对答案:pending 最老 top5(带类型与天龄)
        chase.length > 0 && e("div", { className: "mt-1.5 space-y-0.5" },
          e("div", { className: "text-[8.5px] uppercase tracking-wider text-rose-300/70" }, "最老欠账 TOP5 · 谁该被催对答案"),
          chase.map((item, i) => e("div", { key: item.ref || i, className: "flex items-center gap-1.5 text-[9px]" },
            e("span", { className: "shrink-0 rounded bg-rose-500/15 px-1 py-px tabular-nums text-rose-200" },
              (item.age_days != null ? item.age_days : "—") + " 天"),
            e("span", { className: "shrink-0 text-slate-400" }, item.label || item.action_type || "—"),
            e("span", { className: "shrink-0 tabular-nums text-slate-500" }, item.ref || ""),
            e("span", { className: "truncate text-slate-500", title: item.detail }, item.detail || ""),
          )),
        ),
      ),

      // ── 📈 全组动量:本周 vs 上周 ──
      e("div", { className: "mb-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[9.5px]" },
        e("span", { className: "text-slate-500" }, "全组合计:"),
        e("span", { className: "tabular-nums text-slate-300" },
          `本周 ${fmtPct(overallMomCell(overall, -1))} · 上周 ${fmtPct(overallMomCell(overall, -2))}`),
        e("span", { className: overallMom.cls }, overallMom.text),
        Boolean((data.momentum || overall.momentum || {}).low_sample) &&
          e("span", { className: "text-amber-300/80" }, `样本<${Number(data.min_sample_per_week) || 5},动量仅供参考`),
      ),

      // ── 📊 各组周曲线迷你条 ──
      groups.length === 0
        ? e("div", { className: "text-[10px] leading-relaxed text-amber-300/90" }, "暂无任何台账分组数据")
        : e("div", { className: "space-y-1.5" }, groups.map((g, i) => GroupRow(g, i))),

      e("div", { className: "mt-1.5 text-[9px] text-slate-600" },
        "口径:prediction_ledger 同款裁决按 ISO 周分桶(只读聚合真实表,零 LLM);每周判定<5 诚实样本荒;pending 不计分母;记分卡永不影响任何评分。"),
      data.generated_at && e("div", { className: "mt-0.5 text-[8.5px] text-slate-700 tabular-nums" },
        "生成于 " + String(data.generated_at).slice(0, 19).replace("T", " ") + " UTC"),
    ),
  );
}

// 全组合计的本周/上周命中率(weekly 尾两格;不存在回 null 由 fmtPct 出 —)。
function overallMomCell(overall: Group, offset: -1 | -2): number | null {
  const weekly = Array.isArray(overall.weekly) ? overall.weekly : [];
  const cell = weekly[weekly.length + offset];
  return cell && typeof cell.hit_rate === "number" ? cell.hit_rate : null;
}
