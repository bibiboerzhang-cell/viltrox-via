import { EmptyState } from './EmptyState';

interface TimeSeriesPoint {
  label: string;
  value: number;
  tooltip: string;
}

interface TimeSeriesChartProps {
  data: TimeSeriesPoint[];
}

export function TimeSeriesChart({ data }: TimeSeriesChartProps) {
  if (!data.length) {
    return <EmptyState title="暂无时间序列" body="待同步真实数据后展示。" />;
  }
  const max = Math.max(...data.map((d) => d.value), 1);
  return (
    <>
      <div className="da-line-chart">
        {data.map((d, idx) => (
          <div
            key={`${d.label}-${idx}`}
            className="da-line-chart__bar"
            style={{ height: `${(d.value / max) * 100}%` }}
            data-tooltip={d.tooltip}
          />
        ))}
      </div>
      <div className="da-line-chart__legend"><i /> Posts per day</div>
    </>
  );
}
