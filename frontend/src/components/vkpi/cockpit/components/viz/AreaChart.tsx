// 数据可视化套件 · 面积/曲线图(gtm_viz 走势移植)
// SVG,响应式宽度,token 配色,面积渐变 + 强调末点 + 可选淡网格 + 生长动画(遵守 reduced-motion)。
import { useEffect, useMemo, useRef, useState } from "react";

export function AreaChart({
  data,
  color = "var(--ds-accent)",
  height = 120,
  grid = true,
  yTicks = 4,
  className,
}: {
  data: number[];
  color?: string;
  height?: number;
  grid?: boolean;
  yTicks?: number;
  className?: string;
}) {
  const reduce = typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const [grow, setGrow] = useState(reduce ? 1 : 0);
  const rafRef = useRef(0);
  const W = 300; // viewBox 单位,preserveAspectRatio=none 拉满
  const H = height;
  const pad = 6;

  useEffect(() => {
    if (reduce) return;
    const t0 = performance.now();
    const tick = (t: number) => {
      const p = Math.min((t - t0) / 800, 1);
      setGrow(p);
      if (p < 1) rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [reduce, data]);

  const { line, area, lastX, lastY, ticks } = useMemo(() => {
    if (!data || data.length === 0) return { line: "", area: "", lastX: 0, lastY: 0, ticks: [] as number[] };
    const max = Math.max(...data), min = Math.min(...data);
    const range = max - min || 1;
    const X = (i: number) => pad + (i / (data.length - 1)) * (W - 2 * pad);
    const Y = (v: number) => pad + (1 - (v - min) / range) * (H - 2 * pad);
    const pts = data.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`);
    const tickYs = Array.from({ length: yTicks }, (_, i) => pad + (i / (yTicks - 1)) * (H - 2 * pad));
    return {
      line: `M${pts.join(" L")}`,
      area: `M${pts.join(" L")} L${X(data.length - 1).toFixed(1)},${H} L${X(0).toFixed(1)},${H} Z`,
      lastX: X(data.length - 1),
      lastY: Y(data[data.length - 1]),
      ticks: tickYs,
    };
  }, [data, H, yTicks]);

  const gid = useMemo(() => "area-" + Math.abs(hash(color + data.join(","))), [color, data]);
  // stroke-dashoffset 生长:用一个足够大的 dash 长度
  const DASH = 2000;

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      className={className}
      style={{ width: "100%", height: H, display: "block" }}
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={color} stopOpacity="0.28" />
          <stop offset="1" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      {grid &&
        ticks.map((y, i) => (
          <line key={i} x1={pad} y1={y} x2={W - pad} y2={y} stroke="var(--ds-line)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        ))}
      <path d={area} fill={`url(#${gid})`} opacity={grow} />
      <path
        d={line}
        fill="none"
        stroke={color}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
        style={{ strokeDasharray: DASH, strokeDashoffset: DASH * (1 - grow) }}
      />
      {grow > 0.99 && <circle cx={lastX} cy={lastY} r="3" fill={color} vectorEffect="non-scaling-stroke" />}
    </svg>
  );
}

function hash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return h;
}
