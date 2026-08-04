// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { BadgeCheck, MoreHorizontal, Star } from "lucide-react";
import { AudienceTypeChip } from "./AudienceTypeChip";
import { CandidateKindChip } from "./CandidateKindChip";
import { DeviceSummary } from "./DeviceSummary";
import { GeoTierChip } from "./GeoTierChip";
import { KPAvatar } from "./KPAvatar";
import { PlatformPill } from "./PlatformPill";
import { RefreshStateStripe } from "./RefreshStateStripe";
import { TrendDot } from "./TrendDot";
import { V6FitBar } from "./V6FitBar";
import { formatNumber, formatPercent } from "../lib/format";
import { getCountryInfo } from "../data/countryInfo";

const e = React.createElement;
const ROW_HEIGHT = 60;
const VISIBLE_ROWS = 18;
const OVERSCAN_ROWS = 8;

// 行组件 memo:虚拟滚动每次 setScrollTop 触发整表重渲,但视口内留存的行 payload 未变
// (item 引用稳定、onRowClick/myList 引用稳定、isSelected 仅选中行改动)→ 跳过重渲,
// 只有进出视口 / 选中态变化的行才重画。稳定 key 由调用方在 map 处按 id 组合传入。
const KOLRow = React.memo(function KOLRow({ item, isSelected, onRowClick, myList }: any) {
  return e("tr", {
    className: isSelected ? "is-selected" : "",
    onClick: () => onRowClick(item)
  },
    // KOL · Platform (with refresh-state stripe + candidate-kind chip)
    e("td", { style: { position: "relative" } },
      e(RefreshStateStripe, { state: item.refresh_state }),
      e("div", { className: "flex items-center gap-2.5 min-w-0", style: { paddingLeft: 4 } },
        e(KPAvatar, {
        name: item.display_name || item.handle,
        color: item.avatar_color,
        size: 28,
        title: "查看 " + (item.handle || item.display_name || "KOL") + " 详情",
        onAvatarClick: () => onRowClick(item)
      }),
        e("div", { className: "min-w-0" },
          e("div", { className: "flex items-center gap-1.5" },
            e("span", { className: "text-[12px] text-white font-medium truncate max-w-[160px]" }, item.handle),
            myList?.has(item.id) && e(Star, { size: 10, className: "text-amber-400 shrink-0", style: { fill: "#fbbf24" }, title: "在我的列表" } as any),
            item.linked_main_kol_id && e(BadgeCheck, { size: 11, className: "text-emerald-400 shrink-0", title: "已链接主表" } as any),
            item.devices?.has_viltrox && e("span", {
              className: "shrink-0 px-1 rounded text-[9px] font-medium",
              style: { background: "rgba(168,85,247,0.18)", color: "#c4b5fd" },
              title: "已用 Viltrox 镜头"
            }, "V")
          ),
          e("div", { className: "flex items-center gap-1.5 mt-0.5 flex-wrap" },
            e(PlatformPill, { platform: item.platform }),
            e(CandidateKindChip, { kind: item.candidate_kind, size: "xs" }),
            e("span", { className: "text-[10px] text-slate-500 truncate max-w-[120px]" }, item.industry_label)
          )
        )
      )
    ),
    // Followers + Real%
    e("td", null,
      e("div", { className: "text-[12px] text-white tabular-nums font-medium" }, formatNumber(item.followers)),
      e("div", { className: "text-[10px] tabular-nums",
        style: { color: item.real_followers_pct >= 80 ? "#86efac" : item.real_followers_pct >= 50 ? "#fde68a" : "#fca5a5" }
      }, item.real_followers_pct ? item.real_followers_pct + "% 真实" : "—")
    ),
    // 互动率：Real ER 只展示后端样本/验证真值；普通互动率保留原名并要求来源时间完整。
    e("td", null,
      item.real_er_pct != null
        ? e(React.Fragment, null,
            e("div", { className: "text-[12px] text-white tabular-nums font-medium" }, formatPercent(item.real_er_pct, 2)),
            e("div", { className: "text-[10px] text-slate-500 tabular-nums" }, item.real_er_sample_n ? `Real ER · 样本 ${item.real_er_sample_n}` : "Real ER · 已验证")
          )
        : item.engagement_rate_displayable && item.engagement_rate != null
          ? e("div", { title: `互动率来源 ${item.engagement_rate_source || "已记录来源"}${item.engagement_rate_updated_at ? ` · 更新 ${item.engagement_rate_updated_at}` : ""}` },
              e("div", { className: "text-[12px] text-white tabular-nums font-medium" }, formatPercent(item.engagement_rate, 2)),
              e("div", { className: "text-[10px] text-slate-500 tabular-nums" }, "互动率")
            )
          : null
    ),
    // Type · Geo
    e("td", null,
      e("div", { className: "flex items-center gap-1" },
        e(AudienceTypeChip, { type: item.audience_type }),
        e(GeoTierChip, { tier: item.geo_tier })
      ),
      item.geo_distribution && e("div", { className: "text-[10px] text-slate-500 mt-0.5 truncate" },
        item.geo_distribution.slice(0, 3).map((g: any) => {
          const info = getCountryInfo(g.country);
          return info ? info.flag + " " + Math.round(g.share * 100) + "%" : "待评估";
        }).join(" ")
      )
    ),
    // Trend
    e("td", null,
      e(TrendDot, { resonance: item.trend_resonance }),
      item.trend_hits?.length > 0 && e("div", { className: "text-[10px] text-slate-500 mt-0.5 truncate" },
        item.trend_hits[0]
      )
    ),
    // Device
    e("td", null,
      e(DeviceSummary, { devices: item.devices }),
      item.devices?.upgrade_window && e("div", { className: "text-[10px] mt-0.5",
        style: {
          color: item.devices.upgrade_window === "high" || item.devices.upgrade_window === "very high" ? "#86efac"
               : item.devices.upgrade_window === "medium" ? "#fde68a" : "#94a3b8"
        }
      }, "升级窗口:" + item.devices.upgrade_window)
    ),
    // V6 Fit
    e("td", null,
      e(V6FitBar, { score: item.v6_fit, kind: item.candidate_kind }),
      item.weekly_views_delta != null && e("div", { className: "text-[10px] tabular-nums mt-0.5",
        style: { color: item.weekly_views_delta > 0 ? "#86efac" : item.weekly_views_delta < 0 ? "#fca5a5" : "#94a3b8" }
      }, "本周 " + (item.weekly_views_delta > 0 ? "+" : "") + item.weekly_views_delta.toFixed(1) + "%")
    ),
    // Action
    e("td", { onClick: (ev: any) => ev.stopPropagation() },
      e("button", {
        onClick: (ev: any) => {
          ev.stopPropagation();
          onRowClick(item);
        },
        title: "查看详情",
        "aria-label": "查看 " + (item.handle || item.display_name || "KOL") + " 详情",
        className: "p-1 rounded hover:bg-white/[0.06] text-slate-400 hover:text-white"
      },
        e(MoreHorizontal, { size: 13 })
      )
    )
  );
});

export function KOLTable({ items, onRowClick, selectedItemId, myList }: any) {
  const [scrollTop, setScrollTop] = React.useState(0);
  React.useEffect(() => {
    setScrollTop(0);
  }, [items]);
  const totalRows = items.length;
  const startIndex = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN_ROWS);
  const endIndex = Math.min(totalRows, startIndex + VISIBLE_ROWS + OVERSCAN_ROWS * 2);
  const visibleItems = items.slice(startIndex, endIndex);
  const topSpacer = startIndex * ROW_HEIGHT;
  const bottomSpacer = Math.max(0, (totalRows - endIndex) * ROW_HEIGHT);

  return e("div", { className: "rounded-xl border border-white/[0.08] bg-white/[0.025] backdrop-blur-xl overflow-hidden" },
    e("div", {
      className: "overflow-auto max-h-[calc(100vh-460px)]",
      onScroll: (event: any) => setScrollTop(event.currentTarget.scrollTop || 0),
    },
      e("table", { className: "kp-table" },
        e("thead", null,
          e("tr", null,
            e("th", null, "KOL · 平台"),
            e("th", { style: { width: 100 } }, "粉丝 / 真实%"),
            e("th", { style: { width: 90 } }, "互动率"),
            e("th", { style: { width: 110 } }, "类型 · Geo"),
            e("th", { style: { width: 90 } }, "Trend"),
            e("th", { style: { width: 140 } }, "设备"),
            e("th", { style: { width: 110 } }, "V6 Fit"),
            e("th", { style: { width: 40 } }, ""),
          )
        ),
        e("tbody", null,
          topSpacer > 0 && e("tr", { "aria-hidden": true },
            e("td", { colSpan: 8, style: { height: topSpacer, padding: 0, borderBottom: 0 } })
          ),
          visibleItems.map((item: any, offset: any) => {
            const index = startIndex + offset;
            return e(KOLRow, {
              key: [item.id, item.platform, item.handle, index].filter(Boolean).join(":"),
              item,
              isSelected: selectedItemId === item.id,
              onRowClick,
              myList,
            });
          }),
          bottomSpacer > 0 && e("tr", { "aria-hidden": true },
            e("td", { colSpan: 8, style: { height: bottomSpacer, padding: 0, borderBottom: 0 } })
          )
        )
      )
    )
  );
}
