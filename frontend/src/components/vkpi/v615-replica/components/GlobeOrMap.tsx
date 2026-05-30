// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { AnimatePresence } from "framer-motion";
import { Globe } from "lucide-react";
import { Globe } from "./Globe";
import { RealMap } from "./RealMap";

const e = React.createElement;

export function GlobeOrMap({ pins, accentColor, onPinClick, focusTarget, useRealMap, mapZoom }) {
  // V5.2: 不再用 AnimatePresence(它会 mount/unmount 导致 Leaflet 死亡)
  // 改成两个组件常驻,只用 opacity + pointer-events 切换可见性
  return e("div", { className: "absolute inset-0" },
    // 3D Globe 层(始终存在,只是 City+ 时不可见)
    e("div", {
      className: "absolute inset-0 transition-opacity duration-500",
      style: {
        opacity: useRealMap ? 0 : 1,
        pointerEvents: useRealMap ? "none" : "auto",
        zIndex: useRealMap ? 1 : 2,
      }
    },
      e(Globe, { pins: useRealMap ? [] : pins, accentColor, onPinClick, focusTarget: null })
    ),
    // 2D 真实地图层(始终存在,只是 World/Country 时不可见)
    e("div", {
      className: "absolute inset-0 transition-opacity duration-500",
      style: {
        opacity: useRealMap ? 1 : 0,
        pointerEvents: useRealMap ? "auto" : "none",
        zIndex: useRealMap ? 2 : 1,
      }
    },
      e(RealMap, { pins: useRealMap ? pins : [], accentColor, onPinClick, focusTarget, defaultZoom: mapZoom })
    )
  );
}
