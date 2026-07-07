// U2 动效基元:纯 SVG 迷你趋势线(零依赖,全库通用,供 W3-W6 复用)。
// 设计:按自身 min/max 归一,只看形状不比绝对值;入场描线一次(400ms,
// prefers-reduced-motion 降级为静态,见 motion.css);null/非数值点跳过(诚实空桶,不编 0);
// 有效点 <2 时安静返回 null(绝不画假线)。纯展示,零请求,零评分。
import React from "react";
import "./motion.css";

export interface SparklineProps {
  /** 时间序列(旧→新);null/undefined/非数值点自动跳过(空桶如实留白) */
  data: Array<number | null | undefined>;
  /** viewBox 宽高(默认 64×18,随容器缩放) */
  width?: number;
  height?: number;
  /** 线色:任意 CSS 颜色;默认 currentColor(跟随外层文字色,方便主题一致) */
  stroke?: string;
  strokeWidth?: number;
  /** 线下柔和面积填充(同色 10% 透明) */
  fill?: boolean;
  className?: string;
  /** SVG 原生 tooltip 文案 */
  title?: string;
}

type Pt = { x: number; y: number };

export function Sparkline({
  data,
  width = 64,
  height = 18,
  stroke = "currentColor",
  strokeWidth = 1.5,
  fill = false,
  className,
  title,
}: SparklineProps) {
  const geom = React.useMemo(() => {
    const src = Array.isArray(data) ? data : [];
    const valid: Array<{ i: number; v: number }> = [];
    for (let i = 0; i < src.length; i++) {
      const v = Number(src[i]);
      if (src[i] != null && Number.isFinite(v)) valid.push({ i, v });
    }
    if (valid.length < 2 || src.length < 2) return null;
    const min = valid.reduce((acc, p) => Math.min(acc, p.v), Infinity);
    const max = valid.reduce((acc, p) => Math.max(acc, p.v), -Infinity);
    const flat = max === min;
    const range = max - min || 1;
    const pad = 2;
    const spanX = width - 2 * pad;
    const spanY = height - 2 * pad;
    const pts: Pt[] = valid.map((p) => ({
      x: pad + (p.i / (src.length - 1)) * spanX,
      // 平线居中显示(否则贴底,视觉上像 0)
      y: flat ? height / 2 : height - pad - ((p.v - min) / range) * spanY,
    }));
    const line = pts
      .map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(2)} ${p.y.toFixed(2)}`)
      .join(" ");
    const area =
      line +
      ` L${pts[pts.length - 1].x.toFixed(2)} ${(height - pad).toFixed(2)}` +
      ` L${pts[0].x.toFixed(2)} ${(height - pad).toFixed(2)} Z`;
    return { pts, line, area, last: pts[pts.length - 1] };
  }, [data, width, height]);

  if (!geom) return null;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className={className}
      data-ui="sparkline"
      role="img"
      aria-label={title || "趋势迷你图"}
    >
      {title ? <title>{title}</title> : null}
      {fill ? <path d={geom.area} fill={stroke} opacity={0.1} stroke="none" /> : null}
      <path
        className="vk-spark-path"
        d={geom.line}
        pathLength={1}
        fill="none"
        stroke={stroke}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
      <circle cx={geom.last.x} cy={geom.last.y} r={strokeWidth} fill={stroke} stroke="none" />
    </svg>
  );
}
