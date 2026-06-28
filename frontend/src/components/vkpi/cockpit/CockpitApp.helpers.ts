// @ts-nocheck
// Pure helpers extracted verbatim from CockpitApp.tsx (行为不变抽取).
// 这些纯函数原本是容器内 useMemo 的函数体,现以显式入参的形式上移;
// 调用点 useMemo 的依赖数组保持不变,行为必然不变。

import { Calendar, Camera, PartyPopper, Ticket, Users, Video } from "lucide-react";
import { eventCoords } from "./normalizers";

// 真实 events → 统一 UI 形态(Upcoming 卡 / 地图落点 / preview 共用同一映射)。
export function buildMappedEvents(eventRows) {
  const EVENT_TYPE_VISUAL = {
    tradeshow: { icon: Ticket, color: "#a855f7" },
    media: { icon: Camera, color: "#06b6d4" },
    webinar: { icon: Video, color: "#10b981" },
    kol_meetup: { icon: PartyPopper, color: "#fbbf24" },
    internal: { icon: Users, color: "#94a3b8" },
  };
  const STATUS_LABEL = { planning: "筹备中", active: "进行中", done: "已结束", cancelled: "已取消" };
  return (Array.isArray(eventRows) ? eventRows : []).map((row) => {
    const visual = EVENT_TYPE_VISUAL[row.type_key] || { icon: Calendar, color: "#a855f7" };
    const start = new Date(String(row.start_date));
    const startStr = String(row.start_date || "").slice(0, 10);
    const dateLabel = Number.isNaN(start.getTime())
      ? startStr
      : start.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
    const locParts = [row.location_city, row.location_country].filter(Boolean);
    const health = Number(row.health_score);
    const status = String(row.status || "");
    return {
      id: row.id,
      title: String(row.title || "未命名活动"),
      date: dateLabel,
      startDate: startStr,
      location: String(row.location_name || locParts.join(" · ") || "地点待补"),
      venue: String(row.location_name || locParts.join(" · ") || ""),
      country: String(row.location_country || "").toUpperCase(),
      city: String(row.location_city || ""),
      lat: row.location_lat != null ? Number(row.location_lat) : null,
      lng: row.location_lng != null ? Number(row.location_lng) : null,
      icon: visual.icon,
      color: visual.color,
      // 真实 health_score 作为进度条;无则 0(不编造)。
      materialProgress: Number.isFinite(health) ? Math.max(0, Math.min(100, health)) : 0,
      materialStatus: STATUS_LABEL[status] || String(row.status_label || "进行中"),
      raw: row,
    };
  });
}

// Upcoming 卡:start_date >= 今天,升序,取前 6。
export function buildUpcomingEvents(mappedEvents) {
  const nowLocal = new Date();
  const todayStr = `${nowLocal.getFullYear()}-${String(nowLocal.getMonth() + 1).padStart(2, "0")}-${String(nowLocal.getDate()).padStart(2, "0")}`;
  return mappedEvents
    .filter((ev) => String(ev.startDate || "") >= todayStr)
    .sort((a, b) => String(a.startDate || "").localeCompare(String(b.startDate || "")))
    .slice(0, 6);
}

// 地图落点:每个「有定位」的活动一个 pin。
export function buildEventPins(mappedEvents) {
  return mappedEvents
    .map((ev) => {
      const coords = eventCoords(ev.country, ev.lat, ev.lng, ev.id || ev.title);
      if (!coords) return null;
      return {
        id: ev.id,
        name: ev.title,
        handle: ev.title,
        lat: coords.lat,
        lng: coords.lng,
        count: 1,
        revenue: "",
        color: ev.color,
        scale: 0.9,
        isEvent: true,
        eventData: ev,
        country: String(ev.country || "").toUpperCase(),
        city: ev.city,
      };
    })
    .filter(Boolean);
}

// 计算当前要在地球上显示的 pins。
export function buildPins({ currentMode, isAvailable, country, city, item, venue, hierarchy, eventPins }) {
  if (!currentMode || !isAvailable) return [];

  // 真实活动上图:有定位的活动逐个落点(点击 → preview)。
  if (currentMode.isEvents) {
    return eventPins.filter((p) => {
      if (country && String(p.country || "").toUpperCase() !== String(country).toUpperCase()) return false;
      if (city && String(p.city || "").toLowerCase() !== String(city).toLowerCase()) return false;
      return true;
    });
  }

  // Level 0:World view — 所有国家
  if (!country) {
    return Object.entries(hierarchy).map(([name, data]) => ({
      name, lat: data.lat, lng: data.lng,
      count: data.count, revenue: data.revenue,
      color: currentMode.pinColor,
      scale: 1,
    }));
  }

  // Level 1:Country view — 该国所有城市
  if (!city && hierarchy[country]?.cities) {
    return Object.entries(hierarchy[country].cities).map(([cityName, cityData]) => ({
      name: cityName,
      country, lat: cityData.lat, lng: cityData.lng,
      count: cityData.count, revenue: cityData.revenue,
      color: currentMode.pinColor,
      scale: 0.75,
    }));
  }

  // Level 2:City view — 该城市具体 KOL / Store
  if (!item && hierarchy[country]?.cities[city]) {
    const cityData = hierarchy[country].cities[city];
    const items = cityData.kols || cityData.stores || [];
    return items.map((d) => ({
      ...d, country, city,
      color: currentMode.pinColor,
      scale: 0.6,
    }));
  }

  // Level 3:Item view — 选了具体 KOL/Store
  if (item && hierarchy[country]?.cities[city]) {
    const cityData = hierarchy[country].cities[city];
    const items = cityData.kols || cityData.stores || [];
    const sel = items.find((d) => (d.handle || d.name) === item);
    if (!sel) return [];

    // 如果没选 venue 但 item 有 venues,显示所有 venues
    if (!venue && sel.venues && sel.venues.length > 0) {
      return [
        { ...sel, country, city, color: currentMode.pinColor, scale: 1.0, isParent: true },
        ...sel.venues.map((v) => ({
          ...v, country, city, parentItem: sel.handle || sel.name,
          color: currentMode.pinColor, scale: 0.5,
        }))
      ];
    }

    // Level 4:Venue 选中 — 单个高亮
    if (venue && sel.venues) {
      const v = sel.venues.find((x) => x.name === venue);
      if (v) return [{ ...v, country, city, parentItem: sel.handle || sel.name, color: currentMode.pinColor, scale: 1.4 }];
    }

    // 没 venues,就显示 item 自己
    return [{ ...sel, country, city, color: currentMode.pinColor, scale: 1.3 }];
  }

  return [];
}

// 计算 focusTarget。
export function buildFocusTarget({ country, city, item, venue, hierarchy, currentMode }) {
  if (!currentMode) return null;
  // V6.9.2: Events viewing — 根据 country/city 飞
  if (currentMode.isEvents) {
    if (city && hierarchy[country]?.cities[city]) {
      const c = hierarchy[country].cities[city];
      return { lat: c.lat, lng: c.lng, zoom: 11 };
    }
    if (country && hierarchy[country]) {
      return { lat: hierarchy[country].lat, lng: hierarchy[country].lng, zoom: 4 };
    }
    return { lat: 30, lng: 0, zoom: 2 };
  }
  // V4.7: 选了 country 立刻切 2D 地图,focusTarget 用 Leaflet zoom 体系
  // venue: 街景级
  if (venue && hierarchy[country]?.cities[city]) {
    const items = hierarchy[country].cities[city].kols || hierarchy[country].cities[city].stores || [];
    const sel = items.find((d) => (d.handle || d.name) === item);
    const v = sel?.venues?.find((x) => x.name === venue);
    if (v) return { lat: v.lat, lng: v.lng, zoom: 18 };
  }
  // item: 街区级
  if (item && hierarchy[country]?.cities[city]) {
    const items = hierarchy[country].cities[city].kols || hierarchy[country].cities[city].stores || [];
    const sel = items.find((d) => (d.handle || d.name) === item);
    if (sel) return { lat: sel.lat, lng: sel.lng, zoom: 16 };
  }
  // city: 城市级
  if (city && hierarchy[country]?.cities[city]) {
    const c = hierarchy[country].cities[city];
    return { lat: c.lat, lng: c.lng, zoom: 13 };
  }
  // country: 国家级
  if (country && hierarchy[country]) {
    const c = hierarchy[country];
    return { lat: c.lat, lng: c.lng, zoom: 5 };
  }
  // V6.1: World 视图
  return { lat: 30, lng: 0, zoom: 2 };
}

// Top List 数据(根据当前层级动态生成)。
export function buildTopListData({ currentMode, country, city, item, hierarchy, viewMode }) {
  if (!currentMode) return { title: "", items: [] };
  if (!country) {
    const total = viewMode === "kols"
      ? Math.max(1, Object.values(hierarchy).reduce((sum, item) => sum + (Number(item.count) || 0), 0))
      : 100;
    return {
      title: viewMode === "kols" ? "Top Regions by KOL Count" : "Top Regions by Revenue",
      items: Object.entries(hierarchy)
        .slice()
        .sort(([, a], [, b]) => (b.count || parseFloat((b.revenue || "$0").replace(/[$k,M]/g, "")) || 0) - (a.count || parseFloat((a.revenue || "$0").replace(/[$k,M]/g, "")) || 0))
        .slice(0, 7)
        .map(([name, d]) => ({
          label: name,
          value: viewMode === "kols" ? `${Number(d.count || 0).toLocaleString()} KOLs` : d.revenue,
          barWidth: viewMode === "kols" ? Math.max(4, Math.min(100, (Number(d.count || 0) / total) * 100)) : Math.max(20, 100 - 0),
        }))
    };
  }
  if (!city && hierarchy[country]?.cities) {
    return {
      title: `Top Cities in ${country}`,
      items: Object.entries(hierarchy[country].cities)
        .slice()
        .sort(([, a], [, b]) => (b.count || parseFloat((b.revenue || "$0").replace(/[$k,]/g, "")) || 0) - (a.count || parseFloat((a.revenue || "$0").replace(/[$k,]/g, "")) || 0))
        .map(([name, d]) => ({
          label: name,
          value: viewMode === "kols" ? `${d.count} KOLs` : d.revenue,
          barWidth: Math.max(6, Math.min(100, (Number(d.count || 0) / Math.max(1, Number(hierarchy[country].count || 0))) * 100)),
        }))
    };
  }
  if (city && hierarchy[country]?.cities[city] && !item) {
    const cityData = hierarchy[country].cities[city];
    const items = cityData.kols || cityData.stores || [];
    return {
      title: viewMode === "kols" ? `KOLs in ${city}` : `Dealers in ${city}`,
      // 2026-06-14 诚实化:此层无 per-KOL 真实排序量级,改用统一占位条宽(不再 Math.random 伪造排名)。
      items: items.map((d) => ({
        label: d.handle || d.name,
        value: d.engagement || d.revenue,
        barWidth: 60,
      }))
    };
  }
  // V4.5: Venue level — 显示该 KOL/Store 的所有 venue 地点
  if (item && hierarchy[country]?.cities[city]) {
    const items = hierarchy[country].cities[city].kols || hierarchy[country].cities[city].stores || [];
    const sel = items.find((d) => (d.handle || d.name) === item);
    if (sel && sel.venues && sel.venues.length > 0) {
      return {
        title: `${sel.handle || sel.name} · Locations`,
        items: sel.venues.map((v) => ({
          label: v.name,
          value: v.type,
          barWidth: 80,
        }))
      };
    }
  }
  return { title: "", items: [] };
}

// Country options
export function buildCountryOptions({ currentMode, hierarchy, viewMode }) {
  if (!currentMode) return [];
  return [
    { key: "", label: "All Countries", sub: "World view" },
    ...Object.entries(hierarchy)
      .sort(([, a], [, b]) => (b.count || parseFloat((b.revenue || "$0").replace(/[$k,M]/g, "")) || 0) - (a.count || parseFloat((a.revenue || "$0").replace(/[$k,M]/g, "")) || 0))
      .map(([name, data]) => ({
        key: name,
        label: name,
        sub: viewMode === "kols" ? `${data.count} KOLs` : `Revenue ${data.revenue}`,
        badge: viewMode === "kols" ? data.count : data.revenue,
      }))
  ];
}

// City options
export function buildCityOptions({ country, hierarchy, viewMode }) {
  if (!country || !hierarchy[country]?.cities) return [];
  return [
    { key: "", label: `All of ${country}`, sub: "Country view" },
    ...Object.entries(hierarchy[country].cities)
      .sort(([, a], [, b]) => (b.count || parseFloat((b.revenue || "$0").replace(/[$k,]/g, "")) || 0) - (a.count || parseFloat((a.revenue || "$0").replace(/[$k,]/g, "")) || 0))
      .map(([name, data]) => ({
        key: name,
        label: name,
        sub: viewMode === "kols" ? `${data.count} KOLs` : `Revenue ${data.revenue}`,
        badge: viewMode === "kols" ? data.count : data.revenue,
      }))
  ];
}

// Item options(KOL / Store)
export function buildItemOptions({ city, country, hierarchy }) {
  if (!city || !hierarchy[country]?.cities[city]) return [];
  const cityData = hierarchy[country].cities[city];
  const items = cityData.kols || cityData.stores || [];
  return [
    { key: "", label: `All in ${city}`, sub: "City view" },
    ...items.map((d) => ({
      key: d.handle || d.name,
      label: d.handle || d.name,
      sub: d.niche || d.type,
      badge: d.engagement || d.revenue,
    }))
  ];
}

// V4.5: Venue options(街道级 / 店内楼层 / Landmark)
export function buildVenueOptions({ item, city, country, hierarchy }) {
  if (!item || !city || !hierarchy[country]?.cities[city]) return [];
  const cityData = hierarchy[country].cities[city];
  const items = cityData.kols || cityData.stores || [];
  const sel = items.find((d) => (d.handle || d.name) === item);
  if (!sel || !sel.venues || sel.venues.length === 0) return [];
  return [
    { key: "", label: `All venues`, sub: `${sel.handle || sel.name} all locations` },
    ...sel.venues.map((v) => ({
      key: v.name,
      label: v.name,
      sub: v.note || v.type,
      badge: v.type,
    }))
  ];
}
