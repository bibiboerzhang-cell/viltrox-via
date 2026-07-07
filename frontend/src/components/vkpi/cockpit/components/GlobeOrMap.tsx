// Verbatim from vkpi_v6.15.7_integrated.html


import { AnimatePresence } from "framer-motion";
import { Globe } from "./Globe";
import { RealMap } from "./RealMap";

export function GlobeOrMap({ pins, accentColor, onPinClick, focusTarget, useRealMap, mapZoom }: any) {
  // V5.2: 不再用 AnimatePresence(它会 mount/unmount 导致 Leaflet 死亡)
  // 改成两个组件常驻,只用 opacity + pointer-events 切换可见性
  return (
    <div className="absolute inset-0">
      {/* 3D Globe 层(始终存在,只是 City+ 时不可见) */}
      <div
        className="absolute inset-0 transition-opacity duration-500"
        style={{
          opacity: useRealMap ? 0 : 1,
          pointerEvents: useRealMap ? "none" : "auto",
          zIndex: useRealMap ? 1 : 2,
        }}
      >
        <Globe pins={useRealMap ? [] : pins} accentColor={accentColor} onPinClick={onPinClick} focusTarget={null} />
      </div>
      {/* 2D 真实地图层(始终存在,只是 World/Country 时不可见) */}
      <div
        className="absolute inset-0 transition-opacity duration-500"
        style={{
          opacity: useRealMap ? 1 : 0,
          pointerEvents: useRealMap ? "auto" : "none",
          zIndex: useRealMap ? 2 : 1,
        }}
      >
        <RealMap pins={useRealMap ? pins : []} accentColor={accentColor} onPinClick={onPinClick} focusTarget={focusTarget} defaultZoom={mapZoom} />
      </div>
    </div>
  );
}
