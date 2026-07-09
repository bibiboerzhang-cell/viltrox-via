// 品牌脉搏面板:最近 90 天全池 KOL 内容里的品牌声量周度趋势(Dashboard 右栏卡片)。
// 数据:GET /api/admin/vkpi/market/brand-pulse?window_days=90 —— 纯读端聚合已有数据
// (证据标题词表 + final_v1 深析文本),零新采集/零 LLM/零写库。
// 展示:Viltrox 自身曲线(SoV/排名)置顶 + 竞品迷你趋势条 + 升降箭头 + top 例证视频(悬停)。
// 诚实态:接口失败/空数据整块安静缺席或一行灰字;空桶周如实标注,绝不编数。
// 红线:纯展示,绝不渲染/触碰 viltrox_fit_score 与 rule_v0。
// U2:卡头加 SoV 周曲线 Sparkline(客户端派生,零契约改动)+ 新鲜度呼吸灯
//     (最新入库证据 posted_at;绿≤24h/黄≤72h/红=stale,title 给具体时间)。
import React from "react";
import { Activity, Minus, TrendingDown, TrendingUp } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import { FreshnessDot } from "./ui/FreshnessDot";
import { Sparkline } from "./ui/Sparkline";

const e = React.createElement;

type ExampleItem = {
  evidence_id?: number; title?: string; content_url?: string; platform?: string;
  posted_at?: string; view_count?: number | null; kol_pool_id?: number | null;
  kol_name?: string; matched_via?: string;
};
type BrandItem = {
  key?: string; brand?: string; brand_type?: string; category?: string;
  total_videos?: number; kol_count?: number; weekly?: number[];
  prev_sum?: number; recent_sum?: number; trend?: string; momentum_pct?: number;
  top_examples?: ExampleItem[];
  share_of_voice?: number | null; rank?: number | null; brand_count_ranked?: number;
};
type WeekBucket = { week_start?: string; videos_scanned?: number; brand_videos?: number; sparse?: boolean };
type PulseResp = {
  status?: string; reason?: string; window_days?: number;
  window_start?: string; window_end?: string;
  weeks?: WeekBucket[]; viltrox?: BrandItem; brands?: BrandItem[];
  groups?: { rising?: string[]; falling?: string[]; stable?: string[] };
  coverage?: { videos_scanned?: number; brand_hit_videos?: number; sparse_weeks?: number };
};

function fmtNum(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(Number(n))) return "—";
  const v = Number(n);
  if (v >= 1_000_000) return (Math.round(v / 100_000) / 10).toFixed(1) + "M";
  if (v >= 1_000) return (Math.round(v / 100) / 10).toFixed(1) + "K";
  return String(v);
}

// 迷你周度趋势条:每周一根,按该品牌自身峰值归一(看形状,不比绝对值)。
function WeeklyBars(weekly: number[], accent: string) {
  const max = weekly.reduce((acc, v) => Math.max(acc, Number(v) || 0), 0);
  return e("div", { className: "flex h-[14px] items-end gap-[1.5px]" },
    weekly.map((v, i) => {
      const count = Number(v) || 0;
      const hPct = max > 0 ? Math.max(count > 0 ? 18 : 0, Math.round((count / max) * 100)) : 0;
      return e("div", {
        key: i,
        className: "w-[4px] rounded-sm " + (count > 0 ? accent : "bg-white/[0.06]"),
        style: { height: (count > 0 ? hPct : 12) + "%" },
        title: `第${i + 1}周 ×${count}`,
      });
    }),
  );
}

function TrendBadge(trend: string, momentum: number) {
  if (trend === "rising") {
    return e("span", { className: "flex items-center gap-0.5 text-good" },
      e(TrendingUp, { size: 10, strokeWidth: 2 }),
      e("span", { className: "text-[9px] tabular-nums" }, (momentum > 0 ? "+" : "") + momentum + "%"));
  }
  if (trend === "falling") {
    return e("span", { className: "flex items-center gap-0.5 text-crit" },
      e(TrendingDown, { size: 10, strokeWidth: 2 }),
      e("span", { className: "text-[9px] tabular-nums" }, momentum + "%"));
  }
  return e("span", { className: "flex items-center gap-0.5 text-muted" },
    e(Minus, { size: 10, strokeWidth: 2 }),
    e("span", { className: "text-[9px]" }, "平稳"));
}

function exampleTitle(b: BrandItem): string {
  const ex = Array.isArray(b.top_examples) ? b.top_examples[0] : null;
  if (!ex) return "";
  return `例证:${ex.title || ex.content_url || ""}` +
    (ex.kol_name ? ` — ${ex.kol_name}` : "") +
    (ex.view_count != null ? `(播放 ${fmtNum(ex.view_count)})` : "");
}

// 单品牌行:名称 + 类型 + 迷你趋势条 + 升降 + 提及数;悬停显示 top 例证。
function BrandRow(b: BrandItem, i: number, highlight: boolean) {
  const weekly = Array.isArray(b.weekly) ? b.weekly : [];
  const typeTag = b.brand_type === "self" ? "" : b.brand_type === "oem" ? "原厂" : b.brand_type === "third_party" ? "副厂" : "";
  const barAccent = highlight ? "bg-cyan-400/80" : "bg-slate-400/50";
  return e("div", {
    key: b.key || i,
    className: "flex items-center gap-2 px-3 py-1.5" + (highlight ? " bg-cyan-400/[0.04]" : ""),
    title: exampleTitle(b),
  },
    e("div", { className: "w-[74px] min-w-0 shrink-0" },
      e("div", { className: "truncate text-[11px] leading-none " + (highlight ? "font-semibold text-cyan-200" : "text-ink-2") }, b.brand || b.key || "—"),
      typeTag && e("div", { className: "mt-0.5 text-[8px] leading-none text-muted" }, typeTag),
    ),
    e("div", { className: "min-w-0 flex-1" }, WeeklyBars(weekly, barAccent)),
    e("div", { className: "w-[52px] shrink-0 text-right" }, TrendBadge(String(b.trend || "stable"), Number(b.momentum_pct) || 0)),
    e("div", { className: "w-[58px] shrink-0 text-right" },
      e("span", { className: "text-[10px] tabular-nums text-ink-2" }, "×" + (Number(b.total_videos) || 0)),
      e("span", { className: "ml-1 text-[8.5px] text-muted" }, (Number(b.kol_count) || 0) + "人"),
    ),
  );
}

export function BrandPulsePanel({ apiToken }: any) {
  const [data, setData] = React.useState<PulseResp | null>(null);

  // 挂载后只读拉取一次(后端纯聚合,窗口 90 天,数据日更粒度,无需轮询)。
  React.useEffect(() => {
    setData(null);
    if (!apiToken) return;
    let cancelled = false;
    void apiFetch<PulseResp>("/api/admin/vkpi/market/brand-pulse?window_days=90", { timeoutMs: 15000 }, apiToken)
      .then((payload) => { if (!cancelled) setData(payload && typeof payload === "object" ? payload : null); })
      .catch(() => { if (!cancelled) setData(null); });
    return () => { cancelled = true; };
  }, [apiToken]);

  // U2 派生量(零契约改动,纯客户端):
  //   sovWeekly:第 i 周 SoV = Viltrox 提及 ÷ 全部品牌提及(无品牌提及的周 → null,不编 0%);
  //   latestEvidence:所有 top_examples posted_at 的最大值(数据新鲜度的诚实近似)。
  const derived = React.useMemo(() => {
    const v = data?.viltrox;
    const bs = Array.isArray(data?.brands) ? data!.brands! : [];
    const vw: number[] = Array.isArray(v?.weekly) ? (v!.weekly as number[]) : [];
    const sovWeekly: Array<number | null> = [];
    for (let i = 0; i < vw.length; i++) {
      let total = Number(vw[i]) || 0;
      for (const b of bs) total += Number(b.weekly?.[i]) || 0;
      sovWeekly.push(total > 0 ? ((Number(vw[i]) || 0) / total) * 100 : null);
    }
    let latestMs = 0;
    let latestEvidence: string | null = null;
    const scan = (items?: ExampleItem[]) => {
      for (const ex of items || []) {
        const raw = ex && ex.posted_at ? String(ex.posted_at) : "";
        const t = raw ? new Date(raw).getTime() : NaN;
        if (Number.isFinite(t) && t > latestMs) { latestMs = t; latestEvidence = raw; }
      }
    };
    scan(v?.top_examples);
    for (const b of bs) scan(b.top_examples);
    const validWeeks = sovWeekly.filter((x) => x != null).length;
    return { sovWeekly, latestEvidence: latestEvidence as string | null, hasSov: validWeeks >= 2 };
  }, [data]);

  if (!apiToken || !data) return null;
  if (String(data.status || "") === "error") return null; // 聚合失败:安静缺席,不甩后端报错

  const viltrox = data.viltrox || {};
  const brands = Array.isArray(data.brands) ? data.brands : [];
  const groups = data.groups || {};
  const rising = Array.isArray(groups.rising) ? groups.rising : [];
  const falling = Array.isArray(groups.falling) ? groups.falling : [];
  const coverage = data.coverage || {};
  const sparseWeeks = Number(coverage.sparse_weeks) || 0;
  const sov = typeof viltrox.share_of_voice === "number" ? Math.round(viltrox.share_of_voice * 1000) / 10 : null;
  const noSignal = data.status === "no_data_in_window" || data.status === "no_brand_signal";

  return e("div", { className: "mb-4 rounded-xl border border-line bg-panel overflow-hidden" },
    e("div", { className: "flex items-center justify-between gap-2 px-3 py-1.5 border-b border-line" },
      e("div", { className: "flex items-center gap-2" },
        e(Activity, { size: 13, className: "text-cyan-400", strokeWidth: 2 }),
        e("span", { className: "text-[11px] font-medium text-ink-2" }, `品牌脉搏 · ${Number(data.window_days) || 90}天`),
        // U2 新鲜度呼吸灯:最新入库证据的发布时间(无证据 → 空心灰圈,诚实「不知道」)
        !noSignal && e(FreshnessDot, { key: "fresh", ts: derived.latestEvidence, label: "最新证据" }),
      ),
      !noSignal && e("div", { className: "flex items-center gap-1.5" },
        // U2 SoV 周曲线:只看形状(客户端派生);无品牌提及的周跳过,绝不编 0%
        derived.hasSov && e("span", {
          key: "sov-spark",
          className: "inline-flex h-[16px] w-[64px] items-center text-cyan-300/80",
        }, e(Sparkline, {
          data: derived.sovWeekly, width: 64, height: 16, fill: true,
          className: "block h-full w-full",
          title: "SoV 周走势:Viltrox 提及 ÷ 全部品牌提及(按周,客户端派生;无品牌提及的周跳过)",
        })),
        sov != null && e("span", { className: "rounded bg-cyan-400/10 px-1.5 py-0.5 text-[9px] tabular-nums text-cyan-200", title: "Viltrox 声量占比(视频×品牌口径)" }, `SoV ${sov}%`),
        viltrox.rank != null && e("span", { className: "text-[9px] tabular-nums text-muted" }, `#${viltrox.rank}/${Number(viltrox.brand_count_ranked) || "—"}`),
      ),
    ),
    noSignal
      // 诚实空态:窗口内没数据/词表零命中,一行灰字如实说,绝不编数。
      ? e("div", { className: "px-3 py-2 text-[10px] text-muted" },
          data.status === "no_data_in_window" ? "窗口内暂无入库视频证据" : "窗口内视频未命中品牌词表")
      : e(React.Fragment, null,
          e("div", { className: "divide-y divide-line" },
            BrandRow(viltrox, -1, true),
            brands.slice(0, 7).map((b, i) => BrandRow(b, i, false)),
          ),
          (rising.length > 0 || falling.length > 0) && e("div", { className: "flex flex-wrap items-center gap-x-2.5 gap-y-0.5 border-t border-line px-3 py-1.5 text-[9px]" },
            rising.length > 0 && e("span", { className: "text-good" }, "升温 " + rising.map((k) => String(k)).slice(0, 4).join(" / ") + (rising.length > 4 ? ` 等${rising.length}个` : "")),
            falling.length > 0 && e("span", { className: "text-crit" }, "降温 " + falling.map((k) => String(k)).slice(0, 4).join(" / ") + (falling.length > 4 ? ` 等${falling.length}个` : "")),
          ),
          e("div", { className: "px-3 pb-1.5 pt-1 text-[8.5px] leading-relaxed text-muted" },
            `扫描 ${Number(coverage.videos_scanned) || 0} 条证据 · 命中 ${Number(coverage.brand_hit_videos) || 0} 条` +
            (sparseWeeks > 0 ? ` · ${sparseWeeks} 周无入库数据(空桶)` : "") +
            " · 趋势已按采集密度归一 · 不参与 Fit 评分"),
        ),
  );
}
