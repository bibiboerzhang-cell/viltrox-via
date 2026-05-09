import type { VkpiTrendPoint } from '../vkpiTypes';
import { numberFormatter } from '../shared/vkpiFormatters';

export function TrendChart({ points }: { points: VkpiTrendPoint[] }) {
  const safePoints = points.length ? points : [{ label: '暂无数据', gmv: 0, netContribution: 0 }];
  const width = 520;
  const height = 230;
  const padding = { top: 18, right: 20, bottom: 38, left: 54 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  const max = Math.max(1, ...safePoints.flatMap((point) => [point.gmv, point.netContribution])) * 1.15;
  const toX = (index: number) => padding.left + (index / Math.max(safePoints.length - 1, 1)) * chartWidth;
  const toY = (value: number) => padding.top + chartHeight - (value / max) * chartHeight;
  const gmvPoints = safePoints.map((point, index) => `${toX(index)},${toY(point.gmv)}`).join(' ');
  const netPoints = safePoints.map((point, index) => `${toX(index)},${toY(point.netContribution)}`).join(' ');
  const gridValues = [0, Math.round(max * 0.25), Math.round(max * 0.5), Math.round(max * 0.75), Math.round(max)];

  return (
    <div className="vkpi-chart-shell">
      <div className="vkpi-chart-legend">
        <span><i className="is-solid" />播放量</span>
        <span><i className="is-dashed" />销售额</span>
      </div>
      <svg className="vkpi-trend-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="播放量和销售额趋势图">
        {gridValues.map((value, index) => {
          const y = toY(value);
          return (
            <g key={`${value}-${index}`}>
              <line x1={padding.left} y1={y} x2={width - padding.right} y2={y} className="vkpi-chart-grid" />
              <text x={12} y={y + 4} className="vkpi-chart-label">{numberFormatter.format(value)}</text>
            </g>
          );
        })}
        <polyline points={gmvPoints} className="vkpi-trend-line is-gmv" />
        <polyline points={netPoints} className="vkpi-trend-line is-net" />
        {safePoints.map((point, index) => (
          <g key={point.label}>
            <circle cx={toX(index)} cy={toY(point.gmv)} r="4" className="vkpi-trend-dot" />
            <text x={toX(index)} y={height - 10} textAnchor="middle" className="vkpi-chart-label">{point.label.replace('May ', 'May ')}</text>
          </g>
        ))}
      </svg>
    </div>
  );
}
