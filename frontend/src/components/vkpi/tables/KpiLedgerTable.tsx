import type { ReactNode } from 'react';
import type { VkpiDashboardData } from '../vkpiTypes';
import { numberFormatter } from '../shared/vkpiFormatters';
import { TableShell, type TableShellColumn } from '../cockpit/components/ui/TableShell';

type LedgerRow = VkpiDashboardData['kpiLedger'][number];

const COLUMNS: TableShellColumn[] = [
  { key: 'ledgerDate', header: '日期' },
  { key: 'staff', header: '成员' },
  { key: 'project', header: '项目 / 产品' },
  { key: 'kol', header: 'KOL' },
  { key: 'metric', header: '指标' },
  { key: 'metricValue', header: '值' },
  { key: 'source', header: '来源证据' },
  { key: 'confidence', header: '置信度' },
  { key: 'createdAt', header: '创建' },
];

function renderCell(row: LedgerRow, col: TableShellColumn): ReactNode {
  switch (col.key) {
    case 'ledgerDate': return row.ledgerDate;
    case 'staff': return <>{row.staffName || row.staffId || '-'}<br /><small>{row.employeeCode || row.staffId || ''}</small></>;
    case 'project': return <>{row.projectName || row.projectId || '-'}<br /><small>{row.productSku || row.projectId || ''}</small></>;
    case 'kol': return <>{row.kolName || row.kolId || '-'}<br /><small>{row.kolId ? `ID ${row.kolId}` : ''}</small></>;
    case 'metric': return <>{row.metricLabel || row.metricKey}<br /><small>{row.metricKey}</small></>;
    case 'metricValue': return numberFormatter.format(row.metricValue);
    case 'source': return <>{row.sourceType}<br /><small>{row.sourceRef}</small></>;
    case 'confidence': return row.confidence || '-';
    case 'createdAt': return row.createdAt;
    default: return null;
  }
}

export function KpiLedgerTable({ rows, loading, error }: { rows: VkpiDashboardData['kpiLedger']; loading?: boolean; error?: ReactNode }) {
  return (
    <TableShell<LedgerRow>
      columns={COLUMNS}
      rows={rows}
      loading={loading}
      error={error}
      emptyText="暂无 KPI ledger。请先产生 KOL 认领、项目阶段、短链、内容、成本或销售记录，再运行当天工作量计入。"
      rowKey={(row) => row.id}
      renderCell={renderCell}
    />
  );
}
