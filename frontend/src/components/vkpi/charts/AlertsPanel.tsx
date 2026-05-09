import type { VkpiAlertItem } from '../vkpiTypes';

export function AlertsPanel({ alerts }: { alerts: VkpiAlertItem[] }) {
  return (
    <section className="vkpi-card vkpi-panel-card">
      <div className="vkpi-card__header">
        <h2>提醒</h2>
        <button className="vkpi-link-button" type="button">查看全部</button>
      </div>
      <div className="vkpi-alert-list">
        {alerts.length ? (
          alerts.map((alert) => (
            <button key={alert.id} className={`vkpi-alert is-${alert.severity}`} type="button">
              <i />
              <span>{alert.label}{alert.description ? <small>({alert.description})</small> : null}</span>
              <strong>{alert.count}</strong>
            </button>
          ))
        ) : (
          <div className="vkpi-empty-state">当前周期没有未处理提醒。</div>
        )}
      </div>
    </section>
  );
}
