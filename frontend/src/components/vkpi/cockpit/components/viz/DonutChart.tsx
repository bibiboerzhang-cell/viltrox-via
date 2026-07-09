// 数据可视化套件 · 饼/环图(gtm_viz 预算环移植)
// SVG,全吃 --ds-* token,分段间留缝,可选中心大标 + 图例。
import { useMemo } from "react";

export interface DonutSegment {
  label: string;
  value: number;
  color?: string; // 允许覆盖,默认走 --ds-* 调色轮
}

const PALETTE = ["var(--ds-accent)", "var(--ds-accent-2)", "var(--ds-good)", "var(--ds-warn)", "var(--ds-info)", "var(--ds-crit)"];

export function DonutChart({
  segments,
  size = 128,
  thickness = 16,
  centerTop,
  centerSub,
  legend = false,
  gap = 0.04,
}: {
  segments: DonutSegment[];
  size?: number;
  thickness?: number;
  centerTop?: string;
  centerSub?: string;
  legend?: boolean;
  gap?: number;
}) {
  const total = useMemo(() => segments.reduce((a, s) => a + (s.value || 0), 0) || 1, [segments]);
  const r = (size - thickness) / 2;
  const c = size / 2;
  const circ = 2 * Math.PI * r;

  let acc = 0;
  const arcs = segments.map((s, i) => {
    const frac = (s.value || 0) / total;
    const dash = Math.max(frac * circ - gap * circ, 0);
    const node = {
      key: i,
      color: s.color || PALETTE[i % PALETTE.length],
      dasharray: `${dash} ${circ - dash}`,
      dashoffset: -(acc * circ),
    };
    acc += frac;
    return node;
  });

  const svg = (
    <svg viewBox={`0 0 ${size} ${size}`} style={{ width: size, height: size, flex: "none" }} aria-hidden="true">
      <circle cx={c} cy={c} r={r} fill="none" stroke="var(--ds-line)" strokeWidth={thickness} />
      {arcs.map((a) => (
        <circle
          key={a.key}
          cx={c}
          cy={c}
          r={r}
          fill="none"
          stroke={a.color}
          strokeWidth={thickness}
          strokeDasharray={a.dasharray}
          strokeDashoffset={a.dashoffset}
          strokeLinecap="butt"
          transform={`rotate(-90 ${c} ${c})`}
        />
      ))}
      {centerTop && (
        <text x={c} y={c - 3} textAnchor="middle" style={{ fill: "var(--ds-text)", font: "680 20px var(--ds-font-sans)" }}>
          {centerTop}
        </text>
      )}
      {centerSub && (
        <text x={c} y={c + 14} textAnchor="middle" style={{ fill: "var(--ds-muted)", font: "500 9px var(--ds-font-sans)" }}>
          {centerSub}
        </text>
      )}
    </svg>
  );

  if (!legend) return svg;

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
      {svg}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 9, minWidth: 0 }}>
        {segments.map((s, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11.5 }}>
            <span style={{ width: 9, height: 9, borderRadius: 3, background: s.color || PALETTE[i % PALETTE.length], flex: "none" }} />
            <span style={{ flex: 1, color: "var(--ds-text-2)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.label}</span>
            <span style={{ fontWeight: 600, color: "var(--ds-text)", fontVariantNumeric: "tabular-nums" }}>{s.value.toLocaleString()}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
