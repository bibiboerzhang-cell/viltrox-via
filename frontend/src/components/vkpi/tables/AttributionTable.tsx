import type { VkpiAttributionRow } from '../vkpiTypes';
import { currencyFormatter } from '../shared/vkpiFormatters';

export function AttributionTable({ rows }: { rows: VkpiAttributionRow[] }) {
  return (
    <div className="vkpi-table-wrap"><table className="vkpi-table"><thead><tr><th>来源</th><th>Source Ref</th><th>项目</th><th>KOL</th><th>员工</th><th>收入</th><th>置信度</th><th>时间</th></tr></thead><tbody>{rows.length ? rows.map((row) => <tr key={row.id}><td>{row.source}</td><td>{row.sourceRef || '-'}</td><td>{row.projectId || '-'}</td><td>{row.kolId || '-'}</td><td>{row.staffId || '-'}</td><td>{currencyFormatter.format(row.revenue)}</td><td>{row.confidence || '-'}</td><td>{row.occurredAt}</td></tr>) : <tr><td className="vkpi-table-empty" colSpan={8}>暂无真实归因记录。</td></tr>}</tbody></table></div>
  );
}

