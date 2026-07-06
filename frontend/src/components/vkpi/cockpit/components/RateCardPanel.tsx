// 件B KOL 报价卡面板:估价区间 + 已录报价列表 + 手动录入表单(抽屉增益块)。
// 数据:GET /api/admin/vkpi/kol-pool/{id}/rate-estimate(真报价中位数优先,
//       无报价走 CPM 行业基准兜底 cpm_benchmark_v0, confidence=low)
//     + GET /api/admin/vkpi/kol-pool/{id}/rates(已录报价列表)
//     + POST 同 /rates 端点手动录入(amount_usd 必填,source/confidence/date 可选)。
// 诚实态:估价永远带 method/confidence/basis 溯源;基准兜底如实标「行业基准 v0 待喂养校准」;
//        数据不足展示后端 reason,不本地编数字。接口整体失败则安静缺席(非阻塞增益块)。
// 红线:纯展示 + 单表录入(vkpi_kol_rates);零触 viltrox_fit_score 与 rule_v0。
import React from "react";
import { CircleDollarSign } from "lucide-react";
import { apiFetch, jsonBody } from "../../../../services/http";
import { SectionFold } from "./SectionFold";

const e = React.createElement;

type EstimateResp = {
  status?: string; reason?: string;
  estimated_usd_p50?: number | null; low?: number | null; high?: number | null;
  method?: string | null; source_count?: number; confidence?: string; currency?: string;
  basis?: {
    kind?: string; followers?: number | null; tier?: string; platform?: string;
    views_used?: number | null; view_source?: string; cpm_usd_per_1k?: number[];
    benchmark_version?: string; note?: string; latest_recorded_at?: string | null;
    source_breakdown?: Record<string, number>;
  };
};
type RateItem = {
  id?: number; platform?: string; content_type?: string; amount_usd?: number | null;
  currency?: string; source?: string; confidence?: string; note?: string;
  effective_date?: string | null; created_at?: string | null;
};
type RatesResp = { status?: string; reason?: string; items?: RateItem[]; count?: number };

const SOURCE_LABEL: Record<string, string> = {
  contract: "合同价",
  outreach_reply: "外联回复",
  negotiation: "谈判报价",
  cpm_benchmark: "基准存档",
};
const CONTENT_TYPES: Array<{ v: string; label: string }> = [
  { v: "dedicated_video", label: "定制专片" },
  { v: "integrated_mention", label: "植入口播" },
  { v: "short_video", label: "短视频" },
  { v: "post", label: "图文帖" },
  { v: "other", label: "其他" },
];
const CONTENT_LABEL: Record<string, string> = CONTENT_TYPES.reduce(
  (acc, c) => ({ ...acc, [c.v]: c.label }), {} as Record<string, string>);

function fmtUsd(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(Number(n))) return "—";
  const v = Number(n);
  return "$" + (v >= 10000 ? Math.round(v).toLocaleString("en-US") : (Math.round(v * 100) / 100).toLocaleString("en-US"));
}

function fmtNum(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(Number(n))) return "—";
  const v = Number(n);
  if (v >= 1_000_000) return (Math.round(v / 100_000) / 10).toFixed(1) + "M";
  if (v >= 1_000) return (Math.round(v / 100) / 10).toFixed(1) + "K";
  return String(v);
}

function confidenceChip(confidence?: string) {
  const c = String(confidence || "").toLowerCase();
  const cls = c === "high"
    ? "bg-emerald-500/10 text-emerald-200"
    : c === "medium" ? "bg-sky-500/10 text-sky-200" : "bg-amber-500/10 text-amber-200";
  const label = c === "high" ? "置信 高" : c === "medium" ? "置信 中" : "置信 低";
  return e("span", { className: "rounded px-1.5 py-0.5 text-[9px] " + cls }, label);
}

// 空态行:诚实空态统一渲染(reason 直接来自后端,不本地编造)。
function EmptyLine(reason: string) {
  return e("div", { className: "text-[10px] leading-relaxed text-amber-300/90" }, reason || "暂无数据");
}

const inputCls = "w-full rounded border border-white/10 bg-black/30 px-1.5 py-1 text-[10px] text-slate-200 outline-none focus:border-cyan-300/40";

export function RateCardPanel({ apiToken, kolPoolId }: any) {
  const [estimate, setEstimate] = React.useState<EstimateResp | null>(null);
  const [rates, setRates] = React.useState<RatesResp | null>(null);
  const [reloadKey, setReloadKey] = React.useState(0);
  const [amount, setAmount] = React.useState("");
  const [contentType, setContentType] = React.useState("dedicated_video");
  const [source, setSource] = React.useState("outreach_reply");
  const [effectiveDate, setEffectiveDate] = React.useState("");
  const [note, setNote] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const [formMsg, setFormMsg] = React.useState("");

  // 开抽屉/换 KOL 拉估价 + 已录列表(均为轻量聚合读);整体失败安静缺席。
  React.useEffect(() => {
    if (reloadKey === 0) { setEstimate(null); setRates(null); }
    if (!apiToken || !kolPoolId) return;
    let cancelled = false;
    const base = `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}`;
    void apiFetch<EstimateResp>(`${base}/rate-estimate`, {}, apiToken)
      .then((p) => { if (!cancelled) setEstimate(p && typeof p === "object" ? p : null); })
      .catch(() => { if (!cancelled) setEstimate(null); });
    void apiFetch<RatesResp>(`${base}/rates`, {}, apiToken)
      .then((p) => { if (!cancelled) setRates(p && typeof p === "object" ? p : null); })
      .catch(() => { if (!cancelled) setRates(null); });
    return () => { cancelled = true; };
  }, [apiToken, kolPoolId, reloadKey]);

  // 换 KOL 清表单(避免上一个 KOL 的草稿串台)。
  React.useEffect(() => {
    setAmount(""); setNote(""); setEffectiveDate(""); setFormMsg("");
    setContentType("dedicated_video"); setSource("outreach_reply");
    setReloadKey(0);
  }, [kolPoolId]);

  const submit = () => {
    if (submitting || !apiToken || !kolPoolId) return;
    const parsed = Number(amount);
    if (!amount || !Number.isFinite(parsed) || parsed <= 0) {
      setFormMsg("金额必须是大于 0 的数字(USD)");
      return;
    }
    setSubmitting(true);
    setFormMsg("");
    void apiFetch<{ status?: string; reason?: string }>(
      `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/rates`,
      {
        method: "POST",
        body: jsonBody({
          amount_usd: parsed,
          content_type: contentType,
          source,
          note: note.trim(),
          effective_date: effectiveDate || undefined,
        }),
      },
      apiToken,
    )
      .then((resp) => {
        if (resp && resp.status === "ok") {
          setAmount(""); setNote(""); setEffectiveDate("");
          setFormMsg("已录入");
          setReloadKey((k) => k + 1); // 重拉:估价切真报价中位数
        } else {
          setFormMsg(String((resp && resp.reason) || "录入失败"));
        }
      })
      .catch((err) => { setFormMsg(String((err && err.message) || "录入失败")); })
      .finally(() => { setSubmitting(false); });
  };

  if (!apiToken || !kolPoolId) return null;
  if (!estimate && !rates) return null; // 双接口都没回(未接线/失败):安静缺席
  if (estimate && String(estimate.status || "") === "error") return null;

  const est = estimate || {};
  const basis = est.basis || {};
  const isBenchmark = est.method === "cpm_benchmark_v0";
  const items = (rates && Array.isArray(rates.items)) ? rates.items : [];

  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    e(SectionFold, {
      id: "rate-card",
      header: e(React.Fragment, null,
        e(CircleDollarSign, { size: 11, className: "text-emerald-300" }),
        e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "报价卡 · 请他要花多少钱"),
        (Number(est.source_count) || 0) > 0 && e("span", { className: "rounded bg-emerald-500/10 px-1.5 py-0.5 text-[9px] text-emerald-200" }, `真报价 ×${Number(est.source_count) || 0}`),
      ),
    },
      // ── 估价区间(method/confidence/basis 全程溯源)──
      est.status === "ready"
        ? e("div", { className: "rounded border border-white/[0.05] bg-black/20 px-2.5 py-2" },
            e("div", { className: "flex flex-wrap items-baseline gap-x-2 gap-y-1" },
              e("span", { className: "text-[15px] font-semibold tabular-nums text-slate-100" }, fmtUsd(est.estimated_usd_p50)),
              e("span", { className: "text-[10px] tabular-nums text-slate-400" }, `区间 ${fmtUsd(est.low)} – ${fmtUsd(est.high)}`),
              confidenceChip(est.confidence),
              e("span", {
                className: "rounded px-1.5 py-0.5 text-[9px] " + (isBenchmark ? "bg-amber-500/10 text-amber-200" : "bg-cyan-500/10 text-cyan-200"),
              }, isBenchmark ? "行业CPM基准 v0" : "已录报价中位数"),
            ),
            e("div", { className: "mt-1 text-[9px] leading-relaxed text-slate-500" },
              isBenchmark
                ? `依据:${String(basis.tier || "—")} 档 × ${String(basis.platform || "—")} CPM $${(basis.cpm_usd_per_1k || [])[0] ?? "—"}–$${(basis.cpm_usd_per_1k || [])[1] ?? "—"}/千播 × 均播放 ${fmtNum(basis.views_used)}(${
                    basis.view_source === "pool_avg_views" ? "账号均播放"
                      : basis.view_source === "evidence_avg_views" ? "证据视频均播放" : "按粉丝数保守折算"
                  })· 行业基准 v0 待喂养校准,仅供谈判锚点`
                : `依据:${Number(est.source_count) || 0} 条真实报价取中位数(${Object.entries(basis.source_breakdown || {}).map(([k, v]) => `${SOURCE_LABEL[k] || k}×${v}`).join(" / ") || "—"})· 最近录入 ${String(basis.latest_recorded_at || "").slice(0, 10) || "—"}`),
          )
        : EmptyLine(String((est as any).reason || "估价暂不可用")),

      // ── 已录报价列表 ──
      e("div", { className: "mt-2 mb-1 flex items-center gap-1.5" },
        e("span", { className: "text-[10px] font-medium text-slate-300" }, "已录报价"),
        rates && rates.status === "ready" && e("span", { className: "text-[8.5px] text-slate-600" }, `${Number(rates.count) || 0} 条`),
      ),
      rates && rates.status === "ready" && items.length > 0
        ? e("div", { className: "space-y-1" },
            items.slice(0, 8).map((r, i) => e("div", { key: r.id || i, className: "rounded border border-white/[0.05] bg-black/20 px-2 py-1" },
              e("div", { className: "flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[9.5px]" },
                e("span", { className: "font-semibold tabular-nums text-slate-200" }, fmtUsd(r.amount_usd)),
                e("span", { className: "rounded bg-white/[0.05] px-1.5 py-0.5 text-[9px] text-slate-300" }, SOURCE_LABEL[String(r.source || "")] || r.source || "—"),
                r.content_type && e("span", { className: "text-slate-500" }, CONTENT_LABEL[String(r.content_type)] || r.content_type),
                confidenceChip(r.confidence),
                e("span", { className: "text-slate-600" }, String(r.effective_date || r.created_at || "").slice(0, 10)),
              ),
              r.note && e("div", { className: "mt-0.5 text-[9px] leading-relaxed text-slate-500", title: r.note }, r.note),
            )),
          )
        : EmptyLine(String((rates && rates.reason) || "报价列表暂不可用")),

      // ── 手动录入表单(POST 真端点;成功后重拉,估价即切真报价中位数)──
      e("div", { className: "mt-2 rounded border border-white/[0.05] bg-black/20 px-2 py-1.5" },
        e("div", { className: "mb-1 text-[9.5px] font-medium text-slate-400" }, "录一条报价(USD)"),
        e("div", { className: "grid grid-cols-2 gap-1.5" },
          e("input", {
            className: inputCls, placeholder: "金额,如 1500", inputMode: "decimal",
            value: amount, onChange: (ev: any) => setAmount(String(ev.target.value)),
          }),
          e("select", { className: inputCls, value: source, onChange: (ev: any) => setSource(String(ev.target.value)) },
            e("option", { value: "outreach_reply" }, "外联回复开价"),
            e("option", { value: "negotiation" }, "谈判过程报价"),
            e("option", { value: "contract" }, "合同成交价"),
          ),
          e("select", { className: inputCls, value: contentType, onChange: (ev: any) => setContentType(String(ev.target.value)) },
            CONTENT_TYPES.map((c) => e("option", { key: c.v, value: c.v }, c.label)),
          ),
          e("input", {
            className: inputCls, type: "date", title: "生效日期(可选)",
            value: effectiveDate, onChange: (ev: any) => setEffectiveDate(String(ev.target.value)),
          }),
        ),
        e("input", {
          className: inputCls + " mt-1.5", placeholder: "备注(可选,如:含 2 条 IG story)",
          value: note, onChange: (ev: any) => setNote(String(ev.target.value)),
        }),
        e("div", { className: "mt-1.5 flex items-center gap-2" },
          e("button", {
            type: "button", disabled: submitting,
            className: "rounded border border-emerald-300/20 bg-emerald-500/10 px-2.5 py-1 text-[10px] text-emerald-200 hover:bg-emerald-500/20 disabled:opacity-50",
            onClick: submit,
          }, submitting ? "录入中…" : "录入报价"),
          formMsg && e("span", {
            className: "text-[9px] " + (formMsg === "已录入" ? "text-emerald-300" : "text-amber-300/90"),
          }, formMsg),
        ),
      ),

      e("div", { className: "mt-1.5 text-[9px] text-slate-600" },
        "口径:真实报价(合同/外联/谈判)中位数优先;无报价按粉丝层级×平台 CPM 基准折算(v0 待喂养校准,置信低);独立参考信号,不参与 V6 Fit 评分。"),
    ),
  );
}
