import type { VkpiDashboardData } from '../vkpiTypes';
import { currencyFormatter } from '../shared/vkpiFormatters';

export function ProductCostTable({ rows }: { rows: VkpiDashboardData['productCosts'] }) {
  return (
    <div className="vkpi-table-wrap"><table className="vkpi-table"><thead><tr><th>SKU</th><th>产品</th><th>镜头成本</th><th>状态</th><th>更新</th></tr></thead><tbody>{rows.length ? rows.map((row) => <tr key={row.id || row.productSku}><td><strong>{row.productSku}</strong></td><td>{row.productName || '-'}</td><td>{currencyFormatter.format(row.unitCost)}</td><td>{row.active ? '启用' : '停用'}</td><td>{row.updatedAt || '-'}</td></tr>) : <tr><td className="vkpi-table-empty" colSpan={5}>暂无产品成本。配置后，发货阶段会自动计入镜头成本。</td></tr>}</tbody></table></div>
  );
}

