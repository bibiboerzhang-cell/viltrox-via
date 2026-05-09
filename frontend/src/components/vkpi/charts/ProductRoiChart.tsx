import React from 'react';
import type { VkpiProductRoiItem } from '../vkpiTypes';
import { currencyFormatter } from '../shared/vkpiFormatters';

export function ProductRoiChart({ items }: { items: VkpiProductRoiItem[] }) {
  const safeItems = items.length ? items : [{ product: '暂无项目数据', roi: 0, gmv: 0, cost: 0 }];
  const maxCost = Math.max(1, ...safeItems.map((item) => item.cost || 0));
  const maxGmv = Math.max(1, ...safeItems.map((item) => item.gmv));
  return (
    <div className="vkpi-product-chart">
      <div className="vkpi-product-chart__legend">
        <span><i />成本</span>
        <span><i className="is-dot" />销售额</span>
      </div>
      <div className="vkpi-product-chart__plot">
        {safeItems.map((item) => (
          <div className="vkpi-product-chart__item" key={item.product}>
            <div className="vkpi-product-chart__track">
              <span className="vkpi-product-chart__dot" style={{ bottom: `${(item.gmv / maxGmv) * 86 + 4}%` }} />
              <i style={{ height: `${((item.cost || 0) / maxCost) * 84 + 8}%` }} title={`成本 ${currencyFormatter.format(item.cost || 0)} · 销售 ${currencyFormatter.format(item.gmv)}`} />
            </div>
            <small>{item.product.split('\n').map((line) => <React.Fragment key={line}>{line}<br /></React.Fragment>)}</small>
          </div>
        ))}
      </div>
    </div>
  );
}
