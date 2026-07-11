// Verbatim from vkpi_v6.15.7_integrated.html


import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useTheme } from "../../../../app/providers/ThemeProvider";

function finitePin(pin: any) {
  return Number.isFinite(Number(pin?.lat)) && Number.isFinite(Number(pin?.lng));
}

function nearestPinEdges(pins: any[]) {
  const valid = pins.filter(finitePin);
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
    
    const map = L.map(container).setView([initialLat, initialLng], initialZoom);
    
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

      const networkColor = accentColor || "#3f9bff";
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

      pins.filter(finitePin).forEach((p: any) => {
      const color = p.color || accentColor || "#a855f7";
      
      const icon = L.divIcon({
        html: `<div class="vkpi-pin-wrapper" style="--pin-color: ${color}">
                 <div class="vkpi-pin-pulse"></div>
                 <div class="vkpi-pin-ring"></div>
                 <div class="vkpi-pin-dot"></div>
               </div>`,
        className: '',
        iconSize: [30, 30],
        iconAnchor: [15, 15],
      });
      
      const marker = L.marker([p.lat, p.lng], { icon }).addTo(map);
      
      // Hover 信息卡
      const handle = p.handle || p.name || "";
      const initials = handle.replace(/^@/, "").slice(0, 2).toUpperCase();
      const avatarBg = p.handle 
        ? `linear-gradient(135deg, #8b5cf6, #3b82f6)` 
        : p.parentItem 
        ? `linear-gradient(135deg, #f59e0b, #ec4899)` 
        : `linear-gradient(135deg, #10b981, #06b6d4)`;
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
      
      const tooltipHtml = `
        <div class="vkpi-pin-card">
          <div class="vkpi-pin-card-header">
            <div class="vkpi-pin-avatar" style="background: ${avatarBg}">${initials}</div>
            <div class="vkpi-pin-info">
              <div class="vkpi-pin-name">${handle}</div>
              ${platform ? `<div class="vkpi-pin-platform">${platform}</div>` : ""}
            </div>
          </div>
          ${location !== "—" ? `<div class="vkpi-pin-row"><span class="vkpi-pin-key">Location</span><span class="vkpi-pin-val">${location}</span></div>` : ""}
          ${metrics ? `<div class="vkpi-pin-row"><span class="vkpi-pin-key">${metricLabel}</span><span class="vkpi-pin-val" style="color: ${color}">${metrics}</span></div>` : ""}
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
