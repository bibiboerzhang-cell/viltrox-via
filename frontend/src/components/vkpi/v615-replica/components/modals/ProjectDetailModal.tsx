// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useState } from "react";
import { motion } from "framer-motion";
import { AlertTriangle, ArrowLeft, Package, Search, Send, Share2, Target, UserPlus, Users, X } from "lucide-react";
import { useT } from "../../lib/i18n";
import { CAMPAIGN_ICONS } from "../../data/campaignIcons";
import { PLATFORM_ICONS_MAP } from "../../data/platformIconsMap";
import { updateProject } from "../../../../../services/vkpi/projects-api";
import { ShareModal } from "../../../shared/ShareModal";

const e = React.createElement;

export function ProjectDetailModal({ project, onClose, onOpenFullPage, staff = [], apiToken, onAssigned, canManage = false }) {
  const { t } = useT();
  const [activeTab, setActiveTab] = useState("kols");  // kols / pending / assets / new-kol
  const [assignBusy, setAssignBusy] = useState(false);  // 指派给员工(管理层把项目派给某员工 → 该员工 own-only 即可见)
  const [shareOpen, setShareOpen] = useState(false);  // 分享 modal(成员管理走真后端)
  if (!project) return null;
  const IconComp = CAMPAIGN_ICONS[project.iconKey] || CAMPAIGN_ICONS.default;
  const maxFunnel = Math.max(...project.funnel.map(f => f.count), 1);
  
  return e(React.Fragment, null,
   shareOpen && e(ShareModal, {
    kind: "project",
    targetId: String(project.projectId || project.id || ""),
    targetName: project.name,
    staff,
    apiToken,
    onClose: () => setShareOpen(false),
   }),
   e(motion.div, {
    initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 },
    className: "v615-modal fixed inset-0 flex items-center justify-center bg-black/75 backdrop-blur-md p-4 overflow-y-auto",
    style: { zIndex: 9999 },
    onClick: onClose,
  },
    e(motion.div, {
      initial: { scale: 0.95, opacity: 0, y: 20 }, animate: { scale: 1, opacity: 1, y: 0 }, exit: { scale: 0.95, opacity: 0 },
      onClick: (ev) => ev.stopPropagation(),
      className: "relative w-full max-w-3xl max-h-[92vh] flex flex-col rounded-2xl border border-white/10 bg-[#0a1020] shadow-2xl overflow-hidden",
    },
      // Header
      e("div", { className: "shrink-0 border-b border-white/[0.06] px-5 py-3.5 flex items-center gap-3" },
        e("div", {
          className: "w-9 h-9 rounded-md flex items-center justify-center border border-white/[0.08]",
          style: { background: `linear-gradient(135deg, ${project.iconColor}22, ${project.iconColor}11)` }
        }, e(IconComp, { size: 18, style: { color: project.iconColor } })),
        e("div", { className: "flex-1 min-w-0" },
          e("div", { className: "flex items-center gap-2 mb-0.5" },
            e("h2", { className: "text-sm font-semibold text-white truncate" }, project.name),
            e("span", {
              className: "shrink-0 text-[9px] font-bold px-1.5 py-0.5 rounded",
              style: { background: `${project.healthColor}28`, color: project.healthColor }
            }, `健康 ${project.healthScore}`),
            e("span", { className: "shrink-0 text-[9px] text-slate-500" }, t(project.statusLabel))
          ),
          e("div", { className: "text-[10px] text-slate-500" }, 
            "最近更新 " + project.lastUpdate + " · 负责人 " + project.owner + " · 开始 " + project.startDate)
        ),
        e("div", { className: "flex items-center gap-1.5" },
          apiToken && staff.length > 0 && e("select", {
            value: "", disabled: assignBusy, title: "指派给员工",
            className: "rounded-md border border-white/[0.08] bg-white/[0.04] px-2 py-1 text-[10px] text-slate-300 hover:bg-white/[0.08] outline-none max-w-[120px]",
            onChange: async (ev) => {
              const sid = ev.target.value; if (!sid) return;
              setAssignBusy(true);
              try { await updateProject(apiToken, String(project.projectId || project.id || ""), { assignedStaffId: sid }); onAssigned && onAssigned(); }
              finally { setAssignBusy(false); }
            },
          }, [e("option", { key: "_", value: "" }, assignBusy ? "指派中…" : "指派给…"), ...staff.map(s => e("option", { key: s.id, value: String(s.id), style: { background: "#0a1020" } }, s.name))]),
          canManage && apiToken && e("button", {
            onClick: () => setShareOpen(true),
            className: "rounded-md border border-purple-500/30 bg-purple-500/[0.08] px-2 py-1 text-[10px] text-purple-200 hover:bg-purple-500/[0.15] flex items-center gap-1",
            title: "分享给团队成员",
          }, e(Share2, { size: 10 }), "分享"),
          e("button", { className: "rounded-md border border-white/[0.08] bg-white/[0.04] px-2 py-1 text-[10px] text-slate-300 hover:bg-white/[0.08]" }, t("编辑")),
          e("button", { onClick: () => onOpenFullPage && onOpenFullPage(project), className: "rounded-md border border-purple-500/30 bg-purple-500/[0.08] px-2 py-1 text-[10px] text-purple-200 hover:bg-purple-500/[0.15] flex items-center gap-1" },
            "打开完整页 ", e(ArrowLeft, { size: 9, className: "rotate-180" })),
          e("button", { onClick: onClose, className: "rounded-md border border-white/10 bg-white/5 p-1.5 text-slate-400 hover:text-white hover:bg-white/10" }, e(X, { size: 14 }))
        )
      ),
      // Body
      e("div", { className: "flex-1 overflow-y-auto p-5 space-y-4" },
        // 4 stats(去预算 / 销售 / ROI)
        e("div", { className: "grid grid-cols-4 gap-2" },
          [
            { label: "参与 KOL", value: project.stats.kolCount, sub: "当前项目行" },
            { label: "已发布",   value: project.stats.published, sub: `发布率 ${project.stats.publishRate}%` },
            { label: "总曝光",   value: project.stats.totalReach, sub: "自动汇总" },
            { label: "短链点击", value: typeof project.stats.shortClicks === "number" ? project.stats.shortClicks.toLocaleString() : project.stats.shortClicks, sub: `日均 ${project.stats.dailyClicks}` },
          ].map((s, i) => e("div", { key: i, className: "rounded-md border border-white/[0.06] bg-white/[0.025] px-3 py-2" },
            e("div", { className: "text-[9px] text-slate-500 mb-0.5" }, s.label),
            e("div", { className: "text-lg font-semibold text-white tabular-nums" }, s.value),
            e("div", { className: "text-[9px] text-slate-500" }, s.sub)
          ))
        ),
        // 9 步 KOL 漏斗
        e("div", { className: "rounded-md border border-white/[0.06] bg-white/[0.02] p-3" },
          e("div", { className: "flex items-center justify-between mb-2.5" },
            e("div", { className: "text-[11px] font-medium text-white flex items-center gap-1.5" },
              e(Target, { size: 12, className: "text-purple-300" }),
              "KOL 进度漏斗"
            ),
            e("span", { className: "text-[9px] text-slate-500" }, "每日刷新")
          ),
          e("div", { className: "grid grid-cols-9 gap-1" },
            project.funnel.map((f, i) => {
              const isBottleneck = f.bottleneck;
              const isMilestone = f.milestone;
              const isEmpty = f.count === 0;
              return e("div", {
                key: i,
                className: "rounded text-center py-1.5 px-1",
                style: {
                  background: isBottleneck ? "rgba(245,158,11,0.12)" 
                    : isMilestone ? "rgba(16,185,129,0.12)" 
                    : "rgba(255,255,255,0.03)",
                  border: isBottleneck ? "0.5px solid rgba(245,158,11,0.3)" 
                    : isMilestone ? "0.5px solid rgba(16,185,129,0.3)" 
                    : "0.5px solid rgba(255,255,255,0.04)",
                }
              },
                e("div", { 
                  className: "text-[8px]", 
                  style: { color: isBottleneck ? "#fbbf24" : isMilestone ? "#6ee7b7" : isEmpty ? "rgba(255,255,255,0.4)" : "rgba(255,255,255,0.55)" }
                }, (i + 1) + "." + t(f.name)),
                e("div", {
                  className: "text-sm font-bold",
                  style: { color: isBottleneck ? "#fbbf24" : isMilestone ? "#6ee7b7" : isEmpty ? "rgba(255,255,255,0.45)" : "#fff" }
                }, f.count)
              );
            })
          ),
          e("div", { className: "mt-2 text-[9px] flex items-center gap-1.5", style: { color: project.funnel.some(f => f.bottleneck) ? "#fbbf24" : "rgba(255,255,255,0.5)" } },
            project.funnel.some(f => f.bottleneck) && e(AlertTriangle, { size: 10 }),
            e("span", null, "当前瓶颈:" + project.bottleneckText)
          )
        ),
        // Tabs
        e("div", { className: "border-b border-white/[0.06] flex gap-4" },
          [
            { key: "kols",    label: "参与 KOL",     icon: Users,    count: project.kolList.length },
            { key: "pending", label: "待发布内容",   icon: Send,     count: project.pendingPublishes.length },
            { key: "assets",  label: "物料",         icon: Package,  count: project.assets.length },
            { key: "new-kol", label: "新增 KOL",     icon: UserPlus, count: project.newKolSuggestions.length },
          ].map(tab => e("button", {
            key: tab.key,
            onClick: () => setActiveTab(tab.key),
            className: "flex items-center gap-1.5 text-[11px] py-2 px-0.5 border-b-2 transition-colors",
            style: {
              borderColor: activeTab === tab.key ? "#a855f7" : "transparent",
              color: activeTab === tab.key ? "#fff" : "rgba(255,255,255,0.5)",
              fontWeight: activeTab === tab.key ? 500 : 400,
            }
          }, e(tab.icon, { size: 11 }), tab.label, 
            tab.count > 0 && e("span", { className: "text-[9px] rounded-full bg-white/[0.08] px-1.5 py-0.5" }, tab.count)
          ))
        ),
        // Tab content
        // 参与 KOL
        activeTab === "kols" && (
          project.kolList.length === 0 
            ? e("div", { className: "text-center py-8 text-[11px] text-slate-500" }, t("暂无 KOL"))
            : e("div", { className: "rounded-md border border-white/[0.06] overflow-hidden" },
                // header
                e("div", { className: "grid gap-2 px-3 py-1.5 text-[9px] text-slate-500 bg-white/[0.02] border-b border-white/[0.04]", style: { gridTemplateColumns: "2fr 1fr 1fr 0.6fr 0.8fr 0.7fr" } },
                  e("span", null, "KOL"), e("span", null, "平台"), e("span", null, "当前阶段"), e("span", null, t("已发")), e("span", null, "曝光"), e("span", { className: "text-right" }, t("操作"))
                ),
                // rows
                project.kolList.map((k, i) => e("div", {
                  key: i,
                  className: "grid gap-2 px-3 py-2 items-center border-b border-white/[0.03] last:border-b-0 hover:bg-white/[0.02]",
                  style: { gridTemplateColumns: "2fr 1fr 1fr 0.6fr 0.8fr 0.7fr" }
                },
                  e("div", { className: "flex items-center gap-2" },
                    e("div", {
                      className: "shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold text-white",
                      style: { background: k.color }
                    }, k.avatar),
                    e("span", { className: "text-[11px] text-white truncate" }, k.handle)
                  ),
                  e("span", { className: "text-[10px] text-slate-400" }, k.platform),
                  e("span", { className: "text-[10px] font-medium", style: { color: k.stageColor } }, t(k.stage)),
                  e("span", { className: "text-[10px] tabular-nums", style: { color: k.published > 0 ? "#fff" : "rgba(255,255,255,0.4)" } }, k.published),
                  e("span", { className: "text-[10px] tabular-nums", style: { color: k.reach !== "—" ? "#fff" : "rgba(255,255,255,0.4)" } }, k.reach),
                  e("button", { className: "text-[10px] text-purple-300 hover:text-purple-200 text-right" }, t(k.action) + " →")
                ))
              )
        ),
        // 待发布内容
        activeTab === "pending" && (
          project.pendingPublishes.length === 0
            ? e("div", { className: "text-center py-8 text-[11px] text-slate-500" }, "暂无待发布")
            : e("div", { className: "space-y-2" },
                project.pendingPublishes.map((p, i) => {
                  const platCfg = PLATFORM_ICONS_MAP[p.platform] || PLATFORM_ICONS_MAP.default;
                  return e("div", { key: i, className: "rounded-md border border-white/[0.06] bg-white/[0.02] p-3 flex gap-3" },
                    e("div", {
                      className: "shrink-0 w-9 h-9 rounded-full flex items-center justify-center text-[12px] font-bold text-white",
                      style: { background: p.color }
                    }, p.avatar),
                    e("div", { className: "flex-1 min-w-0" },
                      e("div", { className: "flex items-center gap-2 mb-1" },
                        e("span", { className: "text-[11px] font-medium text-white" }, p.kol),
                        e("span", { className: "flex items-center gap-1 text-[9px] text-slate-500" },
                          e(platCfg.Icon, { size: 10, style: { color: platCfg.color } }),
                          p.platform
                        ),
                        e("span", { className: "text-[9px] text-slate-500" }, "· " + p.date),
                        e("span", { className: "ml-auto text-[9px] font-medium px-1.5 py-0.5 rounded", style: { background: `${p.statusColor}22`, color: p.statusColor } }, t(p.status))
                      ),
                      e("div", { className: "text-[10px] text-slate-300 truncate" }, p.title)
                    )
                  );
                })
              )
        ),
        // 物料
        activeTab === "assets" && (
          project.assets.length === 0
            ? e("div", { className: "text-center py-8 text-[11px] text-slate-500" }, "暂无物料")
            : e("div", { className: "space-y-1.5" },
                project.assets.map((a, i) => {
                  const stColors = { ready: "#10b981", draft: "#fbbf24", review: "#60a5fa", todo: "#64748b" };
                  const stLabels = { ready: "已就绪", draft: "草稿中", review: "审核中", todo: "待办" };
                  return e("div", { key: i, className: "flex items-center gap-2 rounded-md border border-white/[0.06] bg-white/[0.02] px-3 py-2" },
                    e("div", {
                      className: "shrink-0 w-2 h-2 rounded-full",
                      style: { background: stColors[a.status] }
                    }),
                    e("span", { className: "flex-1 text-[11px] text-white" }, a.name),
                    e("span", { className: "text-[9px]", style: { color: stColors[a.status] } }, t(stLabels[a.status])),
                    e("span", { className: "text-[9px] text-slate-500 w-12 text-right" }, a.date)
                  );
                })
              )
        ),
        // 新增 KOL 搜索
        activeTab === "new-kol" && e("div", { className: "space-y-2" },
          e("div", { className: "flex gap-2" },
            e("div", { className: "flex-1 flex items-center gap-2 rounded-md border border-white/[0.08] bg-white/[0.025] px-3 py-1.5" },
              e(Search, { size: 12, className: "text-slate-500" }),
              e("input", {
                type: "text",
                placeholder: "搜索 KOL handle / 平台 / 风格",
                className: "flex-1 bg-transparent text-[11px] text-white placeholder:text-slate-500 outline-none"
              })
            ),
            e("button", { className: "rounded-md border border-white/[0.08] px-3 py-1.5 text-[10px] text-white/25", disabled: true, title: "AI 推荐待接入" }, t("AI 推荐"))
          ),
          e("div", { className: "text-[10px] text-slate-500 mt-1 mb-1" }, t("Claude 基于品类匹配度推荐")),
          project.newKolSuggestions.length === 0
            ? e("div", { className: "text-center py-8 text-[11px] text-slate-500" }, "暂无推荐")
            : project.newKolSuggestions.map((k, i) => e("div", {
                key: i, className: "rounded-md border border-white/[0.06] bg-white/[0.02] p-3 flex gap-3 hover:bg-white/[0.03]"
              },
                e("div", {
                  className: "shrink-0 w-9 h-9 rounded-full flex items-center justify-center text-[12px] font-bold text-white",
                  style: { background: k.color }
                }, k.avatar),
                e("div", { className: "flex-1 min-w-0" },
                  e("div", { className: "flex items-center gap-2 mb-0.5" },
                    e("span", { className: "text-[11px] font-medium text-white" }, k.handle),
                    e("span", { className: "text-[9px] text-slate-500" }, "· " + k.platform + " · " + k.followers),
                    e("span", { className: "text-[9px] text-emerald-300" }, "· ER " + k.er),
                    e("span", { className: "ml-auto text-[9px] font-medium px-1.5 py-0.5 rounded", style: { background: k.fit === "高" ? "rgba(16,185,129,0.18)" : "rgba(245,158,11,0.18)", color: k.fit === "高" ? "#6ee7b7" : "#fbbf24" } }, "适合度 " + k.fit)
                  ),
                  e("div", { className: "text-[10px] text-slate-400 mb-1" }, k.reason),
                  e("button", { className: "text-[10px] text-white/25", disabled: true, title: "邀请待接入" }, t("邀请合作 →"))
                )
              ))
        )
      ),
      // Footer
      e("div", { className: "shrink-0 border-t border-white/[0.06] px-5 py-2.5 flex items-center justify-between" },
        e("div", { className: "text-[10px] text-slate-500" }, t("数据来自 V-KPI 后端 · 每日自动同步")),
        e("div", { className: "flex gap-1.5" },
          e("button", {
            onClick: onClose,
            className: "rounded-md border border-white/10 bg-white/[0.03] px-3 py-1 text-[11px] text-slate-300 hover:bg-white/[0.06]"
          }, t("关闭"))
        )
      )
    )
   )
  );
}
