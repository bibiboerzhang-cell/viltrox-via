import { EmptyState } from './EmptyState';

interface BarRow {
  label: string;
  value: number;
}

interface BarChartProps {
  data: BarRow[];
  valueLabel?: string;
}

export function BarChart({ data, valueLabel = '次数' }: BarChartProps) {
  if (!data.length) {
    return <EmptyState title="暂无数据" body="导入真实帖子后会展示分布。" />;
  }
  const max = Math.max(...data.map((d) => d.value), 1);
  return (
    <div className="da-bar-chart">
      {data.map((d, idx) => (
        <div key={`${d.label}-${idx}`} className="da-bar-row">
          <span className="da-bar-row__label">{d.label}</span>
          <span
            className="da-bar-row__bar"
            style={{ width: `${(d.value / max) * 100}%` }}
            title={`${d.label}: ${d.value} ${valueLabel}`}
          />
          <span className="da-bar-row__value">{d.value}</span>
        </div>
      ))}
    </div>
  );
}
