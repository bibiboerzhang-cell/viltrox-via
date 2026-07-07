import type { ReactNode } from 'react';
import type { VkpiLinkRow } from '../vkpiTypes';
import { numberFormatter } from '../shared/vkpiFormatters';
import { TableShell, type TableShellColumn } from '../cockpit/components/ui/TableShell';

const COLUMNS: TableShellColumn[] = [
  { key: 'slug', header: 'Slug' },
  { key: 'destination', header: '目标', cellClassName: 'vkpi-url-cell' },
  { key: 'project', header: '项目' },
  { key: 'clicks', header: '点击' },
  { key: 'validClicks', header: '有效点击' },
  { key: 'botClicks', header: '机器人' },
  { key: 'status', header: '状态' },
  { key: 'healthStatus', header: '健康' },
  { key: 'updatedAt', header: '更新' },
  { key: 'actions', header: '操作' },
];

export function LinkTable({ links, loading, error, onSelectLink, onPauseLink, onArchiveLink, onHealthCheckLink }: { links: VkpiLinkRow[]; loading?: boolean; error?: ReactNode; onSelectLink?: (link: VkpiLinkRow) => void; onPauseLink?: (linkId: string) => Promise<void>; onArchiveLink?: (linkId: string) => Promise<void>; onHealthCheckLink?: (linkId: string) => Promise<void> }) {
  const renderCell = (link: VkpiLinkRow, col: TableShellColumn): ReactNode => {
    switch (col.key) {
      case 'slug': return <button className="vkpi-link-button" type="button" onClick={() => onSelectLink?.(link)}><strong>{link.slug}</strong></button>;
      case 'destination': return link.destination;
      case 'project': return link.projectName || link.projectId || '-';
      case 'clicks': return numberFormatter.format(link.clicks);
      case 'validClicks': return numberFormatter.format(link.validClicks);
      case 'botClicks': return numberFormatter.format(link.botClicks);
      case 'status': return link.status;
      case 'healthStatus': return link.healthStatus;
      case 'updatedAt': return link.updatedAt;
      case 'actions': return (
        <>
          <button className="vkpi-link-button" type="button" onClick={() => onSelectLink?.(link)}>详情</button>{' '}
          <button className="vkpi-link-button" type="button" onClick={() => void onHealthCheckLink?.(link.id)}>检查</button>{' '}
          <button className="vkpi-link-button" type="button" onClick={() => void onPauseLink?.(link.id)} disabled={link.status === 'paused' || link.status === 'archived'}>暂停</button>{' '}
          <button className="vkpi-link-button" type="button" onClick={() => void onArchiveLink?.(link.id)} disabled={link.status === 'archived'}>归档</button>
        </>
      );
      default: return null;
    }
  };

  return (
    <TableShell<VkpiLinkRow>
      columns={COLUMNS}
      rows={links}
      loading={loading}
      error={error}
      emptyText="暂无短链。请创建真实短链后再查看点击和归因。"
      rowKey={(link) => link.id}
      renderCell={renderCell}
    />
  );
}
