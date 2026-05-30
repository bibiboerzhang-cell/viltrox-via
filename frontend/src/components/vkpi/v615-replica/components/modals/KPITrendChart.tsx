// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useEffect, useRef, useState } from "react";

const e = React.createElement;

export function KPITrendChart({ data, color }) {
  const canvasRef = useRef(null);
  const [size, setSize] = useState({ w: 600, h: 120 });
  
  useEffect(() => {
    if (!canvasRef.current) return;
    const updateSize = () => {
      if (canvasRef.current) {
        setSize({ w: canvasRef.current.clientWidth, h: canvasRef.current.clientHeight });
      }
    };
    updateSize();
    const ro = new ResizeObserver(updateSize);
    ro.observe(canvasRef.current);
    return () => ro.disconnect();
  }, []);
  
  if (!data || data.length === 0) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const pad = 10;
  const w = size.w, h = size.h;
  const stepX = (w - pad * 2) / (data.length - 1);
  const points = data.map((v, i) => [
    pad + i * stepX,
    h - pad - ((v - min) / range) * (h - pad * 2),
  ]);
  const linePath = points.map((p, i) => `${i === 0 ? "M" : "L"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
  const areaPath = `${linePath} L${points[points.length - 1][0]},${h - pad} L${pad},${h - pad} Z`;
  
  return e("div", { ref: canvasRef, className: "h-full w-full" },
    e("svg", { viewBox: `0 0 ${w} ${h}`, className: "h-full w-full", preserveAspectRatio: "none" },
      e("defs", null,
        e("linearGradient", { id: "kpi-gradient", x1: "0", y1: "0", x2: "0", y2: "1" },
          e("stop", { offset: "0%", stopColor: color, stopOpacity: 0.3 }),
          e("stop", { offset: "100%", stopColor: color, stopOpacity: 0 })
        )
      ),
      e("path", { d: areaPath, fill: "url(#kpi-gradient)" }),
      e("path", { d: linePath, fill: "none", stroke: color, strokeWidth: 1.5, strokeLinecap: "round", strokeLinejoin: "round" }),
      // 最后一个点
      e("circle", { 
        cx: points[points.length - 1][0], 
        cy: points[points.length - 1][1], 
        r: 3, 
        fill: color 
      })
    )
  );
}
