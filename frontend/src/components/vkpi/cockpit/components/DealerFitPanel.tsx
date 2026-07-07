// CB4 Dealer 适配卡:某 SKU 的硬件零售商 geo/category 适配目标榜(Channel Brain 层)。
// 数据:GET /api/admin/vkpi/channel/dealer-fit?sku=<sku> —— 纯读聚合
//   (domain: channel.dealer_scoring;geo 市场密度先验 + 门店品类适配 + 合作等级)。
// 诚实态:vkpi_dealers 本地 0 行(硬件品牌盲区)→ 后端 status="data_missing" + ready_when,
//   本面板如实渲染「诚实空态」,绝不本地编数;接口 error 显示原因不甩堆栈。
// 红线:纯展示,绝不渲染/触碰 viltrox_fit_score 与 rule_v0;geo/category 适配 ≠ 真实销售潜力。
import React from "react";
import { Store } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import { SkeletonBlock } from "./ui/SkeletonBlock";

const e = React.createElement;

type Component = { value?: number; weight?: number; known?: boolean; basis?: string };
type Target = {
  dealer_id?: number | string | null;
  name?: string | null;
  city?: string | null;
  state?: string | null;
  source?: string | null;
  dealer_fit_score?: number | null;
  components?: { geo?: Component; category?: Component; tier?: Component };
  notes?: string[];
};
type DealerFitResp = {
  status?: string;
  reason?: string;
  sku?: string;
  product?: { sku?: string; category_main?: string; family?: string; model_name?: string; mount?: string } | null;
  product_note?: string;
  count?: number;
  targets?: Target[];
  basis?: string;
  note?: string;
  ready_when?: string;
  limitations?: string[];
};

function scoreColor(score: number): string {
  if (score >= 75) return "rgba(52,211,153,0.75)"; // emerald
  if (score >= 55) return "rgba(34,211,238,0.65)"; // cyan
  if (score >= 40) return "rgba(251,191,36,0.55)"; // amber
  return "rgba(148,163,184,0.45)"; // slate
}

// 分量小徽章:已知=实心 / 未知(中性)=虚线 + title 带 basis(可解释)
function CompChip(label: string, c?: Component) {
  const known = !!(c && c.known);
  const val = c && typeof c.value === "number" ? Math.round(c.value * 100) : null;
  return e(
    "span",
    {
      className:
        "shrink-0 rounded border px-1.5 py-0.5 text-[9px] tabular-nums " +
        (known
          ? "border-white/[0.1] bg-white/[0.05] text-slate-300"
          : "border-dashed border-amber-300/25 bg-amber-500/5 text-amber-200/80"),
      title: (c && c.basis) || undefined,
    },
    label + " " + (val == null ? "—" : val + (known ? "" : "·中性")),
  );
}

function TargetRow(t: Target, i: number) {
  const score = Number(t.dealer_fit_score) || 0;
  return e(
    "div",
    { key: (t.dealer_id != null ? String(t.dealer_id) : "") + i, className: "rounded border border-white/[0.05] bg-black/20 px-2 py-1.5" },
    e(
      "div",
      { className: "flex items-center gap-2" },
      e("span", { className: "shrink-0 text-[9px] tabular-nums text-slate-500 w-4" }, String(i + 1)),
      e("span", { className: "min-w-0 flex-1 truncate text-[10px] text-slate-200", title: String(t.name || "") }, t.name || "(无名)"),
      (t.city || t.state) &&
        e("span", { className: "shrink-0 text-[9px] text-slate-500" }, [t.city, t.state].filter(Boolean).join(", ")),
      e("span", { className: "shrink-0 text-[10px] font-semibold tabular-nums text-slate-100" }, score.toFixed(0)),
    ),
    // 分数条
    e(
      "div",
      { className: "mt-1 h-[5px] w-full overflow-hidden rounded-full bg-white/[0.04]" },
      e("div", { className: "h-full rounded-full", style: { width: Math.max(2, score) + "%", background: scoreColor(score) } }),
    ),
    // 分量徽章(geo/category/tier;未知走虚线中性)
    e(
      "div",
      { className: "mt-1 flex flex-wrap items-center gap-1" },
      CompChip("geo", t.components && t.components.geo),
      CompChip("品类", t.components && t.components.category),
      CompChip("合作", t.components && t.components.tier),
    ),
  );
}

export function DealerFitPanel({ apiToken, sku: skuProp }: { apiToken?: string; sku?: string }) {
  const [sku, setSku] = React.useState<string>(skuProp || "");
  const [query, setQuery] = React.useState<string>(skuProp || "");
  const [data, setData] = React.useState<DealerFitResp | null>(null);
  const [loading, setLoading] = React.useState(false);

  React.useEffect(() => {
    if (skuProp && skuProp !== query) {
      setSku(skuProp);
      setQuery(skuProp);
    }
  }, [skuProp]); // eslint-disable-line react-hooks/exhaustive-deps

  React.useEffect(() => {
    setData(null);
    if (!apiToken || !query.trim()) return;
    let cancelled = false;
    setLoading(true);
    void apiFetch<DealerFitResp>(
      `/api/admin/vkpi/channel/dealer-fit?sku=${encodeURIComponent(query.trim())}`,
      {},
      apiToken,
    )
      .then((payload) => {
        if (!cancelled) setData(payload && typeof payload === "object" ? payload : null);
      })
      .catch(() => {
        if (!cancelled) setData(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken, query]);

  if (!apiToken) return null;

  const submit = () => setQuery(sku);
  const targets = data && Array.isArray(data.targets) ? data.targets : [];
  const prod = (data && data.product) || null;

  return e(
    "div",
    { className: "px-5 py-3 border-b border-white/[0.06]" },
    // ── 卡头 ──
    e(
      "div",
      { className: "flex items-center gap-1.5" },
      e(Store, { size: 11, className: "text-cyan-300" }),
      e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "Dealer 适配 · geo/category"),
      prod &&
        e(
          "span",
          { className: "rounded bg-white/[0.05] px-1.5 py-0.5 text-[9px] text-slate-300", title: String(prod.model_name || "") },
          String(prod.category_main || prod.family || ""),
        ),
      data && typeof data.count === "number" &&
        e("span", { className: "ml-auto text-[9px] tabular-nums text-slate-500" }, `${data.count} 家门店`),
    ),

    // ── SKU 输入 ──
    e(
      "div",
      { className: "mt-2 flex items-center gap-1.5" },
      e("input", {
        type: "text",
        value: sku,
        placeholder: "输入 SKU(如 AF-85-F1.8-FE)",
        onChange: (ev: any) => setSku(String(ev.target.value)),
        onKeyDown: (ev: any) => {
          if (ev.key === "Enter") submit();
        },
        className:
          "min-w-0 flex-1 rounded border border-white/[0.08] bg-black/30 px-2 py-1 text-[10px] text-slate-200 placeholder:text-slate-600 focus:outline-none focus:border-cyan-400/40",
      }),
      e(
        "button",
        {
          type: "button",
          onClick: submit,
          className:
            "shrink-0 rounded border border-cyan-400/30 bg-cyan-500/10 px-2 py-1 text-[10px] text-cyan-200 hover:bg-cyan-500/20",
        },
        "评分",
      ),
    ),

    // ── 加载态 ──
    loading && e("div", { className: "mt-2" }, e(SkeletonBlock, { lines: 3 })),

    // ── 错误(诚实回原因,不甩堆栈)──
    !loading && data && String(data.status || "") === "error" &&
      e("div", { className: "mt-2 text-[10px] text-rose-300/90" }, "评分失败:" + String(data.reason || "unknown")),

    // ── 诚实空态(data_missing:vkpi_dealers 0 行)──
    !loading && data && String(data.status || "") === "data_missing" &&
      e(
        "div",
        { className: "mt-2 rounded border border-dashed border-amber-300/25 bg-amber-500/[0.04] px-2.5 py-2" },
        e("div", { className: "text-[10px] font-medium text-amber-200/90" }, "暂无 Dealer 数据(硬件品牌盲区)"),
        data.note && e("div", { className: "mt-0.5 text-[9px] text-slate-400" }, String(data.note)),
        data.ready_when &&
          e("div", { className: "mt-1 text-[9px] leading-relaxed text-slate-500" }, "就绪条件:" + String(data.ready_when)),
      ),

    // ── 目标榜(ok)──
    !loading && data && String(data.status || "") === "ok" && targets.length > 0 &&
      e("div", { className: "mt-2 space-y-1" }, targets.slice(0, 12).map((t, i) => TargetRow(t, i))),

    // ── 口径脚注(始终诚实:非真实销售潜力)──
    !loading && data && (data.basis || data.product_note) &&
      e(
        "div",
        { className: "mt-1.5 text-[9px] leading-relaxed text-slate-600" },
        String(data.basis || ""),
        data.product_note ? "(" + String(data.product_note) + ")" : "",
      ),
  );
}

export default DealerFitPanel;
