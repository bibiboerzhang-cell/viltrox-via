// Verbatim from vkpi_v6.15.7_integrated.html


import { useId, useMemo } from "react";

export function Sparkline({ color, data, height = 36, width = 84, fluid = false }: any) {
  const path = useMemo(() => {
    const values = Array.isArray(data)
      ? data.filter((value: unknown): value is number => typeof value === "number" && Number.isFinite(value))
      : [];
    if (values.length < 2) return { line: "", area: "", last: null };
    const max = Math.max(...values), min = Math.min(...values);
    const range = max - min || 1;
    const points: Array<{ x: number; y: number; point: string }> = values.map((v: number, i: number) => {
      const x = (i / (values.length - 1)) * (width - 4) + 2;
      const y = height - 4 - ((v - min) / range) * (height - 8);
      return { x, y, point: `${x.toFixed(1)},${y.toFixed(1)}` };
    });
    const pointList = points.map((point) => point.point).join(" L");
    return {
      line: `M${pointList}`,
      area: `M${pointList} L${width - 2},${height} L2,${height} Z`,
      last: points[points.length - 1],
    };
  }, [data, height, width]);
  const gradId = `g-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  if (!path.last) return null;
  return (
    <svg
      className="ds-kpi__spark"
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
      <path className="ds-sparkline__area" d={path.area} fill={`url(#${gradId})`} />
      <path className="ds-sparkline__line" d={path.line} fill="none" stroke={color} strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      <path className="ds-sparkline__flow" d={path.line} fill="none" stroke={color} strokeWidth="2.8" strokeLinecap="round" pathLength="1" vectorEffect="non-scaling-stroke" />
      {path.last ? (
        <circle
          className="ds-sparkline__endpoint"
          cx={path.last.x}
          cy={path.last.y}
          r="2.8"
          fill={color}
          vectorEffect="non-scaling-stroke"
        />
      ) : null}
    </svg>
  );
}
