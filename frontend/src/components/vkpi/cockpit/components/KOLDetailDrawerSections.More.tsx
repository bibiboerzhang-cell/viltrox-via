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

// Audience Stats · 估算 BETA(ensemble_v1 / 受众情报 v2):性别环(归一外推)+ Top countries + 语言 +
// 年龄 4 桶 + 评论情报(购买意向/品牌提及/活跃时段/铁粉/互动质量)+ 共同粉丝 + 创作者浓度。
// 数据源 item.audience_estimated(detail_bundle 透传的 audience_estimated_json)。刷新/展开的 state 与
// handler 全部由父层 KOLDetailDrawer 持有并经 props 注入(本文件保持零内部 state)。旧「受众语言估算·
// 评论法」在无 ensemble 数据时作 fallback 展示。附 创作者所在地(诚实,非粉丝地理)。红线:纯展示,零触 fit。
const GENDER_COLORS = { male: "#3b82f6", female: "#ec4899", unknown: "#475569" };
const AGE_BUCKET_ORDER = ["0-18", "19-29", "30-39", "40+"];

export function KOLDrawerGeoDistribution({ item, geoDistribution, apiToken, audienceState = {}, onRefreshAudience, audienceExpand = null, onToggleAudienceBlock }: any) {
  const aud = (item.audience_languages || {}) as any;
  const langs = Array.isArray(aud.languages) ? aud.languages : [];
  const hasLang = langs.length > 0 && Number(aud.sample_size || 0) > 0;
  const hasGeo = geoDistribution.length > 0;
  const est = (item.audience_estimated && typeof item.audience_estimated === "object") ? item.audience_estimated as any : null;
  const hasEst = Boolean(est && Number(est.sample_size || 0) > 0);
  const canRefresh = Boolean(apiToken && item?.id && typeof onRefreshAudience === "function");
  const canExpand = typeof onToggleAudienceBlock === "function";
  if (!hasEst && !hasLang && !hasGeo && !canRefresh) return null;

  // ── 性别归一(v2 发布口径):male/(male+female) 外推 100;v1 旧 JSON 无 gender_normalized 时客户端补算 ──
  const sampleN = hasEst ? Number(est.sample_size || 0) : 0;
  const gn = (hasEst && est.gender_normalized && typeof est.gender_normalized === "object") ? est.gender_normalized as any : null;
  const rawMale = hasEst ? Math.max(0, Number(est.gender?.male_pct) || 0) : 0;
  const rawFemale = hasEst ? Math.max(0, Number(est.gender?.female_pct) || 0) : 0;
  let ringMale = 0;
  let genderDeterminedN = 0;
  let genderDeterminedPct = 0;
  if (gn && Number(gn.determined_n || 0) > 0) {
    ringMale = Math.min(100, Math.max(0, Number(gn.male_pct) || 0));
    genderDeterminedN = Number(gn.determined_n || 0);
    genderDeterminedPct = Number(gn.determined_pct || 0);
  } else if (rawMale + rawFemale > 0) {
    ringMale = Math.round(1000 * rawMale / (rawMale + rawFemale)) / 10;
    genderDeterminedPct = Math.round((rawMale + rawFemale) * 10) / 10;
    genderDeterminedN = Math.round(sampleN * (rawMale + rawFemale) / 100);
  }
  const ringFemale = genderDeterminedN > 0 ? Math.round((100 - ringMale) * 10) / 10 : 0;
  const hasGenderRing = genderDeterminedN > 0;

  const estCountries = hasEst && Array.isArray(est.top_countries) ? est.top_countries.slice(0, 6) : [];
  const estLangs = hasEst && Array.isArray(est.languages) ? est.languages.slice(0, 6) : [];
  // ── v2 各块数据(coverage/method 等实现细节留在 JSON 内部,不渲染)──
  const ageMeta = (hasEst && est.age_bins && typeof est.age_bins === "object") ? est.age_bins as any : null;
  const ageBins = ageMeta && Array.isArray(ageMeta.bins) ? ageMeta.bins : [];
  const agePctByBucket: Record<string, number> = {};
  for (const b of ageBins) agePctByBucket[String(b.bucket)] = Number(b.pct) || 0;
  const ageMaxBucket = ageBins.length ? ageBins.reduce((m: any, b: any) => (Number(b.pct) > Number(m.pct) ? b : m), ageBins[0]).bucket : "";
  const intel = (hasEst && est.comment_intel && typeof est.comment_intel === "object") ? est.comment_intel as any : null;
  const intelN = intel ? Number(intel.sample_size || 0) : 0;
  const pi = intelN > 0 && intel.purchase_intent && typeof intel.purchase_intent === "object" ? intel.purchase_intent : null;
  const piSamples = pi && Array.isArray(pi.samples) ? pi.samples : [];
  const brands = intelN > 0 && Array.isArray(intel.brand_mentions) ? intel.brand_mentions : [];
  const brandSamples = brands.flatMap((b: any) => (Array.isArray(b.samples) ? b.samples.map((s: any) => ({ ...s, brand: b.brand })) : []));
  const hours = intelN > 0 && intel.active_hours && Array.isArray(intel.active_hours?.hist) ? intel.active_hours : null;
  const maxHist = hours ? Math.max(1, ...hours.hist.map((v: any) => Number(v) || 0)) : 1;
  const engage = intelN > 0 && intel.engagement && typeof intel.engagement === "object" ? intel.engagement : null;
  const fans = intelN > 0 && Array.isArray(intel.superfans) ? intel.superfans.slice(0, 5) : [];
  const overlap = (hasEst && est.overlap && typeof est.overlap === "object") ? est.overlap as any : null;
  const overlapItems = overlap && Array.isArray(overlap.items) ? overlap.items : [];
  const density = (hasEst && est.creator_density && typeof est.creator_density === "object") ? est.creator_density as any : null;

  const refreshBusy = audienceState.status === "loading";
  const refreshLabel = refreshBusy ? "刷新中…"
    : audienceState.status === "pending" ? "抓取评论中…可稍后再刷新"
    : hasEst ? "刷新受众统计" : "生成受众统计";
  const generatedDate = hasEst && est.generated_at ? String(est.generated_at).slice(0, 10) : "";

  const expandLink = (key: string, label: string) =>
    canExpand && e("button", {
      type: "button",
      onClick: () => onToggleAudienceBlock(key),
      className: "mt-1 text-[9px] text-cyan-300/80 hover:text-cyan-200 underline underline-offset-2",
    }, audienceExpand === key ? "收起" : label);
  const evidenceRow = (s: any, i: number, prefix?: string) =>
    e("div", { key: i, className: "text-[9.5px] leading-snug" },
      prefix && e("span", { className: "text-slate-500" }, prefix + " "),
      e("span", { className: "text-slate-500" }, "@" + String(s.author_handle || s.author || "—") + " "),
      e("span", { className: "text-white" }, String(s.text || s || "")),
      s.created_at && e("span", { className: "text-slate-600" }, " · " + String(s.created_at).slice(0, 10))
    );

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
      // 性别环(归一发布口径:男/女两色补满 100,小字标外推基数;不可判定时诚实不画环)
      hasEst && hasGenderRing && e("div", { className: "flex items-center gap-3 mb-1" },
        e("div", {
          className: "relative shrink-0",
          style: {
            width: 56, height: 56, borderRadius: "50%",
            background: `conic-gradient(${GENDER_COLORS.male} 0% ${ringMale}%, ${GENDER_COLORS.female} ${ringMale}% 100%)`,
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
            e("span", { className: "text-white tabular-nums font-medium" }, ringMale + "%")
          ),
          e("div", { className: "flex items-center gap-1.5" },
            e("span", { className: "w-2 h-2 rounded-full", style: { background: GENDER_COLORS.female } }),
            e("span", { className: "text-slate-300" }, "女"),
            e("span", { className: "text-white tabular-nums font-medium" }, ringFemale + "%")
          )
        )
      ),
      hasEst && hasGenderRing && e("div", { className: "text-[9px] text-slate-500 mb-2.5" },
        `基于可判定样本 ${genderDeterminedN}(占 ${genderDeterminedPct}%)外推`
      ),
      hasEst && !hasGenderRing && e("div", { className: "text-[9.5px] text-slate-500 mb-2.5" }, "性别:样本内无可判定信号(诚实不外推)"),
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
      // 年龄 4 桶(BETA;只在已判定人群内归一)
      ageBins.length > 0 && e("div", { className: "mb-2.5" },
        e("div", { className: "text-[10px] text-slate-500 mb-1" }, "年龄段"),
        e("div", { className: "space-y-1.5" },
          AGE_BUCKET_ORDER.map((bucket: string, i: number) => bar(
            e("span", { className: "text-white font-medium w-[52px]" }, bucket),
            agePctByBucket[bucket] || 0,
            bucket === ageMaxBucket ? "#a855f7" : "#64748b",
            i,
          ))
        ),
        e("div", { className: "text-[9px] text-slate-500 mt-1" },
          `已判定 ${Number(ageMeta?.determined_n) || 0} 人(占 ${Number(ageMeta?.determined_pct) || 0}%)`
        )
      ),
      // ── 评论情报(词表/直方统计,带证据可下钻)──
      pi && e("div", { className: "mb-2.5 rounded-md border border-white/[0.05] bg-black/20 p-2" },
        e("div", { className: "flex items-center justify-between" },
          e("span", { className: "text-[10px] text-slate-400" }, "购买意向"),
          e("span", { className: "text-[11px] text-white font-medium tabular-nums" }, `${Number(pi.count) || 0} 条 · ${Number(pi.pct) || 0}%`)
        ),
        piSamples.length > 0 && e("div", { className: "mt-1 text-[10px] text-slate-300 leading-snug" },
          "“" + String(piSamples[0]?.text || piSamples[0] || "") + "”"
        ),
        piSamples.length > 0 && expandLink("intent", `查看评论 ${piSamples.length} 条`),
        audienceExpand === "intent" && e("div", { className: "mt-1.5 space-y-1 border-t border-white/[0.05] pt-1.5" },
          piSamples.map((s: any, i: number) => evidenceRow(s, i))
        )
      ),
      brands.length > 0 && e("div", { className: "mb-2.5" },
        e("div", { className: "text-[10px] text-slate-500 mb-1" }, "品牌提及"),
        e("div", { className: "flex flex-wrap gap-1" },
          brands.map((b: any, i: number) => e("span", {
            key: i,
            className: "px-1.5 py-0.5 rounded text-[9.5px] border",
            style: /viltrox/i.test(String(b.brand)) ? {
              background: "rgba(168,85,247,0.15)", borderColor: "rgba(168,85,247,0.3)", color: "#d8b4fe",
            } : {
              background: "rgba(248,113,113,0.08)", borderColor: "rgba(248,113,113,0.18)", color: "#cbd5e1",
            },
          }, `${b.brand} ×${Number(b.count) || 0}`))
        ),
        brandSamples.length > 0 && expandLink("brands", `查看评论 ${brandSamples.length} 条`),
        audienceExpand === "brands" && e("div", { className: "mt-1.5 space-y-1" },
          brandSamples.map((s: any, i: number) => evidenceRow(s, i, `[${s.brand}]`))
        )
      ),
      hours && Number(hours.timed_n || 0) > 0 && e("div", { className: "mb-2.5" },
        e("div", { className: "text-[10px] text-slate-500 mb-1" }, "评论活跃时段 · UTC"),
        e("div", { className: "flex items-end gap-px h-[18px]" },
          hours.hist.map((v: any, i: number) => e("div", {
            key: i,
            title: `UTC ${i}:00 · ${Number(v) || 0} 条`,
            className: "flex-1 rounded-sm",
            style: {
              height: Math.max(2, Math.round(18 * (Number(v) || 0) / maxHist)) + "px",
              background: (hours.top_hours || []).includes(i) ? "#06b6d4" : "rgba(100,116,139,0.4)",
            },
          }))
        ),
        hours.suggestion && e("div", { className: "text-[9px] text-slate-500 mt-1" },
          `建议发帖 ${hours.suggestion}(峰值 ${Number(hours.peak_hour_comment_count) || 0} 条 · 样本 ${Number(hours.timed_n) || 0})`
        )
      ),
      engage && e("div", { className: "mb-2.5 grid grid-cols-3 gap-1.5" },
        [
          { label: "评论/视频", value: engage.comments_per_video ?? "—" },
          { label: "回复率", value: (Number(engage.reply_pct) || 0) + "%" },
          { label: "赞中位", value: engage.likes_median ?? "—" },
        ].map((m, i) => e("div", { key: i, className: "rounded-md border border-white/[0.05] bg-black/20 px-1.5 py-1 text-center" },
          e("div", { className: "text-[8.5px] text-slate-500" }, m.label),
          e("div", { className: "text-[11px] text-white font-medium tabular-nums" }, String(m.value))
        ))
      ),
      fans.length > 0 && e("div", { className: "mb-2.5" },
        e("div", { className: "text-[10px] text-slate-500 mb-1" }, "铁粉 Top" + fans.length),
        e("div", { className: "space-y-1" },
          fans.map((f: any, i: number) => e("div", { key: i, className: "flex items-center gap-2 text-[10.5px]" },
            e("span", {
              className: "w-4 h-4 rounded-full flex items-center justify-center text-[8px] font-semibold shrink-0",
              style: { background: "rgba(6,182,212,0.15)", color: "#67e8f9" },
            }, String(f.handle || "?").charAt(0).toUpperCase()),
            e("span", { className: "text-white truncate max-w-[180px]" }, "@" + String(f.handle || "—")),
            e("span", { className: "text-slate-500 tabular-nums" }, "×" + (Number(f.count) || 0))
          ))
        ),
        fans.some((f: any) => f.sample) && expandLink("fans", "查看代表评论"),
        audienceExpand === "fans" && e("div", { className: "mt-1.5 space-y-1" },
          fans.filter((f: any) => f.sample).map((f: any, i: number) =>
            evidenceRow({ author_handle: f.handle, text: f.sample }, i)
          )
        )
      ),
      // 共同粉丝(矩阵投放去重参考:重叠高的 KOL 不必都投)
      overlap && e("div", { className: "mb-2.5" },
        e("div", { className: "text-[10px] text-slate-500 mb-1" }, "共同粉丝"),
        overlapItems.length > 0
          ? e("div", { className: "space-y-1" },
              overlapItems.map((o: any, i: number) => e("div", { key: i, className: "flex items-center gap-1.5 text-[10.5px]" },
                e("span", { className: "text-slate-300" }, "与"),
                e("span", { className: "text-white font-medium truncate max-w-[160px]" }, "@" + String(o.handle || o.display_name || ("#" + o.peer_id))),
                e("span", { className: "text-slate-300" }, `共享 ${Number(o.shared_count) || 0} 位评论者`),
                e("span", { className: "text-slate-600 text-[9px] tabular-nums ml-auto" }, "jaccard " + (Number(o.jaccard) || 0).toFixed(3))
              ))
            )
          : e("div", { className: "text-[9.5px] text-slate-500" }, "暂无重叠数据(库内同类 KOL 抓样越多越准)")
      ),
      // 受众创作者浓度(白捡指标)
      density && density.pct !== null && density.pct !== undefined && e("div", { className: "text-[9px] text-slate-500 mb-1" },
        `受众创作者浓度 ${density.pct}%(订阅数超 ${Number(density.min_subscribers) || 1000},已知 ${Number(density.known_n) || 0} 人)`
      ),
      // 底部小字:只留 样本/置信/日期(方法与覆盖细节留在 JSON 内部,不渲染)
      hasEst && e("div", { className: "text-[9px] text-slate-500 leading-relaxed" },
        `样本 ${est.sample_size} 评论者 · 置信 ${est.confidence ?? "—"}` + (generatedDate ? ` · ${generatedDate}` : "")
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
