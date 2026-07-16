// Verbatim from vkpi_v6.15.7_integrated.html


import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useTheme } from "../../../../app/providers/ThemeProvider";

function finitePin(pin: any) {
  return Number.isFinite(Number(pin?.lat)) && Number.isFinite(Number(pin?.lng));
}

// The lines are decorative context, not business evidence.  An all-US Dealer
// map can contain thousands of pins, so an O(n²) nearest-neighbour pass would
// freeze the UI before Leaflet can paint the actual locations.  Keep the exact
// small-map behaviour and fail closed (no decorative network) at large scale.
export const MAX_NETWORK_LINE_PIN_COUNT = 250;
export const MAX_DOM_MARKER_PIN_COUNT = 1_000;

export function shouldUseCanvasMarkers(pins: any[]) {
  return pins.filter(finitePin).length > MAX_DOM_MARKER_PIN_COUNT;
}

export function escapeMapHtml(value: unknown) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character] || character);
}

export function safeMapColor(value: unknown, fallback = "#a855f7") {
  const candidate = String(value ?? "").trim();
  if (/^#[0-9a-f]{3,8}$/i.test(candidate)) return candidate;
  if (/^(?:rgb|hsl)a?\([\d\s.,%/+\-]+\)$/i.test(candidate)) return candidate;
  if (/^var\(--[a-z0-9-]+\)$/i.test(candidate)) return candidate;
  if (/^[a-z]{3,24}$/i.test(candidate)) return candidate;
  return fallback;
}

export function nearestPinEdges(pins: any[]) {
  const valid = pins.filter(finitePin);
  if (valid.length > MAX_NETWORK_LINE_PIN_COUNT) return [];
  const seen = new Set<string>();
  const edges: Array<[any, any]> = [];
  valid.forEach((pin, index) => {
    let nearestIndex = -1;
    let nearestDistance = Number.POSITIVE_INFINITY;
    valid.forEach((candidate, candidateIndex) => {
      if (candidateIndex === index) return;
      const latDelta = Number(pin.lat) - Number(candidate.lat);
      const lngDelta = Number(pin.lng) - Number(candidate.lng);
      const distance = latDelta * latDelta + lngDelta * lngDelta;
      if (distance < nearestDistance) {
        nearestDistance = distance;
        nearestIndex = candidateIndex;
      }
    });
    if (nearestIndex < 0) return;
    const key = [index, nearestIndex].sort((a, b) => a - b).join(":");
    if (seen.has(key)) return;
    seen.add(key);
    edges.push([pin, valid[nearestIndex]]);
  });
  return edges;
}

export function RealMap({ pins, accentColor, onPinClick, focusTarget, defaultZoom = 12 }: any) {
  const { theme } = useTheme();
  const containerRef = useRef<any>(null);
  const mapRef = useRef<any>(null);
  const tileLayerRef = useRef<any>(null);
  const tileStyleRef = useRef<"light" | "dark" | null>(null);
  const markersRef = useRef<any[]>([]);
  const networkRef = useRef<any[]>([]);
  const onPinClickRef = useRef(onPinClick);
  onPinClickRef.current = onPinClick;
  
  // Leaflet init - 只跑一次,跟 minimal_map 一样的代码
  useEffect(() => {
    if (mapRef.current) return; // 已经 init 了就跳过
    
    const container = containerRef.current;
    if (!container) return;
    
    const initialLat = focusTarget?.lat || 37.5;
    const initialLng = focusTarget?.lng || -95.7;
    const initialZoom = focusTarget?.zoom || defaultZoom;
    
    const map = L.map(container, { preferCanvas: true }).setView([initialLat, initialLng], initialZoom);
    
    const initialTheme = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
    const layer = L.tileLayer(`https://{s}.basemaps.cartocdn.com/${initialTheme}_all/{z}/{x}/{y}{r}.png`, {
      attribution: '© CARTO',
      subdomains: 'abcd',
      maxZoom: 19,
    });
    layer.addTo(map);
    tileLayerRef.current = layer;
    tileStyleRef.current = initialTheme;
    
    mapRef.current = map;
    
    // 一次 invalidateSize 确保尺寸
    const resizeTimer = window.setTimeout(() => map.invalidateSize(), 100);
    return () => {
      window.clearTimeout(resizeTimer);
        markersRef.current = [];
        networkRef.current = [];
      tileLayerRef.current = null;
      tileStyleRef.current = null;
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // 深浅主题使用 CARTO 对应的黑白底图，不再在浅色模式里混入暗色瓦片。
  useEffect(() => {
    const map = mapRef.current;
    if (!map || tileStyleRef.current === theme) return;
    if (tileLayerRef.current) map.removeLayer(tileLayerRef.current);
    const layer = L.tileLayer(`https://{s}.basemaps.cartocdn.com/${theme}_all/{z}/{x}/{y}{r}.png`, {
      attribution: '© CARTO',
      subdomains: 'abcd',
      maxZoom: 19,
    });
    layer.addTo(map);
    tileLayerRef.current = layer;
    tileStyleRef.current = theme;
  }, [theme]);
  
  // focusTarget 变化 → flyTo
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !focusTarget) return;
    map.flyTo([focusTarget.lat, focusTarget.lng], focusTarget.zoom || defaultZoom, {
      duration: 1.2,
    });
  }, [focusTarget?.lat, focusTarget?.lng, focusTarget?.zoom]);
  
    // pins 变化 → 重新添加真实节点与节点间的最近邻覆盖路径
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    
    // 清旧 markers
      markersRef.current.forEach((m: any) => map.removeLayer(m));
      markersRef.current = [];
      networkRef.current.forEach((line: any) => map.removeLayer(line));
      networkRef.current = [];

      const networkColor = safeMapColor(accentColor, "#3f9bff");
      nearestPinEdges(pins).forEach(([from, to]) => {
        const points: [number, number][] = [
          [Number(from.lat), Number(from.lng)],
          [Number(to.lat), Number(to.lng)],
        ];
        const base = L.polyline(points, {
          color: networkColor,
          weight: 1.15,
          opacity: 0.28,
          dashArray: "5 9",
          className: "vkpi-map-network-line",
          interactive: false,
        }).addTo(map);
        const flow = L.polyline(points, {
          color: networkColor,
          weight: 2,
          opacity: 0.82,
          dashArray: "1 20",
          className: "vkpi-map-network-line vkpi-map-network-line--flow",
          interactive: false,
        }).addTo(map);
        networkRef.current.push(base, flow);
      });

      const validPins = pins.filter(finitePin);
      const useCanvasMarkers = validPins.length > MAX_DOM_MARKER_PIN_COUNT;
      validPins.forEach((p: any) => {
      const color = safeMapColor(p.color || accentColor, "#a855f7");

      // Thousands of HTML divIcon nodes make a national map janky. Leaflet's
      // canvas renderer keeps the same click/tooltip behaviour at high volume;
      // small maps retain the original branded animated marker.
      const marker = useCanvasMarkers
        ? L.circleMarker([Number(p.lat), Number(p.lng)], {
            radius: 4,
            color,
            weight: 1,
            fillColor: color,
            fillOpacity: 0.82,
          }).addTo(map)
        : L.marker([Number(p.lat), Number(p.lng)], {
            icon: L.divIcon({
              html: `<div class="vkpi-pin-wrapper" style="--pin-color: ${color}">
                       <div class="vkpi-pin-pulse"></div>
                       <div class="vkpi-pin-ring"></div>
                       <div class="vkpi-pin-dot"></div>
                     </div>`,
              className: '',
              iconSize: [30, 30],
              iconAnchor: [15, 15],
            }),
          }).addTo(map);
      
      // Hover 信息卡(DOM 覆盖层,隔离皮肤刀:头像渐变吃 token 随主题;
      // pin/连线本体的取色与渲染逻辑不动 —— 颜色由调用方传运行时 token 计算值)
      const handle = p.handle || p.name || "";
      const initials = handle.replace(/^@/, "").slice(0, 2).toUpperCase();
      const avatarBg = p.handle
        ? `linear-gradient(135deg, var(--ds-accent-2), var(--ds-accent))`
        : p.parentItem
        ? `linear-gradient(135deg, var(--ds-warn), var(--ds-crit))`
        : `linear-gradient(135deg, var(--ds-good), var(--ds-info))`;
      const platform = p.niche || p.type || (p.parentItem ? `Venue · ${p.parentItem}` : "");
      const location = p.city && p.country ? `${p.city}, ${p.country}` : p.country || "—";
      const metrics = p.handle
        ? `${p.followers || ""} followers · Eng. ${p.engagement || "—"}`
        : Number.isFinite(Number(p.count))
        ? `${Number(p.count).toLocaleString()} KOLs`
        : p.revenue
        ? `30d revenue ${p.revenue}`
        : p.note || "";
      const metricLabel = p.handle ? "Reach" : Number.isFinite(Number(p.count)) ? "KOLs" : p.revenue ? "Revenue" : "Note";
      
      const safeHandle = escapeMapHtml(handle);
      const safeInitials = escapeMapHtml(initials);
      const safePlatform = escapeMapHtml(platform);
      const safeLocation = escapeMapHtml(location);
      const safeMetrics = escapeMapHtml(metrics);
      const safeMetricLabel = escapeMapHtml(metricLabel);
      const tooltipHtml = `
        <div class="vkpi-pin-card">
          <div class="vkpi-pin-card-header">
            <div class="vkpi-pin-avatar" style="background: ${avatarBg}">${safeInitials}</div>
            <div class="vkpi-pin-info">
              <div class="vkpi-pin-name">${safeHandle}</div>
              ${platform ? `<div class="vkpi-pin-platform">${safePlatform}</div>` : ""}
            </div>
          </div>
          ${location !== "—" ? `<div class="vkpi-pin-row"><span class="vkpi-pin-key">Location</span><span class="vkpi-pin-val">${safeLocation}</span></div>` : ""}
          ${metrics ? `<div class="vkpi-pin-row"><span class="vkpi-pin-key">${safeMetricLabel}</span><span class="vkpi-pin-val" style="color: ${color}">${safeMetrics}</span></div>` : ""}
          <div class="vkpi-pin-hint">click for details →</div>
        </div>
      `;
      
      marker.bindTooltip(tooltipHtml, {
        direction: "top",
        offset: [0, -10],
        opacity: 1,
        className: "vkpi-pin-tooltip",
      });
      
      marker.on("click", () => {
        if (onPinClickRef.current) onPinClickRef.current(p);
      });
      
      markersRef.current.push(marker);
    });
  }, [pins, accentColor]);
  
  return (
    <div
      ref={containerRef}
      className="absolute inset-0 vkpi-map-wrapper"
      style={{ background: "var(--ds-bg-2)" }}
    />
  );
}
