// CB3 独立站/Shopify 承接建议面板(Conversion Readiness Actions,自取数,纯展示)。
// 数据:GET /api/admin/vkpi/channel/indie-site-actions?sku= —— 后端把「承接层」拆成 4 项
//   就绪度检查(短链就绪 / 落地页+样片+FAQ / 佣金码 / 购买路径),每项 ready|missing|unknown
//   + basis(依据)。Reddit 洞察:PR ≠ sales,曝光先接住。
// 诚实态:SKU 未命中 → status='not_found' + 各项 unknown(结构就位);本地 0 Shopify 订单 →
//   shopify.status='data_missing' + note,面板照实标注,绝不本地编数。接口失败/未授权安静缺席。
// 红线:纯展示,绝不渲染/触碰 viltrox_fit_score 与 rule_v0;复用 ui/ 基元(SkeletonBlock)。
import React from "react";
import { Store, Link2, FileText, Ticket, ShoppingCart, CheckCircle2, CircleSlash, HelpCircle } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import { SkeletonBlock } from "./ui";

const e = React.createElement;

type CheckItem = {
  key?: string;
  label?: string;
  state?: string; // ready | missing | unknown
  detail?: string;
  basis?: string;
  sub_checks?: CheckItem[];
};
type ShopifyBlock = {
  connection_status?: string;
  shop_domain?: string;
  order_count?: number | null;
  status?: string; // ok | data_missing
  note?: string;
  basis?: string;
};
type IndieResp = {
  status?: string; // ok | not_found | invalid
  sku?: string;
  resolved?: boolean;
  note?: string;
  product?: { sku?: string; model_name?: string; marketing_name?: string; product_url?: string; price_usd?: number | null } | null;
  shopify?: ShopifyBlock;
  affiliate?: Record<string, any>;
  checklist?: CheckItem[];
  summary?: { ready?: number; missing?: number; unknown?: number; total?: number };
  generated_at?: string;
};

const STATE_TONE: Record<string, string> = {
  ready: "border-emerald-300/30 bg-emerald-500/[0.12] text-emerald-200",
  missing: "border-rose-300/30 bg-rose-500/[0.12] text-rose-200",
  unknown: "border-amber-300/25 bg-amber-500/[0.08] text-amber-200/90",
};
const STATE_LABEL: Record<string, string> = { ready: "就绪", missing: "缺失", unknown: "待核" };

const ITEM_ICON: Record<string, any> = {
  short_link: Link2,
  landing_page: FileText,
  commission_code: Ticket,
  purchase_path: ShoppingCart,
};

function stateBadge(state: string | undefined) {
  const key = String(state || "unknown");
  const tone = STATE_TONE[key] || STATE_TONE.unknown;
  const Icon = key === "ready" ? CheckCircle2 : key === "missing" ? CircleSlash : HelpCircle;
  return e("span", { className: "inline-flex items-center gap-1 shrink-0 rounded border px-1.5 py-0.5 text-[9px] font-medium " + tone },
    e(Icon, { size: 9 }),
    STATE_LABEL[key] || key,
  );
}

function ChecklistRow(it: CheckItem, i: number) {
  const Icon = ITEM_ICON[String(it.key || "")] || FileText;
  const subs = Array.isArray(it.sub_checks) ? it.sub_checks : [];
  return e("div", { key: it.key || i, className: "rounded border border-white/[0.05] bg-black/15 px-2.5 py-2" },
    e("div", { className: "flex items-center justify-between gap-2" },
      e("div", { className: "flex items-center gap-1.5 min-w-0" },
        e(Icon, { size: 11, className: "shrink-0 text-slate-400" }),
        e("span", { className: "truncate text-[10.5px] font-medium text-slate-200" }, String(it.label || it.key || "—")),
      ),
      stateBadge(it.state),
    ),
    it.detail && e("div", { className: "mt-1 text-[9.5px] leading-relaxed text-slate-400" }, String(it.detail)),
    it.basis && e("div", { className: "mt-0.5 text-[8.5px] leading-relaxed text-slate-600" }, "依据:" + String(it.basis)),
    // 子项(如落地页 URL / 样片 / FAQ):内容能核验的判 ready/missing,页面内容无法核验的诚实 unknown。
    subs.length > 0 && e("div", { className: "mt-1.5 space-y-1 border-l border-white/[0.06] pl-2" },
      subs.map((s: CheckItem, j: number) => e("div", { key: s.key || j, className: "flex items-center justify-between gap-2" },
        e("span", { className: "truncate text-[9px] text-slate-500" }, String(s.label || s.key || "—")),
        stateBadge(s.state),
      )),
    ),
  );
}

export function IndieSitePanel({ apiToken, sku }: any) {
  const [data, setData] = React.useState<IndieResp | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [failed, setFailed] = React.useState(false);

  React.useEffect(() => {
    setData(null);
    setFailed(false);
    const skuClean = String(sku || "").trim();
    if (!apiToken || !skuClean) return;
    let cancelled = false;
    setLoading(true);
    void apiFetch<IndieResp>(
      `/api/admin/vkpi/channel/indie-site-actions?sku=${encodeURIComponent(skuClean)}`,
      { timeoutMs: 20000 },
      apiToken,
    )
      .then((res) => { if (!cancelled) setData(res && typeof res === "object" ? res : null); })
      .catch(() => { if (!cancelled) setFailed(true); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [apiToken, sku]);

  if (!apiToken || !String(sku || "").trim()) return null;
  // 接口失败/未授权:安静缺席(增益块非阻塞),不给用户甩报错。
  if (failed) return null;

  const summary = data?.summary || {};
  const checklist = Array.isArray(data?.checklist) ? data!.checklist! : [];
  const shopify = data?.shopify || {};
  const notFound = data?.status === "not_found";
  const shopifyMissing = shopify.status === "data_missing";

  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    // 头部
    e("div", { className: "flex items-center gap-1.5 mb-2" },
      e(Store, { size: 11, className: "text-sky-400" }),
      e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "承接就绪 · Conversion Readiness"),
      data && e("span", { className: "text-[9px] text-slate-600" }, `就绪 ${Number(summary.ready) || 0}/${Number(summary.total) || checklist.length}`),
    ),

    // 加载:骨架屏(ui/ 基元)
    loading && !data && e("div", { className: "space-y-1.5" },
      e(SkeletonBlock, { className: "h-4 w-40 rounded" }),
      e(SkeletonBlock, { lines: 4 }),
    ),

    data && e(React.Fragment, null,
      // SKU 未命中:诚实说明 + checklist 结构仍就位(各项 unknown)
      notFound && e("div", { className: "mb-2 rounded border border-amber-300/25 bg-amber-500/[0.06] px-2 py-1.5 text-[9.5px] leading-relaxed text-amber-200/90" },
        String(data.note || "SKU 未命中 vkpi_products;承接 checklist 结构就位,各项待人工核验。"),
      ),

      // 本地 0 Shopify 订单:诚实 data_missing 横幅(购买路径未经真实成交验证)
      shopifyMissing && e("div", { className: "mb-2 rounded border border-white/[0.08] bg-white/[0.02] px-2 py-1.5 text-[9.5px] leading-relaxed text-slate-400" },
        e("span", { className: "mr-1 rounded bg-amber-500/15 px-1 py-0.5 text-[8.5px] font-semibold text-amber-200/90" }, "data_missing"),
        String(shopify.note || "本地 0 Shopify 订单;购买路径已搭,未经真实成交验证。"),
      ),

      // 承接 4 项 checklist
      checklist.length > 0
        ? e("div", { className: "space-y-1.5" }, checklist.map((it, i) => ChecklistRow(it, i)))
        : e("div", { className: "text-[10px] leading-relaxed text-slate-500" }, "暂无承接 checklist。"),

      // 汇总脚注(纯展示后端计数,不本地编造)
      data.summary && e("div", { className: "mt-2 flex flex-wrap items-center gap-1.5 text-[8.5px] text-slate-600" },
        e("span", { className: "rounded border border-emerald-300/20 px-1.5 py-0.5 text-emerald-300/80" }, `就绪 ${Number(summary.ready) || 0}`),
        e("span", { className: "rounded border border-rose-300/20 px-1.5 py-0.5 text-rose-300/80" }, `缺失 ${Number(summary.missing) || 0}`),
        e("span", { className: "rounded border border-amber-300/20 px-1.5 py-0.5 text-amber-300/80" }, `待核 ${Number(summary.unknown) || 0}`),
      ),
    ),
  );
}
