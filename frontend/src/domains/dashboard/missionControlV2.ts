import { getOfficialChannelMatrix } from '../channels';
import { getRecommendationFeedbackBacklog } from '../intelligence';
import { getKolPoolCompetitorDashboard, getKolPoolSummary } from '../kol';
import { listBrandSignals } from '../market';
import { apiFetch } from '../../services/http';
import {
  EMPTY_SNAPSHOT,
  mergeDashboardOfficialSummary,
  rows,
  settledValue,
  type Row,
  type Snapshot,
} from './dashboardModel';

export interface MissionControlSnapshot extends Snapshot {
  alerts: Row[];
}

export const EMPTY_MISSION_CONTROL_SNAPSHOT: MissionControlSnapshot = {
  ...EMPTY_SNAPSHOT,
  alerts: [],
};

function payloadRows(payload: Row, ...keys: string[]): Row[] {
  for (const key of keys) {
    const found = rows(payload[key]);
    if (found.length) return found;
  }
  return rows(payload);
}

export async function fetchMissionControlSnapshot(apiToken: string, windowDays: number): Promise<MissionControlSnapshot> {
  const failedSections: string[] = [];
  const [
    dashboardResult,
    trendResult,
    kolSummaryResult,
    kolDistributionResult,
    officialMatrixResult,
    competitorResult,
    brandSignalsResult,
    recentContentResult,
    agentsStatusResult,
    copilotBriefResult,
    tasksStatusResult,
    recommendationBacklogResult,
    alertsResult,
  ] = await Promise.allSettled([
    apiFetch<Row>(`/api/admin/vkpi/dashboard?window_days=${windowDays}`, {}, apiToken),
    apiFetch<{ rows?: Row[] }>('/api/admin/vkpi/dashboard/revenue-trend?window_days=7', {}, apiToken),
    getKolPoolSummary(apiToken),
    apiFetch<Row>('/api/admin/vkpi/dashboard/kol-distribution?limit=200', {}, apiToken),
    getOfficialChannelMatrix(apiToken, { limit: 20 }),
    getKolPoolCompetitorDashboard(apiToken),
    listBrandSignals(apiToken, { status: 'new', limit: 10 }),
    apiFetch<{ items?: Row[] }>('/api/admin/vkpi/dashboard/recent-content?limit=12', {}, apiToken),
    apiFetch<Row>('/api/admin/vkpi/dashboard/agents-status', {}, apiToken),
    apiFetch<Row>('/api/admin/vkpi/dashboard/copilot-brief', {}, apiToken),
    apiFetch<Row>('/api/admin/vkpi/dashboard/tasks?limit=8', {}, apiToken),
    getRecommendationFeedbackBacklog(apiToken, 12),
    apiFetch<Row>('/api/admin/vkpi/alerts?status=open&limit=20', {}, apiToken),
  ]);

  const trend = settledValue(trendResult, { rows: [] }, failedSections, 'revenue-trend');
  const brandSignals = settledValue(brandSignalsResult, { signals: [] }, failedSections, 'brand-signals');
  const recentContent = settledValue(recentContentResult, { items: [] }, failedSections, 'recent-content');
  const dashboard = settledValue(dashboardResult, {}, failedSections, 'dashboard');
  const alertsPayload = settledValue(alertsResult, {}, failedSections, 'alerts');

  return {
    source: failedSections.length ? 'partial' : 'real',
    failedSections,
    dashboard,
    trendRows: rows(trend.rows),
    kolSummary: settledValue(kolSummaryResult, {}, failedSections, 'kol-pool-summary'),
    kolDistribution: settledValue(kolDistributionResult, {}, failedSections, 'kol-distribution'),
    officialMatrix: mergeDashboardOfficialSummary(settledValue(officialMatrixResult, {}, failedSections, 'official-channel-matrix'), dashboard),
    competitorDashboard: settledValue(competitorResult, {}, failedSections, 'competitors-dashboard'),
    brandSignals: rows(brandSignals.signals),
    recentContent: rows(recentContent.items),
    agentsStatus: settledValue(agentsStatusResult, {}, failedSections, 'agents-status'),
    copilotBrief: settledValue(copilotBriefResult, {}, failedSections, 'copilot-brief'),
    tasksStatus: settledValue(tasksStatusResult, {}, failedSections, 'tasks'),
    recommendationBacklog: settledValue(recommendationBacklogResult, {}, failedSections, 'recommendation-feedback-backlog'),
    alerts: payloadRows(alertsPayload, 'alerts', 'items', 'rows'),
    loadedAt: new Date().toISOString(),
  };
}
