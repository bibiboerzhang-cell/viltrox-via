import type { VkpiShareItem } from '../vkpiTypes';

export function DonutChart({ items, centerLabel }: { items: VkpiShareItem[]; centerLabel: string }) {
  const safeItems = items.length ? items : [{ label: '暂无归因', value: 100 }];
  const palette = ['#155dfc', '#4f8df9', '#7fb2fb', '#a9cdfc', '#dce8fb'];
  let cursor = 0;
  const gradient = safeItems
    .map((item, index) => {
      const start = cursor;
      cursor += item.value;
      return `${palette[index % palette.length]} ${start}% ${cursor}%`;
    })
    .join(', ');

  return (
    <div className="vkpi-donut-wrap">
      <div className="vkpi-donut" style={{ background: `conic-gradient(${gradient})` }}>
        <div><span>合计</span><strong>{centerLabel}</strong></div>
      </div>
      <div className="vkpi-donut-legend">
        {safeItems.map((item, index) => (
          <div key={item.label}>
            <i style={{ backgroundColor: palette[index % palette.length] }} />
            <span>{item.label}</span>
            <strong>{item.value}%</strong>
          </div>
        ))}
      </div>
    </div>
  );
}
