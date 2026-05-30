// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useMemo } from "react";
import { motion } from "framer-motion";

const e = React.createElement;

export function MiniTrend({ series }) {
  const w = 240, h = 110, padding = 6;
  const paths = useMemo(() => {
    if (!series || series.length === 0) return [];
    const allValues = series.flatMap((s) => s.data);
    const max = Math.max(...allValues), min = Math.min(...allValues);
    const range = max - min || 1;
    return series.map((s) => {
      const pts = s.data.map((v, i) => {
        const x = (i / (s.data.length - 1)) * (w - 2 * padding) + padding;
        const y = h - padding - ((v - min) / range) * (h - 2 * padding);
        return { x, y };
      });
      const d = pts.map((p, i) => (i === 0 ? `M${p.x.toFixed(1)} ${p.y.toFixed(1)}` : `L${p.x.toFixed(1)} ${p.y.toFixed(1)}`)).join(" ");
      return { ...s, d, points: pts };
    });
  }, [series]);
  return e("svg", { viewBox: `0 0 ${w} ${h}`, className: "h-full w-full" },
    [20,40,60,80,100].map((y) => e("line", { key: y, x1: 0, y1: y, x2: w, y2: y, stroke: "rgba(148,163,184,.08)", strokeWidth: 0.5 })),
    paths.map((s, i) => e(motion.path, {
      key: s.name, d: s.d, fill: "none", stroke: s.color, strokeWidth: 2, strokeLinecap: "round",
      initial: { pathLength: 0, opacity: 0 }, animate: { pathLength: 1, opacity: 1 },
      transition: { duration: 1.2, delay: 0.3 + i * 0.15, ease: "easeOut" },
    })),
    paths[0]?.points.map((p, i) => e("circle", { key: i, cx: p.x, cy: p.y, r: 2, fill: "#fff", opacity: i === paths[0].points.length - 1 ? 1 : 0.4 }))
  );
}
