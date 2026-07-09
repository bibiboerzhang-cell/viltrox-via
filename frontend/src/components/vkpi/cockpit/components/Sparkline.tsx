// Verbatim from vkpi_v6.15.7_integrated.html


import { useMemo } from "react";

export function Sparkline({ color, data, height = 36, width = 84, fluid = false }: any) {
  const path = useMemo(() => {
    if (!data || data.length === 0) return { line: "", area: "" };
    const max = Math.max(...data), min = Math.min(...data);
    const range = max - min || 1;
    const points = data.map((v: any, i: any) => {
      const x = (i / (data.length - 1)) * (width - 4) + 2;
      const y = height - 4 - ((v - min) / range) * (height - 8);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    return { line: `M${points.join(" L")}`, area: `M${points.join(" L")} L${width - 2},${height} L2,${height} Z` };
  }, [data, height, width]);
  const gradId = `g-${(color ?? "").replace("#", "")}-${data?.[0] ?? 0}`;
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio={fluid ? "none" : undefined}
      style={fluid ? { width: "100%", height, display: "block" } : { width, height }}
    >
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={color} stopOpacity="0.42" />
          <stop offset="1" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={path.area} fill={`url(#${gradId})`} />
      <path d={path.line} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
