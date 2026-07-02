// 纯重构:从 KOLDetailDrawer.tsx 抽出的纯展示子区块(只吃 props,零 state/effect/handler)。
// idiom 保真:沿用 e()=React.createElement 写法。所有渲染逻辑逐字搬运,行为零变。
// 红线:本文件绝不渲染 viltrox_fit_score / 不读写评分;长期记忆区严格逐字搬运。

import React from "react";
import { Activity, AlertTriangle, BadgeCheck, Check, ExternalLink, Flame, Heart, Layers, Link2, MapPin, MoreHorizontal, RefreshCw, Send, Shield, ShoppingBag, Sparkles, Star, Target, UserPlus, Video, Zap, X } from "lucide-react";
import { AudienceTypeChip } from "./AudienceTypeChip";
import { CandidateKindChip } from "./CandidateKindChip";
import { GeoTierChip } from "./GeoTierChip";
import { candidateKindGroup } from "../lib/candidateKind";
import { formatPercent } from "../lib/format";
import { COUNTRY_INFO } from "../data/countryInfo";
import { PlatformPill } from "./PlatformPill";
import { CopyEmailButton, KOLDetailAvatar, RepresentativeVideoCard } from "./KOLDetailDrawer";
import { SectionFold } from "./SectionFold";
import { asArray, compactText, concernLabel, fixedOrDash, numberOr, pctOrZero, recordOr, scoreText, scoreValue } from "./KOLDetailDrawer.helpers";

const e = React.createElement;

// ─── Header ───
export function KOLDrawerHeader({ item, devices, detailLoading, detailError, onClose }: any) {
  return e("div", { className: "px-5 py-4 border-b border-white/[0.06]" },
    e("div", { className: "flex items-start gap-3 mb-2" },
      e(KOLDetailAvatar, { item, size: 44 }),
      e("div", { className: "flex-1 min-w-0" },
        // 标题优先人话显示名(2026-07-02:YT 频道 handle 是 UCxxxx 乱码串,顶成大标题很难看);
        // handle 与显示名不同才降为副行,相同不重复渲染。
        e("div", { className: "flex items-center gap-1.5" },
          e("h2", { className: "text-[14px] font-semibold text-white truncate" }, item.display_name || item.handle),
          item.linked_main_kol_id && e(BadgeCheck, { size: 12, className: "text-emerald-400 shrink-0" }),
        ),
        item.handle && item.handle !== (item.display_name || item.handle) && e("div", { className: "text-[11px] text-slate-400 truncate" }, item.handle),
      ),
      // V6 Fit · 紧凑右上
      item.v6_fit != null && e("div", { className: "text-right shrink-0 px-2.5 py-1 rounded-md border border-white/[0.06] bg-white/[0.02]" },
        e("div", { className: "text-[8px] text-slate-500 uppercase tracking-wider leading-none mb-0.5" }, "V6 Fit"),
        e("div", { className: "text-[18px] font-semibold tabular-nums leading-none",
          style: { color: item.v6_fit >= 85 ? "#10b981" : item.v6_fit >= 70 ? "#fbbf24" : "#fb923c" }
        }, item.v6_fit)
      ),
      e("button", { onClick: onClose, className: "rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white shrink-0" },
        e(X, { size: 13 })
      )
    ),
    // chips 单独一行
    e("div", { className: "flex items-center gap-1.5 flex-wrap" },
      e(CandidateKindChip, { kind: item.candidate_kind }),
      e(PlatformPill, { platform: item.platform }),
      e(AudienceTypeChip, { type: item.audience_type }),
      e(GeoTierChip, { tier: item.geo_tier }),
      item.country && e("span", { className: "text-[10px] text-slate-500 inline-flex items-center gap-1" },
        e(MapPin, { size: 9 }),
        item.country
      ),
      devices.has_viltrox && e("span", {
        className: "inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-medium",
        style: { background: "rgba(168,85,247,0.18)", color: "#c4b5fd" }
      }, e(Check, { size: 9 }), "已用 Viltrox"),
      devices.competitor_brands.length > 0 && e("span", {
        className: "inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-medium",
        style: { background: "rgba(248,113,113,0.15)", color: "#fca5a5" },
        title: "在用友商:" + devices.competitor_brands.join(", ")
      }, e(AlertTriangle, { size: 9 }), "友商用户"),
    ),
    // C-fix:账户信息块(粉丝/关注/帖数/均赞均评/语言/首末视频)——此前 toCockpitKolPoolRows
    //   白名单截断,抽屉拿不到;现已透传,在此诚实展示(缺值显「—」)。
    e("div", { className: "mt-3 grid grid-cols-3 gap-x-3 gap-y-1.5 text-[10px]" },
      [
        ["粉丝", item.followers],
        ["关注", item.following],
        ["帖数", item.posts_count],
        ["均播放", item.avg_views],
        ["均赞", item.avg_likes],
        ["均评", item.avg_comments],
      ].map(([label, value], i) =>
        e("div", { key: i, className: "flex flex-col" },
          e("span", { className: "text-[8px] uppercase tracking-wider text-slate-500" }, label),
          e("span", { className: "text-slate-200 tabular-nums" }, value != null ? Number(value).toLocaleString() : "—")
        )
      ),
      e("div", { className: "flex flex-col" },
        e("span", { className: "text-[8px] uppercase tracking-wider text-slate-500" }, "语言"),
        e("span", { className: "text-slate-200" }, item.language || "—")
      ),
      e("div", { className: "col-span-2 flex flex-col" },
        e("span", { className: "text-[8px] uppercase tracking-wider text-slate-500" }, "视频区间"),
        e("span", { className: "text-slate-200 text-[9px]" },
          (item.first_video_at || "—") + " → " + (item.last_video_at || "—"))
      )
    ),
    (detailLoading || detailError) && e("div", {
      className: "mt-3 rounded-md border px-2.5 py-2 text-[10.5px] leading-snug",
      style: detailError
        ? { background: "rgba(251,113,133,0.10)", borderColor: "rgba(251,113,133,0.28)", color: "#fecdd3" }
        : { background: "rgba(96,165,250,0.10)", borderColor: "rgba(96,165,250,0.24)", color: "#bfdbfe" }
    }, detailError ? "详情 API 无信号: " + detailError + "；当前显示列表快照。" : "正在读取详情；先显示列表快照。"),
    // ── New-candidate action bar: promote / discard ──
    candidateKindGroup(item.candidate_kind) === "new" && e("div", {
      className: "mt-3 flex items-center gap-2 px-2.5 py-2 rounded-md border",
      style: {
        background: "rgba(168,85,247,0.06)",
        borderColor: "rgba(168,85,247,0.25)",
      }
    },
      e(Sparkles, { size: 12, className: "text-purple-300 shrink-0" }),
      e("div", { className: "flex-1 min-w-0" },
        e("div", { className: "text-[10.5px] text-purple-100" },
          item.candidate_kind === "new_promoted"  ? "新发现 · 高潜候选" :
          item.candidate_kind === "new_validated" ? "新发现 · 已通过基础校验" :
                                                     "新发现 · 校验中"
        ),
        e("div", { className: "text-[9.5px] text-slate-400 truncate" },
          "来源 query: ", e("span", { className: "text-slate-300" }, item.source_query || "—"),
          "  ·  发现于 ", item.discovered_at || "—",
          "  ·  validation_score ", item.validation_score
        ),
      ),
      item.candidate_kind === "new_promoted" && e("button", {
        onClick: (ev: any) => ev.stopPropagation(),
        disabled: true,
        className: "flex items-center gap-1 rounded-md px-2.5 py-1 text-[10px] font-medium text-slate-400 shrink-0 cursor-not-allowed",
        style: { background: "rgba(148,163,184,0.14)" }
      }, e(UserPlus, { size: 10 }), "待接入写入")
    )
  );
}

// ── Why V6 Fit = N? · 速读 4 bullets(规则生成) ──
export function KOLDrawerWhyFitCard({ v6Breakdown, loyaltySignals, geoDistribution, trendHits, item, devices, competitorCollabs, potentialConcerns }: any) {
  const b = v6Breakdown;
  const dims: any[] = [
    { key: "loyalty",  Icon: Heart, color: "#86efac",
      insight: (v: any) => "高忠诚度 ×" + fixedOrDash(v) + " · 老粉 " + (loyaltySignals.old_fans_pct ?? "—") + "% · 回复率 " + (loyaltySignals.creator_reply_pct ?? "—") + "%" },
    { key: "geo_match", Icon: Target, color: "#c4b5fd",
      insight: (v: any) => "海外 Geo 命中 ×" + fixedOrDash(v) + " · " + ((COUNTRY_INFO as any)[geoDistribution?.[0]?.country]?.flag || "") + " " + (geoDistribution?.[0]?.country || "—") + " 占 " + Math.round(pctOrZero(geoDistribution?.[0]?.share)) + "%" },
    { key: "trend",    Icon: Flame, color: "#fda4af",
      insight: (v: any) => "本周流行命中 ×" + fixedOrDash(v) + (trendHits.length ? " · #" + trendHits[0] : " · 未命中") },
    { key: "real_er",  Icon: (v: any) => v >= 1 ? Check : AlertTriangle, color: (v: any) => v >= 1 ? "#86efac" : "#fde68a",
      insight: (v: any) => "Real ER ×" + fixedOrDash(v) + " · 去水后 " + formatPercent(item.real_er_pct, 2) },
    { key: "upgrade",  Icon: Zap, color: "#fde68a",
      insight: (v: any) => "升级窗口 " + (devices.upgrade_window || "—") + " · 系数 ×" + fixedOrDash(v) },
    { key: "industry", Icon: Video, color: "#c4b5fd",
      insight: (v: any) => "行业 Tier " + (item.industry_tier || "—") + " · " + (item.industry_label || "") + " ×" + fixedOrDash(v) },
    { key: "platform_native", Icon: Activity, color: "#94a3b8",
      insight: (v: any) => "平台原生度 ×" + fixedOrDash(v) },
    { key: "price_match", Icon: ShoppingBag, color: "#94a3b8",
      insight: (v: any) => "价位匹配 ×" + fixedOrDash(v) },
  ];
  // 取偏离 1 最远的 top 3(最影响分数的维度)
  const scored = dims
    .filter(d => b[d.key] != null)
    .map(d => ({ ...d, value: b[d.key], deviation: Math.abs(b[d.key] - 1) }))
    .sort((a, x) => x.deviation - a.deviation)
    .slice(0, 3);
  // 第 4 条:风险/机会(永远显示一条,平衡感)
  let risk;
  if (competitorCollabs.length > 0) {
    risk = { Icon: Shield, color: "#fca5a5", text: "友商合作历史 · " + competitorCollabs.map((c: any) => typeof c === "string" ? c : (c.brand || "")).join(", ") };
  } else if (devices.competitor_brands.length > 0) {
    risk = { Icon: AlertTriangle, color: "#fde68a", text: "当前在用友商 · " + devices.competitor_brands.join(", ") };
  } else if (potentialConcerns.length > 0) {
    risk = { Icon: AlertTriangle, color: "#fde68a", text: concernLabel(potentialConcerns[0]) };
  } else {
    risk = { Icon: Check, color: "#86efac", text: "无友商合作 · 合作记录干净" };
  }
  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    e("div", { className: "flex items-center justify-between mb-2" },
      e("div", { className: "flex items-center gap-1.5" },
        e(Sparkles, { size: 11, className: "text-purple-400" }),
        e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "为什么 V6 Fit = " + item.v6_fit + "?")
      ),
      e("span", { className: "text-[9px] text-slate-600" }, "速读 · 看完整公式 ↓")
    ),
    e("div", { className: "space-y-1.5" },
      scored.map((d, i) => {
        const IconComp = typeof d.Icon === "function" && !d.Icon.$$typeof ? d.Icon(d.value) : d.Icon;
        const color = typeof d.color === "function" ? d.color(d.value) : d.color;
        return e("div", { key: i, className: "flex items-start gap-2 text-[11px] leading-snug" },
          e("span", { className: "shrink-0 inline-flex items-center justify-center w-4 h-4", style: { color } }, e(IconComp, { size: 11 })),
          e("span", { className: "text-slate-300" }, d.insight(d.value))
        );
      }),
      e("div", { className: "flex items-start gap-2 text-[11px] leading-snug pt-1.5 mt-1 border-t border-white/[0.04]" },
        e("span", { className: "shrink-0 inline-flex items-center justify-center w-4 h-4", style: { color: risk.color } }, e(risk.Icon, { size: 11 })),
        e("span", { className: "text-slate-300" }, risk.text)
      )
    )
  );
}

// ── 3-card grid: Real ER / Geo / Loyalty ──
// 【B6 Trend 诚实摘除 2026-07】原第 4 张「TREND RESONANCE」卡已移除:后端根本没有 trend_score/
// trend_resonance 数据源(列表映射恒 null → 卡片恒显示 —/100 · Trend数据待接入,纯占位噪音)。
// 数据接入后恢复方式:在下方 grid 里补回 Flame 图标的 Trend Resonance 卡(实现见 git 历史
// 2026-07-02 之前版本,值 pctOrZero(item.trend_resonance).toFixed(0) + trendHits 命中数 sub),
// 并把 grid-cols-3 改回 grid-cols-2(2x2)。
export function KOLDrawerMetricGrid({ item, loyaltySignals }: any) {
  // 【C3 空态合并】三卡全空(Real ER / Audience·HHI / Loyalty 的值全是 —)时不摆三张空卡,
  // 合并成一行淡字引导;有任一真值则照常渲染卡片。
  const realErEmpty = numberOr(item.real_er_pct) == null;
  const audienceEmpty = !item.audience_type && numberOr(item.hhi) == null;
  const loyaltyEmpty = numberOr(item.loyalty_score) == null && loyaltySignals.old_fans_pct == null;
  if (realErEmpty && audienceEmpty && loyaltyEmpty) {
    return e("div", { className: "px-5 py-2.5 border-b border-white/[0.06]" },
      e("div", { className: "text-[10px] text-slate-500 leading-relaxed" },
        "待补数据:互动 · 受众集中度 · 忠诚度 —— 视频/评论抓取后自动出现")
    );
  }
  return e("div", { className: "px-5 py-3 border-b border-white/[0.06] grid grid-cols-3 gap-2" },
    // Real Engagement
    e("div", { className: "rounded-md border border-white/[0.06] bg-white/[0.02] p-2.5" },
      e("div", { className: "flex items-center gap-1.5 mb-1.5" },
        e(Heart, { size: 10, className: "text-rose-400" }),
        e("span", { className: "text-[9px] uppercase tracking-wider text-slate-400" }, "Real Engagement")
      ),
      e("div", { className: "flex items-baseline gap-1.5" },
        e("span", { className: "text-xl font-light text-white tabular-nums" }, formatPercent(item.real_er_pct, 2)),
        item.er_calibration != null && e("span", { className: "text-[10px] text-rose-400 tabular-nums" }, item.er_calibration + "%")
      ),
      e("div", { className: "text-[9px] text-slate-500" },
        "原始 ", formatPercent(item.engagement_rate, 1), " · 去 ", Math.abs(item.er_calibration || 0), "% 水分"
      )
    ),
    // Audience Type · HHI
    e("div", { className: "rounded-md border border-white/[0.06] bg-white/[0.02] p-2.5" },
      e("div", { className: "flex items-center gap-1.5 mb-1.5" },
        e(MapPin, { size: 10, className: "text-amber-400" }),
        e("span", { className: "text-[9px] uppercase tracking-wider text-slate-400" }, "Audience · HHI")
      ),
      e("div", { className: "flex items-baseline gap-1.5" },
        e(AudienceTypeChip, { type: item.audience_type }),
        e("span", { className: "text-[10px] text-slate-500 tabular-nums" }, "HHI " + fixedOrDash(item.hhi || 0, 2))
      ),
      e("div", { className: "text-[9px] text-slate-500 mt-1" },
        item.audience_type === "Global" ? "适合国际展会 / 跨市场" :
        item.audience_type === "Regional" ? "适合区域 campaign" :
        "适合本地线下活动"
      )
    ),
    // Loyalty
    e("div", { className: "rounded-md border border-white/[0.06] bg-white/[0.02] p-2.5" },
      e("div", { className: "flex items-center gap-1.5 mb-1.5" },
        e(Shield, { size: 10, className: "text-emerald-400" }),
        e("span", { className: "text-[9px] uppercase tracking-wider text-slate-400" }, "Loyalty Depth")
      ),
      e("div", { className: "flex items-baseline gap-1.5" },
        e("span", { className: "text-xl font-light text-white tabular-nums" }, fixedOrDash(item.loyalty_score, 2)),
      ),
      e("div", { className: "text-[9px] text-slate-500" },
        loyaltySignals.old_fans_pct != null ? "老粉 " + loyaltySignals.old_fans_pct + "% · 回复率 " + loyaltySignals.creator_reply_pct + "%" : "—"
      )
    )
    // (B6)Trend Resonance 卡原位于此 —— 数据接入后按顶部注释恢复。
  );
}

// ── 11 维度雷达: persisted backend dimensions_11_json only ──
export function KOLDrawerRadar11({ dims, dimensions11 }: any) {
  return e("div", { className: "px-5 py-4 border-b border-white/[0.06]" },
    e("div", { className: "flex items-center justify-between mb-3" },
      e("div", { className: "flex items-center gap-1.5" },
        e(Target, { size: 11, className: "text-purple-400" }),
        e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "11 维度评估")
      ),
      e("span", { className: "text-[9px] text-slate-500" },
        "规则画像 · ",
        scoreText(dimensions11?.overall_score),
        " · conf ",
        fixedOrDash(recordOr(dimensions11?.confidence).overall, 2)
      )
    ),
    (() => {
      return e("div", { className: "flex items-center gap-4" },
        e("svg", { width: 160, height: 160, viewBox: "-100 -100 200 200", className: "shrink-0" },
          [20, 40, 60, 80].map(r => e("circle", { key: r, cx: 0, cy: 0, r, className: "radar-bg", fill: "none", stroke: "rgba(255,255,255,0.06)" })),
          dims.map((d: any, i: number) => {
            const angle = (i / dims.length) * 2 * Math.PI - Math.PI / 2;
            return e("line", {
              key: i, x1: 0, y1: 0,
              x2: 80 * Math.cos(angle), y2: 80 * Math.sin(angle),
              stroke: "rgba(255,255,255,0.06)", strokeWidth: 1
            });
          }),
          e("polygon", {
            fill: "rgba(168,85,247,0.18)",
            stroke: "#a855f7",
            strokeWidth: 1.5,
            points: dims.map((d: any, i: number) => {
              const angle = (i / dims.length) * 2 * Math.PI - Math.PI / 2;
              const r = (scoreValue(d.value) / 100) * 80;
              return `${r * Math.cos(angle)},${r * Math.sin(angle)}`;
            }).join(" ")
          })
        ),
        e("div", { className: "flex-1 grid grid-cols-2 gap-x-3 gap-y-1 text-[10px]" },
          dims.map((d: any, i: number) => e("div", { key: i, className: "flex items-center justify-between" },
            e("span", { className: "text-slate-400" }, d.label),
            e("span", { className: "text-white tabular-nums font-medium" }, scoreText(d.value))
          ))
        )
      );
    })()
  );
}

// ── 长期记忆(W3)· 显式独立于 V6 Fit · 不影响排序 ──
// 红线:本区块纯渲染聚合记忆,绝不渲染任何 viltrox/v6_fit 数值。
export function KOLDrawerMemorySection({ kolMemory }: any) {
  const memRecord = recordOr(kolMemory);
  const snap = recordOr(memRecord.snapshot);
  const hasMemory = memRecord.status === "ready" || memRecord.status === "missing";
  if (!hasMemory) return null;
  const contentStyle = compactText(snap.content_style, 320);
  const productLines = Array.isArray(snap.recommended_product_lines)
    ? snap.recommended_product_lines.map((x: any) => compactText(x, 60)).filter(Boolean)
    : [];
  const risk = recordOr(snap.risk);
  const riskFlags = Array.isArray(risk.risk_flags)
    ? risk.risk_flags.map((x: any) => compactText(x, 80)).filter(Boolean)
    : [];
  const riskVerdict = compactText(risk.final_verdict, 160);
  const fulfillment = recordOr(snap.fulfillment);
  const timeline = Array.isArray(snap.timeline) ? snap.timeline : [];
  const EVENT_LABEL = {
    discovered: "发现",
    favorited: "收藏",
    assigned: "派单",
    shipped: "寄样",
    published: "发布",
    analyzed: "深析",
    failed: "失败",
  };
  const formatWhen = (value: any) => {
    const text = String(value || "").trim();
    if (!text) return "";
    return text.length >= 10 ? text.slice(0, 10) : text;
  };
  const fulfillmentItems = [
    { label: "派单", value: numberOr(fulfillment.assigned_count) || 0 },
    { label: "寄样", value: numberOr(fulfillment.shipped_count) || 0 },
    { label: "发布", value: numberOr(fulfillment.published_count) || 0 },
    { label: "失败任务", value: numberOr(fulfillment.failed_jobs_count) || 0 },
  ];
  return e("div", { className: "px-5 py-4 border-b border-white/[0.06]" },
    // 标题 · 显式区隔评分
    e("div", { className: "flex items-center gap-1.5 mb-3" },
      e(Layers, { size: 11, className: "text-violet-400" }),
      e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "长期记忆"),
      e("span", { className: "ml-1.5 text-[9px] px-1.5 py-0.5 rounded border border-violet-400/20 bg-violet-400/[0.06] text-violet-300/90" }, "独立于 V6 Fit · 不影响排序"),
      memRecord.status === "missing" && e("span", { className: "ml-auto text-[9px] text-slate-600 italic" }, "暂无聚合数据")
    ),
    // 内容风格
    e("div", { className: "mb-3" },
      e("div", { className: "flex items-center gap-1 mb-1" },
        e(Sparkles, { size: 10, className: "text-cyan-400/80" }),
        e("span", { className: "text-[10px] text-slate-500" }, "内容风格")
      ),
      contentStyle
        ? e("p", { className: "text-[11px] text-slate-300 leading-relaxed" }, contentStyle)
        : e("span", { className: "text-[10px] text-slate-600 italic" }, "—")
    ),
    // 推荐产品线
    e("div", { className: "mb-3" },
      e("div", { className: "flex items-center gap-1 mb-1" },
        e(ShoppingBag, { size: 10, className: "text-emerald-400/80" }),
        e("span", { className: "text-[10px] text-slate-500" }, "推荐产品线")
      ),
      productLines.length > 0
        ? e("div", { className: "flex items-center gap-1.5 flex-wrap" },
            productLines.map((line: any, i: number) => e("span", {
              key: i,
              className: "text-[10px] px-1.5 py-0.5 rounded border border-emerald-400/15 bg-emerald-400/[0.05] text-emerald-200/90",
            }, line))
          )
        : e("span", { className: "text-[10px] text-slate-600 italic" }, "—")
    ),
    // 风险
    e("div", { className: "mb-3" },
      e("div", { className: "flex items-center gap-1 mb-1" },
        e(Shield, { size: 10, className: "text-amber-400/80" }),
        e("span", { className: "text-[10px] text-slate-500" }, "风险")
      ),
      (riskFlags.length > 0 || riskVerdict)
        ? e("div", { className: "space-y-1" },
            riskFlags.length > 0 && e("div", { className: "flex items-start gap-1.5 flex-wrap" },
              riskFlags.map((flag: any, i: number) => e("span", {
                key: i,
                className: "inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border border-amber-400/20 bg-amber-400/[0.06] text-amber-200/90",
              },
                e(AlertTriangle, { size: 9, className: "text-amber-400" }),
                flag
              ))
            ),
            riskVerdict && e("p", { className: "text-[10px] text-slate-400 leading-relaxed" }, riskVerdict)
          )
        : e("span", { className: "text-[10px] text-slate-600 italic" }, "无明显风险标记")
    ),
    // 合作履约
    e("div", { className: "mb-3" },
      e("div", { className: "flex items-center gap-1 mb-1.5" },
        e(Activity, { size: 10, className: "text-sky-400/80" }),
        e("span", { className: "text-[10px] text-slate-500" }, "合作履约")
      ),
      e("div", { className: "grid grid-cols-4 gap-1.5" },
        fulfillmentItems.map((stat, i) => e("div", {
          key: i,
          className: "px-2 py-1.5 rounded-md border border-white/[0.06] bg-white/[0.02] text-center",
        },
          e("div", { className: "text-[14px] font-semibold tabular-nums text-white leading-none mb-0.5" }, stat.value),
          e("div", { className: "text-[9px] text-slate-500 leading-none" }, stat.label)
        ))
      )
    ),
    // 时间线
    e("div", null,
      e("div", { className: "flex items-center gap-1 mb-1.5" },
        e(Star, { size: 10, className: "text-violet-400/80" }),
        e("span", { className: "text-[10px] text-slate-500" }, "时间线")
      ),
      timeline.length > 0
        ? e("div", { className: "space-y-1.5" },
            timeline.slice(0, 12).map((ev: any, i: number) => {
              const evRecord = recordOr(ev);
              const evType = String(evRecord.event_type || "").trim();
              return e("div", { key: i, className: "flex items-center gap-2 text-[10px]" },
                e("span", { className: "w-1 h-1 rounded-full bg-violet-400/60 shrink-0" }),
                e("span", { className: "text-slate-300 w-[40px] shrink-0" }, (EVENT_LABEL as any)[evType] || evType || "事件"),
                e("span", { className: "text-slate-500 tabular-nums shrink-0" }, formatWhen(evRecord.occurred_at)),
                evRecord.ref_type && e("span", { className: "text-slate-600 truncate" }, String(evRecord.ref_type) + (evRecord.ref_id ? " · " + String(evRecord.ref_id) : ""))
              );
            })
          )
        : e("span", { className: "text-[10px] text-slate-600 italic" }, "暂无生命周期事件")
    )
  );
}

// ── 联系方式 & 代表视频 ──
export function KOLDrawerContactAndVideos({ item, representativeVideos, onOpenVideo }: any) {
  return e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
    e("div", { className: "flex items-center gap-1.5 mb-2" },
      e(Send, { size: 11, className: "text-cyan-400" }),
      e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "联系方式 & 代表作")
    ),
    // 联系方式
    e("div", { className: "space-y-1 mb-3" },
      e("div", { className: "flex items-center gap-2 text-[11px]" },
        e("span", { className: "text-slate-500 w-[40px]" }, "邮箱"),
        item.email
          ? e("span", { className: "text-cyan-300" }, item.email)
          : e("span", { className: "text-slate-500 italic" }, "未收集 · 邀请时需先添加"),
        item.email && e(CopyEmailButton, { email: item.email })
      ),
      e("div", { className: "flex items-center gap-2 text-[11px]" },
        e("span", { className: "text-slate-500 w-[40px]" }, "主页"),
        item.profile_url
          ? e("a", {
              href: item.profile_url, target: "_blank", rel: "noreferrer",
              className: "text-cyan-300 hover:text-cyan-200 truncate flex-1"
            }, item.profile_url.replace("https://", ""))
          : e("span", { className: "text-slate-500" }, "—")
      )
    ),
    // 代表作视频
    // 【C5】三张大缩略图(grid-cols-3 + aspect-video + 标题块)改为一行紧凑小图(高约 56px 横排,
    // hover 放大 + title 提示),省抽屉纵向空间;点击行为不变(仍走 onOpenVideo 开播放器)。
    representativeVideos.length > 0 && e("div", null,
      e("div", { className: "text-[10px] text-slate-500 mb-1.5" }, "代表作"),
      e("div", { className: "flex flex-wrap items-center gap-1.5" },
        representativeVideos.map((v: any, i: number) => e(RepresentativeVideoCard, {
          key: v.evidence_id || v.watch_url || v.url || v.title || i,
          video: v,
          index: i,
          onOpen: onOpenVideo,
          compact: true,
        }))
      )
    )
  );
}

// 地基B:内容契合深析(content_fit_v1)——基于视频画面/故事 + 评论的适配判断(胜过粉丝数)。
export function KOLDrawerContentFit({ apiToken, item, contentFit, contentFitBusy, contentFitError, onAnalyze }: any) {
  if (!(apiToken && item.id)) return null;
  return e("div", { className: "px-5 py-4 border-b border-white/[0.06]" },
    e("div", { className: "flex items-center gap-1.5 mb-2" },
      e(Sparkles, { size: 11, className: "text-cyan-300" }),
      e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "内容契合深析"),
      e("span", { className: "text-[9px] text-slate-600" }, "基于视频画面/故事 + 评论 · 胜过粉丝数"),
      e("button", {
        className: "ml-auto rounded-md border border-cyan-300/25 bg-cyan-300/[0.08] px-2 py-1 text-[10px] font-medium text-cyan-100 hover:bg-cyan-300/[0.16] disabled:opacity-50",
        disabled: contentFitBusy,
        onClick: () => onAnalyze(Boolean(contentFit)),
        title: contentFit ? "重新深析(force,重算 LLM)" : "按需触发深析(读已有视频分析+评论,经 LLM 综合)",
      }, contentFitBusy ? "深析中…" : contentFit ? "重新深析" : "开始深析")
    ),
    contentFitError && e("div", { className: "text-[10px] text-amber-300/90 mb-2" }, contentFitError),
    !contentFit && !contentFitError && !contentFitBusy && e("div", { className: "text-[11px] text-slate-500" },
      "尚无内容契合深析结果。点击「开始深析」基于该 KOL 过往视频的画面/故事与粉丝评论生成适配判断。"
    ),
    contentFit && (() => {
      const r = recordOr(contentFit.result);
      const verdict = String(r.fit_verdict || "");
      const verdictColor = verdict === "fit" ? "text-emerald-300" : verdict === "not_fit" ? "text-red-300" : "text-amber-300";
      const verdictLabel = verdict === "fit" ? "适合" : verdict === "not_fit" ? "不适合" : verdict === "partial_fit" ? "部分适合" : (verdict || "—");
      const reasons = asArray(r.fit_reasons);
      const basis = recordOr(r.evidence_basis);
      return e("div", { className: "space-y-2.5" },
        // 创作者类型 + 判定 + 置信度
        e("div", { className: "flex items-center flex-wrap gap-2" },
          e("span", { className: "px-1.5 py-0.5 rounded text-[9px] font-medium uppercase tracking-wider bg-cyan-300/[0.10] text-cyan-200" }, "创作者类型"),
          e("span", { className: "text-[12px] text-white font-medium" }, r.creator_type || "—"),
          e("span", { className: "ml-auto flex items-center gap-1 text-[11px] " + verdictColor },
            e(Target, { size: 11 }), verdictLabel,
            e("span", { className: "text-slate-500" }, "· 置信 " + fixedOrDash(r.confidence, 2))
          )
        ),
        // 过往视频画面/故事综述
        r.content_summary && e("div", null,
          e("div", { className: "text-[10px] text-slate-500 mb-0.5" }, "过往视频画面/故事综述"),
          e("div", { className: "text-[11px] text-slate-300 leading-relaxed" }, r.content_summary)
        ),
        // 受众信号(评论)
        r.audience_signal && e("div", null,
          e("div", { className: "flex items-center gap-1 text-[10px] text-slate-500 mb-0.5" },
            e(Heart, { size: 9, className: "text-rose-300/80" }), "受众信号(粉丝评论)"
          ),
          e("div", { className: "text-[11px] text-slate-300 leading-relaxed" }, r.audience_signal)
        ),
        // 逐条契合理由(基于内容证据)
        reasons.length > 0 && e("div", null,
          e("div", { className: "text-[10px] text-slate-500 mb-1" }, "契合判断(逐条基于内容证据)"),
          e("ul", { className: "space-y-1" },
            reasons.map((reason: any, i: number) => e("li", { key: i, className: "flex gap-1.5 text-[11px] text-slate-300 leading-relaxed" },
              e("span", { className: "text-cyan-400/70 mt-px" }, "·"),
              e("span", null, String(reason))
            ))
          )
        ),
        // 证据基础脚注
        e("div", { className: "text-[9px] text-slate-600 tabular-nums pt-1" },
          "依据 " + (basis.video_count ?? 0) + " 条视频分析 · " + (basis.comment_count ?? 0) + " 条评论"
          + (contentFit.model ? " · " + String(contentFit.model) : "")
        )
      );
    })()
  );
}

// 行为不变重构:KOLDrawerDevices / KOLDrawerGeoDistribution / KOLDrawerV6Breakdown / KOLDrawerTrendHits
// 已搬到 ./KOLDetailDrawerSections.More(逐字搬运,只读展示)。此处 re-export 保留对外契约。
export { KOLDrawerDevices, KOLDrawerGeoDistribution, KOLDrawerV6Breakdown, KOLDrawerTrendHits } from "./KOLDetailDrawerSections.More";

// ── 文本区块合集: Viltrox 适配判断 / 推荐产品线 / 风险点 / 品牌合作历史 ──
// 四小块逐块包 SectionFold(各自现有标题行原样进 header),内容做 children;纯包裹,行为零变。
// 【C4】foldDefaultOpen:新发现/校验中候选(candidateKindGroup === "new")时父层传 false,
// 四块默认收起省屏;SectionFold 先读 localStorage(用户手动折叠记忆优先于本默认值)。
export function KOLDrawerTextSections({ item, recommendedProductLines, potentialConcerns, brandCollaborations, competitorCollabs, foldDefaultOpen = true }: any) {
  return e(React.Fragment, null,
    // ── Viltrox Fit reason ──
    e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
      e(SectionFold, {
        id: "fit-judgment",
        defaultOpen: foldDefaultOpen,
        header: e(React.Fragment, null,
          e(Sparkles, { size: 11, className: "text-purple-400" }),
          e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "Viltrox 适配判断")
        ),
      },
      item.viltrox_fit_reason
        ? e("p", { className: "text-[11px] text-slate-300 leading-relaxed" }, item.viltrox_fit_reason)
        : e("p", { className: "text-[11px] text-slate-500 leading-relaxed" }, "本地适配原因字段为空 · 等待 enrichment 或人工补全")
      )
    ),
    // ── Recommended products ──
    e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
      e(SectionFold, {
        id: "product-lines",
        defaultOpen: foldDefaultOpen,
        header: e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "推荐产品线"),
      },
      recommendedProductLines.length > 0
        ? e("div", { className: "flex flex-wrap gap-1.5" }, recommendedProductLines.map((p: any, i: number) => e("span", {
          key: i,
          className: "px-2 py-1 rounded-md border text-[10px]",
          style: { background: "rgba(168,85,247,0.08)", borderColor: "rgba(168,85,247,0.3)", color: "#c4b5fd" }
        }, p)))
        : e("div", { className: "text-[11px] text-slate-500" }, "本地推荐字段为空 · 等待 enrichment 或历史合作导入")
      )
    ),
    // ── Concerns ──
    e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
      e(SectionFold, {
        id: "risks",
        defaultOpen: foldDefaultOpen,
        // amber 色随原标题行走(lucide 图标吃 currentColor),整体包一个 span 保持配色不变
        header: e("span", { className: "flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-amber-300" },
          e(AlertTriangle, { size: 10 }), "风险点"
        ),
      },
      potentialConcerns.length > 0
        ? e("div", { className: "space-y-1" }, potentialConcerns.map((c: any, i: number) => e("div", {
          key: i,
          className: "text-[11px] text-slate-300 pl-3 border-l-2 border-amber-400/40 py-0.5"
        }, concernLabel(c))))
        : e("div", { className: "text-[11px] text-slate-500" }, "暂无结构化风险点 · 本地风险字段为空")
      )
    ),
    // ── Brand history ──
    e("div", { className: "px-5 py-3 border-b border-white/[0.06]" },
      e(SectionFold, {
        id: "coop-history",
        defaultOpen: foldDefaultOpen,
        header: e("span", { className: "text-[10px] uppercase tracking-wider text-slate-500" }, "品牌合作历史"),
      },
      brandCollaborations.length > 0
        ? e("div", { className: "space-y-1.5" }, brandCollaborations.map((b: any, i: number) => {
          const isCompetitor = competitorCollabs.includes(b.brand);
          return e("div", { key: i, className: "flex items-center gap-2 text-[10px]" },
            e("span", {
              className: "shrink-0 px-1.5 py-0.5 rounded text-[9px] font-medium",
              style: isCompetitor
                ? { background: "rgba(248,113,113,0.15)", color: "#f87171" }
                : b.brand === "Viltrox"
                ? { background: "rgba(168,85,247,0.18)", color: "#c4b5fd" }
                : { background: "rgba(100,116,139,0.15)", color: "#94a3b8" }
            }, b.brand),
            e("span", { className: "text-slate-500" }, b.year + " · "),
            e("span", { className: "text-slate-300 flex-1 text-[11px]" }, b.deal),
            isCompetitor && e("span", { className: "text-[9px] text-rose-400 font-medium" }, "友商")
          );
        }))
        : e("div", { className: "text-[11px] text-slate-500" }, "本地合作历史为空 · 等待历史合作导入")
      )
    ),
  );
}

// ─── Footer actions ───
// 【C2 行动条粘性】主操作行(加入收藏/添加联系方式/入主表)必须始终钉在抽屉视口底部:
// 抽屉本体是 flex-col(footer 在滚动区之外)天然不滚走,这里再加 sticky bottom-0 + 不透明背景
// + z 提层做双保险 —— 未来若有人把 footer 挪进滚动区,主操作行也不会被内容顶走。
// 次级行(AI深度分析/打开主页/更多)仍在本组件内原位跟随,不单独提层。
export function KOLDrawerFooter({ item, inMyList, onToggleMyList, onContact, onPromote, promoteMsg, canEnqueueVideoAnalysis, videoEnqueueLabel, videoEnqueueTitle, videoEnqueueState, onEnqueueVideoAnalysis }: any) {
  return e("div", { className: "sticky bottom-0 z-20 bg-[#0a1020] px-5 py-3 border-t border-white/[0.06]" },
    // 主操作 3 按钮
    e("div", { className: "flex items-center gap-2 mb-2" },
      e("button", {
        onClick: () => onToggleMyList?.(item.id),
        title: "我的收藏(My KOL 归宿)· 持久化保存",
        className: "flex-1 flex items-center justify-center gap-1.5 rounded-md px-3 py-2 text-[11px] font-medium transition-colors",
        style: inMyList
          ? { background: "rgba(251,191,36,0.15)", color: "#fde68a", border: "1px solid rgba(251,191,36,0.35)" }
          : { background: "rgba(255,255,255,0.04)", color: "#e2e8f0", border: "1px solid rgba(255,255,255,0.1)" }
      }, e(Star, { size: 11, style: inMyList ? { fill: "#fbbf24" } : {} }), inMyList ? "已收藏" : "加入收藏"),
      e("button", {
        onClick: () => onContact?.(item),
        title: "打开本地联系模板: 不发送邮件、不调用 provider",
        className: "flex-1 flex items-center justify-center gap-1.5 rounded-md bg-purple-600 hover:bg-purple-500 px-3 py-2 text-[11px] font-medium text-white"
      }, e(Send, { size: 11 }),
        item.email ? "发起合作邀请" : "添加联系方式"
      ),
      !item.linked_main_kol_id && e("button", {
        onClick: () => onPromote?.(item),
        className: "flex-1 flex items-center justify-center gap-1.5 rounded-md border border-emerald-500/20 bg-emerald-500/[0.05] px-3 py-2 text-[11px] text-emerald-300 hover:bg-emerald-500/[0.12]",
        title: promoteMsg && !promoteMsg.ok ? promoteMsg.text : "把该候选写入主表(promote)"
      }, e(Link2, { size: 11 }), promoteMsg?.ok ? "已入主表 ✓" : "入主表"),
    ),
    // 次操作 icons
    e("div", { className: "flex items-center justify-center gap-1.5" },
      e("button", {
        disabled: !canEnqueueVideoAnalysis,
        onClick: onEnqueueVideoAnalysis,
        className: "flex items-center gap-1 rounded-md border px-2 py-1 text-[10px] transition-colors " + (
          canEnqueueVideoAnalysis
            ? "border-cyan-400/25 bg-cyan-400/[0.07] text-cyan-200 hover:bg-cyan-400/[0.12]"
            : "cursor-not-allowed border-white/[0.06] text-slate-500 opacity-70"
        ),
        title: videoEnqueueTitle
      }, e(RefreshCw, { size: 10, className: videoEnqueueState.status === "loading" ? "animate-spin" : "" }), videoEnqueueLabel),
      item.profile_url && e("a", {
        href: item.profile_url, target: "_blank", rel: "noreferrer",
        className: "flex items-center gap-1 rounded-md border border-white/[0.06] px-2 py-1 text-[10px] text-slate-400 hover:bg-white/[0.04] hover:text-white"
      }, e(ExternalLink, { size: 10 }), "打开主页"),
      e("button", {
        disabled: true,
        className: "flex cursor-not-allowed items-center gap-1 rounded-md border border-white/[0.06] px-2 py-1 text-[10px] text-slate-500 opacity-70",
        title: "待接入: 更多操作需要明确菜单项和权限"
      }, e(MoreHorizontal, { size: 10 }), "更多 · 待接入"),
    ),
    videoEnqueueState.message && e("div", {
      className: "mt-1 text-center text-[10px] leading-snug " + (
        videoEnqueueState.status === "error" || videoEnqueueState.status === "budget_denied"
          ? "text-rose-300"
          : videoEnqueueState.status === "queued" || videoEnqueueState.status === "already_queued"
            ? "text-cyan-200"
            : "text-slate-400"
      )
    }, videoEnqueueState.message)
  );
}
