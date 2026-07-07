import type { ReactNode } from 'react';
import type { VkpiAttributionRow } from '../vkpiTypes';
import { currencyFormatter } from '../shared/vkpiFormatters';
import { TableShell, type TableShellColumn } from '../cockpit/components/ui/TableShell';

// 列定义模块常量:引用稳定,配合 TableShell 行 memo 生效(payload 未变则跳过重渲)。
const COLUMNS: TableShellColumn[] = [
  { key: 'source', header: '来源' },
  { key: 'sourceRef', header: 'Source Ref' },
  { key: 'projectId', header: '项目' },
  { key: 'kolId', header: 'KOL' },
  { key: 'staffId', header: '成员' },
  { key: 'revenue', header: '收入' },
  { key: 'confidence', header: '置信度' },
  { key: 'occurredAt', header: '时间' },
];

function renderCell(row: VkpiAttributionRow, col: TableShellColumn): ReactNode {
  switch (col.key) {
    case 'source': return row.source;
    case 'sourceRef': return row.sourceRef || '-';
    case 'projectId': return row.projectId || '-';
    case 'kolId': return row.kolId || '-';
    case 'staffId': return row.staffId || '-';
    case 'revenue': return currencyFormatter.format(row.revenue);
    case 'confidence': return row.confidence || '-';
    case 'occurredAt': return row.occurredAt;
    default: return null;
  }
}

export function AttributionTable({ rows, loading, error }: { rows: VkpiAttributionRow[]; loading?: boolean; error?: ReactNode }) {
  return (
    <TableShell<VkpiAttributionRow>
      columns={COLUMNS}
      rows={rows}
      loading={loading}
      error={error}
      emptyText="暂无真实归因记录。"
      rowKey={(row) => row.id}
      renderCell={renderCell}
    />
  );
}
