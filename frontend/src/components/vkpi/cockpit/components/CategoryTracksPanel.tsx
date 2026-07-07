// S2 新赛道机会分析面板:「下一个该进的品类/焦段赛道是哪」机会矩阵。
// 数据:GET /api/admin/vkpi/strategy/category-tracks —— 纯 SQL/词表规则聚合库内真数据
// (评论+意向队列近60天 / 证据标题近180天 / vkpi_products 目录),零新采集/零 LLM/零写库。
// 展示:机会矩阵格子图(x=需求声量 y=我方覆盖,格子色=机会分)+ 品类维/焦段维切换
//      + 点开看证据(愿望原声/焦段红格/竞品例证)+ Top 机会排序 + 「不进」清单。
// 诚实态:status==="empty" 如实展示 reason;接口失败整块安静缺席;机会分权重 v0 待校准
//        (basis 原文来自后端,不本地编造)。
// 红线:纯展示,绝不渲染/触碰 viltrox_fit_score 与 rule_v0;绝不编造市场数据。
import React from "react";
import { Ban, LayoutGrid, TrendingDown, TrendingUp } from "lucide-react";
import { apiFetch } from "../../../../services/http";

const e = React.createElement;

type Quote = { text?: string; author?: string; platform?: string; at?: string; likes?: number; source?: string };
type Demand = {
  total?: number; norm?: number;
  comment_mentions?: number; comment_recent?: number; comment_prev?: number;
  comment_trend?: string; comment_mom_pct?: number;
  evidence_mentions?: number; evidence_recent?: number; evidence_prev?: number;
  evidence_trend?: string; evidence_mom_pct?: number;
  wish_count?: number; wish_quotes?: Quote[]; voice_quotes?: Quote[];
};
type Coverage = { sku_count?: number; our_voice_videos?: number; points?: number; norm?: number };
type Competitors = {
  status?: string; reason?: string; total_mentions?: number; brand_count?: number;
  top_brand?: string | null; top_share?: number | null; hhi?: number | null;
  monopoly?: boolean; openness?: number; sample_sufficient?: boolean;
  example?: { brand?: string; title?: string; view_count?: number | null; kol_name?: string; content_url?: string } | null;
};
type Opportunity = { score?: number; demand_norm?: number; weakness?: number; openness?: number; confidence?: string; basis?: string };
type TrackItem = {
  track_id?: string; dimension?: string; key?: string; label?: string; mm?: number; in_catalog?: boolean;
  demand?: Demand; coverage?: Coverage; competitors?: Competitors; opportunity?: Opportunity; confidence?: string;
};
type OppItem = {
  track_id?: string; dimension?: string; label?: string; opportunity?: Opportunity;
  demand?: Demand; coverage?: Coverage; competitors?: Competitors;
  evidence?: { wish_quotes?: Quote[]; voice_quotes?: Quote[]; coverage_red_flags?: string[]; competitor_example?: Competitors["example"]; competitor_note?: string };
};
type NoGoItem = { track_id?: string; dimension?: string; label?: string; reason?: string; demand_total?: number; opportunity_score?: number };
type TracksResp = {
  status?: string; reason?: string; method?: string;
  windows?: { comments?: { days?: number }; evidence?: { days?: number } };
  sources?: { voice_docs?: number; evidence_rows?: number; catalog_skus?: number };
  basis?: { formula?: string; weights?: Record<string, unknown>; normalization?: string };
  category_tracks?: TrackItem[]; focal_tracks?: TrackItem[];
  opportunities?: OppItem[]; no_go?: NoGoItem[];
  mount_signals?: { mount?: string; wish_count?: number; quotes?: Quote[] }[];
};

function fmtNum(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(Number(n))) return "—";
  const v = Number(n);
  if (v >= 1_000_000) return (Math.round(v / 100_000) / 10).toFixed(1) + "M";
  if (v >= 1_000) return (Math.round(v / 100) / 10).toFixed(1) + "K";
  return String(v);
}

// 归一值 → 三档 bin(低/中/高);矩阵格子坐标用。
function binOf(norm: number): number {
  if (norm >= 0.67) return 2;
  if (norm >= 0.34) return 1;
  return 0;
}

// 机会分 → 格子/芯片配色(分越高越亮;0 分暗格)。
function scoreBg(score: number): string {
  if (score >= 40) return "rgba(52,211,153,0.22)";
  if (score >= 20) return "rgba(251,191,36,0.18)";
  if (score > 0) return "rgba(148,163,184,0.10)";
  return "rgba(148,163,184,0.04)";
}

function scoreText(score: number): string {
  if (score >= 40) return "text-emerald-300";
  if (score >= 20) return "text-amber-300";
  if (score > 0) return "text-slate-300";
  return "text-slate-600";
}

function MomBadge(trend: string, momPct: number) {
  if (trend === "rising") {
    return e("span", { className: "inline-flex items-center gap-0.5 text-emerald-400" },
      e(TrendingUp, { size: 9, strokeWidth: 2 }),
      e("span", { className: "text-[8.5px] tabular-nums" }, (momPct > 0 ? "+" : "") + momPct + "%"));
  }
  if (trend === "falling") {
    return e("span", { className: "inline-flex items-center gap-0.5 text-rose-400" },
      e(TrendingDown, { size: 9, strokeWidth: 2 }),
      e("span", { className: "text-[8.5px] tabular-nums" }, momPct + "%"));
  }
  return e("span", { className: "text-[8.5px] text-slate-600" }, "环比平稳");
}

function QuoteRow(q: Quote, i: number) {
  return e("div", { key: i, className: "rounded border border-white/[0.05] bg-black/20 px-2 py-1 text-[9.5px] leading-relaxed text-slate-300" },
    "“", String(q.text || ""), "”",
    e("span", { className: "ml-1 text-slate-600" },
      [q.author, q.platform, q.at ? String(q.at).slice(0, 10) : ""].filter(Boolean).join(" · ")),
  );
}

// 选中赛道的证据详情:四信号数字 + 愿望原声 + 焦段红格 + 竞品例证(全部来自后端,不本地编)。
function TrackDetail(t: TrackItem) {
  const d = t.demand || {};
  const c = t.coverage || {};
  const comp = t.competitors || {};
  const op = t.opportunity || {};
  const wishQuotes = Array.isArray(d.wish_quotes) ? d.wish_quotes : [];
  const voiceQuotes = Array.isArray(d.voice_quotes) ? d.voice_quotes : [];
  const quotes = wishQuotes.length > 0 ? wishQuotes : voiceQuotes;
  const redFlags: string[] = [];
  if ((Number(c.sku_count) || 0) === 0) redFlags.push("目录零 SKU(焦段红格)");
  if ((Number(c.our_voice_videos) || 0) === 0) redFlags.push("我方内容零声量");
  const topSharePct = typeof comp.top_share === "number" ? Math.round(comp.top_share * 100) : null;
  return e("div", { className: "mt-2 rounded-lg border border-white/[0.07] bg-white/[0.02] px-2.5 py-2" },
    e("div", { className: "flex flex-wrap items-center gap-1.5" },
      e("span", { className: "text-[11px] font-semibold text-slate-200" }, t.label || t.key || "—"),
      e("span", { className: "rounded px-1.5 py-0.5 text-[9px] tabular-nums " + scoreText(Number(op.score) || 0), style: { background: scoreBg(Number(op.score) || 0) } },
        "机会分 " + (Number(op.score) || 0)),
      e("span", { className: "text-[8.5px] text-slate-600" }, "置信 " + String(op.confidence || t.confidence || "low")),
      comp.monopoly && e("span", { className: "rounded bg-rose-500/10 px-1.5 py-0.5 text-[9px] text-rose-300" }, "竞品垄断"),
    ),
    // ① 需求声量 + 环比
    e("div", { className: "mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[9px] text-slate-400" },
      e("span", null, "① 需求 ", e("span", { className: "font-medium text-slate-200 tabular-nums" }, String(Number(d.total) || 0))),
      e("span", { className: "tabular-nums" }, `评论 ${Number(d.comment_recent) || 0}/${Number(d.comment_prev) || 0}(近/前30天)`),
      MomBadge(String(d.comment_trend || "stable"), Number(d.comment_mom_pct) || 0),
      e("span", { className: "tabular-nums" }, `证据 ${Number(d.evidence_recent) || 0}/${Number(d.evidence_prev) || 0}(近/前90天)`),
      MomBadge(String(d.evidence_trend || "stable"), Number(d.evidence_mom_pct) || 0),
      (Number(d.wish_count) || 0) > 0 && e("span", { className: "rounded bg-purple-500/10 px-1.5 py-0.5 text-purple-200" }, `愿望 ×${d.wish_count}`),
    ),
    // ② 我方覆盖 ③ 竞品密度
    e("div", { className: "mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[9px] text-slate-400" },
      e("span", null, "② 覆盖 ", e("span", { className: "font-medium text-slate-200 tabular-nums" }, `SKU ${Number(c.sku_count) || 0} + 我方声量 ${Number(c.our_voice_videos) || 0}`)),
      e("span", null, "③ 竞品 ", comp.status === "ready"
        ? e("span", { className: "font-medium text-slate-200" }, `${comp.top_brand || "—"} 占 ${topSharePct != null ? topSharePct + "%" : "—"}(命中 ${Number(comp.total_mentions) || 0} 条 / ${Number(comp.brand_count) || 0} 品牌)`)
        : e("span", { className: "text-slate-600" }, "词表零命中,按未垄断处理(低置信)")),
    ),
    redFlags.length > 0 && e("div", { className: "mt-1 flex flex-wrap gap-1" },
      redFlags.map((f, i) => e("span", { key: i, className: "rounded bg-rose-500/10 px-1.5 py-0.5 text-[8.5px] text-rose-300/90" }, f))),
    quotes.length > 0 && e("div", { className: "mt-1.5 space-y-1" },
      e("div", { className: "text-[8.5px] text-slate-500" }, wishQuotes.length > 0 ? "愿望清单原声:" : "声量原声(无明确愿望表达):"),
      quotes.slice(0, 3).map((q, i) => QuoteRow(q, i)),
    ),
    comp.example && e("div", { className: "mt-1.5 text-[9px] text-slate-500" },
      "竞品例证:",
      comp.example.content_url
        ? e("a", { href: comp.example.content_url, target: "_blank", rel: "noreferrer", className: "text-slate-300 hover:text-cyan-200 hover:underline" }, comp.example.title || comp.example.content_url)
        : e("span", { className: "text-slate-300" }, comp.example.title || "—"),
      e("span", { className: "text-slate-600" }, ` — ${comp.example.brand || ""}${comp.example.view_count != null ? `(播放 ${fmtNum(comp.example.view_count)})` : ""}`),
    ),
    op.basis && e("div", { className: "mt-1.5 text-[8px] leading-relaxed text-slate-600" }, "basis:" + String(op.basis)),
  );
}

export function CategoryTracksPanel({ apiToken }: any) {
  const [data, setData] = React.useState<TracksResp | null>(null);
  const [dim, setDim] = React.useState<"category" | "focal">("category");
  const [selectedId, setSelectedId] = React.useState<string>("");

  // 挂载后只读拉取一次(后端纯聚合库内数据,决定性结果,无需轮询)。
  React.useEffect(() => {
    setData(null);
    if (!apiToken) return;
    let cancelled = false;
    void apiFetch<TracksResp>("/api/admin/vkpi/strategy/category-tracks", { timeoutMs: 20000 }, apiToken)
      .then((payload) => { if (!cancelled) setData(payload && typeof payload === "object" ? payload : null); })
      .catch(() => { if (!cancelled) setData(null); });
    return () => { cancelled = true; };
  }, [apiToken]);

  if (!apiToken || !data) return null;
  if (String(data.status || "") === "error") return null; // 聚合失败:安静缺席,不甩后端报错

  const tracks = (dim === "category" ? data.category_tracks : data.focal_tracks) || [];
  const opportunities = Array.isArray(data.opportunities) ? data.opportunities : [];
  const noGo = Array.isArray(data.no_go) ? data.no_go : [];
  const mounts = Array.isArray(data.mount_signals) ? data.mount_signals : [];
  const sources = data.sources || {};
  const selected = tracks.find((t) => t.track_id === selectedId) || null;

  // 3×3 机会矩阵:x=需求 bin(低→高),y=我方覆盖 bin(高→低,顶行=覆盖高)。
  const cells: TrackItem[][][] = [0, 1, 2].map(() => [0, 1, 2].map(() => [] as TrackItem[]));
  for (const t of tracks) {
    const dx = binOf(Number(t.demand?.norm) || 0);
    const cy = binOf(Number(t.coverage?.norm) || 0);
    cells[2 - cy][dx].push(t);
  }
  for (const row of cells) for (const cell of row) cell.sort((a, b) => (Number(b.opportunity?.score) || 0) - (Number(a.opportunity?.score) || 0));

  const covLabels = ["覆盖高", "覆盖中", "覆盖低"];
  const demandLabels = ["需求低", "需求中", "需求高"];

  return e("div", { className: "mb-4 rounded-xl border border-white/[0.06] bg-white/[0.012] overflow-hidden" },
    // ── 头部:标题 + 品类维/焦段维切换 ──
    e("div", { className: "flex items-center justify-between gap-2 px-3 py-1.5 border-b border-white/[0.05]" },
      e("div", { className: "flex items-center gap-2" },
        e(LayoutGrid, { size: 13, className: "text-emerald-400", strokeWidth: 2 }),
        e("span", { className: "text-[11px] font-medium text-slate-300" }, "新赛道机会 · 品类×焦段"),
      ),
      e("div", { className: "flex items-center gap-1" },
        (["category", "focal"] as const).map((key) => e("button", {
          key,
          type: "button",
          onClick: () => { setDim(key); setSelectedId(""); },
          className: "rounded px-1.5 py-0.5 text-[9px] " + (dim === key ? "bg-emerald-400/15 text-emerald-200" : "text-slate-500 hover:text-slate-300"),
        }, key === "category" ? "品类维" : "焦段维")),
      ),
    ),
    String(data.status || "") === "empty"
      // 诚实空态:reason 直接来自后端,不本地编造。
      ? e("div", { className: "px-3 py-2 text-[10px] text-amber-300/90" }, String(data.reason || "窗口内暂无声量数据"))
      : e(React.Fragment, null,
          // ── 机会矩阵格子图:x=需求 y=我方覆盖,格子色=机会分,点赛道芯片看证据 ──
          e("div", { className: "px-3 pt-2" },
            e("div", { className: "space-y-1" },
              cells.map((row, ri) => e("div", { key: ri, className: "flex items-stretch gap-1" },
                e("div", { className: "flex w-[38px] shrink-0 items-center text-[8.5px] text-slate-600" }, covLabels[ri]),
                row.map((cell, ci) => {
                  const maxScore = cell.reduce((acc, t) => Math.max(acc, Number(t.opportunity?.score) || 0), 0);
                  return e("div", {
                    key: ci,
                    className: "min-h-[44px] flex-1 rounded border border-white/[0.05] p-1",
                    style: { background: scoreBg(maxScore) },
                    title: `${covLabels[ri]} × ${demandLabels[ci]} · ${cell.length} 条赛道`,
                  },
                    e("div", { className: "flex flex-wrap gap-0.5" },
                      cell.slice(0, 6).map((t) => {
                        const score = Number(t.opportunity?.score) || 0;
                        const active = t.track_id === selectedId;
                        return e("button", {
                          key: t.track_id,
                          type: "button",
                          onClick: () => setSelectedId(active ? "" : String(t.track_id || "")),
                          className: "rounded px-1 py-0.5 text-[8.5px] leading-none border " +
                            (active ? "border-emerald-300/50 bg-emerald-400/15 text-emerald-100" : "border-white/[0.07] bg-black/25 hover:border-white/20 " + scoreText(score)),
                          title: `${t.label || t.key}:机会分 ${score}(需求 ${Number(t.demand?.total) || 0} / SKU ${Number(t.coverage?.sku_count) || 0})`,
                        }, `${t.key || "—"} ${score}`);
                      }),
                      cell.length > 6 && e("span", { className: "px-0.5 text-[8px] text-slate-600" }, `+${cell.length - 6}`),
                    ),
                  );
                }),
              )),
              e("div", { className: "flex items-center gap-1 pl-[42px]" },
                demandLabels.map((l) => e("div", { key: l, className: "flex-1 text-center text-[8.5px] text-slate-600" }, l))),
            ),
            // 点开的赛道证据详情
            selected && TrackDetail(selected),
          ),
          // ── Top 机会排序(跨两维,证据齐备)──
          opportunities.length > 0 && e("div", { className: "mt-2 border-t border-white/[0.05] px-3 py-1.5" },
            e("div", { className: "mb-1 text-[9px] text-slate-500" }, "Top 机会赛道(需求高 × 我方弱 × 竞品未垄断)"),
            e("div", { className: "space-y-0.5" },
              opportunities.slice(0, 5).map((op, i) => e("button", {
                key: op.track_id || i,
                type: "button",
                onClick: () => {
                  const d = String(op.dimension || "category") === "focal" ? "focal" : "category";
                  setDim(d);
                  setSelectedId(String(op.track_id || ""));
                },
                className: "flex w-full items-center gap-2 rounded px-1 py-0.5 text-left hover:bg-white/[0.03]",
              },
                e("span", { className: "w-[30px] shrink-0 text-right text-[10px] font-bold tabular-nums " + scoreText(Number(op.opportunity?.score) || 0) }, String(Number(op.opportunity?.score) || 0)),
                e("span", { className: "min-w-0 flex-1 truncate text-[10px] text-slate-300" }, op.label || op.track_id || "—"),
                (Number(op.demand?.wish_count) || 0) > 0 && e("span", { className: "shrink-0 rounded bg-purple-500/10 px-1 py-0.5 text-[8px] text-purple-200" }, `愿望×${op.demand?.wish_count}`),
                e("span", { className: "shrink-0 text-[8.5px] text-slate-600" }, String(op.opportunity?.confidence || "low")),
              )),
            ),
          ),
          // ── 「不进」清单:需求低或竞品垄断,诚实给理由 ──
          noGo.length > 0 && e("div", { className: "border-t border-white/[0.05] px-3 py-1.5" },
            e("div", { className: "mb-1 flex items-center gap-1 text-[9px] text-slate-500" },
              e(Ban, { size: 9, className: "text-rose-400/70" }), "不进清单(需求低 / 竞品垄断)"),
            e("div", { className: "space-y-0.5" },
              noGo.slice(0, 4).map((ng, i) => e("div", { key: ng.track_id || i, className: "text-[9px] leading-relaxed text-slate-500" },
                e("span", { className: "text-slate-400" }, (ng.label || ng.track_id || "—") + ":"),
                String(ng.reason || ""))),
              noGo.length > 4 && e("div", { className: "text-[8.5px] text-slate-600" }, `另有 ${noGo.length - 4} 条不进赛道(理由同上两类)`),
            ),
          ),
          // ── 卡口愿望信号(辅助:目录卡口口径未建,只报声量不硬算覆盖)──
          mounts.length > 0 && e("div", { className: "border-t border-white/[0.05] px-3 py-1.5" },
            e("div", { className: "flex flex-wrap items-center gap-1" },
              e("span", { className: "text-[9px] text-slate-500" }, "卡口愿望信号:"),
              mounts.slice(0, 5).map((m, i) => e("span", {
                key: m.mount || i,
                className: "rounded border border-white/[0.06] bg-black/20 px-1.5 py-0.5 text-[9px] text-slate-300",
                title: (Array.isArray(m.quotes) && m.quotes[0] ? `原声:${m.quotes[0].text || ""}` : ""),
              }, `${m.mount} ×${Number(m.wish_count) || 0}`)),
            ),
          ),
          e("div", { className: "px-3 pb-1.5 pt-1 text-[8.5px] leading-relaxed text-slate-600" },
            `口径:声音 ${Number(sources.voice_docs) || 0} 条(60天)+ 证据 ${Number(sources.evidence_rows) || 0} 条(180天)` +
            `+ 目录 ${Number(sources.catalog_skus) || 0} SKU · 纯词表/规则聚合零 LLM · 机会分权重 v0 待校准 · 不参与 Fit 评分`),
        ),
  );
}
