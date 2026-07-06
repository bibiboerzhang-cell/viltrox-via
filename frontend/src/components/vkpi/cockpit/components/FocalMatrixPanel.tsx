// KOL 焦段矩阵面板:这个 KOL「拍过哪些焦段/产品线,哪块是空白」(深度分析 tab 增益块)。
// 数据:GET /api/admin/vkpi/kol-pool/{id}/focal-matrix —— 纯聚合已有数据
// (evidence 标题 + final_v1 深析文本 词表/正则提焦段与产品线,对照 vkpi_products 目录),零新采集/零 LLM。
// 三块:📐 焦段矩阵格子(covered=拍过 ×N+均播放;空白+目录有 SKU=高亮「可切入」)
//      / 产品线覆盖 chips / 🎯 可切入 TOP(按目录营销价值代理排序)+ 命中我方 SKU 家族。
// 诚实态:每块 status==="empty" 时如实展示 reason;接口失败整块安静缺席(非阻塞增益块);
//         覆盖=拍过该类内容≠用的是我方产品(我方命中单独列 matched_products),不骗数据。
// 红线:纯展示,绝不渲染/触碰 viltrox_fit_score 与 rule_v0。
import React from "react";
import { Aperture, Target } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import { SectionFold } from "./SectionFold";

const e = React.createElement;

type FocalCell = {
  focal?: string; mm?: number; in_catalog?: boolean; covered?: boolean;
  video_count?: number; avg_views?: number | null; title_hits?: number; deep_hits?: number;
  top_example?: { title?: string; view_count?: number | null } | null;
  catalog?: {
    sku_count?: number; official_sku_count?: number; value_usd?: number;
    max_price_usd?: number | null; flagship?: string | null; series?: string[]; lines?: string[];
  };
};
type LineCell = {
  key?: string; label?: string; in_catalog?: boolean; catalog_sku_count?: number;
  covered?: boolean; video_count?: number; avg_views?: number | null; example_title?: string | null;
};
type GapItem = {
  focal?: string; sku_count?: number; official_sku_count?: number; value_usd?: number;
  max_price_usd?: number | null; flagship?: string | null; series?: string[]; lines?: string[];
};
type MatchedItem = {
  family?: string; focal?: string; series?: string | null; aperture?: string | null;
  skus?: string[]; matched_video_count?: number; example_title?: string; example_view_count?: number | null;
};
type Block<T> = { status?: string; reason?: string } & T;
type FocalMatrixResp = {
  status?: string;
  reason?: string;
  basis?: { evidence_count?: number; deep_analyzed_count?: number; catalog_focal_count?: number };
  matrix?: { focals?: FocalCell[]; product_lines?: LineCell[] };
  covered?: Block<{ focal_count?: number; zoom_mentions?: { range_mm?: string; video_count?: number }[] }>;
  gaps?: Block<{ items?: GapItem[]; product_lines?: LineCell[] }>;
  matched_products?: Block<{ items?: MatchedItem[] }>;
  note?: string;
};

function fmtNum(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(Number(n))) return "—";
  const v = Number(n);
  if (v >= 1_000_000) return (Math.round(v / 100_000) / 10).toFixed(1) + "M";
  if (v >= 1_000) return (Math.round(v / 100) / 10).toFixed(1) + "K";
  return String(v);
}

// 空态行:块级诚实空态统一渲染(reason 直接来自后端,不本地编造)。
function EmptyLine(reason: string) {
  return e("div", { className: "text-[10px] leading-relaxed text-amber-300/90" }, reason || "暂无数据");
}

function BlockTitle(icon: React.ReactNode, text: string, extra?: React.ReactNode) {
  return e("div", { className: "flex items-center gap-1.5 mt-2 mb-1" },
    icon,
    e("span", { className: "text-[10px] font-medium text-slate-300" }, text),
    extra || null,
  );
}

// 焦段格子:covered=拍过(实线+计数);目录内空白=高亮「可切入」;目录外覆盖=灰系注明。
function FocalCellBox(cell: FocalCell, i: number) {
  const covered = !!cell.covered;
  const inCatalog = !!cell.in_catalog;
  const cat = cell.catalog || {};
  const tip = covered
    ? `${cell.focal}:${Number(cell.video_count) || 0} 条视频(标题命中 ${Number(cell.title_hits) || 0} / 深析命中 ${Number(cell.deep_hits) || 0})`
      + (cell.top_example?.title ? `\n最佳:${cell.top_example.title}` : "")
    : inCatalog
      ? `${cell.focal} 零覆盖 · 我方 ${Number(cat.sku_count) || 0} SKU`
        + (cat.flagship ? `\n旗舰:${cat.flagship}` : "")
        + (cat.max_price_usd != null ? ` ($${cat.max_price_usd})` : "")
      : String(cell.focal || "");
  const base = "flex flex-col items-center justify-center rounded px-1 py-1 min-w-[52px] text-center";
  const cls = covered
    ? base + " border border-emerald-400/25 bg-emerald-500/[0.08]"
    : inCatalog
      ? base + " border border-dashed border-amber-400/40 bg-amber-500/[0.04]"
      : base + " border border-white/[0.06] bg-black/20";
  return e("div", { key: cell.focal || i, className: cls, title: tip },
    e("span", {
      className: "text-[10px] font-semibold tabular-nums " + (covered ? "text-emerald-200" : inCatalog ? "text-amber-200/90" : "text-slate-400"),
    }, cell.focal || "—"),
    covered
      ? e("span", { className: "text-[8.5px] tabular-nums text-slate-400" },
          `×${Number(cell.video_count) || 0}` + (cell.avg_views != null ? ` 均${fmtNum(cell.avg_views)}` : ""))
      : inCatalog
        ? e("span", { className: "text-[8.5px] text-amber-300/90" }, "可切入")
        : e("span", { className: "text-[8.5px] text-slate-600" }, "目录外"),
  );
}

export function FocalMatrixPanel({ apiToken, kolPoolId }: any) {
  const [data, setData] = React.useState<FocalMatrixResp | null>(null);

  // 开抽屉/换 KOL 只读拉取(后端纯聚合已有数据,读得起);失败静默不渲染。
  React.useEffect(() => {
    setData(null);
    if (!apiToken || !kolPoolId) return;
    let cancelled = false;
    void apiFetch<FocalMatrixResp>(
      `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/focal-matrix`,
      {},
      apiToken,
    )
      .then((payload) => { if (!cancelled) setData(payload && typeof payload === "object" ? payload : null); })
      .catch(() => { if (!cancelled) setData(null); });
    return () => { cancelled = true; };
  }, [apiToken, kolPoolId]);

  if (!apiToken || !kolPoolId || !data) return null;
  if (String(data.status || "") === "error") return null; // 聚合失败:安静缺席,不甩后端报错

  const focals = Array.isArray(data.matrix?.focals) ? data.matrix!.focals! : [];
  const lines = Array.isArray(data.matrix?.product_lines) ? data.matrix!.product_lines! : [];
  const covered = data.covered || {};
  const gaps = data.gaps || {};
  const matched = data.matched_products || {};
  const gapItems = Array.isArray(gaps.items) ? gaps.items : [];
  const matchedItems = Array.isArray(matched.items) ? matched.items : [];
  const zoomMentions = Array.isArray(covered.zoom_mentions) ? covered.zoom_mentions : [];
  const coveredCount = focals.filter((c) => c.covered).length;
  const gapCount = focals.filter((c) => c.in_catalog && !c.covered).length;
  const evidenceCount = Number(data.basis?.evidence_count) || 0;
  const deepCount = Number(data.basis?.deep_analyzed_count) || 0;

  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    e(SectionFold, {
      id: "focal-matrix",
      header: e(React.Fragment, null,
        e(Aperture, { size: 11, className: "text-emerald-300" }),
        e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "焦段矩阵 · 拍过什么/哪块空白"),
        coveredCount > 0 && e("span", { className: "rounded bg-emerald-500/10 px-1.5 py-0.5 text-[9px] text-emerald-200" }, `覆盖 ${coveredCount} 焦段`),
        gapCount > 0 && e("span", { className: "rounded bg-amber-500/10 px-1.5 py-0.5 text-[9px] text-amber-200" }, `空白 ${gapCount}`),
      ),
    },
      // ── 📐 焦段矩阵格子(目录焦段 + KOL 拍过的目录外焦段,按 mm 升序)──
      BlockTitle(e("span", { className: "text-[10px]" }, "📐"), "焦段格子",
        e("span", { className: "text-[8.5px] text-slate-600" },
          `证据 ${evidenceCount} 条 · 深析 ${deepCount} 条 · 词表/正则提取`)),
      covered.status === "empty"
        ? EmptyLine(String(covered.reason || ""))
        : e(React.Fragment, null,
            e("div", { className: "flex flex-wrap gap-1" }, focals.map((c, i) => FocalCellBox(c, i))),
            zoomMentions.length > 0 && e("div", { className: "mt-1 text-[9px] text-slate-500" },
              "变焦段提及:" + zoomMentions.map((z) => `${z.range_mm}(×${Number(z.video_count) || 0})`).join(" · ") + " —— 目录暂无变焦 SKU,仅如实记录"),
          ),

      // ── 产品线覆盖 chips(覆盖=拍过该类内容,不代表用的是我方产品)──
      lines.length > 0 && e(React.Fragment, null,
        BlockTitle(e("span", { className: "text-[10px]" }, "🧰"), "产品线覆盖"),
        e("div", { className: "flex flex-wrap gap-1" },
          lines.map((l, i) => e("span", {
            key: l.key || i,
            title: l.covered
              ? `${l.label}:${Number(l.video_count) || 0} 条相关内容` + (l.example_title ? `\n如:${l.example_title}` : "")
              : `${l.label}:零相关内容 · 我方目录 ${Number(l.catalog_sku_count) || 0} SKU`,
            className: "rounded px-1.5 py-0.5 text-[9px] " + (l.covered
              ? "border border-emerald-400/25 bg-emerald-500/[0.08] text-emerald-200"
              : l.in_catalog
                ? "border border-dashed border-amber-400/40 bg-amber-500/[0.04] text-amber-200/90"
                : "border border-white/[0.06] bg-black/20 text-slate-400"),
          }, l.covered
            ? `${l.label} ×${Number(l.video_count) || 0}` + (l.avg_views != null ? ` · 均${fmtNum(l.avg_views)}` : "")
            : `${l.label} · 可切入`)),
        ),
      ),

      // ── 🎯 可切入 TOP(库里有 SKU 但该 KOL 零覆盖,按目录营销价值代理降序)──
      BlockTitle(e(Target, { size: 10, className: "text-amber-300" }), "可切入空白 TOP",
        gaps.status === "ready" && e("span", { className: "text-[8.5px] text-slate-600" }, "按官方 SKU 数×价格合计排序(目录代理)")),
      gaps.status === "empty"
        ? EmptyLine(String(gaps.reason || ""))
        : gapItems.length === 0
          ? e("div", { className: "text-[10px] text-slate-500" }, "目录焦段全覆盖,没有空白格 🎉")
          : e("div", { className: "space-y-1" },
              gapItems.slice(0, 5).map((g, i) => e("div", {
                key: g.focal || i,
                className: "flex items-center gap-2 rounded border border-white/[0.05] bg-black/20 px-2 py-1 text-[10px]",
              },
                e("span", { className: "shrink-0 text-[9px] font-bold tabular-nums " + (i === 0 ? "text-amber-300" : "text-slate-500") }, "#" + (i + 1)),
                e("span", { className: "w-[52px] shrink-0 font-semibold tabular-nums text-amber-200" }, g.focal || "—"),
                e("span", { className: "truncate text-slate-400", title: g.flagship || undefined },
                  (g.flagship || "—") + (Array.isArray(g.series) && g.series.length ? ` · ${g.series.join("/")}` : "")),
                e("span", { className: "ml-auto shrink-0 tabular-nums text-slate-500" },
                  `${Number(g.official_sku_count) || 0} SKU` + (g.max_price_usd != null ? ` · 至$${fmtNum(g.max_price_usd)}` : "")),
              )),
            ),

      // ── 命中的我方 SKU 家族(焦段+系列/光圈+Viltrox 语境三重匹配,宁缺毋滥)──
      BlockTitle(e("span", { className: "text-[10px]" }, "✅"), "命中我方 SKU"),
      matched.status === "empty"
        ? EmptyLine(String(matched.reason || ""))
        : e("div", { className: "space-y-1" },
            matchedItems.slice(0, 6).map((m, i) => e("div", {
              key: (m.skus && m.skus[0]) || i,
              className: "flex items-center gap-2 rounded border border-emerald-400/15 bg-emerald-500/[0.05] px-2 py-1 text-[10px]",
              title: (m.example_title ? `如:${m.example_title}` : "") + (m.skus && m.skus.length ? `\nSKU:${m.skus.join(", ")}` : ""),
            },
              e("span", { className: "truncate text-emerald-100/90" }, m.family || (m.skus && m.skus[0]) || "—"),
              e("span", { className: "ml-auto shrink-0 tabular-nums text-slate-400" }, `×${Number(m.matched_video_count) || 0} 条`),
            )),
          ),

      e("div", { className: "mt-1.5 text-[9px] text-slate-600" },
        String(data.note || "口径:词表/正则纯聚合已有数据;覆盖≠用我方产品;独立展示信号,不参与 V6 Fit 评分。")),
    ),
  );
}
