import type {
  VkpiAttributionRow,
  VkpiCostRow,
  VkpiLinkRow,
  VkpiProjectRow,
} from '../../components/vkpi/vkpiTypes';
import {
  arrayValue,
  centsToUsd,
  durationLabel,
  numberValue,
  objectValue,
  platformLabel,
  stageValue,
} from '../dashboard';

type Row = Record<string, unknown>;

const stageCountKeys = ['discovery', 'contacted', 'replied', 'agreed', 'shipped', 'received', 'published', 'measured', 'closed'];

function buildStageCounts(value: unknown): Record<string, number> {
  const raw = objectValue(value);
  return Object.fromEntries(stageCountKeys.map((key) => [key, numberValue(raw[key])]));
}

function buildPlatforms(value: unknown, fallback: unknown): ReturnType<typeof platformLabel>[] {
  const raw = arrayValue(value);
  const source = raw.length ? raw : [fallback];
  return Array.from(new Set(source.map((item) => platformLabel(item)).filter(Boolean)));
}

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
    // 2026-07-18 诚实空态修:无归因链路时 gmv/cost 应为 null(显「—」待数据),
    // 不能 centsToUsd(undefined)=0 把「链路不存在」谎报成「实测为零」。
    // 有归因(含值为 0)才给数值。revenue_cents/cost_cents 列不存在时回退 null。
    const attributedGmv = revenueByProject.get(id);
    const gmv = attributedGmv != null
      ? attributedGmv
      : (project.revenue_cents != null ? centsToUsd(project.revenue_cents) : null);
    const attributedCost = costByProject.get(id);
    const cost = attributedCost != null
      ? attributedCost
      : (project.cost_cents != null ? centsToUsd(project.cost_cents) : null);
    const startedAt = String(project.started_at || project.first_event_at || project.created_at || '');
    const closedAt = String(project.closed_at || '');
    const stageStartedAt = String(project.current_stage_started_at || project.last_activity_at || project.updated_at || '');
    const latestEvidencePublishDate = String(project.latest_publish_date || project.latestEvidencePublishDate || '');
    const views = numberValue(project.total_views || project.views || project.view_count || project.play_count || project.impressions || project.content_views);
    return {
      id,
      kolId: project.kol_id ? String(project.kol_id) : undefined,
      kolName: String(project.kol_name || project.channel_name || `KOL ${project.kol_id || ''}`).trim() || '未知 KOL',
      kolHandle: String(project.handle || project.kol_platform || project.platform || '-'),
      platform: platformLabel(project.kol_platform || project.platform),
      campaign: String(project.project_name || project.project_uid || '未命名项目'),
      stage: stageValue(project.stage),
      followStatus: project.follow_status === 'paused' || project.followStatus === 'paused' ? 'paused' : 'active',
      isStarred: project.is_starred === true || project.isStarred === true || project.is_starred === 1,
      starredAt: String(project.starred_at || project.starredAt || ''),
      latestMessageAt: String(project.last_activity_at || project.updated_at || '-'),
      latestMessageSource: 'Manual note',
      views,
      clicks: clicks || null,
      orders: numberValue(project.orders) || null,
      gmv,
      cost,
      roi: (gmv != null && cost != null && cost !== 0) ? Number((gmv / cost).toFixed(2)) : null,
      ownerId: project.assigned_staff_id ? String(project.assigned_staff_id) : project.created_by_staff_id ? String(project.created_by_staff_id) : undefined,
      ownerName: String(project.staff_name || project.assigned_staff_id || '未分配'),
      productSku: String(project.product_sku || ''),
      productName: String(project.product_name || ''),
      marketplace: String(project.marketplace || ''),
      priority: String(project.priority || ''),
      shopifyLink: String(project.shopify_link || ''),
      kolCount: numberValue(project.kol_count),
      kolWithEvidence: numberValue(project.kol_with_evidence),
      evidenceCount: numberValue(project.evidence_count),
      latestEvidencePublishDate,
      platforms: buildPlatforms(project.platforms, project.kol_platform || project.platform),
      stageCounts: buildStageCounts(project.stage_counts || project.stageCounts),
      publishedCount: numberValue(project.published_count || project.publishedCount),
      healthScore: numberValue(project.health_score || project.healthScore),
      healthBasis: String(project.health_basis || project.healthBasis || ''),
      healthBreakdown: objectValue(project.health_breakdown || project.healthBreakdown) as Record<string, number>,
      needsFollowupCount: project.needs_followup_count == null ? null : numberValue(project.needs_followup_count),
      overdueCount: project.overdue_count == null ? null : numberValue(project.overdue_count),
      currentFocus: String(project.current_focus || project.currentFocus || ''),
      bottleneck: String(project.bottleneck || ''),
      churnedCount: numberValue(project.churned_count || project.churnedCount),
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
