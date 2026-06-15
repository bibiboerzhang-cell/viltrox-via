// @ts-nocheck
// Dashboard slice from vkpi_v6.15.7_integrated.html

import React from "react";
import { motion } from "framer-motion";
import { AlertTriangle, DollarSign, Globe2, Loader2, TrendingUp } from "lucide-react";
import { AIIntelligenceCard } from "./components/AIIntelligenceCard";
import { ActiveCampaignsCard } from "./components/ActiveCampaignsCard";
import { Breadcrumb } from "./components/Breadcrumb";
import { ContentCalendarCard } from "./components/ContentCalendarCard";
import { FloatingCard } from "./components/FloatingCard";
import { HierarchyDropdown } from "./components/HierarchyDropdown";
import { KolFunnelCard } from "./components/KolFunnelCard";
import { MetricCard } from "./components/MetricCard";
import { RealMap } from "./components/RealMap";
import { SignalsAlertsCard } from "./components/SignalsAlertsCard";
import { SystemHealthBar } from "./components/SystemHealthBar";
import { TopMoversCard } from "./components/TopMoversCard";
import { UpcomingEventsCard } from "./components/UpcomingEventsCard";
import { KPI_SCOPES } from "./data/kpiScopes";
import { VIEW_MODES } from "./data/viewModes";

const e = React.createElement;
const EMPTY_AI_INSIGHT = {
  confidence: 0,
  updatedLabel: "无信号",
  todayDecision: {
    text: "暂无可执行候选",
    amount: "--",
    reason: "当前没有可用真实候选",
    primaryAction: "查看证据",
    secondaryAction: "稍后处理",
  },
  strengthen: [],
  weaken: [],
  todayContent: [],
  poweredBy: "真实 API / 无信号",
};

export function DashboardReplicaPage(props: any) {
  const {
    kpiScope, setKpiScope, t, setSelectedKpi, globeContainerRef, isAvailable, pins, currentMode,
    venue, item, city, country, setPreviewEvent, handleCountryChange, handleCityChange, handleItemChange,
    setVenue, setSelectedPin, viewMode, setViewMode, countryOptions, cityOptions, itemOptions, venueOptions,
    breadcrumb, goBack, topListData, setSelectedEvent, setSelectedSignal, setShowAllSignals, setShowAIConfirm,
    setAiRegenerating, aiRegenerating, setSelectedMover, setShowAllMovers, setSelectedProject, setShowAllProjects,
    setSelectedPublish, setShowFullCalendar, onOpenProjectsList, onOpenMyKol, onOpenEvents, focusTarget, viewModes = VIEW_MODES,
    showSettingsModal = false,
    metrics = [], campaigns = [], campaignsMeta = {}, calendarDays = [], calendarMeta = {},
    signals = [], aiInsight = EMPTY_AI_INSIGHT, topMovers = [], kolFunnel = null,
    upcomingEvents = [], revenueBySource = [], dashboardLoading = false, dashboardError = "",
    apiToken = "",
  } = props;
  return e("div", { className: "p-4 md:p-6" },

          // P5 系统健康条(真实只读端点;字段缺失显「待接入」,绝不编造)
          e(SystemHealthBar, { apiToken }),

          // 数据新鲜度:按用户要求(2026-06-14)从 dashboard 收起,仅保留后台端点
          // /dashboard/data-freshness(供 admin/Agent OS 取用),不在 dashboard 展示。

          // V6.6: KPI Header — title + scope toggle
          e("div", { className: "mb-3 flex items-end justify-between gap-3 flex-wrap" },
            e("div", null,
              e("h2", { className: "text-base font-semibold text-white" }, "Performance Overview"),
              e("p", { className: "text-[11px] text-slate-500 mt-0.5" }, dashboardLoading ? "正在读取真实 API" : dashboardError ? "真实 API 无信号" : "Real-time KPIs · Last 30 days")
            ),
            // Scope toggle (All / KOL / Company)
            e("div", { className: "inline-flex items-center gap-0 rounded-lg border border-white/[0.08] bg-white/[0.02] p-0.5" },
              Object.entries(KPI_SCOPES).map(([key, sc]) => {
                const active = kpiScope === key;
                return e("button", {
                  key,
                  onClick: () => setKpiScope(key),
                  className: `px-3 py-1 text-[11px] font-medium rounded-md transition-all ${active ? "text-white" : "text-slate-400 hover:text-white"}`,
                  style: active ? { background: sc.accent, color: sc.color } : {}
                }, t(sc.shortLabel));
              })
            )
          ),

          // KPI Strip
          e("div", { className: "mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6" },
            metrics.map((m, i) => e(MetricCard, { 
              key: m.id, item: m, i, scope: kpiScope,
              onClick: () => setSelectedKpi(m.id)
            }))
          ),

          // Main row: Globe + Right rail
          e("div", { className: "grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_360px]" },

            // ─── LEFT ───
            e("div", { className: "space-y-4" },

              e(motion.div, {
                ref: globeContainerRef,
                initial: { opacity: 0 }, animate: { opacity: 1 }, transition: { delay: 0.2 },
                className: "relative overflow-hidden rounded-2xl border border-white/[0.08] bg-[#040712]",
                style: { minHeight: "560px" }
              },
                // 智能切换:Country 及以上用 3D Globe,City 及以下用 2D 真实街道地图
                // useRealMap 判断:选了 city 就切换到 2D
                // V6.1: 直接用 RealMap,跟 minimal_map.html 完全一致的代码
                isAvailable && e(RealMap, {
                  pins, accentColor: currentMode.color,
                  defaultZoom: venue ? 17 : item ? 15 : city ? 12 : country ? 5 : 2,
                  onPinClick: (p) => {
                    // V6.9: Events pin → 第一次弹 preview(防误触)
                    if (p.isEvent && p.eventData) {
                      setPreviewEvent(p.eventData);
                      return;
                    }
                    if (!country) {
                      handleCountryChange(p.name);
                    } else if (!city && p.name) {
                      handleCityChange(p.name);
                    } else if (!item && (p.handle || p.name)) {
                      handleItemChange(p.handle || p.name);
                    } else if (!venue && p.name && p.parentItem !== undefined) {
                      setVenue(p.name);
                    } else {
                      setSelectedPin(p);
                    }
                  },
                  focusTarget
                }),

                // Title overlay 左上(永远在,加半透明背景便于在 2D 街景下可读)
                !showSettingsModal && e("div", { className: "pointer-events-none absolute left-6 top-6 z-overlay-high max-w-md rounded-xl bg-[#040712]/70 backdrop-blur-md px-3 py-2" },
                  e("h2", { className: "text-xl md:text-2xl font-semibold text-white" }, "Marketing Command Center"),
                  e("p", { className: "mt-1 text-xs md:text-sm text-slate-400" },
                    currentMode ? currentMode.desc : "Choose a view to explore real-time activity across the globe"
                  )
                ),

                // 层级选择器(左侧顶部,不顶到右边)
                !showSettingsModal && e("div", { className: "absolute left-6 top-24 z-overlay-high flex flex-wrap items-start gap-2 max-w-[calc(100%-360px)]" },

                  // Step 1: VIEWING
                  e(HierarchyDropdown, {
                    label: "Viewing",
                    value: viewMode,
                    placeholder: "Select",
                    accent: !viewMode,
                    color: currentMode?.color || "#a855f7",
                      options: Object.entries(viewModes).map(([k, m]) => ({
                      key: k,
                      label: m.label,
                      sub: m.desc,
                      badge: m.available ? null : "WAITING",
                      disabled: !m.available,
                    })),
                    onChange: (v) => {
                      if (!viewModes[v].available) return;
                      setViewMode(v); setCountry(""); setCity(""); setItem(""); setVenue("");
                    }
                  }),

                  // Step 2: COUNTRY (events 也显示)
                  viewMode && isAvailable && e(HierarchyDropdown, {
                    label: "Country", value: country, placeholder: "All",
                    color: currentMode.color, options: countryOptions, onChange: handleCountryChange
                  }),

                  // Step 3: CITY (events 也显示)
                  viewMode && isAvailable && country && e(HierarchyDropdown, {
                    label: "City", value: city, placeholder: "All",
                    color: currentMode.color, options: cityOptions, onChange: handleCityChange
                  }),

                  // Step 4: ITEM (events 模式不显示)
                  viewMode && isAvailable && !currentMode.isEvents && city && itemOptions.length > 1 && e(HierarchyDropdown, {
                    label: viewMode === "kols" ? "KOL" : "Dealer",
                    value: item, placeholder: "All",
                    color: currentMode.color, options: itemOptions, onChange: handleItemChange
                  }),

                  // Step 5: VENUE (events 模式不显示)
                  viewMode && isAvailable && !currentMode.isEvents && item && venueOptions.length > 1 && e(HierarchyDropdown, {
                    label: "Address", value: venue, placeholder: "All",
                    color: currentMode.color, options: venueOptions, onChange: setVenue
                  })
                ),

                // Breadcrumb — 单独一行在 dropdown 下方(避免和 Events 卡重合)
                !showSettingsModal && breadcrumb.length > 0 && e("div", { className: "absolute left-6 top-[154px] z-overlay-high" },
                  e(Breadcrumb, { levels: breadcrumb, onGoBack: goBack })
                ),

                // Empty state
                !viewMode && e("div", { className: "absolute inset-0 flex items-center justify-center" },
                  e("div", { className: "max-w-md text-center px-6" },
                    e("div", { className: "mb-6 inline-flex h-20 w-20 items-center justify-center rounded-full border border-white/10 bg-white/[0.02]" },
                      e(Globe2, { size: 36, className: "text-slate-500" })
                    ),
                    e("h3", { className: "mb-2 text-xl font-semibold text-white" }, "Choose what to map"),
                    e("p", { className: "mb-6 text-sm text-slate-400" },
                      "Select a data type above to populate the globe with KOLs, dealer locations, or customer activity."
                    ),
                    e("div", { className: "flex flex-wrap justify-center gap-2 text-xs text-slate-500" },
                      e("span", { className: "rounded-md border border-white/[0.06] bg-white/[0.02] px-3 py-1.5" }, "KOL 分布读取真实 API"),
                      e("span", { className: "rounded-md border border-white/[0.06] bg-white/[0.02] px-3 py-1.5" }, "Dealer locations 待接入"),
                      e("span", { className: "rounded-md border border-yellow-500/20 bg-yellow-500/[0.04] px-3 py-1.5 text-yellow-400/80" },
                        e(Loader2, { size: 11, className: "mr-1 inline-block animate-spin" }),
                        "Customer data pending"
                      )
                    )
                  )
                ),

                // Customer heatmap waiting
                viewMode === "customer" && !isAvailable && e("div", { className: "absolute inset-0 flex items-center justify-center" },
                  e("div", { className: "mx-4 max-w-lg rounded-2xl border border-yellow-500/30 bg-yellow-500/[0.05] p-8 text-center backdrop-blur-xl" },
                    e("div", { className: "mb-4 inline-flex h-14 w-14 items-center justify-center rounded-full bg-yellow-500/20" },
                      e(Loader2, { size: 24, className: "animate-spin text-yellow-400" })
                    ),
                    e("h3", { className: "mb-2 text-lg font-semibold text-white" }, "Awaiting Shopify integration"),
                    e("p", { className: "mb-4 text-sm text-slate-400" },
                      "Customer heatmap will activate automatically once Shopify and Amazon order data is connected."
                    ),
                    e("div", { className: "rounded-lg border border-white/[0.08] bg-black/30 p-3 text-left font-mono text-[10px] text-slate-400" },
                      e("div", { className: "mb-1 text-slate-500" }, "// Auto-activates when this endpoint returns data:"),
                      e("div", { className: "text-amber-300" }, "GET /api/admin/vkpi/shopify/customer-heatmap")
                    )
                  )
                ),

                // ⬇️ Upcoming Events 浮动卡(可拖动 + 可折叠)
                // P2 穿透根治:Settings 浮层(z-1000)打开时,这些 z-800 绝对定位浮卡会从
                // dashboard 栈逃逸穿到设置页之上 → settings 打开就不渲染浮卡(最稳,无状态丢失)。
                !showSettingsModal && isAvailable && viewMode && e("div", {
                  className: "absolute right-6 top-24 w-[280px]",
                  style: { zIndex: 800 }
                },
                  e(UpcomingEventsCard, { events: upcomingEvents, dragConstraintsRef: globeContainerRef, onEventClick: setSelectedEvent, onViewAll: onOpenEvents })
                ),

                // V6.3: Top List 浮动卡(可拖动 / 可缩放 / 可折叠)
                !showSettingsModal && isAvailable && viewMode && topListData.items.length > 0 && e("div", {
                  className: "absolute bottom-6 left-6",
                  style: { zIndex: 800 }
                },
                  e(FloatingCard, {
                    title: topListData.title,
                    icon: TrendingUp,
                    iconColor: currentMode.color,
                    dragConstraintsRef: globeContainerRef,
                    defaultSize: { width: 280, height: "auto" },
                    initialDelay: 0.3,
                  },
                    // 数据来源标注:真实 KOL Pool 国家/城市分布(诚实化 2026-06-14)
                    e("div", { className: "mb-2 flex items-center gap-1.5 text-[9px] text-slate-500" },
                      e("span", { className: "rounded border border-emerald-500/20 bg-emerald-500/[0.06] px-1 py-px text-emerald-400/80" }, "实时"),
                      e("span", null, "KOL Pool 分布")
                    ),
                    topListData.items.slice(0, 7).map((row, i) => e(motion.div, {
                      key: row.label,
                      initial: { opacity: 0, x: -8 }, animate: { opacity: 1, x: 0 },
                      transition: { delay: 0.3 + i * 0.04 },
                      className: "mb-2 cursor-pointer rounded-md px-1 py-0.5 transition-colors hover:bg-white/[0.04] last:mb-0",
                      role: "button",
                      tabIndex: 0,
                      onClick: () => {
                        if (!country) handleCountryChange(row.label);
                        else if (!city) handleCityChange(row.label);
                        else if (!item) handleItemChange(row.label);
                      },
                    },
                      e("div", { className: "mb-1 flex items-center justify-between text-xs gap-2" },
                        e("span", { className: "min-w-0 truncate text-slate-300" }, row.label),
                        e("span", { className: "shrink-0 tabular-nums text-slate-400" }, row.value)
                      ),
                      e("div", { className: "h-0.5 overflow-hidden rounded-full bg-white/[0.06]" },
                        e(motion.div, {
                          initial: { width: 0 },
                          animate: { width: `${row.barWidth}%` },
                          transition: { delay: 0.4 + i * 0.04, duration: 0.8, ease: [0.16, 1, 0.3, 1] },
                          className: "h-full rounded-full",
                          style: { background: `linear-gradient(90deg, ${currentMode.color}, ${currentMode.color}80)` }
                        })
                      )
                    ))
                  )
                ),

                // V6.3: Revenue Donut 浮动卡(仅真实收入分布可用时显示)
                !showSettingsModal && revenueBySource.length > 0 && e("div", {
                  className: "absolute bottom-6 right-6",
                  style: { zIndex: 800 }
                },
                  e(FloatingCard, {
                    title: "Revenue by Source",
                    icon: DollarSign,
                    iconColor: "#a855f7",
                    dragConstraintsRef: globeContainerRef,
                    defaultSize: { width: 220, height: "auto" },
                    initialDelay: 0.5,
                  },
                    e("div", {
                      className: "relative mx-auto mb-3 h-24 w-24 rounded-full",
                      style: { background: `conic-gradient(${revenueBySource.map((s, i, arr) => {
                        const start = arr.slice(0, i).reduce((a, x) => a + x.pct, 0);
                        return `${s.color} ${start}% ${start + s.pct}%`;
                      }).join(", ")})` }
                    },
                      e("div", { className: "absolute inset-3 flex flex-col items-center justify-center rounded-full bg-[#0b1220]" },
                        e("span", { className: "text-base font-medium text-white" }, "--"),
                        e("span", { className: "text-[9px] text-slate-500" }, "Total")
                      )
                    ),
                    revenueBySource.map((s) => e("div", { key: s.label, className: "mb-1.5 flex items-center justify-between text-[11px] last:mb-0" },
                      e("span", { className: "flex items-center text-slate-300" },
                        e("i", { className: "mr-2 inline-block h-1.5 w-1.5 rounded-full", style: { background: s.color } }),
                        s.label
                      ),
                      e("span", { className: "tabular-nums text-slate-400" }, `${s.pct}%`)
                    ))
                  )
                ),

                isAvailable && viewMode && e("div", { className: "globe-hint" },
                  country ? "street-level map · pan · zoom · click pin" : "drag · scroll · click pin to drill in"
                )
              ),

              // V6.12: Bottom row 重构 — Signals + AI Today + Top Movers(三卡持平)
              e("div", { className: "grid grid-cols-1 gap-4 lg:grid-cols-3 items-stretch" },
                // 1. Signals & Alerts (from right rail)
                // 诚实化:绝不回退到 data/signalsAlerts.ts 的假 Sony/CineGear 信号。
                // 信号源异常 → 显式「信号源异常」错误态;真实 API 无数据 → 卡内「暂无信号」空态。
                e("div", { className: "h-full" },
                  dashboardError
                    ? e("div", {
                        className: "h-full rounded-xl border border-red-500/20 bg-red-500/[0.04] p-4 backdrop-blur-xl flex flex-col",
                      },
                        e("div", { className: "mb-3 flex items-center justify-between" },
                          e("div", { className: "flex items-center gap-2" },
                            e(AlertTriangle, { size: 14, className: "text-red-300" }),
                            e("h3", { className: "text-sm font-semibold text-white" }, "Signals & Alerts")
                          ),
                          e("span", { className: "text-[9px] text-red-300/80" }, "信号源异常")
                        ),
                        e("div", { className: "flex-1 flex items-center justify-center" },
                          e("div", { className: "rounded-md border border-dashed border-red-500/20 px-3 py-8 text-center text-[11px] text-red-300/70" },
                            "信号源异常 · 暂不可用"
                          )
                        )
                      )
                    : e(SignalsAlertsCard, {
                        alerts: signals,
                        onAlertClick: (a) => setSelectedSignal(a),
                        onViewAll: () => setShowAllSignals(true)
                      })
                ),
                // 2. AI Today (from right rail)
                e("div", { className: "h-full" },
                  e(AIIntelligenceCard, {
                    insight: aiInsight,
                    onApprove: () => setShowAIConfirm(true),
                    // 2026-06-12 死按钮诚实化:提醒写入接口未接,不再 alert 假动作 → 卡内按钮禁用+title 待接入
                    onLater: null,
                    regenerating: aiRegenerating
                  })
                ),
                // 3. 本周 Top Movers (从 Top KOL Performance 改造)
                e("div", { className: "h-full" },
                  e(TopMoversCard, { 
                    movers: topMovers,
                    onMoverClick: (m) => setSelectedMover(m),
                    onViewAll: () => setShowAllMovers(true)
                  })
                )
              )
            ),

            // ─── RIGHT RAIL (KOL 漏斗 + Active Campaigns + 7 天日历) ───
            e("div", { className: "space-y-4" },
              // 0. KOL 漏斗(2026-06-12 C10/波3 R1:收藏→认领→入项目→已发布)
              e(KolFunnelCard, {
                funnel: kolFunnel,
                onOpenMyKol,
              }),
              // 1. Active Campaigns(在推什么)
              e(ActiveCampaignsCard, {
                campaigns,
                campaignsMeta,
                onCampaignClick: (c) => setSelectedProject(c),
                onViewAll: () => onOpenProjectsList ? onOpenProjectsList() : setShowAllProjects(true)
              }),
              // 2. 7 天推广日历
              e(ContentCalendarCard, {
                days: calendarDays,
                latestDate: calendarMeta?.latestDate,
                onItemClick: (item) => setSelectedPublish(item),
                onViewAll: () => setShowFullCalendar(true)
              })
            )
          )
        );
}
