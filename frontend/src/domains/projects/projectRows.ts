import type {
  VkpiAttributionRow,
  VkpiCostRow,
  VkpiLinkRow,
  VkpiProjectRow,
} from '../../components/vkpi/vkpiTypes';
import {
  centsToUsd,
  durationLabel,
  numberValue,
  platformLabel,
  stageValue,
} from '../dashboard';

type Row = Record<string, unknown>;

export function buildDashboardProjects(
  projects: Row[],
  links: VkpiLinkRow[],
  attributions: VkpiAttributionRow[],
  costs: VkpiCostRow[],
): VkpiProjectRow[] {
  const linksByProject = new Map<string, VkpiLinkRow[]>();
  links.forEach((link) => {
    if (link.projectId) linksByProject.set(link.projectId, [...(linksByProject.get(link.projectId) || []), link]);
  });
  const revenueByProject = new Map<string, number>();
  attributions.forEach((row) => {
    if (row.projectId) revenueByProject.set(row.projectId, (revenueByProject.get(row.projectId) || 0) + row.revenue);
  });
  const costByProject = new Map<string, number>();
  costs.forEach((row) => {
    if (row.projectId && row.status !== 'void') costByProject.set(row.projectId, (costByProject.get(row.projectId) || 0) + row.amount);
  });

  return projects.map((project) => {
    const id = String(project.id || project.project_uid || '');
    const projectLinks = linksByProject.get(id) || [];
    const clicks = projectLinks.reduce((sum, link) => sum + link.validClicks, 0);
    const gmv = revenueByProject.get(id) || centsToUsd(project.revenue_cents);
    const cost = costByProject.get(id) || centsToUsd(project.cost_cents);
    const startedAt = String(project.started_at || project.first_event_at || project.created_at || '');
    const closedAt = String(project.closed_at || '');
    const stageStartedAt = String(project.current_stage_started_at || project.last_activity_at || project.updated_at || '');
    const views = numberValue(project.total_views || project.views || project.view_count || project.play_count || project.impressions || project.content_views);
    return {
      id,
      kolId: project.kol_id ? String(project.kol_id) : undefined,
      kolName: String(project.kol_name || project.channel_name || `KOL ${project.kol_id || ''}`).trim() || '未知 KOL',
      kolHandle: String(project.handle || project.kol_platform || project.platform || '-'),
      platform: platformLabel(project.kol_platform || project.platform),
      campaign: String(project.project_name || project.project_uid || '未命名项目'),
      stage: stageValue(project.stage),
      latestMessageAt: String(project.last_activity_at || project.updated_at || '-'),
      latestMessageSource: 'Manual note',
      views,
      clicks: clicks || null,
      orders: numberValue(project.orders) || null,
      gmv,
      cost,
      roi: cost ? Number((gmv / cost).toFixed(2)) : null,
      ownerId: project.assigned_staff_id ? String(project.assigned_staff_id) : project.created_by_staff_id ? String(project.created_by_staff_id) : undefined,
      ownerName: String(project.staff_name || project.assigned_staff_id || '未分配'),
      productSku: String(project.product_sku || ''),
      productName: String(project.product_name || ''),
      marketplace: String(project.marketplace || ''),
      priority: String(project.priority || ''),
      shopifyLink: String(project.shopify_link || ''),
      createdAt: String(project.created_at || ''),
      startedAt,
      closedAt,
      currentStageStartedAt: stageStartedAt,
      totalDurationLabel: durationLabel(startedAt || project.created_at, closedAt || undefined),
      stageDurationLabel: durationLabel(stageStartedAt),
      stageEventCount: numberValue(project.stage_event_count),
      updatedAt: String(project.updated_at || project.created_at || '-'),
    };
  });
}
