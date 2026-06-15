import type { VkpiDashboardData } from '../vkpiTypes';
import { numberFormatter } from '../shared/vkpiFormatters';

export function KpiLedgerTable({ rows }: { rows: VkpiDashboardData['kpiLedger'] }) {
  return (
    <div className="vkpi-table-wrap">
      <table className="vkpi-table">
        <thead><tr><th>日期</th><th>成员</th><th>项目 / 产品</th><th>KOL</th><th>指标</th><th>值</th><th>来源证据</th><th>置信度</th><th>创建</th></tr></thead>
        <tbody>{rows.length ? rows.map((row) => (
          <tr key={row.id}>
            <td>{row.ledgerDate}</td>
            <td>{row.staffName || row.staffId || '-'}<br /><small>{row.employeeCode || row.staffId || ''}</small></td>
            <td>{row.projectName || row.projectId || '-'}<br /><small>{row.productSku || row.projectId || ''}</small></td>
            <td>{row.kolName || row.kolId || '-'}<br /><small>{row.kolId ? `ID ${row.kolId}` : ''}</small></td>
            <td>{row.metricLabel || row.metricKey}<br /><small>{row.metricKey}</small></td>
            <td>{numberFormatter.format(row.metricValue)}</td>
            <td>{row.sourceType}<br /><small>{row.sourceRef}</small></td>
            <td>{row.confidence || '-'}</td>
            <td>{row.createdAt}</td>
          </tr>
        )) : <tr><td className="vkpi-table-empty" colSpan={9}>暂无 KPI ledger。请先产生 KOL 认领、项目阶段、短链、内容、成本或销售记录，再运行当天工作量计入。</td></tr>}</tbody>
      </table>
    </div>
  );
}

