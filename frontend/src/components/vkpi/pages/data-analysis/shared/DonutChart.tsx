import { EmptyState } from './EmptyState';

interface DonutChartProps {
  data: { primary: string; total: number };
}

export function DonutChart({ data }: DonutChartProps) {
  if (!data.total) {
    return <EmptyState title="暂无内容类型" body="同步真实帖子后会展示类型分布。" />;
  }
  return (
    <div>
      <div className="da-donut"><span>{data.primary}</span></div>
      <div className="da-donut-legend">
        <span><i /> {data.primary}<strong>{data.total}</strong></span>
      </div>
    </div>
  );
}
