import React from "react";
import { m } from "framer-motion";
import { DollarSign, Globe2, Loader2, TrendingUp } from "lucide-react";
import { Breadcrumb } from "./Breadcrumb";
import { FloatingCard } from "./FloatingCard";
import { HierarchyDropdown } from "./HierarchyDropdown";
import { RealMap } from "./RealMap";
import { UpcomingEventsCard } from "./UpcomingEventsCard";

const e = React.createElement;

export function DashboardCommandCenter(props: any) {
  const {
    globeContainerRef, isAvailable, pins, currentMode, venue, item, city, country,
    setPreviewEvent, handleCountryChange, handleCityChange, handleItemChange, setVenue,
    setSelectedPin, viewMode, setViewMode, countryOptions, cityOptions, itemOptions,
    venueOptions, breadcrumb, goBack, topListData, setSelectedEvent, focusTarget,
    viewModes, showSettingsModal, upcomingEvents, onOpenEvents, revenueBySource = [],
  } = props;

  return e(m.div, {
    ref: globeContainerRef,
    initial: { opacity: 0 },
    animate: { opacity: 1 },
    transition: { delay: 0.12 },
    className: "vkpi-command-map relative overflow-hidden border border-line bg-[var(--ds-bg-2)]",
    style: { minHeight: "560px" },
  },
    isAvailable && e(RealMap, {
      pins,
      accentColor: currentMode.color,
      defaultZoom: venue ? 17 : item ? 15 : city ? 12 : country ? 5 : 2,
      onPinClick: (pin: any) => {
        if (pin.isEvent && pin.eventData) {
          setPreviewEvent(pin.eventData);
          return;
        }
        if (!country) handleCountryChange(pin.name);
        else if (!city && pin.name) handleCityChange(pin.key || pin.name);
        else if (!item && (pin.handle || pin.name)) handleItemChange(pin.handle || pin.name);
        else if (!venue && pin.name && pin.parentItem !== undefined) setVenue(pin.name);
        else setSelectedPin(pin);
      },
      focusTarget,
    }),

    !showSettingsModal && e("div", { className: "vkpi-command-map__title pointer-events-none absolute left-6 top-6 z-overlay-high max-w-md px-3 py-2" },
      e("h2", { className: "text-xl font-semibold text-ink md:text-2xl" }, "Marketing Command Center"),
      e("p", { className: "mt-1 text-xs text-muted md:text-sm" }, currentMode?.desc || "真实 KOL、Dealer 与 Events 地理分布"),
    ),

    !showSettingsModal && e("div", { className: "absolute left-6 top-24 z-overlay-high flex max-w-[calc(100%-360px)] flex-wrap items-start gap-2" },
      e(HierarchyDropdown, {
        label: "Viewing",
        value: viewMode,
        placeholder: "Select",
        accent: !viewMode,
        color: currentMode?.color || "#a855f7",
        options: Object.entries(viewModes || {}).map(([key, mode]: [string, any]) => ({
          key,
          label: mode.label,
          sub: mode.desc,
          badge: mode.available ? null : "WAITING",
          disabled: !mode.available,
        })),
        onChange: (value: any) => {
          if (!viewModes?.[value]?.available) return;
          setViewMode(value);
          handleCountryChange("");
        },
      }),
      viewMode && isAvailable && e(HierarchyDropdown, {
        label: "Country",
        value: country,
        placeholder: "All",
        color: currentMode.color,
        options: countryOptions,
        onChange: handleCountryChange,
      }),
      viewMode && isAvailable && country && e(HierarchyDropdown, {
        label: "City",
        value: city,
        placeholder: "All",
        color: currentMode.color,
        options: cityOptions,
        onChange: handleCityChange,
      }),
      viewMode && isAvailable && !currentMode.isEvents && city && itemOptions.length > 1 && e(HierarchyDropdown, {
        label: viewMode === "kols" ? "KOL" : "Dealer",
        value: item,
        placeholder: "All",
        color: currentMode.color,
        options: itemOptions,
        onChange: handleItemChange,
      }),
      viewMode && isAvailable && !currentMode.isEvents && item && venueOptions.length > 1 && e(HierarchyDropdown, {
        label: "Address",
        value: venue,
        placeholder: "All",
        color: currentMode.color,
        options: venueOptions,
        onChange: setVenue,
      }),
    ),

    !showSettingsModal && breadcrumb.length > 0 && e("div", { className: "absolute left-6 top-[154px] z-overlay-high" },
      e(Breadcrumb, { levels: breadcrumb, onGoBack: goBack }),
    ),

    !viewMode && e("div", { className: "absolute inset-0 flex items-center justify-center" },
      e("div", { className: "max-w-md px-6 text-center" },
        e("div", { className: "mb-6 inline-flex h-20 w-20 items-center justify-center rounded-full border border-line bg-panel" }, e(Globe2, { size: 36, className: "text-muted" })),
        e("h3", { className: "mb-2 text-xl font-semibold text-ink" }, "Choose what to map"),
        e("p", { className: "mb-6 text-sm text-muted" }, "选择 KOL、Dealer 或 Events，地图将按真实数据切换。"),
        e("div", { className: "flex flex-wrap justify-center gap-2 text-xs text-muted" },
          e("span", { className: "rounded-md border border-line bg-panel px-3 py-1.5" }, "KOL 分布读取真实 API"),
          e("span", { className: "rounded-md border border-line bg-panel px-3 py-1.5" }, "Dealer locations 已接入"),
          e("span", { className: "rounded-md border border-warn-soft bg-warn-soft px-3 py-1.5 text-warn" }, e(Loader2, { size: 11, className: "mr-1 inline-block animate-spin" }), "Customer data pending"),
        ),
      ),
    ),

    viewMode === "customer" && !isAvailable && e("div", { className: "absolute inset-0 flex items-center justify-center" },
      e("div", { className: "mx-4 max-w-lg rounded-2xl border border-warn-soft bg-warn-soft p-8 text-center backdrop-blur-xl" },
        e("div", { className: "mb-4 inline-flex h-14 w-14 items-center justify-center rounded-full bg-warn-soft" }, e(Loader2, { size: 24, className: "animate-spin text-warn" })),
        e("h3", { className: "mb-2 text-lg font-semibold text-ink" }, "Awaiting Shopify integration"),
        e("p", { className: "text-sm text-muted" }, "Customer heatmap 会在 Shopify 订单数据接入后自动启用。"),
      ),
    ),

    !showSettingsModal && isAvailable && viewMode && e("div", { className: "absolute right-6 top-24 w-[280px]", style: { zIndex: 800 } },
      e(UpcomingEventsCard, { events: upcomingEvents, dragConstraintsRef: globeContainerRef, onEventClick: setSelectedEvent, onViewAll: onOpenEvents }),
    ),

    !showSettingsModal && isAvailable && viewMode && topListData.items.length > 0 && e("div", { className: "absolute bottom-6 left-6", style: { zIndex: 800 } },
      e(FloatingCard, {
        title: topListData.title,
        icon: TrendingUp,
        iconColor: currentMode.color,
        dragConstraintsRef: globeContainerRef,
        defaultSize: { width: 280, height: "auto" },
        initialDelay: 0.2,
      },
        e("div", { className: "mb-2 flex items-center gap-1.5 text-[9px] text-muted" },
          e("span", { className: "rounded border border-good-soft bg-good-soft px-1 py-px text-good" }, "实时"),
          e("span", null, "KOL Pool 分布"),
        ),
        topListData.items.slice(0, 7).map((row: any, index: number) => e(m.div, {
          key: row.label,
          initial: { opacity: 0, x: -8 },
          animate: { opacity: 1, x: 0 },
          transition: { delay: 0.22 + index * 0.04 },
          className: "mb-2 cursor-pointer rounded-md px-1 py-0.5 transition-colors hover:bg-accent-soft last:mb-0",
          role: "button",
          tabIndex: 0,
          onClick: () => {
            const value = row.key || row.label;
            if (!country) handleCountryChange(value);
            else if (!city) handleCityChange(value);
            else if (!item) handleItemChange(value);
          },
        },
          e("div", { className: "mb-1 flex items-center justify-between gap-2 text-xs" },
            e("span", { className: "min-w-0 truncate text-ink-2" }, row.label),
            e("span", { className: "shrink-0 tabular-nums text-muted" }, row.value),
          ),
          e("div", { className: "h-0.5 overflow-hidden rounded-full bg-card" },
            e(m.div, {
              initial: { width: 0 },
              animate: { width: `${row.barWidth}%` },
              transition: { delay: 0.3 + index * 0.04, duration: 0.8, ease: [0.16, 1, 0.3, 1] },
              className: "h-full rounded-full",
              style: { background: `linear-gradient(90deg, ${currentMode.color}, ${currentMode.color}80)` },
            }),
          ),
        )),
      ),
    ),

    !showSettingsModal && revenueBySource.length > 0 && e("div", { className: "absolute bottom-6 right-6", style: { zIndex: 800 } },
      e(FloatingCard, {
        title: "Revenue by Source",
        icon: DollarSign,
        iconColor: "#a855f7",
        dragConstraintsRef: globeContainerRef,
        defaultSize: { width: 220, height: "auto" },
        initialDelay: 0.4,
      },
        revenueBySource.map((source: any) => e("div", { key: source.label, className: "mb-1.5 flex items-center justify-between text-[11px] last:mb-0" },
          e("span", { className: "flex items-center text-ink-2" }, e("i", { className: "mr-2 inline-block h-1.5 w-1.5 rounded-full", style: { background: source.color } }), source.label),
          e("span", { className: "tabular-nums text-muted" }, `${source.pct}%`),
        )),
      ),
    ),

    isAvailable && viewMode && e("div", { className: "globe-hint" }, country ? "street-level map · pan · zoom · click pin" : "drag · scroll · click pin to drill in"),
  );
}
