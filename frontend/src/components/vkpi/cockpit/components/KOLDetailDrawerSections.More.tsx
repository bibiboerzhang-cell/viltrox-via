// 纯重构:从 KOLDetailDrawerSections.tsx 抽出的内聚展示子区块(只吃 props,零 state/effect/handler)。
// idiom 保真:沿用 e()=React.createElement 写法。所有渲染逻辑逐字搬运,行为零变。
// 红线:本文件渲染 V6 Fit Breakdown 仅做只读展示(scoreText/fixedOrDash),绝不读写评分逻辑。

import React from "react";
import { Camera, Check, Flame, Globe2, Layers } from "lucide-react";
import { GeoTierChip } from "./GeoTierChip";
import { formatNumber } from "../lib/format";
import { BRAND_TIER } from "../data/brandTier";
import { getCountryInfo } from "../data/countryInfo";
import { fixedOrDash, pctOrZero, scoreText } from "./KOLDetailDrawer.helpers";

const e = React.createElement;

// ── 当前设备 & 升级机会 ──
export function KOLDrawerDevices({ item, devices }: any) {
  if (!devices.camera_body) return null;
  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    e("div", { className: "flex items-center gap-1.5 mb-2" },
      e(Camera, { size: 11, className: "text-slate-400" }),
      e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "当前设备 & 升级机会")
    ),
    // Camera body
    e("div", { className: "flex items-center gap-2 mb-2" },
      e("span", { className: "text-[10px] text-slate-500" }, "机身"),
      e("span", { className: "text-[12px] text-white font-medium" }, devices.camera_body)
    ),
    // Lenses
    devices.lenses.length > 0 && e("div", { className: "space-y-1 mb-2" },
      e("div", { className: "text-[10px] text-slate-500 mb-1" }, "在用镜头"),
      devices.lenses.map((l: any, i: number) => {
        const bi = (BRAND_TIER as any)[l.brand] || { color: "#94a3b8", label: l.brand };
        return e("div", { key: i, className: "flex items-center gap-2 text-[11px]" },
          e("span", {
            className: "px-1.5 py-0.5 rounded text-[9px] font-medium uppercase tracking-wider",
            style: {
              background: bi.tier === "viltrox" ? "rgba(168,85,247,0.18)"
                       : bi.tier === "competitor" ? "rgba(248,113,113,0.15)"
                       : "rgba(148,163,184,0.12)",
              color: bi.color
            }
          }, bi.label),
          e("span", { className: "text-white" }, l.model),
          l.type === "viltrox" && e("span", { className: "ml-auto text-[9px] text-emerald-400 inline-flex items-center gap-0.5" }, e(Check, { size: 9 }), "已用")
        );
      })
    ),
    // Upgrade window
    e("div", { className: "flex items-center gap-2 mt-2 pt-2 border-t border-white/[0.04]" },
      e("span", { className: "text-[10px] text-slate-500" }, "Upgrade 窗口"),
      e("span", {
        className: "px-2 py-0.5 rounded text-[10px] font-medium",
        style: {
          background: devices.upgrade_window === "very high" ? "rgba(16,185,129,0.2)"
                   : devices.upgrade_window === "high" ? "rgba(16,185,129,0.15)"
                   : devices.upgrade_window === "medium" ? "rgba(251,191,36,0.15)"
                   : "rgba(100,116,139,0.15)",
          color: devices.upgrade_window === "very high" ? "#34d399"
               : devices.upgrade_window === "high" ? "#86efac"
               : devices.upgrade_window === "medium" ? "#fde68a"
               : "#94a3b8"
        }
      }, devices.upgrade_window),
      e("span", { className: "text-[10px] text-slate-500" }, "× 系数 " + fixedOrDash(item.upgrade_factor || 1, 2))
    )
  );
}

// ── Geo distribution ──
const LANG_LABEL: Record<string, string> = {
  en: "英语", es: "西语", pt: "葡语", de: "德语", fr: "法语", it: "意语", id: "印尼语",
  tr: "土耳其语", nl: "荷语", zh: "中文", ja: "日语", ko: "韩语", ru: "俄语", ar: "阿语",
  th: "泰语", hi: "印地语", he: "希伯来语",
};

// Audience Stats · 估算 BETA(ensemble_v1):性别环 + Top countries + 语言分布 + 样本量/覆盖率/置信度,
// 数据源 item.audience_estimated(detail_bundle 透传的 audience_estimated_json)。刷新按钮的 state/handler
// 由父层 KOLDetailDrawer 持有并经 props 注入(本文件保持零内部 state)。旧「受众语言估算·评论法」在
// 无 ensemble 数据时作 fallback 展示。附 创作者所在地(诚实,非粉丝地理)。红线:纯展示,零触 fit。
const GENDER_COLORS = { male: "#3b82f6", female: "#ec4899", unknown: "#475569" };

export function KOLDrawerGeoDistribution({ item, geoDistribution, apiToken, audienceState = {}, onRefreshAudience }: any) {
  const aud = (item.audience_languages || {}) as any;
  const langs = Array.isArray(aud.languages) ? aud.languages : [];
  const hasLang = langs.length > 0 && Number(aud.sample_size || 0) > 0;
  const hasGeo = geoDistribution.length > 0;
  const est = (item.audience_estimated && typeof item.audience_estimated === "object") ? item.audience_estimated as any : null;
  const hasEst = Boolean(est && Number(est.sample_size || 0) > 0);
  const canRefresh = Boolean(apiToken && item?.id && typeof onRefreshAudience === "function");
  if (!hasEst && !hasLang && !hasGeo && !canRefresh) return null;

  const malePct = hasEst ? Math.min(100, Math.max(0, Number(est.gender?.male_pct) || 0)) : 0;
  const femalePct = hasEst ? Math.min(100 - malePct, Math.max(0, Number(est.gender?.female_pct) || 0)) : 0;
  const unknownPct = Math.max(0, Math.round((100 - malePct - femalePct) * 10) / 10);
  const estCountries = hasEst && Array.isArray(est.top_countries) ? est.top_countries.slice(0, 6) : [];
  const estLangs = hasEst && Array.isArray(est.languages) ? est.languages.slice(0, 6) : [];
  const coverage = (hasEst && est.coverage && typeof est.coverage === "object") ? est.coverage : {};
  const refreshBusy = audienceState.status === "loading";
  const refreshLabel = refreshBusy ? "刷新中…"
    : audienceState.status === "pending" ? "抓取评论中…可稍后再刷新"
    : hasEst ? "刷新受众统计" : "生成受众统计";
  const generatedDate = hasEst && est.generated_at ? String(est.generated_at).slice(0, 10) : "";

  const bar = (label: React.ReactNode, pct: number, color: string, key: any) =>
    e("div", { key, className: "flex items-center gap-2 text-[11px]" },
      label,
      e("div", { className: "flex-1 geo-bar-bg max-w-[120px]" },
        e("div", { className: "geo-bar-fill", style: { width: Math.min(100, pct) + "%", background: color } })
      ),
      e("span", { className: "text-slate-300 tabular-nums w-[42px] text-right" }, (Math.round(pct * 10) / 10) + "%")
    );

  return e("div", { className: "px-5 py-3 border-b border-white/[0.06] space-y-3" },
    // ── Audience Stats · 估算 BETA 面板(ensemble_v1)──
    (hasEst || canRefresh) && e("div", null,
      e("div", { className: "flex items-center gap-1.5 mb-2" },
        e(Globe2, { size: 11, className: "text-cyan-400" }),
        e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "Audience Stats · 估算"),
        e("span", { className: "px-1 py-px rounded text-[8px] font-semibold tracking-wider bg-amber-400/15 text-amber-300 border border-amber-400/25" }, "BETA")
      ),
      // 性别环(两色 conic-gradient donut)+ 图例
      hasEst && e("div", { className: "flex items-center gap-3 mb-2.5" },
        e("div", {
          className: "relative shrink-0",
          style: {
            width: 56, height: 56, borderRadius: "50%",
            background: `conic-gradient(${GENDER_COLORS.male} 0% ${malePct}%, ${GENDER_COLORS.female} ${malePct}% ${malePct + femalePct}%, ${GENDER_COLORS.unknown} ${malePct + femalePct}% 100%)`,
          },
        },
          e("div", { className: "absolute inset-[10px] rounded-full bg-[#0a1020] flex items-center justify-center" },
            e("span", { className: "text-[8px] text-slate-500" }, "性别")
          )
        ),
        e("div", { className: "space-y-0.5 text-[10px]" },
          e("div", { className: "flex items-center gap-1.5" },
            e("span", { className: "w-2 h-2 rounded-full", style: { background: GENDER_COLORS.male } }),
            e("span", { className: "text-slate-300" }, "男"),
            e("span", { className: "text-white tabular-nums font-medium" }, malePct + "%")
          ),
          e("div", { className: "flex items-center gap-1.5" },
            e("span", { className: "w-2 h-2 rounded-full", style: { background: GENDER_COLORS.female } }),
            e("span", { className: "text-slate-300" }, "女"),
            e("span", { className: "text-white tabular-nums font-medium" }, femalePct + "%")
          ),
          e("div", { className: "flex items-center gap-1.5" },
            e("span", { className: "w-2 h-2 rounded-full", style: { background: GENDER_COLORS.unknown } }),
            e("span", { className: "text-slate-500" }, "未知"),
            e("span", { className: "text-slate-400 tabular-nums" }, unknownPct + "%")
          )
        )
      ),
      // Top countries(国旗 + 条)
      estCountries.length > 0 && e("div", { className: "mb-2.5" },
        e("div", { className: "text-[10px] text-slate-500 mb-1" }, "Top Countries"),
        e("div", { className: "space-y-1.5" },
          estCountries.map((c: any, i: number) => {
            const cInfo = getCountryInfo(c.code) || { code: c.code, flag: "·", name: c.code, tier: "?" };
            const pct = Number(c.pct) || 0;
            return e("div", { key: i, className: "flex items-center gap-2 text-[11px]" },
              e("span", { style: { fontSize: 12 } }, cInfo.flag),
              e("span", { className: "text-white font-medium w-[28px]" }, cInfo.code),
              e("div", { className: "flex-1 geo-bar-bg max-w-[120px]" },
                e("div", { className: "geo-bar-fill", style: { width: Math.min(100, pct) + "%", background: i === 0 ? "#10b981" : "#64748b" } })
              ),
              e("span", { className: "text-slate-300 tabular-nums w-[42px] text-right" }, (Math.round(pct * 10) / 10) + "%")
            );
          })
        )
      ),
      // 语言分布
      estLangs.length > 0 && e("div", { className: "mb-2" },
        e("div", { className: "text-[10px] text-slate-500 mb-1" }, "语言"),
        e("div", { className: "space-y-1.5" },
          estLangs.map((l: any, i: number) => bar(
            e("span", { className: "text-white font-medium w-[52px]" }, LANG_LABEL[l.lang] || l.lang),
            Number(l.pct) || 0,
            i === 0 ? "#06b6d4" : "#64748b",
            i,
          ))
        )
      ),
      // 样本量 / 覆盖率 / 置信度 小字(诚实口径)
      hasEst && e("div", { className: "text-[9px] text-slate-500 leading-relaxed" },
        `样本 ${est.sample_size} 评论者 · 覆盖 自报${Number(coverage.declared_pct) || 0}% 人名${Number(coverage.name_pct) || 0}% 语言${Number(coverage.lang_pct) || 0}% · 置信 ${est.confidence ?? "—"}`
        + (est.shrinkage?.applied ? " · 同垂类收缩" : "")
        + (generatedDate ? ` · ${generatedDate}` : "")
      ),
      hasEst && e("div", { className: "text-[9px] text-amber-400/70" }, est.note || "估算值,非平台官方粉丝数据"),
      !hasEst && e("div", { className: "text-[10px] text-slate-500" }, "暂无受众画像数据 — 点击下方生成(评论者抽样估算,非官方数据)"),
      // 刷新按钮(loading 态;pending_comments=评论抓取中)
      canRefresh && e("button", {
        type: "button",
        disabled: refreshBusy,
        onClick: onRefreshAudience,
        className: "mt-2 flex w-full items-center justify-center gap-1.5 rounded-md border border-cyan-400/25 bg-cyan-400/[0.06] px-3 py-1.5 text-[10.5px] font-medium text-cyan-200 transition-colors hover:bg-cyan-400/[0.12] disabled:opacity-50",
      }, refreshLabel),
      audienceState.message && e("div", {
        className: "mt-1 text-[9.5px] leading-relaxed " + (audienceState.status === "error" ? "text-rose-300" : audienceState.status === "pending" ? "text-amber-300" : "text-slate-500")
      }, audienceState.message)
    ),
    // ── fallback:旧「受众语言估算·评论法」(无 ensemble 数据时仍展示已有语言分布)──
    !hasEst && hasLang && e("div", null,
      e("div", { className: "flex items-center gap-1.5 mb-2" },
        e(Globe2, { size: 11, className: "text-cyan-400" }),
        e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "受众语言估算 · 评论法")
      ),
      e("div", { className: "space-y-1.5" },
        langs.slice(0, 6).map((l: any, i: number) =>
          e("div", { key: i, className: "flex items-center gap-2 text-[11px]" },
            e("span", { className: "text-white font-medium w-[52px]" }, LANG_LABEL[l.lang] || l.lang),
            e("div", { className: "flex-1 geo-bar-bg max-w-[120px]" },
              e("div", { className: "geo-bar-fill", style: { width: Math.min(100, Number(l.pct) || 0) + "%", background: i === 0 ? "#06b6d4" : "#64748b" } })
            ),
            e("span", { className: "text-slate-300 tabular-nums w-[36px] text-right" }, (Number(l.pct) || 0) + "%")
          )
        )
      ),
      e("div", { className: "mt-1.5 text-[9px] text-slate-500" }, `样本 ${aud.sample_size} 评论 · 已判 ${aud.determined_pct || 0}% · 置信 ${aud.confidence}`),
      Array.isArray(aud.top_markets) && aud.top_markets.length > 0 && e("div", { className: "text-[9px] text-slate-500" }, "推测市场: " + aud.top_markets.join(" · ")),
      e("div", { className: "text-[9px] text-amber-400/70" }, aud.note || "估算值,非平台官方粉丝数据")
    ),
    hasGeo && e("div", null,
      e("div", { className: "flex items-center gap-1.5 mb-2" },
        e(Globe2, { size: 11, className: "text-slate-400" }),
        e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "创作者所在地")
      ),
      e("div", { className: "space-y-1.5" },
        geoDistribution.map((g: any, i: number) => {
          const cInfo = getCountryInfo(g.country) || { code: g.country, flag: "·", name: g.country, tier: "?" };
          const sharePct = pctOrZero(g.share);
          return e("div", { key: i, className: "flex items-center gap-2 text-[11px]" },
            e("span", { style: { fontSize: 12 } }, cInfo.flag),
            e("span", { className: "text-white font-medium w-[28px]" }, cInfo.code),
            e(GeoTierChip, { tier: cInfo.tier }),
            e("div", { className: "flex-1 geo-bar-bg max-w-[120px]" },
              e("div", { className: "geo-bar-fill", style: { width: sharePct + "%", background: cInfo.tier === "A" ? "#10b981" : cInfo.tier === "B" ? "#fbbf24" : "#64748b" } })
            ),
            e("span", { className: "text-slate-300 tabular-nums w-[40px] text-right" }, sharePct.toFixed(0) + "%")
          );
        })
      )
    )
  );
}

// ── V6 Fit Breakdown ──
export function KOLDrawerV6Breakdown({ item, v6Breakdown }: any) {
  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    e("div", { className: "flex items-center gap-1.5 mb-2" },
      e(Layers, { size: 11, className: "text-purple-400" }),
      e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "V6 Fit 公式 Breakdown")
    ),
    e("div", { className: "space-y-1.5" },
      [
        { label: "Base Match (内容)",        value: v6Breakdown?.base ?? item.v6_fit,         suffix: "" },
        { label: "× Industry Tier",          value: v6Breakdown?.industry,     suffix: "" },
        { label: "× Upgrade Factor",         value: v6Breakdown?.upgrade ?? item.upgrade_factor,      suffix: "" },
        { label: "× Geo Match (海外加权)",    value: v6Breakdown?.geo_match ?? item.geo_match,    suffix: "" },
        { label: "× Real ER",                value: v6Breakdown?.real_er,      suffix: "" },
        { label: "× Loyalty Depth",          value: v6Breakdown?.loyalty,      suffix: "" },
        { label: "× Trend Resonance",        value: v6Breakdown?.trend ?? item.trend_resonance,        suffix: "" },
        { label: "× Platform Native",        value: v6Breakdown?.platform_native, suffix: "" },
        { label: "× Price Match",            value: v6Breakdown?.price_match,  suffix: "" },
        { label: "× Network Boost",          value: v6Breakdown?.network,      suffix: "" },
      ].map((m, i) => {
        const val = m.value;
        const isBase = i === 0;
        const isPositive = !isBase && val >= 1.0;
        const isNegative = !isBase && val < 1.0;
        return e("div", { key: i, className: "flex items-center justify-between text-[11px]" },
          e("span", { className: "text-slate-400" }, m.label),
          e("span", {
            className: "tabular-nums font-medium",
            style: { color: isBase ? "#fff" : isPositive ? "#86efac" : isNegative ? "#fca5a5" : "#fff" }
          }, isBase ? (val ?? "—") : fixedOrDash(val))
        );
      }),
      v6Breakdown?.competitor_decay < 0 && e("div", { className: "flex items-center justify-between text-[11px]" },
        e("span", { className: "text-slate-400" }, "− Competitor Decay"),
        e("span", { className: "tabular-nums font-medium text-rose-400" }, v6Breakdown.competitor_decay)
      ),
      e("div", { className: "border-t border-white/[0.06] pt-1.5 mt-1 flex items-center justify-between" },
        e("span", { className: "text-[11px] text-slate-300 font-medium" }, "= Final V6 Fit"),
        e("span", { className: "text-[14px] font-semibold tabular-nums",
          style: { color: item.v6_fit >= 85 ? "#10b981" : item.v6_fit >= 70 ? "#fbbf24" : "#fb923c" }
        }, scoreText(item.v6_fit))
      )
    )
  );
}

// ── Trend hits ──
export function KOLDrawerTrendHits({ trendHits }: any) {
  if (!(trendHits.length > 0)) return null;
  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    e("div", { className: "flex items-center gap-1.5 mb-2" },
      e(Flame, { size: 11, className: "text-rose-400" }),
      e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "本周 Trend 命中")
    ),
    e("div", { className: "flex flex-wrap gap-1" },
      trendHits.map((t: any, i: number) => e("span", {
        key: i,
        className: "px-2 py-0.5 rounded text-[10px] border",
        style: { background: "rgba(239,68,68,0.08)", borderColor: "rgba(239,68,68,0.2)", color: "#fda4af" }
      }, "#" + t))
    )
  );
}
