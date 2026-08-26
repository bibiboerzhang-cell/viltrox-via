// KOL 焦段矩阵面板:这个 KOL「拍过哪些焦段/产品线,哪块是空白」(深度分析 tab 增益块)。
// 数据:GET /api/admin/vkpi/kol-pool/{id}/focal-matrix —— 纯聚合已有数据
// (evidence 标题 + final_v1 深析文本 词表/正则提焦段与产品线,对照 vkpi_products 目录),零新采集/零 LLM。
// 三块:📐 焦段矩阵格子(covered=拍过 ×N+均播放;空白+目录有 SKU=高亮「可切入」)
//      / 产品线覆盖 chips / 🎯 适配产品机会(卡口硬闸+装备价带+系列多样性)+ 命中我方 SKU 家族。
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
type ProductOpportunity = GapItem & {
  sku?: string; product_name?: string; line?: string; mount?: string | null;
  price_usd?: number | null; product_url?: string | null; recommendation_score?: number;
  compatibility_status?: string; confidence?: string; price_fit?: string;
  reasons?: string[]; score_breakdown?: Record<string, number>;
};
type CreatorContext = {
  camera_body?: string | null; mount?: string | null; mount_status?: string;
  mount_evidence?: string; lens_brands?: string[]; content_lane?: string;
  catalog_price_ceiling_proxy_usd?: number | null; price_tier_status?: string;
  price_tier_evidence?: string; recommendation_status?: string; recommendation_stage?: string;
  deep_evidence_count?: number; price_proxy_note?: string;
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
  gaps?: Block<{
    items?: GapItem[]; recommendations?: ProductOpportunity[]; product_lines?: LineCell[];
    creator_context?: CreatorContext; recommendation_status?: string;
    recommendation_reason?: string; ranking_method?: string;
  }>;
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
  const opportunities = Array.isArray(gaps.recommendations) ? gaps.recommendations : [];
  const creatorContext = gaps.creator_context || {};
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

      // ── 🎯 适配产品机会:卡口冲突硬排除;无机身/卡口证据不生成伪 Top1。──
      BlockTitle(e(Target, { size: 10, className: "text-amber-300" }), "适配产品机会 TOP",
        e("span", { className: "text-[8.5px] text-slate-600" }, "卡口硬闸 · 装备价带 · 系列去重")),
      e("div", { className: "mb-1.5 flex flex-wrap gap-1 text-[8.5px]" },
        e("span", {
          className: "rounded border px-1.5 py-0.5 " + (creatorContext.camera_body
            ? "border-cyan-400/20 bg-cyan-400/[0.06] text-cyan-200"
            : "border-amber-400/20 bg-amber-400/[0.05] text-amber-200"),
          title: creatorContext.mount_evidence || undefined,
        }, creatorContext.camera_body || "机身待补"),
        e("span", {
          className: "rounded border px-1.5 py-0.5 " + (creatorContext.mount
            ? "border-emerald-400/20 bg-emerald-400/[0.06] text-emerald-200"
            : "border-amber-400/20 bg-amber-400/[0.05] text-amber-200"),
          title: creatorContext.mount_evidence || undefined,
        }, creatorContext.mount || (creatorContext.mount_status === "conflict" ? "卡口信号冲突" : "卡口待核验")),
        creatorContext.content_lane && e("span", { className: "rounded border border-white/[0.06] bg-black/20 px-1.5 py-0.5 text-slate-400" },
          `内容 ${creatorContext.content_lane}`),
        e("span", {
          className: "rounded border px-1.5 py-0.5 " + (creatorContext.recommendation_stage === "deep_validated"
            ? "border-emerald-400/20 bg-emerald-400/[0.06] text-emerald-200"
            : "border-amber-400/20 bg-amber-400/[0.05] text-amber-200"),
          title: creatorContext.recommendation_stage === "deep_validated"
            ? `已有 ${Number(creatorContext.deep_evidence_count) || 0} 条深度分析证据`
            : "仅档案/标题/设备词证据；深度分析完成后会自动升级",
        }, creatorContext.recommendation_stage === "deep_validated" ? "深析已验证" : "初步推荐"),
        creatorContext.catalog_price_ceiling_proxy_usd != null
          ? e("span", {
              className: "rounded border border-white/[0.06] bg-black/20 px-1.5 py-0.5 text-slate-400",
              title: `${creatorContext.price_tier_evidence || ""}\n${creatorContext.price_proxy_note || ""}`,
            }, `装备价带≤$${fmtNum(creatorContext.catalog_price_ceiling_proxy_usd)}`)
          : e("span", {
              className: "rounded border border-amber-400/20 bg-amber-400/[0.05] px-1.5 py-0.5 text-amber-200",
              title: creatorContext.price_proxy_note || undefined,
            }, "价格层待补"),
        Array.isArray(creatorContext.lens_brands) && creatorContext.lens_brands.length > 0
          ? e("span", { className: "rounded border border-white/[0.06] bg-black/20 px-1.5 py-0.5 text-slate-400" },
              `常用 ${creatorContext.lens_brands.slice(0, 3).join("/")}`)
          : null,
      ),
      gaps.status === "empty"
        ? EmptyLine(String(gaps.reason || ""))
        : gapCount === 0
          ? e("div", { className: "text-[10px] text-slate-500" }, "目录焦段全覆盖,没有空白格 🎉")
          : opportunities.length === 0
            ? EmptyLine(String(gaps.recommendation_reason || "机身/卡口/常用镜头证据不足,暂不生成个性化 Top1。"))
            : e("div", { className: "space-y-1" },
                opportunities.slice(0, 5).map((g, i) => e("div", {
                  key: g.sku || `${g.focal || "gap"}-${i}`,
                  className: "rounded border border-white/[0.05] bg-black/20 px-2 py-1.5 text-[10px]",
                  title: [
                    ...(Array.isArray(g.reasons) ? g.reasons : []),
                    g.sku ? `SKU: ${g.sku}` : "",
                  ].filter(Boolean).join("\n"),
                },
                  e("div", { className: "flex items-center gap-2" },
                    e("span", { className: "shrink-0 text-[9px] font-bold tabular-nums " + (i === 0 ? "text-amber-300" : "text-slate-500") }, "#" + (i + 1)),
                    e("span", { className: "w-[42px] shrink-0 font-semibold tabular-nums text-amber-200" }, g.focal || "—"),
                    g.product_url
                      ? e("a", { href: g.product_url, target: "_blank", rel: "noreferrer", className: "min-w-0 truncate text-cyan-200 hover:text-cyan-100" }, g.product_name || g.flagship || g.sku || "—")
                      : e("span", { className: "min-w-0 truncate text-slate-300" }, g.product_name || g.flagship || g.sku || "—"),
                    e("span", { className: "ml-auto shrink-0 tabular-nums text-slate-500" },
                      g.price_usd != null ? `$${fmtNum(g.price_usd)}` : "价格待补"),
                  ),
                  e("div", { className: "mt-1 flex flex-wrap items-center gap-1 text-[8.5px] text-slate-500" },
                    e("span", { className: "rounded bg-violet-500/10 px-1 py-0.5 text-violet-200" },
                      Array.isArray(g.series) && g.series.length ? g.series.join("/") : "Standard"),
                    e("span", { className: "rounded bg-emerald-500/10 px-1 py-0.5 text-emerald-200" }, g.mount || "卡口待核验"),
                    e("span", { className: "rounded bg-white/[0.04] px-1 py-0.5" }, `置信 ${g.confidence || "—"}`),
                    g.recommendation_score != null && e("span", { className: "ml-auto tabular-nums" }, `score ${g.recommendation_score}`),
                  ),
                  Array.isArray(g.reasons) && g.reasons.length > 0
                    ? e("div", { className: "mt-1 truncate text-[8.5px] text-slate-600" }, g.reasons.slice(0, 2).join(" · "))
                    : null,
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
