import type { KolPoolRefreshState } from './KolPoolPanel.types';
import { decisionProfile, refreshStateLabel } from './KolPoolPanel.utils';

export function KolPoolSkeletonRows() {
  return (
    <>
      {[0, 1, 2, 3, 4, 5].map((item) => (
        <tr className="vkpi-kol-pool-skeleton-row" key={item} aria-hidden="true">
          <td><span className="vkpi-skeleton vkpi-skeleton-avatar" /></td>
          <td><span className="vkpi-skeleton vkpi-skeleton-pill" /></td>
          <td>
            <span className="vkpi-skeleton vkpi-skeleton-line is-medium" />
            <span className="vkpi-skeleton vkpi-skeleton-line is-long" />
          </td>
          <td><span className="vkpi-skeleton vkpi-skeleton-line is-medium" /></td>
          <td><span className="vkpi-skeleton vkpi-skeleton-pill" /></td>
          <td><span className="vkpi-skeleton vkpi-skeleton-line is-short" /></td>
          <td><span className="vkpi-skeleton vkpi-skeleton-line is-short" /></td>
          <td><span className="vkpi-skeleton vkpi-skeleton-line is-short" /></td>
          <td><span className="vkpi-skeleton vkpi-skeleton-line is-short" /></td>
          <td><span className="vkpi-skeleton vkpi-skeleton-pill" /></td>
          <td><span className="vkpi-skeleton vkpi-skeleton-pill" /></td>
          <td><span className="vkpi-skeleton vkpi-skeleton-line is-medium" /></td>
          <td><span className="vkpi-skeleton vkpi-skeleton-line is-long" /></td>
        </tr>
      ))}
    </>
  );
}

export function DecisionCell({ decision }: { decision: ReturnType<typeof decisionProfile> }) {
  return (
    <div className={`vkpi-kol-pool-decision-cell ${decision.tone}`}>
      <div>
        <strong>{decision.label}</strong>
        <span>{decision.nextAction}</span>
      </div>
      <em>{decision.score}</em>
    </div>
  );
}

export function CoverageChip({ label, value, total }: { label: string; value: number; total: number }) {
  const ratio = total ? Math.round((value / total) * 100) : 0;
  return <span className={ratio === 100 ? 'vkpi-chip is-success' : 'vkpi-chip'}>{label} {value}/{total} · {ratio}%</span>;
}

export function RefreshStateNotice({ refresh }: { refresh: KolPoolRefreshState }) {
  const freshness = refresh.freshness;
  const tone = refresh.triggered ? ' is-info' : refresh.reason === 'on_demand_refresh_disabled' ? ' is-warning' : '';
  return (
    <div className={`vkpi-alert${tone}`} style={{ marginBottom: 12 }}>
      <strong>{refreshStateLabel(refresh)}</strong>
      {freshness && (
        <div className="vkpi-help-text">
          层级 {freshness.tier || 'cold'} · 阈值 {freshness.threshold_days ?? '-'} 天 ·
          {freshness.days_old === null || freshness.days_old === undefined ? ' 从未刷新' : ` 已 ${freshness.days_old} 天`} ·
          搜索计数 {refresh.search_marker?.search_count_30d ?? freshness.search_count_30d ?? 0}
        </div>
      )}
      {refresh.task_id && <div className="vkpi-help-text">后台任务: {refresh.task_id}</div>}
    </div>
  );
}
