// #51 品牌露出 · 百家饭指数(深度分析 tab 小块):该 KOL 已深析(final_v1)视频里
// Viltrox vs 竞品的露出比例 → 专情度条 + 竞品列表。
// 数据:GET /kol-pool/{id}/competitor-exposure —— 纯读已有深析产物(零新分析/零 LLM),
// 后端当日缓存。指数 0-100 越高越「专情」,带样本量置信折扣(小样本不吹满)。
// 诚实态:深析样本 <3 不画指数条,明说「深析样本不足」;接口缺失/失败安静缺席(增益块非阻塞)。
// 红线:纯展示,绝不渲染/触碰 viltrox_fit_score 与 rule_v0。
import React from "react";
import { PieChart } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import { SectionFold } from "./SectionFold";

const e = React.createElement;

type BrandCount = { brand?: string; count?: number };
type ExposureResp = {
  status?: string;
  sample_size?: number;
  min_sample?: number;
  viltrox_videos?: number;
  competitor_videos?: number;
  competitor_exposures?: number;
  viltrox_rate?: number | null;
  index?: number | null;
  index_band?: string;
  competitor_breakdown?: BrandCount[];
  viltrox_products?: Array<{ name?: string; count?: number }>;
  cached?: boolean;
  reason?: string;
};

const BAND_LABEL: Record<string, string> = {
  loyal: "专情", lean_loyal: "偏专情", mixed: "均衡", variety: "百家饭",
};
const BAND_COLOR: Record<string, string> = {
  loyal: "#a855f7", lean_loyal: "#c084fc", mixed: "#fbbf24", variety: "#f87171",
};

export function KOLDrawerBrandExposure({ apiToken, kolPoolId }: any) {
  const [data, setData] = React.useState<ExposureResp | null>(null);

  // 开抽屉/换 KOL 只读拉取(后端当日缓存,不会每次全算);失败静默不渲染。
  React.useEffect(() => {
    setData(null);
    if (!apiToken || !kolPoolId) return;
    let cancelled = false;
    void apiFetch<ExposureResp>(
      `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/competitor-exposure`,
      {},
      apiToken,
    )
      .then((payload) => { if (!cancelled) setData(payload && typeof payload === "object" ? payload : null); })
      .catch(() => { if (!cancelled) setData(null); });
    return () => { cancelled = true; };
  }, [apiToken, kolPoolId]);

  if (!apiToken || !kolPoolId || !data) return null;
  const status = String(data.status || "");
  if (status === "error") return null; // 聚合失败:安静缺席,不给用户甩后端报错

  const sampleSize = Number(data.sample_size) || 0;
  const minSample = Number(data.min_sample) || 3;
  const index = typeof data.index === "number" ? data.index : null;
  const band = String(data.index_band || "");
  const viltroxVideos = Number(data.viltrox_videos) || 0;
  const competitorExposures = Number(data.competitor_exposures) || 0;
  const breakdown = Array.isArray(data.competitor_breakdown) ? data.competitor_breakdown : [];
  const insufficient = status === "no_deep_analysis" || status === "insufficient_sample";

  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    e(SectionFold, {
      id: "brandexposure",
      header: e(React.Fragment, null,
        e(PieChart, { size: 11, className: "text-purple-400" }),
        e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "品牌露出 · 百家饭指数"),
        index != null && band && e("span", {
          className: "rounded px-1.5 py-0.5 text-[9px] font-medium",
          style: { background: (BAND_COLOR[band] || "#94a3b8") + "1f", color: BAND_COLOR[band] || "#94a3b8" },
        }, BAND_LABEL[band] || band),
      ),
    },
    // ── 诚实态:样本不足不画指数(不拿 1-2 条视频外推「专情/百家饭」)──
    insufficient && e("div", { className: "text-[10px] leading-relaxed text-amber-300/90" },
      status === "no_deep_analysis"
        ? "该 KOL 还没有已深析视频 — 先在上方跑「KOL深度分析理解」,品牌露出比会自动点亮。"
        : `深析样本不足(${sampleSize}/${minSample} 条),暂不计算专情指数 — 多深析几条视频后这里会点亮。`,
    ),
    // 深析够了但内容里全程没出现任何品牌(自家/竞品都没有):如实说明。
    status === "no_brand_signal" && e("div", { className: "text-[10px] leading-relaxed text-slate-500" },
      `已深析 ${sampleSize} 条视频,画面与口播均未识别到 Viltrox 或竞品品牌 — 无法计算露出比。`,
    ),
    // ── 专情度条(index 0-100,越高越专情;紫=Viltrox 侧,红=百家饭侧)──
    status === "ok" && index != null && e(React.Fragment, null,
      e("div", { className: "flex items-center gap-2 text-[11px]" },
        e("span", { className: "text-slate-400 shrink-0" }, "专情度"),
        e("div", { className: "relative flex-1 h-[8px] rounded-full overflow-hidden", style: { background: "rgba(248,113,113,0.18)" } },
          e("div", {
            className: "absolute inset-y-0 left-0 rounded-full",
            style: { width: Math.min(100, Math.max(0, index)) + "%", background: "linear-gradient(90deg, rgba(168,85,247,0.55), #a855f7)" },
          }),
        ),
        e("span", { className: "text-white font-semibold tabular-nums w-[30px] text-right" }, String(index)),
      ),
      e("div", { className: "mt-1 text-[9px] text-slate-500" },
        `Viltrox 出现 ${viltroxVideos} 条视频 · 竞品露出 ${competitorExposures} 次(视频×品牌去重) · 深析样本 ${sampleSize} 条`,
      ),
      typeof data.viltrox_rate === "number" && e("div", { className: "text-[9px] text-slate-600" },
        `原始露出比 ${(Math.round(data.viltrox_rate * 1000) / 10).toFixed(1)}%(指数含小样本置信折扣,非同一数值)`,
      ),
    ),
    // ── 竞品列表(chips:品牌 × 出现视频数;Viltrox chip 恒置首位对照)──
    (status === "ok" || (insufficient && (viltroxVideos > 0 || breakdown.length > 0))) && e("div", { className: "mt-2" },
      e("div", { className: "text-[10px] text-slate-500 mb-1" }, "品牌露出明细" + (insufficient ? "(样本不足,仅原始计数)" : "")),
      e("div", { className: "flex flex-wrap gap-1" },
        viltroxVideos > 0 && e("span", {
          className: "px-1.5 py-0.5 rounded text-[9.5px] border",
          style: { background: "rgba(168,85,247,0.15)", borderColor: "rgba(168,85,247,0.3)", color: "#d8b4fe" },
        }, `Viltrox ×${viltroxVideos}`),
        breakdown.map((b: BrandCount, i: number) => e("span", {
          key: i,
          className: "px-1.5 py-0.5 rounded text-[9.5px] border",
          style: { background: "rgba(248,113,113,0.08)", borderColor: "rgba(248,113,113,0.18)", color: "#cbd5e1" },
        }, `${String(b.brand || "—")} ×${Number(b.count) || 0}`)),
      ),
    ),
    e("div", { className: "mt-1.5 text-[9px] text-slate-600" },
      "口径:仅统计已深析(final_v1)视频;独立展示信号,不参与 V6 Fit 评分。",
    )),
  );
}
