// 数据可视化套件 · 预测扇形带图(gtm_viz 核心「动态预演」移植)
// 历史实线+面积 / 预测虚线中位 / 置信扇形带(随时间张开)/ 加码·撤退水平线 / 现在竖线。
// ResizeObserver 取真实宽度(不失真),带从"现在"向右生长的入场动画(遵守 reduced-motion)。
import { useEffect, useMemo, useRef, useState } from "react";

export interface ForecastChartProps {
  history: number[];        // 历史真实值
  forecastMid: number[];    // 预测中位(首点≈history末点)
  spread: number[];         // 各预测点置信半宽(与 forecastMid 等长)
  escalate?: { value: number; label?: string };
  retreat?: { value: number; label?: string };
  vmin?: number;
  vmax?: number;
  height?: number;
  unit?: string;            // y 轴刻度后缀,如 "%"
  nowLabel?: string;
  startLabel?: string;
  endLabel?: string;
}

export function ForecastChart(props: ForecastChartProps) {
  const { history, forecastMid, spread, escalate, retreat, height = 230, unit = "", nowLabel = "现在", startLabel, endLabel } = props;
  const wrapRef = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(600);
  const reduce = typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const [grow, setGrow] = useState(reduce ? 1 : 0);
  const rafRef = useRef(0);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((ents) => setW(Math.max(240, ents[0].contentRect.width || 600)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (reduce) { setGrow(1); return; }
    const t0 = performance.now();
    const tick = (t: number) => {
      const p = Math.min((t - t0) / 900, 1);
      setGrow(p);
      if (p < 1) rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [reduce, W, history, forecastMid]);

  const g = useMemo(() => {
    const H = height, padL = 8, padR = 12, padT = 14, padB = 22;
    const innerW = W - padL - padR, innerH = H - padT - padB;
    const nowIdx = history.length - 1;
    const total = Math.max(1, history.length + forecastMid.length - 1);
    const all = [...history, ...forecastMid, ...forecastMid.map((m, i) => m + (spread[i] || 0)), ...forecastMid.map((m, i) => m - (spread[i] || 0))];
    if (escalate) all.push(escalate.value);
    if (retreat) all.push(retreat.value);
    const lo = props.vmin ?? Math.min(...all);
    const hi = props.vmax ?? Math.max(...all);
    const span = hi - lo || 1;
    const X = (i: number) => padL + (i / total) * innerW;
    const Y = (v: number) => padT + (1 - (v - lo) / span) * innerH;
    const yTicks = Array.from({ length: 5 }, (_, i) => lo + (i / 4) * span);
    return { H, padL, padR, padT, padB, innerW, innerH, nowIdx, total, lo, hi, X, Y, yTicks };
  }, [W, height, history, forecastMid, spread, escalate, retreat, props.vmin, props.vmax]);

  const fN = forecastMid.length;
  const shown = reduce ? fN : 1 + Math.round((fN - 1) * grow);

  // 历史 area + line
  const histPts = history.map((v, i) => `${g.X(i).toFixed(1)},${g.Y(v).toFixed(1)}`);
  const histLine = `M${histPts.join(" L")}`;
  const histArea = `${histLine} L${g.X(g.nowIdx).toFixed(1)},${(g.padT + g.innerH).toFixed(1)} L${g.X(0).toFixed(1)},${(g.padT + g.innerH).toFixed(1)} Z`;

  // 置信带(上界正序 + 下界逆序)
  const upper: string[] = [], lower: string[] = [];
  for (let i = 0; i < shown; i++) { const gi = g.nowIdx + i; upper.push(`${g.X(gi).toFixed(1)},${g.Y(forecastMid[i] + (spread[i] || 0)).toFixed(1)}`); }
  for (let i = shown - 1; i >= 0; i--) { const gi = g.nowIdx + i; lower.push(`${g.X(gi).toFixed(1)},${g.Y(forecastMid[i] - (spread[i] || 0)).toFixed(1)}`); }
  const band = shown > 0 ? `M${upper.concat(lower).join(" L")} Z` : "";

  // 预测中位虚线
  const midPts: string[] = [];
  for (let i = 0; i < shown; i++) { const gi = g.nowIdx + i; midPts.push(`${g.X(gi).toFixed(1)},${g.Y(forecastMid[i]).toFixed(1)}`); }
  const midLine = midPts.length ? `M${midPts.join(" L")}` : "";

  const nowX = g.X(g.nowIdx);
  const gid = "fc-" + history.length + "-" + forecastMid.length;

  return (
    <div ref={wrapRef} style={{ width: "100%" }}>
      <svg viewBox={`0 0 ${W} ${g.H}`} width={W} height={g.H} style={{ display: "block", maxWidth: "100%" }} aria-hidden="true">
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="var(--ds-accent)" stopOpacity="0.16" />
            <stop offset="1" stopColor="var(--ds-accent)" stopOpacity="0" />
          </linearGradient>
        </defs>
        {/* 网格 + y 标 */}
        {g.yTicks.map((v, i) => (
          <g key={i}>
            <line x1={g.padL} y1={g.Y(v)} x2={g.padL + g.innerW} y2={g.Y(v)} stroke="var(--ds-line)" strokeWidth="1" />
            <text x={g.padL + 2} y={g.Y(v) - 4} style={{ fill: "var(--ds-muted)", font: "9px var(--ds-font-sans)" }}>{v.toFixed(0)}{unit}</text>
          </g>
        ))}
        {/* 加码 / 撤退 线 */}
        {escalate && <ReferenceLine x={g.padL} w={g.innerW} y={g.Y(escalate.value)} color="var(--ds-good)" label={escalate.label} />}
        {retreat && <ReferenceLine x={g.padL} w={g.innerW} y={g.Y(retreat.value)} color="var(--ds-crit)" label={retreat.label} />}
        {/* 置信带 */}
        {band && <path d={band} fill="var(--ds-accent)" opacity="0.16" />}
        {/* 历史面积 + 线 */}
        <path d={histArea} fill={`url(#${gid})`} />
        <path d={histLine} fill="none" stroke="var(--ds-accent)" strokeWidth="2.4" strokeLinejoin="round" strokeLinecap="round" />
        {/* 预测中位虚线 */}
        {midLine && <path d={midLine} fill="none" stroke="var(--ds-accent)" strokeWidth="2" strokeDasharray="5 4" strokeLinecap="round" />}
        {/* 现在竖线 */}
        <line x1={nowX} y1={g.padT} x2={nowX} y2={g.padT + g.innerH} stroke="var(--ds-text-2)" strokeWidth="1" opacity="0.5" />
        <text x={nowX} y={g.padT + g.innerH + 13} textAnchor="middle" style={{ fill: "var(--ds-text-2)", font: "9px var(--ds-font-sans)" }}>{nowLabel}</text>
        {startLabel && <text x={g.padL + 6} y={g.padT + g.innerH + 13} style={{ fill: "var(--ds-muted)", font: "9px var(--ds-font-sans)" }}>{startLabel}</text>}
        {endLabel && <text x={g.padL + g.innerW} y={g.padT + g.innerH + 13} textAnchor="end" style={{ fill: "var(--ds-muted)", font: "9px var(--ds-font-sans)" }}>{endLabel}</text>}
        {/* 端点 */}
        <circle cx={nowX} cy={g.Y(history[g.nowIdx])} r="3.2" fill="var(--ds-accent)" />
        {shown > 0 && <circle cx={g.X(g.nowIdx + shown - 1)} cy={g.Y(forecastMid[shown - 1])} r="3.2" fill="var(--ds-accent)" />}
      </svg>
    </div>
  );
}

function ReferenceLine({ x, w, y, color, label }: { x: number; w: number; y: number; color: string; label?: string }) {
  return (
    <g>
      <line x1={x} y1={y} x2={x + w} y2={y} stroke={color} strokeWidth="1" strokeDasharray="5 4" opacity="0.9" />
      {label && <text x={x + w} y={y - 5} textAnchor="end" style={{ fill: color, font: "600 9px var(--ds-font-sans)" }}>{label}</text>}
    </g>
  );
}
