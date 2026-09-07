import { numberFormatter } from '../shared/vkpiFormatters';

/** Display projected KPI values without turning unknown into zero or legacy credit. */
export function kpiValueLabel(value: unknown, eligible?: unknown): string {
  if (eligible === false || value == null || typeof value === 'boolean') return '待核验';
  if (typeof value !== 'number' && typeof value !== 'string') return '待核验';
  if (typeof value === 'string' && !value.trim()) return '待核验';
  const number = Number(value);
  return Number.isFinite(number) ? numberFormatter.format(number) : '待核验';
}

export function workloadValueLabel(summary: Record<string, unknown>): string {
  const value = Object.prototype.hasOwnProperty.call(summary, 'workload_score')
    ? summary.workload_score : summary.kpi_credit;
  return kpiValueLabel(value);
}
