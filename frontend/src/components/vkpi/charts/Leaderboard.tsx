import type { VkpiLeaderboardItem } from '../vkpiTypes';
import { Avatar } from '../shared/Avatar';
import { currencyFormatter } from '../shared/vkpiFormatters';

export function Leaderboard({ items, onOpenStaff }: { items: VkpiLeaderboardItem[]; onOpenStaff?: (item: VkpiLeaderboardItem) => void }) {
  const safeItems = items.length ? items : [{ name: '暂无员工数据', gmv: 0 }];
  const max = Math.max(1, ...safeItems.map((item) => item.gmv));
  return (
    <div className="vkpi-leaderboard">
      {safeItems.map((item) => (
        <button className={`vkpi-leaderboard__row ${item.staffId && onOpenStaff ? 'is-clickable' : ''}`} key={item.name} type="button" onClick={() => {
          if (item.staffId && onOpenStaff) onOpenStaff(item);
        }}>
          <span className="vkpi-leaderboard__person"><Avatar name={item.name} src={item.avatar} size="xs" />{item.name} {item.isTop ? <b>♛</b> : null}</span>
          <div className="vkpi-leaderboard__bar"><i style={{ width: `${(item.gmv / max) * 100}%` }} /></div>
          <strong>{currencyFormatter.format(item.gmv)}</strong>
        </button>
      ))}
      <div className="vkpi-axis-labels"><span>$0</span><span>$100K</span><span>$200K</span><span>$300K</span></div>
    </div>
  );
}
