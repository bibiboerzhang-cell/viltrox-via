// B2 受众地图 v1:该 KOL 受众地理的多信号融合面板(深度分析 tab 增益块)。
// 数据:GET /api/admin/vkpi/kol-pool/{id}/audience-geo —— 评论语言 / 评论者名字字系 /
// 评论者档案国别 / 创作者国别弱先验 的独立分布 + 加权融合,零新采集/零 LLM。
// 展示:国家条(融合分布)+ 每信号小徽章(ready/weak/absent 各有色态,tooltip 带 reason)
// + confidence 徽章;confidence=low 时挂「代理口径」角标 —— 绝不把创作者国别代理伪装成受众实测。
// 诚实态:信号 status==="absent"/"weak" 如实展示 reason;接口失败整块安静缺席(非阻塞增益块)。
// 红线:纯展示,绝不渲染/触碰 viltrox_fit_score 与 rule_v0。
import React from "react";
import { Globe2 } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import { SectionFold } from "./SectionFold";

const e = React.createElement;

type CountryItem = { code?: string; name?: string; pct?: number };
type SignalItem = {
  key?: string; label?: string; status?: string; weight?: number; sample_size?: number;
  reason?: string; distribution?: CountryItem[];
  languages?: { lang?: string; pct?: number }[];
  scripts?: { script?: string; label?: string; pct?: number }[];
  latin_share_pct?: number | null;
  source_breakdown?: Record<string, number>;
  peak_hours_utc?: number[]; region_tilt?: string; proxy?: boolean;
};
type GeoResp = {
  status?: string; reason?: string; kol_pool_id?: number;
  geo_source?: string; proxy_only?: boolean;
  confidence?: string; confidence_reason?: string;
  comment_rows?: number;
  countries?: CountryItem[];
  signals?: SignalItem[];
  signals_used?: string[];
  note?: string;
};

const CONF_STYLE: Record<string, string> = {
  high: "bg-emerald-500/10 text-emerald-200 border-emerald-400/20",
  medium: "bg-amber-500/10 text-amber-200 border-amber-400/20",
  low: "bg-slate-500/10 text-slate-400 border-white/[0.08]",
};
const CONF_LABEL: Record<string, string> = { high: "置信 高", medium: "置信 中", low: "置信 低" };
const TILT_LABEL: Record<string, string> = {
  asia_pacific: "偏亚太时段", europe_africa: "偏欧非时段", americas: "偏美洲时段", flat: "无明显时段倾向",
};

function fmtPct(p: number | null | undefined): string {
  return typeof p === "number" && Number.isFinite(p) ? p.toFixed(1) + "%" : "—";
}

// 信号小徽章:ready=青 / weak=琥珀 / absent=暗灰 / side=淡紫;tooltip 带 reason/样本。
function SignalBadge(sig: SignalItem, i: number) {
  const status = String(sig.status || "absent");
  const cls =
    status === "ready" ? "border-cyan-400/20 bg-cyan-500/10 text-cyan-200"
    : status === "weak" ? "border-amber-400/20 bg-amber-500/10 text-amber-200"
    : status === "side" ? "border-purple-400/20 bg-purple-500/10 text-purple-200/80"
    : "border-white/[0.06] bg-black/20 text-slate-600";
  const extra =
    status === "ready" || status === "side" ? ` ×${Number(sig.sample_size) || 0}`
    : status === "weak" ? " 弱" : " 缺席";
  const tipParts: string[] = [];
  if (sig.reason) tipParts.push(String(sig.reason));
  if (status === "ready" && (Number(sig.sample_size) || 0) > 0) tipParts.push(`样本 ${sig.sample_size}`);
  if (sig.key === "activity_timezone" && sig.region_tilt) tipParts.push(TILT_LABEL[sig.region_tilt] || "");
  if (sig.key === "commenter_script" && sig.latin_share_pct != null) tipParts.push(`拉丁字系占 ${fmtPct(sig.latin_share_pct)}(国别不可分)`);
  return e("span", {
    key: sig.key || i,
    className: "rounded border px-1.5 py-0.5 text-[9px] " + cls,
    title: tipParts.filter(Boolean).join(" · ") || undefined,
  }, (sig.label || sig.key || "—") + extra);
}

export function AudienceGeoPanel({ apiToken, kolPoolId }: any) {
  const [data, setData] = React.useState<GeoResp | null>(null);

  // 开抽屉/换 KOL 只读拉取(后端纯聚合已有数据,读得起);失败静默不渲染。
  React.useEffect(() => {
    setData(null);
    if (!apiToken || !kolPoolId) return;
    let cancelled = false;
    void apiFetch<GeoResp>(
      `/api/admin/vkpi/kol-pool/${encodeURIComponent(String(kolPoolId))}/audience-geo`,
      {},
      apiToken,
    )
      .then((payload) => { if (!cancelled) setData(payload && typeof payload === "object" ? payload : null); })
      .catch(() => { if (!cancelled) setData(null); });
    return () => { cancelled = true; };
  }, [apiToken, kolPoolId]);

  if (!apiToken || !kolPoolId || !data) return null;
  if (String(data.status || "") !== "ready") return null; // 聚合失败:安静缺席,不甩后端报错

  const conf = String(data.confidence || "low");
  const countries = Array.isArray(data.countries) ? data.countries : [];
  const signals = Array.isArray(data.signals) ? data.signals : [];
  const maxPct = countries.reduce((acc, c) => Math.max(acc, Number(c.pct) || 0), 0);
  const proxyOnly = data.proxy_only === true || conf === "low";
  const langSig = signals.find((s) => s.key === "comment_language");
  const scriptSig = signals.find((s) => s.key === "commenter_script");

  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    e(SectionFold, {
      id: "audience-geo",
      header: e(React.Fragment, null,
        e(Globe2, { size: 11, className: "text-cyan-300" }),
        e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "受众地图 · 多信号融合"),
        e("span", { className: "rounded border px-1.5 py-0.5 text-[9px] " + (CONF_STYLE[conf] || CONF_STYLE.low), title: data.confidence_reason || "" },
          CONF_LABEL[conf] || conf),
        // 「代理口径」角标:confidence=low(仅创作者国别代理)时必挂,绝不伪装成受众实测
        proxyOnly && e("span", {
          className: "rounded border border-rose-400/25 bg-rose-500/10 px-1.5 py-0.5 text-[9px] text-rose-300",
          title: "无受众实测信号,以下分布主要来自创作者自报国别(弱先验),不代表受众所在地",
        }, "代理口径"),
      ),
    },
      // ── 国家条:融合后的 top 国家分布 ──
      countries.length === 0
        ? e("div", { className: "text-[10px] leading-relaxed text-amber-300/90" },
            "暂无任何受众地理信号(评论未入库且创作者国别为空)")
        : e("div", { className: "space-y-1" },
            countries.slice(0, 6).map((c, i) => {
              const pct = Number(c.pct) || 0;
              const widthPct = maxPct > 0 ? Math.max(6, Math.round((pct / maxPct) * 100)) : 0;
              return e("div", { key: c.code || i, className: "flex items-center gap-2 text-[10px]" },
                e("span", { className: "w-[30px] shrink-0 font-mono text-slate-400" }, c.code || "—"),
                e("span", { className: "w-[64px] shrink-0 truncate text-slate-300", title: c.name || c.code }, c.name || c.code || "—"),
                e("div", { className: "relative h-[7px] flex-1 overflow-hidden rounded-full bg-white/[0.05]" },
                  e("div", {
                    className: "absolute inset-y-0 left-0 rounded-full",
                    style: { width: widthPct + "%", background: "linear-gradient(90deg, rgba(34,211,238,0.35), rgba(34,211,238,0.75))" },
                  }),
                ),
                e("span", { className: "w-[44px] shrink-0 text-right tabular-nums text-slate-400" }, fmtPct(pct)),
              );
            }),
          ),

      // ── 每信号小徽章(独立信号态,tooltip 带 reason)──
      e("div", { className: "mt-2 flex flex-wrap items-center gap-1" },
        e("span", { className: "text-[9px] text-slate-500" }, "信号:"),
        signals.map((s, i) => SignalBadge(s, i)),
      ),

      // ── 明细补充行:语言分布 / 字系分布(有则带,纯展示)──
      langSig && langSig.status === "ready" && Array.isArray(langSig.languages) && langSig.languages.length > 0 &&
        e("div", { className: "mt-1 text-[9px] text-slate-500" },
          "评论语言:" + langSig.languages.slice(0, 5).map((l) => `${l.lang} ${fmtPct(Number(l.pct))}`).join(" · ")),
      scriptSig && (scriptSig.status === "ready" || scriptSig.status === "weak") && Array.isArray(scriptSig.scripts) && scriptSig.scripts.length > 0 &&
        e("div", { className: "mt-1 text-[9px] text-slate-500" },
          "名字字系:" + scriptSig.scripts.slice(0, 5).map((s) => `${s.label || s.script} ${fmtPct(Number(s.pct))}`).join(" · ")),

      proxyOnly && countries.length > 0 && e("div", { className: "mt-1.5 text-[9px] leading-relaxed text-rose-300/80" },
        "以上为创作者国别代理口径(无评论侧受众信号),仅弱先验参考,非受众实测。"),

      e("div", { className: "mt-1.5 text-[9px] text-slate-600" },
        String(data.note || "多信号估算口径,非平台官方受众数据") +
        (Number(data.comment_rows) > 0 ? ` · 评论样本 ${data.comment_rows} 条` : "") +
        " · 独立展示信号,不参与 V6 Fit 评分。"),
    ),
  );
}
