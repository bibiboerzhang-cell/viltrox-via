// S1 行业对照面板:「Viltrox 在行业里站在哪」竞品全景对照(Dashboard 右栏卡片)。
// 数据:GET /api/admin/vkpi/strategy/industry-benchmark?window_days=90 —— 纯读端聚合已有数据
// (brand_pulse 同源品牌口径 + focal_matrix 焦段词表 + SKU 目录),零新采集/零 LLM/零写库。
// 三块:① 声量份额排名条(Viltrox 高亮) ② head_to_head 对照表(竞品行可展开:
// 三行对比 + 规则模板一句话差距 + 例证视频链接) ③ 焦段格局热区(sku_weak/voice_weak 格子)。
// 诚实态:接口失败整块安静缺席;无数据/词表零命中一行灰字;数字都带 basis/置信,绝不本地编数。
// 红线:纯展示,绝不渲染/触碰 viltrox_fit_score 与 rule_v0。
import React from "react";
import { BarChart3, ChevronDown, ChevronRight, Grid3X3, Swords } from "lucide-react";
import { apiFetch } from "../../../../services/http";

const e = React.createElement;

type ExampleItem = {
  evidence_id?: number | null; title?: string; content_url?: string; platform?: string;
  posted_at?: string; view_count?: number | null; kol_name?: string; matched_via?: string;
};
type BrandItem = {
  key?: string; brand?: string; brand_type?: string; category?: string;
  videos?: number; kol_count?: number; total_views?: number | null; avg_views?: number | null;
  views_known?: number; trend?: string; momentum_pct?: number;
  share_of_voice?: number | null; rank?: number | null; brand_count_ranked?: number;
  engagement?: { avg_rate?: number | null; sample?: number; confidence?: string };
  focals?: { focal?: string; mm?: number; video_count?: number }[];
  top_examples?: ExampleItem[];
};
type H2HRow = { metric?: string; label?: string; viltrox?: number | null; competitor?: number | null };
type H2HItem = { key?: string; brand?: string; rows?: H2HRow[]; verdict?: string };
type GridCell = {
  focal?: string; mm?: number; competitor_videos?: number; competitor_brands?: string[];
  viltrox_videos?: number; in_catalog?: boolean; sku_count?: number; official_sku_count?: number;
  flagship?: string | null; sku_weak?: boolean; voice_weak?: boolean;
};
type BenchResp = {
  status?: string; reason?: string; window_days?: number;
  viltrox?: BrandItem; competitors?: BrandItem[]; brand_count_ranked?: number;
  head_to_head?: H2HItem[];
  focal_grid?: { status?: string; reason?: string; cells?: GridCell[]; opportunities?: GridCell[]; voice_weak_cells?: GridCell[] };
  basis?: { videos_scanned?: number; brand_hit_videos?: number; deep_analyzed_in_window?: number };
  confidence?: { level?: string; reason?: string };
};

function fmtNum(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(Number(n))) return "—";
  const v = Number(n);
  if (v >= 1_000_000) return (Math.round(v / 100_000) / 10).toFixed(1) + "M";
  if (v >= 1_000) return (Math.round(v / 100) / 10).toFixed(1) + "K";
  return String(v);
}

function fmtRate(r: number | null | undefined): string {
  return typeof r === "number" ? (Math.round(r * 1000) / 10).toFixed(1) + "%" : "—";
}

// ── ① 排名条:声量份额横条,Viltrox 高亮 ──
function RankRow(b: BrandItem, highlight: boolean, maxVideos: number) {
  const videos = Number(b.videos) || 0;
  const widthPct = maxVideos > 0 ? Math.max(videos > 0 ? 6 : 0, Math.round((videos / maxVideos) * 100)) : 0;
  const share = typeof b.share_of_voice === "number" ? Math.round(b.share_of_voice * 1000) / 10 : null;
  return e("div", { key: b.key, className: "flex items-center gap-2 px-3 py-1" + (highlight ? " bg-cyan-400/[0.04]" : "") },
    e("span", { className: "w-[18px] shrink-0 text-right text-[9px] tabular-nums " + (highlight ? "text-cyan-300 font-bold" : "text-slate-600") },
      b.rank != null ? "#" + b.rank : "—"),
    e("span", { className: "w-[70px] shrink-0 truncate text-[10.5px] " + (highlight ? "font-semibold text-cyan-200" : "text-slate-300"), title: b.brand }, b.brand || b.key || "—"),
    e("div", { className: "relative h-[8px] flex-1 overflow-hidden rounded-full bg-white/[0.05]" },
      e("div", {
        className: "absolute inset-y-0 left-0 rounded-full",
        style: {
          width: widthPct + "%",
          background: highlight
            ? "linear-gradient(90deg, rgba(34,211,238,0.4), rgba(34,211,238,0.85))"
            : "linear-gradient(90deg, rgba(148,163,184,0.25), rgba(148,163,184,0.55))",
        },
      }),
    ),
    e("span", { className: "w-[40px] shrink-0 text-right text-[9.5px] tabular-nums " + (highlight ? "text-cyan-200" : "text-slate-400") },
      share != null ? share + "%" : "—"),
    e("span", { className: "w-[56px] shrink-0 text-right text-[9px] tabular-nums text-slate-500" },
      "×" + videos + " · " + (Number(b.kol_count) || 0) + "人"),
  );
}

// ── ② head_to_head 展开区:三行对比 + verdict + 例证视频 ──
function H2HDetail(h: H2HItem, comp: BrandItem | undefined) {
  const rows = Array.isArray(h.rows) ? h.rows : [];
  const examples = (comp && Array.isArray(comp.top_examples) ? comp.top_examples : []).slice(0, 3);
  const eng = comp && comp.engagement;
  return e("div", { className: "border-t border-white/[0.04] bg-black/20 px-3 py-2" },
    e("div", { className: "grid grid-cols-[1fr_64px_64px] gap-x-2 text-[9px]" },
      e("span", { className: "text-slate-600" }, "指标"),
      e("span", { className: "text-right text-cyan-300/90" }, "Viltrox"),
      e("span", { className: "text-right text-slate-400" }, h.brand || "竞品"),
      rows.map((r, i) => e(React.Fragment, { key: r.metric || i },
        e("span", { className: "mt-1 text-slate-400", title: r.label }, r.label || r.metric || "—"),
        e("span", { className: "mt-1 text-right tabular-nums text-cyan-100" }, fmtNum(r.viltrox)),
        e("span", { className: "mt-1 text-right tabular-nums text-slate-300" }, fmtNum(r.competitor)),
      )),
    ),
    // 内容质量侧写:该品牌被提及视频的均互动率(样本 <5 后端标 low,如实展示)
    eng && e("div", { className: "mt-1.5 text-[9px] text-slate-500" },
      `内容质量侧写:被提及视频均互动率 ${fmtRate(eng.avg_rate)}(样本 ${Number(eng.sample) || 0} 条` +
      (eng.confidence === "low" ? ",样本偏小低置信" : eng.confidence === "none" ? ",播放数缺失无样本" : "") + ")"),
    h.verdict && e("div", { className: "mt-1.5 rounded bg-amber-400/[0.06] border border-amber-300/10 px-2 py-1 text-[9.5px] leading-relaxed text-amber-100/90" },
      h.verdict),
    examples.length > 0 && e("div", { className: "mt-1.5 space-y-0.5" },
      e("div", { className: "text-[8.5px] text-slate-600" }, "例证视频(按播放排序):"),
      examples.map((ex, i) => e("div", { key: ex.evidence_id || i, className: "flex items-center gap-1.5 text-[9px]" },
        ex.content_url
          ? e("a", {
              href: ex.content_url, target: "_blank", rel: "noreferrer",
              className: "truncate text-slate-300 hover:text-cyan-200 hover:underline",
              title: ex.title || ex.content_url,
            }, ex.title || ex.content_url)
          : e("span", { className: "truncate text-slate-300" }, ex.title || "(无标题)"),
        e("span", { className: "shrink-0 tabular-nums text-slate-600" },
          fmtNum(ex.view_count) + (ex.kol_name ? " · " + ex.kol_name : "")),
      )),
    ),
  );
}

// ── ③ 焦段格局热区:格子按 mm 排,sku_weak / voice_weak / 常规三色 ──
function FocalCell(c: GridCell, i: number) {
  const weak = !!c.sku_weak;
  const voiceWeak = !!c.voice_weak;
  const cls = weak
    ? "border-rose-400/40 bg-rose-500/10 text-rose-200"
    : voiceWeak
      ? "border-amber-400/30 bg-amber-500/10 text-amber-200"
      : (Number(c.viltrox_videos) || 0) > 0
        ? "border-cyan-400/20 bg-cyan-400/[0.06] text-cyan-100"
        : "border-white/[0.08] bg-white/[0.03] text-slate-400";
  const tip = `${c.focal}:竞品声量 ${Number(c.competitor_videos) || 0} 条` +
    ((c.competitor_brands || []).length ? `(${(c.competitor_brands || []).join("/")})` : "") +
    ` · 我方声量 ${Number(c.viltrox_videos) || 0} 条 · 官方 SKU ${Number(c.official_sku_count) || 0} 个` +
    (c.flagship ? ` · 旗舰 ${c.flagship}` : "") +
    (weak ? " —— 竞品有声量而我方 SKU 覆盖弱" : voiceWeak ? " —— 有货无声(有 SKU 但零内容声量)" : "");
  return e("span", {
    key: c.focal || i,
    className: "rounded border px-1.5 py-0.5 text-[9px] tabular-nums " + cls,
    title: tip,
  }, `${c.focal} ${Number(c.competitor_videos) || 0}v${Number(c.viltrox_videos) || 0}`);
}

export function IndustryBenchmarkPanel({ apiToken }: any) {
  const [data, setData] = React.useState<BenchResp | null>(null);
  const [openKey, setOpenKey] = React.useState<string | null>(null);

  // 挂载后只读拉取一次(后端纯聚合,窗口 90 天,数据日更粒度,无需轮询)。
  React.useEffect(() => {
    setData(null);
    if (!apiToken) return;
    let cancelled = false;
    void apiFetch<BenchResp>("/api/admin/vkpi/strategy/industry-benchmark?window_days=90", { timeoutMs: 20000 }, apiToken)
      .then((payload) => { if (!cancelled) setData(payload && typeof payload === "object" ? payload : null); })
      .catch(() => { if (!cancelled) setData(null); });
    return () => { cancelled = true; };
  }, [apiToken]);

  if (!apiToken || !data) return null;
  if (String(data.status || "") === "error") return null; // 聚合失败:安静缺席,不甩后端报错

  const viltrox = data.viltrox || {};
  const competitors = Array.isArray(data.competitors) ? data.competitors : [];
  const compByKey: Record<string, BrandItem> = {};
  competitors.forEach((c) => { if (c.key) compByKey[c.key] = c; });
  const h2h = Array.isArray(data.head_to_head) ? data.head_to_head : [];
  const grid = data.focal_grid || {};
  const cells = Array.isArray(grid.cells) ? grid.cells : [];
  const opportunities = Array.isArray(grid.opportunities) ? grid.opportunities : [];
  const basis = data.basis || {};
  const conf = data.confidence || {};
  const sov = typeof viltrox.share_of_voice === "number" ? Math.round(viltrox.share_of_voice * 1000) / 10 : null;
  const noSignal = data.status === "no_data_in_window" || data.status === "no_brand_signal";
  const ranked = [viltrox, ...competitors].filter((b) => (Number(b.videos) || 0) > 0)
    .sort((a, b) => (Number(a.rank) || 999) - (Number(b.rank) || 999));
  const maxVideos = ranked.reduce((acc, b) => Math.max(acc, Number(b.videos) || 0), 0);

  return e("div", { className: "mb-4 rounded-xl border border-white/[0.06] bg-white/[0.012] overflow-hidden" },
    e("div", { className: "flex items-center justify-between gap-2 px-3 py-1.5 border-b border-white/[0.05]" },
      e("div", { className: "flex items-center gap-2" },
        e(BarChart3, { size: 13, className: "text-cyan-400", strokeWidth: 2 }),
        e("span", { className: "text-[11px] font-medium text-slate-300" }, `行业对照 · ${Number(data.window_days) || 90}天`),
      ),
      !noSignal && e("div", { className: "flex items-center gap-1.5" },
        sov != null && e("span", { className: "rounded bg-cyan-400/10 px-1.5 py-0.5 text-[9px] tabular-nums text-cyan-200", title: "Viltrox 声量占比(视频×品牌口径,与品牌脉搏同源)" }, `SoV ${sov}%`),
        viltrox.rank != null && e("span", { className: "text-[9px] tabular-nums text-slate-500" }, `#${viltrox.rank}/${Number(data.brand_count_ranked) || "—"}`),
        conf.level && e("span", {
          className: "rounded px-1.5 py-0.5 text-[8.5px] " + (conf.level === "high" ? "bg-emerald-500/10 text-emerald-300" : conf.level === "medium" ? "bg-amber-500/10 text-amber-300" : "bg-rose-500/10 text-rose-300"),
          title: String(conf.reason || ""),
        }, `置信 ${conf.level}`),
      ),
    ),
    noSignal
      // 诚实空态:窗口内没数据/词表零命中,一行灰字如实说,绝不编数。
      ? e("div", { className: "px-3 py-2 text-[10px] text-slate-500" },
          data.status === "no_data_in_window" ? "窗口内暂无入库视频证据" : "窗口内视频未命中品牌词表")
      : e(React.Fragment, null,
          // ── ① 声量份额排名条 ──
          e("div", { className: "divide-y divide-white/[0.03] py-0.5" },
            ranked.slice(0, 8).map((b) => RankRow(b, b.key === "viltrox", maxVideos)),
          ),

          // ── ② head_to_head 对照表(竞品行可展开) ──
          h2h.length > 0 && e("div", { className: "border-t border-white/[0.05]" },
            e("div", { className: "flex items-center gap-1.5 px-3 pt-1.5 pb-0.5" },
              e(Swords, { size: 10, className: "text-amber-300" }),
              e("span", { className: "text-[9px] uppercase tracking-wider text-slate-500" }, "Viltrox vs 竞品(点击展开)"),
            ),
            h2h.slice(0, 8).map((h, i) => {
              const open = openKey === h.key;
              const comp = h.key ? compByKey[h.key] : undefined;
              return e("div", { key: h.key || i, className: "border-b border-white/[0.03] last:border-b-0" },
                e("button", {
                  type: "button",
                  className: "flex w-full items-center gap-1.5 px-3 py-1 text-left hover:bg-white/[0.02]",
                  onClick: () => setOpenKey(open ? null : String(h.key || "")),
                },
                  open ? e(ChevronDown, { size: 10, className: "shrink-0 text-slate-500" }) : e(ChevronRight, { size: 10, className: "shrink-0 text-slate-600" }),
                  e("span", { className: "w-[70px] shrink-0 truncate text-[10px] text-slate-300" }, h.brand || h.key || "—"),
                  e("span", { className: "min-w-0 flex-1 truncate text-[9px] text-slate-500", title: h.verdict }, h.verdict || ""),
                ),
                open && H2HDetail(h, comp),
              );
            }),
          ),

          // ── ③ 焦段格局热区 ──
          cells.length > 0 && e("div", { className: "border-t border-white/[0.05] px-3 py-1.5" },
            e("div", { className: "flex items-center gap-1.5 pb-1" },
              e(Grid3X3, { size: 10, className: "text-rose-300" }),
              e("span", { className: "text-[9px] uppercase tracking-wider text-slate-500" }, "焦段格局(竞品声量 v 我方声量)"),
              opportunities.length > 0 && e("span", { className: "rounded bg-rose-500/10 px-1.5 py-0.5 text-[8.5px] text-rose-300", title: "竞品有声量而我方该焦段零官方 SKU" }, `SKU 空档 ×${opportunities.length}`),
            ),
            e("div", { className: "flex flex-wrap gap-1" }, cells.map((c, i) => FocalCell(c, i))),
            e("div", { className: "mt-1 flex flex-wrap items-center gap-x-2 text-[8.5px] text-slate-600" },
              e("span", { className: "text-rose-300/80" }, "■ 竞品有声量·我方零官方 SKU"),
              e("span", { className: "text-amber-300/80" }, "■ 有货无声"),
              e("span", { className: "text-cyan-200/80" }, "■ 双方都有声量"),
            ),
          ),
          grid.status === "empty" && e("div", { className: "border-t border-white/[0.05] px-3 py-1.5 text-[9px] text-slate-600" },
            String(grid.reason || "焦段格局暂无数据")),

          e("div", { className: "px-3 pb-1.5 pt-1 text-[8.5px] leading-relaxed text-slate-600" },
            `扫描 ${Number(basis.videos_scanned) || 0} 条证据 · 品牌命中 ${Number(basis.brand_hit_videos) || 0}(视频×品牌)· 深析 ${Number(basis.deep_analyzed_in_window) || 0}` +
            " · 词表规则聚合非 LLM · 差距描述为规则模板 · 不参与 Fit 评分"),
        ),
  );
}
