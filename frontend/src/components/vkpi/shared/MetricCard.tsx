import type { VkpiMetricCard } from '../vkpiTypes';
import { Icon } from './Icon';

export function MetricCard({ metric, onClick }: { metric: VkpiMetricCard; onClick?: () => void }) {
  return (
    <article className={`vkpi-metric-card ${onClick ? 'is-clickable' : ''}`} onClick={onClick}>
      <div className="vkpi-metric-card__top">
        <span>{metric.label}</span>
        <Icon name="info" />
      </div>
      <strong>{metric.value}</strong>
      <small className={`vkpi-delta is-${metric.deltaDirection}`}>
        {metric.deltaDirection === 'up' ? '▲' : metric.deltaDirection === 'down' ? '▼' : '•'} {metric.deltaLabel}
      </small>
    </article>
  );
}
