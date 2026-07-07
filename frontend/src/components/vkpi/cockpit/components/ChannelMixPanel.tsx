// CB2 渠道组合器面板:「用哪些渠道推、各占多少」跨渠道预算与权重分配卡(Channel Brain 层)。
// 数据:GET /api/admin/vkpi/channel/mix?sku=&budget_usd=&goal= —— 纯读聚合
// (KOL 复用 strategy_sim 候选/预算逻辑,官号轻量只读,付费引用 growth_playbook,零 LLM/零采集/零写库)。
// 展示:Binet-Field 60/40 分层条(品牌层曝光型 / 激活层转化型)+ 每渠道占比条(占总预算)+
//       每渠道「曝光型 vs 转化型」标注 + 置信度徽章 + Dealer/独立站 data_missing 诚实态。
// 诚实态:Dealer 本地 0 行 / 独立站本地无订单一律 data_missing,原样展示后端 note,绝不本地编数;
//       接口失败/空整块安静缺席(非阻塞增益块)。
// 红线:纯展示,绝不渲染/触碰 viltrox_fit_score 与 rule_v0;复用 ui/ 基元,createElement 风格自拉自判空。
import React from "react";
import { Share2 } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import { FreshnessDot } from "./ui/FreshnessDot";
import { SkeletonBlock } from "./ui/SkeletonBlock";

const e = React.createElement;

type Channel = {
  key?: string;
  name?: string;
  layer?: string;
  layer_label?: string;
  channel_type?: string;
  channel_type_label?: string;
  allocated_usd?: number | null;
  nominal_usd?: number | null;
  weight_pct?: number | null;
  status?: string;
  confidence?: string;
  note?: string;
  dealer_count?: number | null;
};
type LayerBlock = {
  label?: string;
  target_pct?: number | null;
  target_usd?: number | null;
  allocated_usd?: number | null;
  channel_type_label?: string;
};
type MixResp = {
  status?: string;
  reason?: string;
  method?: string;
  sku?: string;
  budget_usd?: number | null;
  goal?: string | null;
  goal_effective?: string | null;
  goal_note?: string | null;
  generated_at?: string | null;
  layers?: { framework?: string; brand?: LayerBlock; activation?: LayerBlock };
  channels?: Channel[];
  allocation_check?: { allocated_total_usd?: number | null; unallocated_usd?: number | null };
  recommendation?: { headline?: string; confidence?: string; confidence_reason?: string };
  basis?: { binet_field?: string };
};

// 层视觉:品牌层=cyan(曝光/长期建设),激活层=amber(转化/短期激活)。
const LAYER_STYLE: Record<string, { bar: string; badge: string }> = {
  brand: { bar: "rgba(34,211,238,0.65)", badge: "bg-cyan-500/15 text-cyan-200 border-cyan-300/20" },
  activation: { bar: "rgba(251,191,36,0.6)", badge: "bg-amber-500/15 text-amber-200 border-amber-300/20" },
};
// 渠道占比条配色:按层取色;data_missing 渠道用中性灰虚线示意。
const CHANNEL_BAR: Record<string, string> = {
  brand: "rgba(34,211,238,0.6)",
  activation: "rgba(251,191,36,0.55)",
};
const CONF_STYLE: Record<string, string> = {
  medium: "bg-emerald-500/15 text-emerald-200 border-emerald-300/20",
  low: "bg-amber-500/15 text-amber-200/90 border-amber-300/15",
  data_missing: "bg-slate-500/15 text-slate-400 border-slate-300/15",
};
const CONF_LABEL: Record<string, string> = {
  medium: "置信中", low: "置信低", data_missing: "数据缺失",
};

function fmtUsd(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(Number(n))) return "—";
  const v = Number(n);
  if (v >= 1000) return "$" + (Math.round(v / 10) / 100).toFixed(2).replace(/\.?0+$/, "") + "K";
  return "$" + (Math.round(v * 100) / 100).toString();
}
function fmtPct(n: number | null | undefined): string {
  return n == null || !Number.isFinite(Number(n)) ? "—" : (Math.round(Number(n) * 10) / 10).toFixed(1) + "%";
}

// 「曝光型 / 转化型」标注徽章(层内配色)
function TypeBadge(layer: string, label: string) {
  const style = (LAYER_STYLE[layer] || LAYER_STYLE.brand).badge;
  return e("span", { className: "shrink-0 rounded border px-1.5 py-0.5 text-[9px] font-medium " + style }, label);
}
function ConfBadge(conf: string) {
  const key = String(conf || "").toLowerCase();
  const style = CONF_STYLE[key] || CONF_STYLE.low;
  return e("span", { className: "shrink-0 rounded border px-1.5 py-0.5 text-[9px] " + style }, CONF_LABEL[key] || key);
}

// 单渠道行:名称 + 类型徽章 + 置信 + 占比条 + 金额;data_missing 渠道诚实弱化。
function ChannelRow(ch: Channel) {
  const layer = String(ch.layer || "brand");
  const isMissing = String(ch.status || "") === "data_missing";
  const wpct = Number(ch.weight_pct) || 0;
  return e("div", { key: ch.key, className: "rounded border border-white/[0.05] bg-black/20 px-2 py-1.5" },
    e("div", { className: "flex items-center gap-1.5" },
      e("span", { className: "min-w-0 flex-1 truncate text-[10px] font-medium text-slate-200", title: ch.note || ch.name || "" },
        String(ch.name || ch.key || "")),
      TypeBadge(layer, String(ch.channel_type_label || (layer === "brand" ? "曝光型" : "转化型"))),
      ConfBadge(String(ch.confidence || "low")),
      e("span", { className: "shrink-0 text-[10px] tabular-nums font-semibold text-slate-100" }, fmtUsd(ch.allocated_usd)),
    ),
    // 占比条(占总预算);data_missing 渠道显示虚线空条 + nominal 说明。
    e("div", { className: "mt-1 flex items-center gap-2" },
      e("div", { className: "h-[6px] flex-1 overflow-hidden rounded-full bg-white/[0.04]" },
        !isMissing && wpct > 0 && e("div", {
          className: "h-full rounded-full",
          style: { width: Math.max(2, wpct) + "%", background: CHANNEL_BAR[layer] || CHANNEL_BAR.brand },
          title: fmtPct(wpct) + " 占总预算",
        }),
      ),
      e("span", { className: "shrink-0 text-[9px] tabular-nums text-slate-500", style: { width: "42px", textAlign: "right" } },
        isMissing ? "—" : fmtPct(wpct)),
    ),
    isMissing && e("div", { className: "mt-0.5 text-[9px] leading-relaxed text-amber-300/80" },
      String(ch.note || "数据缺失(data_missing),不派真金、不编数。")),
  );
}

export function ChannelMixPanel({
  apiToken, sku, budget, goal,
}: { apiToken?: string; sku?: string; budget?: number | string; goal?: string }) {
  const [data, setData] = React.useState<MixResp | null>(null);
  const [loading, setLoading] = React.useState(false);

  React.useEffect(() => {
    setData(null);
    const b = Number(budget);
    if (!apiToken || !sku || !Number.isFinite(b) || b <= 0) return;
    let cancelled = false;
    setLoading(true);
    const q = new URLSearchParams({ sku: String(sku), budget_usd: String(b) });
    if (goal) q.set("goal", String(goal));
    void apiFetch<MixResp>(`/api/admin/vkpi/channel/mix?${q.toString()}`, {}, apiToken)
      .then((payload) => { if (!cancelled) setData(payload && typeof payload === "object" ? payload : null); })
      .catch(() => { if (!cancelled) setData(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [apiToken, sku, budget, goal]);

  if (!apiToken || !sku || !(Number(budget) > 0)) return null;
  if (loading && !data) {
    return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" }, e(SkeletonBlock, { lines: 4 }));
  }
  if (!data || String(data.status || "") === "error") return null; // 聚合失败:安静缺席

  if (data.status === "invalid") {
    return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
      e("div", { className: "flex items-center gap-1.5" },
        e(Share2, { size: 11, className: "text-cyan-300" }),
        e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "渠道组合器"),
      ),
      e("div", { className: "mt-1 text-[10px] text-amber-300/90" }, String(data.reason || "输入非法")),
    );
  }

  const layers = data.layers || {};
  const brand = layers.brand || {};
  const activation = layers.activation || {};
  const brandPct = Number(brand.target_pct) || 0;
  const actPct = Number(activation.target_pct) || 0;
  const channels = Array.isArray(data.channels) ? data.channels : [];
  const brandChannels = channels.filter((c) => c.layer === "brand");
  const actChannels = channels.filter((c) => c.layer === "activation");
  const rec = data.recommendation || {};

  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    // ── 卡头:标题 + SKU + 预算 + goal + 新鲜度 ──
    e("div", { className: "flex items-center gap-1.5" },
      e(Share2, { size: 11, className: "text-cyan-300" }),
      e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "渠道组合器 · Binet-Field 60/40"),
      data.goal_effective && e("span", { className: "rounded bg-white/[0.05] px-1.5 py-0.5 text-[9px] text-slate-300" }, String(data.goal_effective)),
      e(FreshnessDot, { ts: data.generated_at, label: "生成于" }),
      e("span", { className: "ml-auto text-[9px] tabular-nums text-slate-500" }, "预算 " + fmtUsd(data.budget_usd)),
    ),

    // ── 分层占比条(品牌层曝光 / 激活层转化)──
    (brandPct > 0 || actPct > 0) && e("div", { className: "mt-2" },
      e("div", { className: "flex h-[10px] w-full overflow-hidden rounded-full bg-white/[0.04]" },
        brandPct > 0 && e("div", { className: "h-full", style: { width: brandPct + "%", background: LAYER_STYLE.brand.bar }, title: `品牌层 ${fmtPct(brandPct)}(曝光型)` }),
        actPct > 0 && e("div", { className: "h-full", style: { width: actPct + "%", background: LAYER_STYLE.activation.bar }, title: `激活层 ${fmtPct(actPct)}(转化型)` }),
      ),
      e("div", { className: "mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[9px]" },
        e("span", { className: "inline-flex items-center gap-1" },
          e("span", { className: "inline-block h-2 w-2 rounded-full", style: { background: LAYER_STYLE.brand.bar } }),
          e("span", { className: "text-slate-400" }, `品牌层 ${fmtPct(brandPct)} · 曝光型`),
          e("span", { className: "tabular-nums text-slate-500" }, fmtUsd(brand.target_usd)),
        ),
        e("span", { className: "inline-flex items-center gap-1" },
          e("span", { className: "inline-block h-2 w-2 rounded-full", style: { background: LAYER_STYLE.activation.bar } }),
          e("span", { className: "text-slate-400" }, `激活层 ${fmtPct(actPct)} · 转化型`),
          e("span", { className: "tabular-nums text-slate-500" }, fmtUsd(activation.target_usd)),
        ),
      ),
    ),

    // ── 品牌层渠道 ──
    brandChannels.length > 0 && e("div", { className: "mt-2" },
      e("div", { className: "mb-1 text-[9px] uppercase tracking-wider text-cyan-300/70" }, "品牌层 · 长期曝光"),
      e("div", { className: "space-y-1" }, brandChannels.map((c) => ChannelRow(c))),
    ),
    // ── 激活层渠道 ──
    actChannels.length > 0 && e("div", { className: "mt-2" },
      e("div", { className: "mb-1 text-[9px] uppercase tracking-wider text-amber-300/70" }, "激活层 · 短期转化"),
      e("div", { className: "space-y-1" }, actChannels.map((c) => ChannelRow(c))),
    ),

    // ── goal 退回说明(未知 goal 时后端诚实标注)──
    data.goal_note && e("div", { className: "mt-1.5 text-[9px] leading-relaxed text-amber-300/80" }, String(data.goal_note)),

    // ── 推荐摘要 + 置信 ──
    rec.headline && e("div", { className: "mt-2 rounded border border-cyan-300/10 bg-cyan-500/[0.04] px-2 py-1.5" },
      e("div", { className: "flex items-start gap-1.5" },
        rec.confidence && ConfBadge(String(rec.confidence)),
        e("div", { className: "min-w-0 flex-1 text-[9.5px] leading-relaxed text-slate-300" }, String(rec.headline)),
      ),
    ),

    e("div", { className: "mt-1.5 text-[9px] leading-relaxed text-slate-600" },
      "口径:Binet-Field 60/40 跨渠道分配(纯读锚点,非成交实测);KOL 复用 strategy_sim 候选/预算,"
      + "Dealer 0 行与独立站本地无订单诚实 data_missing,不参与 V6 Fit 评分,不触发任何采集/写库。"),
  );
}
