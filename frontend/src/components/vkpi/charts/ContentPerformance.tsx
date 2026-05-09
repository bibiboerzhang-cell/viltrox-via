import type { VkpiShareItem } from '../vkpiTypes';
import { numberFormatter } from '../shared/vkpiFormatters';

export function ContentPerformance({ items }: { items: VkpiShareItem[] }) {
  const safeItems = items.length ? items : [{ label: '暂无内容数据', value: 0 }];
  const max = Math.max(1, ...safeItems.map((item) => item.value));
  return (
    <div className="vkpi-content-bars">
      {safeItems.map((item) => (
        <div key={item.label} className="vkpi-content-bars__row">
          <span>{item.label}</span>
          <div><i style={{ width: `${(item.value / max) * 100}%` }} /></div>
          <strong>{numberFormatter.format(item.value)}</strong>
        </div>
      ))}
      <div className="vkpi-axis-labels"><span>0</span><span>200K</span><span>400K</span><span>600K</span></div>
    </div>
  );
}
