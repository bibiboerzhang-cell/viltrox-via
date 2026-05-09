import type { VkpiFunnelItem } from '../vkpiTypes';
import { numberFormatter } from '../shared/vkpiFormatters';

export function FunnelChart({ items }: { items: VkpiFunnelItem[] }) {
  const safeItems = items.length ? items : [{ label: '暂无数据', value: 0, rateLabel: '0%' }];
  const max = Math.max(1, ...safeItems.map((item) => item.value));
  return (
    <div className="vkpi-funnel">
      {safeItems.map((item, index) => {
        const width = 54 + (item.value / max) * 44;
        return (
          <div className="vkpi-funnel__row" key={item.label}>
            <div className="vkpi-funnel__bar" style={{ width: `${width}%`, opacity: 1 - index * 0.12 }} aria-hidden="true" />
            <div className="vkpi-funnel__meta">
              <span>{item.label}</span>
              <strong>{numberFormatter.format(item.value)} <em>({item.rateLabel})</em></strong>
            </div>
          </div>
        );
      })}
    </div>
  );
}
