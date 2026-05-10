import { useMemo, useState } from 'react';
import type { VkpiAlertItem } from '../vkpiTypes';

type AlertFilter = 'all' | 'comment_intelligence' | 'workflow';

function filterLabel(filter: AlertFilter): string {
  if (filter === 'comment_intelligence') return '评论风险';
  if (filter === 'workflow') return '流程';
  return '全部';
}

function formatAlertMeta(alert: VkpiAlertItem): string {
  if (alert.triageGroup !== 'comment_intelligence') return alert.targetType || alert.ruleKey || 'workflow';
  const parts = [
    alert.hostileCount ? `${alert.hostileCount} hostile` : '',
    alert.criticalCount ? `${alert.criticalCount} critical` : '',
    alert.negativeCount ? `${alert.negativeCount} negative` : '',
  ].filter(Boolean);
  return parts.length ? parts.join(' / ') : `${alert.flaggedComments || alert.count} flagged`;
}

export function AlertsPanel({
  alerts,
  onResolveAlert,
  onOpenAlert,
}: {
  alerts: VkpiAlertItem[];
  onResolveAlert?: (alertId: string) => void | Promise<void>;
  onOpenAlert?: (alertId: string) => void | Promise<void>;
}) {
  const [filter, setFilter] = useState<AlertFilter>('all');
  const filteredAlerts = useMemo(() => {
    if (filter === 'all') return alerts;
    return alerts.filter((alert) => alert.triageGroup === filter);
  }, [alerts, filter]);
  const commentRiskCount = alerts.filter((alert) => alert.triageGroup === 'comment_intelligence').length;
  const dangerCount = alerts.filter((alert) => alert.severity === 'danger').length;

  return (
    <section className="vkpi-card vkpi-panel-card">
      <div className="vkpi-card__header">
        <div>
          <h2>提醒</h2>
          <span>{dangerCount ? `${dangerCount} 条高风险` : '未处理提醒'}</span>
        </div>
        <button className="vkpi-link-button" type="button" onClick={() => setFilter('all')}>查看全部</button>
      </div>
      <div className="vkpi-alert-toolbar" aria-label="提醒筛选">
        {(['all', 'comment_intelligence', 'workflow'] as AlertFilter[]).map((item) => (
          <button
            key={item}
            type="button"
            className={item === filter ? 'is-active' : ''}
            onClick={() => setFilter(item)}
          >
            {filterLabel(item)}
            {item === 'comment_intelligence' && commentRiskCount ? <strong>{commentRiskCount}</strong> : null}
          </button>
        ))}
      </div>
      <div className="vkpi-alert-list">
        {filteredAlerts.length ? (
          filteredAlerts.map((alert) => (
            <article key={alert.id} className={`vkpi-alert is-${alert.severity}`}>
              <i />
              <span>
                {alert.label}
                <small>{formatAlertMeta(alert)}</small>
                {alert.platform ? <em>{alert.platform}</em> : null}
              </span>
              <div className="vkpi-alert-actions">
                <strong>{alert.count}</strong>
                {onOpenAlert ? (
                  <button type="button" onClick={() => void onOpenAlert(alert.id)}>详情</button>
                ) : null}
                {onResolveAlert ? (
                  <button type="button" onClick={() => void onResolveAlert(alert.id)}>处理</button>
                ) : null}
              </div>
            </article>
          ))
        ) : (
          <div className="vkpi-empty-state">当前周期没有未处理提醒。</div>
        )}
      </div>
    </section>
  );
}
