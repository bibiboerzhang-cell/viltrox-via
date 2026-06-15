import type { VkpiCostRow } from '../vkpiTypes';
import { currencyFormatter } from '../shared/vkpiFormatters';

export function CostLedgerTable({ rows, onSelect }: { rows: VkpiCostRow[]; onSelect?: (costId: string) => void }) {
  return (
    <div className="vkpi-table-wrap">
      <table className="vkpi-table">
        <thead><tr><th>ID</th><th>项目</th><th>KOL</th><th>成员</th><th>类型</th><th>金额</th><th>状态</th><th>发生时间</th><th>审核</th><th>备注</th><th>操作</th></tr></thead>
        <tbody>
          {rows.length ? rows.map((row) => (
            <tr key={row.id}>
              <td>{row.id}</td>
              <td>{row.projectName || row.projectId || '-'}<br /><small>{row.productSku || ''}</small></td>
              <td>{row.kolName || row.kolId || '-'}</td>
              <td>{row.staffName || row.staffId || '-'}</td>
              <td>{row.costType}</td>
              <td>{currencyFormatter.format(row.amount)}</td>
              <td>{row.status}</td>
              <td>{row.incurredAt}</td>
              <td>{row.approvedAt || '-'}{row.voidedAt ? <><br /><small>作废：{row.voidedAt}</small></> : null}</td>
              <td>{row.note || row.sourceRef || '-'}</td>
              <td><button className="vkpi-link-button" type="button" onClick={() => onSelect?.(row.id)}>选择</button></td>
            </tr>
          )) : <tr><td className="vkpi-table-empty" colSpan={11}>暂无成本明细。镜头成本在发货后自动计入，成员登记快递 / 推广费后会出现在这里。</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

