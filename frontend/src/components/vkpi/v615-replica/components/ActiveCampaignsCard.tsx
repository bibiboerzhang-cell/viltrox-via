// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { motion } from "framer-motion";
import { Plus, Zap } from "lucide-react";
import { Avatar } from "./Avatar";
import { useT } from "../lib/i18n";
import { CAMPAIGN_ICONS } from "../data/campaignIcons";

const e = React.createElement;

export function ActiveCampaignsCard({ campaigns, campaignsMeta, onCampaignClick, onViewAll }) {
  const { t } = useT();
  const realCount = campaignsMeta?.isReal ? Number(campaignsMeta.activeCount || 0) : null;
  const activeCount = realCount ?? campaigns.filter(c => c.status !== "done").length;
  const windowDays = Number(campaignsMeta?.windowDays || 30);
  const isStarredSource = campaignsMeta?.source === "starred_projects";
  return e(motion.div, {
    initial: { opacity: 0, y: 8 },
    animate: { opacity: 1, y: 0 },
    transition: { delay: 0.12 },
    className: "rounded-xl border border-white/[0.08] bg-white/[0.025] p-4 backdrop-blur-xl",
  },
    // Header
    e("div", { className: "mb-3 flex items-center justify-between" },
      e("div", { className: "flex items-center gap-2" },
        e(Zap, { size: 14, className: "text-purple-300" }),
        e("h3", { className: "text-sm font-semibold text-white" }, "Active Campaigns")
      ),
      e("div", { className: "flex items-center gap-2" },
        e("span", { className: "text-[10px] text-slate-400" }, isStarredSource ? `${activeCount} starred` : `${activeCount} active`)
      )
    ),
    // Campaign list
    e("div", { className: "space-y-2" },
      campaigns.length === 0
        ? e("div", { className: "rounded-md border border-dashed border-white/[0.08] px-3 py-6 text-center" },
            e("div", { className: "text-[11px] font-medium text-slate-300" }, isStarredSource ? "还没标记重点项目" : campaignsMeta?.isReal ? "当前口径为 0 个 active" : "暂无真实项目数据"),
            e("div", { className: "mx-auto mt-1 max-w-[220px] text-[10px] leading-relaxed text-slate-500" },
              isStarredSource
                ? "在 Projects 卡片点亮「重点」后，这里只显示你自己的重点项目。"
                : campaignsMeta?.isReal
                ? `口径: 未关闭项目 + 寄样/到货/出片阶段, 或近 ${windowDays} 天新增视频证据。`
                : "等待项目工作流接入。"
            )
          )
        : campaigns.map((c) => {
        const IconComp = CAMPAIGN_ICONS[c.iconKey] || CAMPAIGN_ICONS.default;
        return e("div", {
          key: c.id,
          onClick: () => onCampaignClick && onCampaignClick(c),
          className: "flex gap-2.5 cursor-pointer rounded-md p-2 hover:bg-white/[0.04] transition-colors",
          style: { opacity: c.status === "done" ? 0.55 : 1 }
        },
          // Product icon
          e("div", {
            className: "shrink-0 w-9 h-9 rounded-md flex items-center justify-center border border-white/[0.08]",
            style: { background: `linear-gradient(135deg, ${c.iconColor}22, ${c.iconColor}11)` }
          }, e(IconComp, { size: 18, style: { color: c.iconColor } })),
          // Content
          e("div", { className: "flex-1 min-w-0" },
            // Name + health
            e("div", { className: "flex items-center justify-between gap-2 mb-1" },
              e("span", { className: "text-[12px] font-medium text-white truncate" }, c.name),
              e("span", {
                className: "shrink-0 text-[9px] font-bold px-1.5 py-0.5 rounded",
                style: { background: `${c.healthColor}28`, color: c.healthColor }
              }, c.healthScore === "待评估" ? "待评估" : `健康 ${c.healthScore}`)
            ),
            // 4 mini stats
            e("div", { className: "grid grid-cols-4 gap-1 mb-1" },
              (c.source === "active_campaigns" ? ["KOL", `近${windowDays}天`, "曝光", "实操"] : ["KOL", "已发", "曝光", "点击"]).map((label, idx) => {
                const val = c.source === "active_campaigns"
                  ? [c.stats.kolCount, c.recentVideoCount, c.stats.totalReach, c.executionKolCount][idx]
                  : [c.stats.kolCount, c.stats.published, c.stats.totalReach, c.stats.shortClicks][idx];
                const display = typeof val === "number" ? val.toLocaleString() : val;
                return e("div", { key: label },
                  e("div", { className: "text-[8px] text-slate-500 leading-tight" }, label),
                  e("div", { 
                    className: "text-[10px] font-medium leading-tight",
                    style: { color: (val === 0 || val === "—") ? "rgba(255,255,255,0.45)" : "#fff" }
                  }, display)
                );
              })
            ),
            // KOL avatars + bottleneck
            e("div", { className: "flex items-center justify-between" },
              // Avatar stack
              e("div", { className: "flex items-center" },
                c.kolList.slice(0, 3).map((k, i) => e("div", {
                  key: i,
                  className: "w-[18px] h-[18px] rounded-full flex items-center justify-center text-[8px] font-bold text-white",
                  style: {
                    background: k.color,
                    border: "1.5px solid #0a1020",
                    marginLeft: i === 0 ? 0 : -6,
                    zIndex: 10 - i,
                  }
                }, k.avatar)),
                c.kolList.length > 3 && e("span", {
                  className: "ml-1 text-[9px] text-slate-500"
                }, "+" + (c.kolList.length - 3))
              ),
              // Bottleneck note
              e("span", { 
                className: "text-[9px] truncate ml-2",
                style: { color: c.funnel.some(f => f.bottleneck) ? "#fbbf24" : "rgba(255,255,255,0.45)" }
              }, c.status === "done" ? "已完成" : c.bottleneckText.split("(")[0])
            )
          )
        );
      })
    ),
    // Footer
    e("div", { className: "mt-3 border-t border-white/[0.06] pt-2 text-center" },
      e("button", { onClick: onViewAll, className: "text-[10px] text-slate-300 hover:text-white" }, t("查看全部 →"))
    )
  );
}
