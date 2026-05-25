import type {
  VkpiAlertItem,
  VkpiAttributionRow,
  VkpiCostRow,
  VkpiDashboardData,
  VkpiDeltaDirection,
  VkpiKpiLedgerEntry,
  VkpiLinkRow,
  VkpiMetricCard,
  VkpiProductRoiItem,
  VkpiScopeContext,
  VkpiShareItem,
  VkpiTrendPoint,
} from '../../components/vkpi/vkpiTypes';
import {
  centsToUsd,
  compact,
  money,
  numberValue,
  percent,
} from './dashboardFormat';
import {
  platformDisplayLabel,
  platformLabel,
} from './dashboardPlatform';

type Row = Record<string, unknown>;

function delta(
  direction: VkpiDeltaDirection = 'flat',
  label = '接口数据',
): Pick<VkpiMetricCard, 'deltaDirection' | 'deltaLabel'> {
  return { deltaDirection: direction, deltaLabel: label };
}

function metricMap(rows: Row[]): Map<string, Row> {
  const entries: Array<[string, Row]> = [];
  rows.forEach((row) => {
    const key = String(row.metric_key || row.key || '');
    if (key) entries.push([key, row]);
  });
  return new Map(entries);
}

function metricMeta(metrics: Map<string, Row>, key: string): Pick<VkpiMetricCard, 'metricValueId' | 'sourceCount' | 'drilldownUrl' | 'unit' | 'calculation'> {
  const row = metrics.get(key);
  if (!row) return {};
  const metricValueId = numberValue(row.metric_value_id || row.metricValueId);
  return {
    metricValueId: metricValueId || undefined,
    sourceCount: numberValue(row.source_count),
    drilldownUrl: String(row.drilldown_url || row.drilldownUrl || ''),
    unit: String(row.unit || ''),
    calculation: (row.calculation && typeof row.calculation === 'object' ? row.calculation : undefined) as Record<string, unknown> | undefined,
  };
}

function metricNumber(metrics: Map<string, Row>, key: string): number {
  const row = metrics.get(key);
  return row ? numberValue(row.value_numeric ?? row.value ?? 0) : 0;
}

function metricSourceLabel(metrics: Map<string, Row>, key: string): Pick<VkpiMetricCard, 'deltaDirection' | 'deltaLabel'> {
  const row = metrics.get(key);
  if (!row) return delta('flat', '未生成快照');
  return delta('flat', `来源 ${numberValue(row.source_count)} 条`);
}

export function buildDashboardMetrics(rawMetrics: Row[] = []): VkpiMetricCard[] {
  const lineage = metricMap(rawMetrics);
  const views = metricNumber(lineage, 'views');
  const sales = centsToUsd(metricNumber(lineage, 'gmv'));
  const cost = centsToUsd(metricNumber(lineage, 'cost'));
  const newKol = metricNumber(lineage, 'new_kol');
  const published = metricNumber(lineage, 'published_content');
  const activeProjects = metricNumber(lineage, 'active_projects');
  return [
    { key: 'views', label: '播放量', value: compact(views), ...metricSourceLabel(lineage, 'views'), ...metricMeta(lineage, 'views') },
    { key: 'cost', label: '成本', value: money(cost), ...metricSourceLabel(lineage, 'cost'), ...metricMeta(lineage, 'cost') },
    { key: 'gmv', label: '本周销售额', value: money(sales), ...metricSourceLabel(lineage, 'gmv'), ...metricMeta(lineage, 'gmv') },
    { key: 'new_kol', label: '新增 KOL', value: compact(newKol), ...metricSourceLabel(lineage, 'new_kol'), ...metricMeta(lineage, 'new_kol') },
    { key: 'published_content', label: '已发布内容', value: compact(published), ...metricSourceLabel(lineage, 'published_content'), ...metricMeta(lineage, 'published_content') },
    { key: 'active_projects', label: '进行中项目', value: compact(activeProjects), ...metricSourceLabel(lineage, 'active_projects'), ...metricMeta(lineage, 'active_projects') },
  ];
}

export function buildDashboardRevenueTrend(rows: Row[]): VkpiTrendPoint[] {
  if (!rows.length) return [];
  return rows.map((row) => {
    const views = numberValue(row.views || row.total_views || row.play_count || row.impressions);
    const sales = centsToUsd(row.sales_cents || row.gmv_cents || row.revenue_cents);
    const cost = centsToUsd(row.cost_cents);
    return {
      label: String(row.date || row.day || '').slice(5).replace('-', '/') || '-',
      gmv: views,
      netContribution: sales,
      views,
      sales,
      cost,
    };
  });
}

function stageLabel(stage: string): string {
  const labels: Record<string, string> = {
    discovery: '发现',
    claimed: '已认领',
    contacted: '已联系',
    replied: '已回复',
    agreed: '已合作',
    shipped: '已发货',
    received: '已到货',
    published: '已发布',
    measured: '已统计',
    closed: '已关闭',
  };
  return labels[stage] || stage || '未知';
}

export function buildDashboardFunnel(raw: Row[]): VkpiDashboardData['funnel'] {
  const priority = ['claimed', 'contacted', 'replied', 'agreed', 'shipped', 'received', 'published', 'measured'];
  const byStage = new Map(raw.map((row) => [String(row.stage || row.to_stage || '').toLowerCase(), numberValue(row.count)]));
  const max = Math.max(1, ...priority.map((stage) => byStage.get(stage) || 0));
  return priority.map((stage) => {
    const value = byStage.get(stage) || 0;
    return { label: stageLabel(stage), value, rateLabel: percent(value / max) };
  });
}

export function buildDashboardLeaderboard(staffRows: Row[], fallbackRows: Row[]): VkpiDashboardData['staffLeaderboard'] {
  const source = staffRows.length ? staffRows : fallbackRows;
  return source.slice(0, 8).map((row, index) => ({
    staffId: row.staff_id || row.id ? String(row.staff_id || row.id) : undefined,
    name: String(row.staff_name || row.name || row.email || `员工 ${row.staff_id || index + 1}`),
    gmv: centsToUsd(row.gmv_cents || row.revenue_cents),
    avatar: String(row.avatar_url || ''),
    isTop: index === 0,
  }));
}

export function buildDashboardProductRoi(rows: Row[]): VkpiProductRoiItem[] {
  const mapped = rows.slice(0, 8).map((row) => {
    const revenue = numberValue(row.sales_cents || row.revenue_cents || row.gmv_cents);
    const cost = numberValue(row.cost_cents);
    return {
      product: String(row.product_name || row.product_sku || row.project_name || '产品'),
      roi: cost ? Number((revenue / cost).toFixed(2)) : 0,
      gmv: centsToUsd(revenue),
      sales: centsToUsd(revenue),
      cost: centsToUsd(cost),
      views: numberValue(row.views || row.total_views || row.play_count || row.impressions),
    };
  });
  return mapped.length ? mapped : [{ product: '暂无项目数据', roi: 0, gmv: 0 }];
}

export function buildDashboardPlatformShare(rows: Row[]): VkpiShareItem[] {
  const totals = new Map<string, number>();
  rows.forEach((row) => {
    const key = platformLabel(row.source_platform || row.platform);
    totals.set(key, (totals.get(key) || 0) + numberValue(row.revenue_cents));
  });
  const total = Array.from(totals.values()).reduce((sum, value) => sum + value, 0);
  if (!total) return [{ label: '暂无归因', value: 100 }];
  return Array.from(totals.entries()).map(([label, value]) => ({
    label: platformDisplayLabel(label),
    value: Number(((value / total) * 100).toFixed(1)),
  }));
}

function parseJsonObject(value: unknown): Record<string, unknown> {
  if (value && typeof value === 'object' && !Array.isArray(value)) return value as Record<string, unknown>;
  if (typeof value !== 'string' || !value.trim()) return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

export function buildDashboardAlerts(rows: Row[]): VkpiAlertItem[] {
  return rows.slice(0, 6).map((row) => {
    const severityRaw = String(row.severity || 'info').toLowerCase();
    const severity = severityRaw === 'critical' || severityRaw === 'high' ? 'danger' : severityRaw === 'warning' ? 'warning' : 'info';
    const metadata = parseJsonObject(row.metadata_json);
    const ruleKey = String(row.rule_key || '');
    const triageGroup = ruleKey.startsWith('comment_intelligence')
      ? 'comment_intelligence'
      : String(row.target_type || '').includes('project')
        ? 'workflow'
        : 'system';
    return {
      id: String(row.id || row.alert_key || `${row.rule_key || 'alert'}-${row.created_at || ''}`),
      alertKey: String(row.alert_key || ''),
      label: String(row.title || row.rule_key || '提醒'),
      count: numberValue(metadata.flagged_comments || 1) || 1,
      severity: severity as VkpiAlertItem['severity'],
      description: String(row.body || row.target_type || ''),
      ruleKey,
      targetType: String(row.target_type || ''),
      targetId: row.target_id ? String(row.target_id) : undefined,
      createdAt: String(row.created_at || row.updated_at || ''),
      platform: String(metadata.platform || row.platform || ''),
      triageGroup,
      negativeCount: numberValue(metadata.negative_count),
      criticalCount: numberValue(metadata.critical_count),
      hostileCount: numberValue(metadata.hostile_count),
      flaggedComments: numberValue(metadata.flagged_comments),
      windowDays: numberValue(metadata.window_days),
    };
  });
}

export function buildDashboardKpiLedger(rows: Row[]): VkpiKpiLedgerEntry[] {
  return rows.map((row) => ({
    id: String(row.id || `${row.ledger_date || ''}-${row.source_ref || ''}`),
    ledgerDate: String(row.ledger_date || ''),
    staffId: row.staff_id ? String(row.staff_id) : undefined,
    staffName: String(row.staff_name || ''),
    employeeCode: String(row.employee_code || ''),
    kolId: row.kol_id ? String(row.kol_id) : undefined,
    kolName: String(row.kol_name || ''),
    projectId: row.project_id ? String(row.project_id) : undefined,
    projectName: String(row.project_name || ''),
    productSku: String(row.product_sku || ''),
    metricKey: String(row.metric_key || ''),
    metricLabel: String(row.metric_label || row.metric_key || ''),
    metricValue: numberValue(row.metric_value),
    sourceType: String(row.source_type || ''),
    sourceRef: String(row.source_ref || ''),
    confidence: String(row.confidence || ''),
    createdAt: String(row.created_at || ''),
  })).filter((row) => row.id);
}

export function buildScopeContext(row: Row | undefined): VkpiScopeContext | undefined {
  if (!row || typeof row !== 'object') return undefined;
  const scopeMode = String(row.scope_mode || '');
  if (!scopeMode) return undefined;
  const idString = (value: unknown): string | undefined => {
    const next = numberValue(value);
    return next ? String(next) : undefined;
  };
  return {
    actorStaffId: idString(row.actor_staff_id),
    requestedStaffId: idString(row.requested_staff_id),
    effectiveStaffId: idString(row.effective_staff_id),
    canViewAll: Boolean(row.can_view_all),
    scopeMode,
    role: String(row.role || ''),
    isOwner: Boolean(row.is_owner),
    domain: String(row.domain || ''),
  };
}

export function emptyEvidence(): VkpiDashboardData['evidence'] {
  return {
    gmv: [],
    cost: [],
    roi: [],
    net_contribution: [],
    views: [],
    active_projects: [],
    published_content: [],
    valid_clicks: [],
    alerts: [],
    new_kol: [],
  };
}

export function hasAnyDashboardData(
  summary: Row,
  projects: Row[],
  links: VkpiLinkRow[],
  attributions: VkpiAttributionRow[],
  costs: VkpiCostRow[],
  alerts: Row[],
  rawMetrics: Row[],
): boolean {
  const hasMetricSources = rawMetrics.some((row) => numberValue(row.source_count) > 0 || numberValue(row.value_numeric ?? row.value) > 0);
  return Boolean(
    hasMetricSources
    || numberValue(summary.revenue_cents || summary.all_gmv_including_company_cents)
    || numberValue(summary.cost_cents)
    || numberValue(summary.total_views || summary.views || summary.view_count || summary.play_count || summary.impressions)
    || projects.length
    || links.length
    || attributions.length
    || costs.length
    || alerts.length
  );
}

export function buildWeeklySummary(summary: Row, staffRows: Row[], alerts: Row[]): string {
  const sales = centsToUsd(summary.revenue_cents || summary.all_gmv_including_company_cents);
  const cost = centsToUsd(summary.cost_cents);
  const views = numberValue(summary.total_views || summary.views || summary.view_count || summary.play_count || summary.impressions);
  return `当前周期确认销售额为 ${money(sales)}，成本为 ${money(cost)}（镜头成本发货自动计入，员工只登记快递和推广费），已抓取播放量为 ${compact(views)}。当前范围内共有 ${staffRows.length} 名员工数据，还有 ${alerts.length} 条未处理提醒，需要在周报审批前完成复核。`;
}
