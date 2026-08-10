// Verbatim from vkpi_v6.15.7_integrated.html


import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { feature } from "topojson-client";
import { useTheme } from "../../../../app/providers/ThemeProvider";

export type RealMapBasemapMode = "local" | "online";

export const DEFAULT_REAL_MAP_BASEMAP_MODE: RealMapBasemapMode = "local";
export const LOCAL_WORLD_TOPOLOGY_URL = "/data/world-110m.json";

export function shouldRequestExternalBasemap(mode: RealMapBasemapMode) {
  return mode === "online";
}

export function cartoTileUrl(theme: unknown) {
  const style = theme === "dark" ? "dark" : "light";
  return `https://{s}.basemaps.cartocdn.com/${style}_all/{z}/{x}/{y}{r}.png`;
}

export function basemapModeAfterTileError(mode: RealMapBasemapMode): RealMapBasemapMode {
  return mode === "online" ? "local" : mode;
}

export function worldTopologyToGeoJson(topology: any) {
  const countryObject = topology?.objects?.countries;
  if (!countryObject) throw new Error("world topology is missing countries");
  return feature(topology, countryObject);
}

function finitePin(pin: any) {
  return Number.isFinite(Number(pin?.lat)) && Number.isFinite(Number(pin?.lng));
}

// The lines are decorative context, not business evidence.  An all-US Dealer
// map can contain thousands of pins, so an O(n²) nearest-neighbour pass would
// freeze the UI before Leaflet can paint the actual locations.  Keep the exact
// small-map behaviour and fail closed (no decorative network) at large scale.
export const MAX_NETWORK_LINE_PIN_COUNT = 250;
export const MAX_DOM_MARKER_PIN_COUNT = 1_000;
export const LOW_ZOOM_CLUSTER_MAX = 7;
export const MAP_CLUSTER_GRID_SIZE = 36;
export const NETWORK_LINE_MIN_ZOOM = 8;

export interface MapPinCluster {
  key: string;
  lat: number;
  lng: number;
  members: any[];
  count: number;
  exactCoordinates: boolean;
}

type ProjectPin = (pin: any) => { x: number; y: number };

function stablePinKey(pin: any) {
  return [
    pin?.id ?? pin?.dealer_id ?? pin?.event_id ?? pin?.key ?? pin?.handle ?? pin?.name ?? "",
    Number(pin?.lat).toFixed(7),
    Number(pin?.lng).toFixed(7),
    pin?.type ?? pin?.niche ?? "",
  ].map((value) => String(value)).join("\u0001");
}

function coordinateKey(pin: any) {
  return `${Number(pin.lat).toFixed(7)}:${Number(pin.lng).toFixed(7)}`;
}

/**
 * Build display-only clusters without changing any source coordinate.
 *
 * At low zoom, pins share a deterministic projected-pixel grid.  Above the
 * threshold, only exact-coordinate duplicates remain grouped so two Dealer
 * records at the same address never silently cover each other.
 */
export function clusterPinsForZoom(
  pins: any[],
  project: ProjectPin,
  zoom: number,
  gridSize = MAP_CLUSTER_GRID_SIZE,
): MapPinCluster[] {
  const valid = (Array.isArray(pins) ? pins : [])
    .filter(finitePin)
    .slice()
    .sort((left, right) => stablePinKey(left).localeCompare(stablePinKey(right)));
  const lowZoom = Number(zoom) <= LOW_ZOOM_CLUSTER_MAX;
  const safeGridSize = Number.isFinite(gridSize) && gridSize > 0 ? gridSize : MAP_CLUSTER_GRID_SIZE;
  const groups = new Map<string, any[]>();

  if (lowZoom) {
    const projected = valid.map((pin) => {
      const point = project(pin);
      return { pin, x: Number(point.x), y: Number(point.y) };
    });
    const parents = projected.map((_, index) => index);
    const find = (index: number): number => {
      let root = index;
      while (parents[root] !== root) root = parents[root];
      while (parents[index] !== index) {
        const next = parents[index];
        parents[index] = root;
        index = next;
      }
      return root;
    };
    const union = (left: number, right: number) => {
      const leftRoot = find(left);
      const rightRoot = find(right);
      if (leftRoot === rightRoot) return;
      parents[Math.max(leftRoot, rightRoot)] = Math.min(leftRoot, rightRoot);
    };
    const cells = new Map<string, number[]>();
    const maxDistanceSquared = safeGridSize * safeGridSize;

    projected.forEach((point, index) => {
      const cellX = Math.floor(point.x / safeGridSize);
      const cellY = Math.floor(point.y / safeGridSize);
      for (let xOffset = -1; xOffset <= 1; xOffset += 1) {
        for (let yOffset = -1; yOffset <= 1; yOffset += 1) {
          const candidates = cells.get(`${cellX + xOffset}:${cellY + yOffset}`) || [];
          candidates.forEach((candidateIndex) => {
            const candidate = projected[candidateIndex];
            const xDelta = point.x - candidate.x;
            const yDelta = point.y - candidate.y;
            if (xDelta * xDelta + yDelta * yDelta <= maxDistanceSquared) {
              union(index, candidateIndex);
            }
          });
        }
      }
      const cellKey = `${cellX}:${cellY}`;
      const cell = cells.get(cellKey) || [];
      cell.push(index);
      cells.set(cellKey, cell);
    });

    projected.forEach(({ pin }, index) => {
      const key = `proximity:${find(index)}`;
      const members = groups.get(key) || [];
      members.push(pin);
      groups.set(key, members);
    });
  } else {
    valid.forEach((pin) => {
      const key = `coordinate:${coordinateKey(pin)}`;
      const members = groups.get(key) || [];
      members.push(pin);
      groups.set(key, members);
    });
  }

  return Array.from(groups.entries())
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([bucketKey, rawMembers]) => {
      const members = rawMembers.slice().sort((left, right) => stablePinKey(left).localeCompare(stablePinKey(right)));
      const lat = members.reduce((sum, pin) => sum + Number(pin.lat), 0) / members.length;
      const lng = members.reduce((sum, pin) => sum + Number(pin.lng), 0) / members.length;
      const exactCoordinates = new Set(members.map(coordinateKey)).size === 1;
      return {
        key: `${bucketKey.split(":")[0]}:${members.map(stablePinKey).join("|")}`,
        lat,
        lng,
        members,
        count: members.length,
        exactCoordinates,
      };
    });
}

export function clusterClickAction(cluster: MapPinCluster) {
  if (cluster.count <= 1) return "pin" as const;
  return cluster.exactCoordinates ? "members" as const : "zoom" as const;
}

export function shouldShowNetworkLines(zoom: number, pins: any[]) {
  const count = (Array.isArray(pins) ? pins : []).filter(finitePin).length;
  return Number(zoom) >= NETWORK_LINE_MIN_ZOOM && count > 1 && count <= MAX_NETWORK_LINE_PIN_COUNT;
}

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

// 地图打点/连线的颜色最终喂给 Leaflet(canvas circleMarker 的 2D 上下文、SVG polyline
// 属性),这些出口不解析 CSS var(),必须给具体色值 —— 这是 canvas 场景的硬限制,
// 没法像普通 DOM 一样直接写 var(--ds-accent)。所以运行时从 :root 计算样式读 token,
// 主题切换后重渲染即取到新值;读不到(测试 jsdom 无样式表/极早期渲染)才退回原写死兜底色。
export function readMapCssToken(name: string, fallback: string) {
  try {
    if (typeof window === "undefined" || typeof document === "undefined") return fallback;
    const value = window.getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  } catch {
    return fallback;
  }
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
  const localLayerRef = useRef<any>(null);
  const tileLayerRef = useRef<any>(null);
  const tileErrorHandlerRef = useRef<(() => void) | null>(null);
  const tileStyleRef = useRef<"light" | "dark" | null>(null);
  const markersRef = useRef<any[]>([]);
  const networkRef = useRef<any[]>([]);
  const onPinClickRef = useRef(onPinClick);
  const [basemapMode, setBasemapMode] = useState<RealMapBasemapMode>(DEFAULT_REAL_MAP_BASEMAP_MODE);
  const [basemapNotice, setBasemapNotice] = useState("");
  const [worldGeoJson, setWorldGeoJson] = useState<any>(null);
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
    
    mapRef.current = map;
    
    // 一次 invalidateSize 确保尺寸
    const resizeTimer = window.setTimeout(() => map.invalidateSize(), 100);
    return () => {
      window.clearTimeout(resizeTimer);
      markersRef.current = [];
      networkRef.current = [];
      if (tileLayerRef.current && tileErrorHandlerRef.current) {
        tileLayerRef.current.off("tileerror", tileErrorHandlerRef.current);
      }
      localLayerRef.current = null;
      tileLayerRef.current = null;
      tileErrorHandlerRef.current = null;
      tileStyleRef.current = null;
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // 默认只读同源的世界 TopoJSON，避免 Dealers 首屏把第三方瓦片可用性
  // 变成整页的隐性前置条件。转换后由 Leaflet geoJSON 绘制，pin/zoom/cluster 层不变。
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    fetch(LOCAL_WORLD_TOPOLOGY_URL, { cache: "force-cache", signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`world topology HTTP ${response.status}`);
        return response.json();
      })
      .then((topology) => {
        if (!cancelled) setWorldGeoJson(worldTopologyToGeoJson(topology));
      })
      .catch((error) => {
        if (cancelled || error?.name === "AbortError") return;
        setBasemapNotice("本地世界底图暂时不可用；真实点位与筛选仍可正常使用。");
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, []);

  // 在线街道底图只有用户显式开启后才创建瓦片层。任一 CARTO tileerror
  // 立即移除在线层并回到已缓存的本地世界底图，不吞掉故障。
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const localStyle = theme === "dark"
      ? {
          color: readMapCssToken("--ds-line", "#334155"),
          weight: 0.7,
          opacity: 0.8,
          fillColor: readMapCssToken("--ds-bg-2", "#172033"),
          fillOpacity: 0.78,
        }
      : {
          color: readMapCssToken("--ds-line", "#cbd5e1"),
          weight: 0.7,
          opacity: 0.85,
          fillColor: readMapCssToken("--ds-bg-2", "#e8eef6"),
          fillOpacity: 0.9,
        };

    if (!shouldRequestExternalBasemap(basemapMode)) {
      if (tileLayerRef.current) {
        if (tileErrorHandlerRef.current) {
          tileLayerRef.current.off("tileerror", tileErrorHandlerRef.current);
        }
        map.removeLayer(tileLayerRef.current);
        tileLayerRef.current = null;
        tileErrorHandlerRef.current = null;
        tileStyleRef.current = null;
      }

      if (worldGeoJson) {
        if (!localLayerRef.current) {
          localLayerRef.current = L.geoJSON(worldGeoJson, {
            interactive: false,
            // Keep the country geometry below both DOM markers and the
            // high-volume canvas marker renderer, regardless of fetch timing.
            pane: "tilePane",
            style: localStyle,
          });
        } else {
          localLayerRef.current.setStyle(localStyle);
        }
        if (typeof map.hasLayer !== "function" || !map.hasLayer(localLayerRef.current)) {
          localLayerRef.current.addTo(map);
        }
      }
      return;
    }

    if (localLayerRef.current) {
      // Keep the hidden fallback in the current theme so tileerror can reveal
      // it synchronously without a light/dark flash before React rerenders.
      localLayerRef.current.setStyle(localStyle);
      if (typeof map.hasLayer !== "function" || map.hasLayer(localLayerRef.current)) {
        map.removeLayer(localLayerRef.current);
      }
    }

    const normalizedTheme = theme === "dark" ? "dark" : "light";
    if (tileLayerRef.current && tileStyleRef.current === normalizedTheme) return;
    if (tileLayerRef.current) {
      if (tileErrorHandlerRef.current) tileLayerRef.current.off("tileerror", tileErrorHandlerRef.current);
      map.removeLayer(tileLayerRef.current);
    }

    const layer = L.tileLayer(cartoTileUrl(normalizedTheme), {
      attribution: "© CARTO",
      subdomains: "abcd",
      maxZoom: 19,
    });
    const handleTileError = () => {
      if (tileLayerRef.current !== layer) return;
      layer.off("tileerror", handleTileError);
      map.removeLayer(layer);
      tileLayerRef.current = null;
      tileErrorHandlerRef.current = null;
      tileStyleRef.current = null;
      if (localLayerRef.current) localLayerRef.current.addTo(map);
      setBasemapNotice("在线街道底图加载失败，已回退到本地世界底图；真实点位与筛选不受影响。");
      setBasemapMode(basemapModeAfterTileError("online"));
    };
    layer.on("tileerror", handleTileError);
    layer.addTo(map);
    tileLayerRef.current = layer;
    tileErrorHandlerRef.current = handleTileError;
    tileStyleRef.current = normalizedTheme;
  }, [basemapMode, theme, worldGeoJson]);
  
  // focusTarget 变化 → flyTo
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !focusTarget) return;
    map.flyTo([focusTarget.lat, focusTarget.lng], focusTarget.zoom || defaultZoom, {
      duration: 1.2,
    });
  }, [focusTarget?.lat, focusTarget?.lng, focusTarget?.zoom]);
  
    // pins / zoom 变化 → 重新生成显示簇。低缩放只显示簇，不伪改业务坐标。
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const sourcePins = Array.isArray(pins) ? pins : [];

    const clearDisplayLayers = () => {
      markersRef.current.forEach((m: any) => map.removeLayer(m));
      markersRef.current = [];
      networkRef.current.forEach((line: any) => map.removeLayer(line));
      networkRef.current = [];
    };

    const pinLabel = (pin: any, index: number) => String(
      pin?.handle || pin?.name || pin?.city || pin?.country || `Point ${index + 1}`,
    );

    const buildMembersPopup = (cluster: MapPinCluster) => {
      const root = document.createElement("div");
      root.className = "vkpi-map-cluster-members";

      const heading = document.createElement("div");
      heading.className = "vkpi-map-cluster-members__title";
      heading.textContent = `${cluster.count} 个同坐标点位`;
      root.appendChild(heading);

      cluster.members.forEach((member, index) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "vkpi-map-cluster-members__item";
        button.textContent = pinLabel(member, index);
        button.addEventListener("click", () => {
          if (onPinClickRef.current) onPinClickRef.current(member);
          map.closePopup();
        });
        root.appendChild(button);
      });

      return root;
    };

    const renderDisplayLayers = () => {
      clearDisplayLayers();

      const zoom = map.getZoom();
      // 兜底色改为运行时读 token(--ds-accent / --ds-accent-2),原 hex 只在读不到时垫底。
      const accentFallback = readMapCssToken("--ds-accent", "#3f9bff");
      const accent2Fallback = readMapCssToken("--ds-accent-2", "#a855f7");
      const networkColor = safeMapColor(accentColor, accentFallback);
      if (shouldShowNetworkLines(zoom, sourcePins)) {
        nearestPinEdges(sourcePins).forEach(([from, to]) => {
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
      }

      const validPins = sourcePins.filter(finitePin);
      const useCanvasMarkers = validPins.length > MAX_DOM_MARKER_PIN_COUNT;
      const clusters = clusterPinsForZoom(
        validPins,
        (pin) => map.project(L.latLng(Number(pin.lat), Number(pin.lng)), zoom),
        zoom,
      );

      clusters.forEach((cluster) => {
        const action = clusterClickAction(cluster);
        if (action !== "pin") {
          const first = cluster.members[0] || {};
          const color = safeMapColor(first.color || accentColor, accent2Fallback);
          const countLabel = cluster.count > 999 ? "999+" : String(cluster.count);
          const marker = L.marker([cluster.lat, cluster.lng], {
            icon: L.divIcon({
              html: `<div class="vkpi-map-cluster" style="--cluster-color: ${color}" role="button" aria-label="${cluster.count} map points"><span>${countLabel}</span></div>`,
              className: "",
              iconSize: [34, 34],
              iconAnchor: [17, 17],
            }),
          }).addTo(map);

          const tooltipTitle = action === "members"
            ? `${cluster.count} 个同坐标点位`
            : `${cluster.count} 个附近点位`;
          const tooltipHint = action === "members" ? "点击选择具体记录" : "点击放大地图";
          marker.bindTooltip(
            `<div class="vkpi-pin-card vkpi-pin-card--cluster"><div class="vkpi-pin-name">${escapeMapHtml(tooltipTitle)}</div><div class="vkpi-pin-hint">${escapeMapHtml(tooltipHint)} →</div></div>`,
            { direction: "top", offset: [0, -10], opacity: 1, className: "vkpi-pin-tooltip" },
          );

          if (action === "members") {
            marker.bindPopup(buildMembersPopup(cluster), {
              className: "vkpi-map-cluster-popup",
              closeButton: true,
              maxWidth: 320,
            });
          }

          marker.on("click", () => {
            if (action === "members") {
              marker.openPopup();
              return;
            }
            const bounds = L.latLngBounds(
              cluster.members.map((member) => [Number(member.lat), Number(member.lng)] as [number, number]),
            );
            map.fitBounds(bounds, { padding: [48, 48], maxZoom: 18 });
          });

          markersRef.current.push(marker);
          return;
        }

        const p = cluster.members[0];
        const color = safeMapColor(p.color || accentColor, accent2Fallback);

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
                className: "",
                iconSize: [24, 24],
                iconAnchor: [12, 12],
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
    };

    renderDisplayLayers();
    map.on("zoomend moveend", renderDisplayLayers);
    return () => {
      map.off("zoomend moveend", renderDisplayLayers);
      clearDisplayLayers();
    };
  }, [pins, accentColor]);
  
  return (
    <div className="absolute inset-0 vkpi-map-wrapper" style={{ background: "var(--ds-bg-2)" }}>
      <div ref={containerRef} className="absolute inset-0" />
      <div className="absolute bottom-3 right-3 z-[1000] flex max-w-[min(24rem,calc(100%-1.5rem))] flex-col items-end gap-1.5">
        <button
          type="button"
          aria-pressed={basemapMode === "online"}
          className="rounded-md border border-[var(--ds-line)] bg-[var(--ds-panel)] px-2.5 py-1.5 text-xs font-medium text-[var(--ds-text)] shadow-sm backdrop-blur hover:bg-[var(--ds-bg-2)]"
          onClick={() => {
            const nextMode = basemapMode === "online" ? "local" : "online";
            setBasemapMode(nextMode);
            setBasemapNotice(nextMode === "online"
              ? "已开启在线街道底图；网络异常时会自动回退本地底图。"
              : "已切换到本地世界底图。");
          }}
        >
          {basemapMode === "online" ? "本地世界底图" : "在线街道底图"}
        </button>
        {basemapNotice ? (
          <div
            role="status"
            className="rounded-md border border-[var(--ds-line)] bg-[var(--ds-panel)] px-2.5 py-1.5 text-[11px] leading-4 text-[var(--ds-text-2)] shadow-sm backdrop-blur"
          >
            {basemapNotice}
          </div>
        ) : null}
      </div>
    </div>
  );
}
