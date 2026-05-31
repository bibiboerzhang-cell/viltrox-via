import { useCallback, useEffect, useMemo, useState } from 'react';
import { getProjectDetail } from '../../../domains/projects';
import type { VkpiProjectDetail, VkpiProjectRow } from '../vkpiTypes';
import { coerceProjectStage, objectValue, platformFromRaw, safeNumber, textValue } from '../shared/vkpiDataUtils';

interface UseProjectDetailArgs {
  apiToken?: string;
  projectId?: string | null;
  fallbackProject?: VkpiProjectRow;
}

interface ProjectDetailState {
  detail: VkpiProjectDetail | null;
  loading: boolean;
  error: string;
  notFound: boolean;
}

function centsToUsd(value: unknown): number {
  return safeNumber(value) / 100;
}

function durationLabel(start?: string) {
  const raw = String(start || '').trim();
  if (!raw || raw === '-') return '-';
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return '-';
  const days = Math.max(0, Math.ceil((Date.now() - parsed.getTime()) / 86400000));
  if (days <= 0) return '今天';
  return `${days} 天`;
}

function firstText(...values: unknown[]) {
  for (const value of values) {
    const text = String(value ?? '').trim();
    if (text) return text;
  }
  return '';
}

function sumCents(rows: Array<Record<string, unknown>>, ...keys: string[]) {
  return rows.reduce((sum, row) => {
    for (const key of keys) {
      if (row[key] != null) return sum + safeNumber(row[key]);
    }
    return sum;
  }, 0);
}

function sumNumber(rows: Array<Record<string, unknown>>, ...keys: string[]) {
  return rows.reduce((sum, row) => {
    for (const key of keys) {
      if (row[key] != null) return sum + safeNumber(row[key]);
    }
    return sum;
  }, 0);
}

function boolValue(value: unknown) {
  if (typeof value === 'boolean') return value;
  return ['1', 'true', 'yes'].includes(String(value || '').trim().toLowerCase());
}

function jsonObject(value: unknown): Record<string, unknown> {
  if (!value) return {};
  if (typeof value === 'object') return value as Record<string, unknown>;
  try {
    const parsed = JSON.parse(String(value));
    return parsed && typeof parsed === 'object' ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

function currentStageStartedAt(detail: VkpiProjectDetail, stage: string, project: Record<string, unknown>) {
  const explicit = firstText(project.current_stage_started_at, project.stage_started_at);
  if (explicit) return explicit;
  const matchedEvent = detail.events.find((event) => String(event.to_stage || '').toLowerCase() === stage);
  return firstText(matchedEvent?.effective_at, project.last_activity_at, project.updated_at, project.created_at);
}

function firstEventAt(detail: VkpiProjectDetail, project: Record<string, unknown>) {
  const oldest = [...detail.events].reverse().find((event) => event.effective_at || event.created_at);
  return firstText(project.started_at, oldest?.effective_at, oldest?.created_at, project.created_at);
}

function assignmentStage(value: unknown): VkpiProjectRow['stage'] {
  const raw = String(value || '').trim().toLowerCase();
  if (raw === 'discovered') return 'discovery';
  if (raw === 'device_sent') return 'shipped';
  if (raw === 'arrived') return 'received';
  if (raw === 'content_posted') return 'published';
  if (raw === 'reviewed') return 'measured';
  if (raw === 'churned') return 'closed';
  return coerceProjectStage(raw);
}

function assignmentToProjectRow(
  item: Record<string, unknown>,
  detail: VkpiProjectDetail,
  projectRow: VkpiProjectRow,
  index: number,
): VkpiProjectRow {
  const project = detail.project || {};
  const assignmentId = firstText(item.assignment_id, item.id) || String(index + 1);
  const kolPoolId = firstText(item.kol_pool_id);
  const stage = assignmentStage(item.stage);
  const startedAt = firstText(item.created_at, project.created_at, projectRow.startedAt);
  const stageStartedAt = firstText(item.updated_at, item.created_at, project.updated_at, projectRow.currentStageStartedAt);
  const latestMessageAt = firstText(item.latest_publish_date, item.updated_at, item.created_at, project.updated_at, projectRow.latestMessageAt);
  const views = safeNumber(item.total_views);
  const shippingMeta = jsonObject(jsonObject(item.metadata_json).shipping);
  const evidenceCount = safeNumber(item.evidence_count);
  const ownerId = firstText(item.assigned_staff_id, project.assigned_staff_id, project.created_by_staff_id, projectRow.ownerId);
  const ownerName = firstText(item.assigned_staff_name, project.staff_name, project.owner_name, projectRow.ownerName, ownerId);
  return {
    id: `assignment:${assignmentId}`,
    projectId: projectRow.id,
    assignmentId,
    kolPoolId,
    kolId: kolPoolId || undefined,
    kolName: textValue(item.kol_name || item.display_name || item.handle, '未知 KOL'),
    kolHandle: textValue(item.handle || item.profile_url || item.kol_name, '-'),
    kolAvatar: firstText(item.avatar_url) || undefined,
    kolProfileUrl: firstText(item.profile_url, item.channel_url) || undefined,
    videoUrl: firstText(item.video_url, item.content_url, item.evidence_url) || undefined,
    evidenceUrl: firstText(item.evidence_url, item.video_url, item.content_url) || undefined,
    evidenceTitle: firstText(item.evidence_title, item.video_title, item.title) || undefined,
    evidenceThumbnailUrl: firstText(item.evidence_thumbnail_url, item.thumbnail_url) || undefined,
    evidencePublishDate: firstText(item.evidence_publish_date) || undefined,
    latestVideoUrl: firstText(item.latest_video_url, item.latest_evidence_url) || undefined,
    latestEvidenceUrl: firstText(item.latest_evidence_url, item.latest_video_url) || undefined,
    latestEvidenceTitle: firstText(item.latest_evidence_title) || undefined,
    latestEvidenceThumbnailUrl: firstText(item.latest_evidence_thumbnail_url) || undefined,
    latestEvidencePublishDate: firstText(item.latest_evidence_publish_date, item.latest_publish_date) || undefined,
    platform: platformFromRaw(item.kol_platform || item.platform),
    campaign: projectRow.campaign,
    stage,
    latestMessageAt,
    latestMessageSource: 'Manual note',
    views,
    likes: safeNumber(item.total_likes),
    comments: safeNumber(item.total_comments),
    clicks: null,
    orders: null,
    gmv: 0,
    cost: 0,
    roi: null,
    ownerId: ownerId || undefined,
    ownerName: textValue(ownerName, '未分配'),
    ownerAvatar: projectRow.ownerAvatar,
    productSku: projectRow.productSku,
    productName: projectRow.productName,
    marketplace: projectRow.marketplace,
    priority: projectRow.priority,
    shopifyLink: projectRow.shopifyLink,
    trackingNumber: boolValue(item.is_placeholder_tracking) ? undefined : firstText(item.tracking_number) || undefined,
    trackingCarrier: firstText(shippingMeta.carrier) || undefined,
    trackingStatus: firstText(shippingMeta.status) || undefined,
    isFakeTracking: boolValue(item.is_placeholder_tracking),
    kolCount: 1,
    kolWithEvidence: item.has_video_evidence ? 1 : 0,
    evidenceCount,
    createdAt: startedAt,
    startedAt,
    closedAt: projectRow.closedAt,
    currentStageStartedAt: stageStartedAt,
    totalDurationLabel: durationLabel(startedAt),
    stageDurationLabel: durationLabel(stageStartedAt),
    stageEventCount: evidenceCount,
    updatedAt: firstText(item.updated_at, latestMessageAt, projectRow.updatedAt),
  };
}

export function projectDetailToRow(detail: VkpiProjectDetail, fallback?: VkpiProjectRow): VkpiProjectRow {
  const project = detail.project || {};
  const stage = coerceProjectStage(project.stage || fallback?.stage);
  const startedAt = firstEventAt(detail, project);
  const stageStartedAt = currentStageStartedAt(detail, stage, project);
  const linkSummary = detail.link_summary || {};
  const validClicks = safeNumber(linkSummary.valid_click_count || linkSummary.click_count);
  const revenue = centsToUsd(detail.roi?.revenue_cents ?? sumCents(detail.sales_attributions, 'revenue_cents', 'gmv_cents'));
  const cost = centsToUsd(detail.roi?.cost_cents ?? sumCents(detail.costs, 'amount_cents', 'cost_cents'));
  const contentViews = sumNumber(detail.content_posts || [], 'views', 'view_count', 'play_count', 'impressions');
  const contentLikes = sumNumber(detail.content_posts || [], 'likes', 'like_count');
  const contentComments = sumNumber(detail.content_posts || [], 'comments', 'comment_count');
  const projectViews = safeNumber(project.total_views || project.views || project.view_count || project.play_count || project.impressions || project.content_views);
  const orders = safeNumber(linkSummary.order_count) || detail.link_orders?.length || detail.sales_attributions.length || null;
  const latestMessageAt = firstText(
    detail.events[0]?.effective_at,
    detail.messages?.[0]?.captured_at,
    detail.content_posts?.[0]?.published_at,
    project.last_activity_at,
    project.updated_at,
    fallback?.latestMessageAt,
  );

  return {
    id: textValue(project.id || fallback?.id, fallback?.id || ''),
    kolId: firstText(project.kol_id, fallback?.kolId) || undefined,
    kolName: textValue(project.kol_name || project.channel_name || fallback?.kolName, fallback?.kolName || '未知 KOL'),
    kolHandle: textValue(project.handle || project.kol_handle || project.channel_handle || fallback?.kolHandle, fallback?.kolHandle || '-'),
    kolAvatar: firstText(project.avatar_url, project.kol_avatar, fallback?.kolAvatar) || undefined,
    platform: platformFromRaw(project.kol_platform || project.platform || fallback?.platform),
    campaign: textValue(project.project_name || project.project_uid || fallback?.campaign, fallback?.campaign || '未命名推广'),
    stage,
    latestMessageAt,
    latestMessageSource: fallback?.latestMessageSource || 'Manual note',
    views: contentViews || projectViews || fallback?.views || 0,
    likes: contentLikes || fallback?.likes || 0,
    comments: contentComments || fallback?.comments || 0,
    clicks: validClicks || fallback?.clicks || null,
    orders,
    gmv: revenue || fallback?.gmv || 0,
    cost: cost || fallback?.cost || 0,
    roi: cost ? Number((revenue / cost).toFixed(2)) : fallback?.roi || null,
    ownerId: firstText(project.assigned_staff_id, project.created_by_staff_id, fallback?.ownerId) || undefined,
    ownerName: textValue(project.staff_name || project.owner_name || project.assigned_staff_id || fallback?.ownerName, fallback?.ownerName || '未分配'),
    ownerAvatar: fallback?.ownerAvatar,
    productSku: firstText(project.product_sku, fallback?.productSku),
    productName: firstText(project.product_name, fallback?.productName),
    marketplace: firstText(project.marketplace, fallback?.marketplace),
    priority: firstText(project.priority, fallback?.priority),
    shopifyLink: firstText(project.shopify_link, project.shopify_url, fallback?.shopifyLink),
    trackingNumber: boolValue(project.is_placeholder_tracking || project.is_fake_tracking) ? undefined : firstText(project.tracking_number, fallback?.trackingNumber) || undefined,
    trackingCarrier: firstText(project.tracking_carrier, fallback?.trackingCarrier) || undefined,
    trackingStatus: firstText(project.tracking_status, fallback?.trackingStatus) || undefined,
    isFakeTracking: boolValue(project.is_placeholder_tracking || project.is_fake_tracking || fallback?.isFakeTracking),
    kolCount: safeNumber(project.kol_count) || fallback?.kolCount,
    kolWithEvidence: safeNumber(project.kol_with_evidence) || fallback?.kolWithEvidence,
    evidenceCount: safeNumber(project.evidence_count) || fallback?.evidenceCount,
    platforms: fallback?.platforms,
    stageCounts: objectValue(project.stage_counts || project.stageCounts) as Record<string, number>,
    publishedCount: safeNumber(project.published_count || project.publishedCount) || fallback?.publishedCount,
    healthScore: safeNumber(project.health_score || project.healthScore) || fallback?.healthScore,
    healthBasis: firstText(project.health_basis, project.healthBasis, fallback?.healthBasis),
    healthBreakdown: objectValue(project.health_breakdown || project.healthBreakdown || fallback?.healthBreakdown) as Record<string, number>,
    needsFollowupCount: project.needs_followup_count == null ? fallback?.needsFollowupCount : safeNumber(project.needs_followup_count),
    overdueCount: project.overdue_count == null ? fallback?.overdueCount : safeNumber(project.overdue_count),
    currentFocus: firstText(project.current_focus, project.currentFocus, fallback?.currentFocus),
    bottleneck: firstText(project.bottleneck, fallback?.bottleneck),
    churnedCount: safeNumber(project.churned_count || project.churnedCount) || fallback?.churnedCount,
    createdAt: firstText(project.created_at, fallback?.createdAt),
    startedAt,
    closedAt: firstText(project.closed_at, fallback?.closedAt),
    currentStageStartedAt: stageStartedAt,
    totalDurationLabel: durationLabel(startedAt),
    stageDurationLabel: durationLabel(stageStartedAt),
    stageEventCount: detail.events.length || fallback?.stageEventCount || 0,
    updatedAt: firstText(project.updated_at, latestMessageAt, fallback?.updatedAt),
  };
}

export function useProjectDetail({ apiToken, projectId, fallbackProject }: UseProjectDetailArgs) {
  const [reloadKey, setReloadKey] = useState(0);
  const [state, setState] = useState<ProjectDetailState>({
    detail: null,
    loading: false,
    error: '',
    notFound: false,
  });

  useEffect(() => {
    if (!projectId) {
      setState({ detail: null, loading: false, error: '', notFound: false });
      return;
    }
    if (!apiToken) {
      setState({
        detail: null,
        loading: false,
        error: '缺少 API token，当前只能显示列表快照。',
        notFound: false,
      });
      return;
    }

    let cancelled = false;
    setState((current) => ({ ...current, loading: true, error: '', notFound: false }));
    getProjectDetail(apiToken, projectId)
      .then((detail) => {
        if (cancelled) return;
        setState({ detail, loading: false, error: '', notFound: false });
      })
      .catch((error) => {
        if (cancelled) return;
        const message = error instanceof Error ? error.message : '项目详情加载失败';
        const notFound = /404|not found|不存在|project not found/i.test(message);
        setState({
          detail: null,
          loading: false,
          error: notFound ? '项目不存在' : message,
          notFound,
        });
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken, projectId, reloadKey]);

  const project = useMemo(() => (
    state.detail ? projectDetailToRow(state.detail, fallbackProject) : fallbackProject
  ), [fallbackProject, state.detail]);
  const participatingRows = useMemo(() => {
    if (!state.detail || !project) return [];
    const rawRows = state.detail.participating_kols || state.detail.project_kol_assignments || [];
    return rawRows.map((item, index) => assignmentToProjectRow(item, state.detail as VkpiProjectDetail, project, index));
  }, [project, state.detail]);
  const refresh = useCallback(async () => {
    setReloadKey((key) => key + 1);
  }, []);

  return {
    ...state,
    project,
    participatingRows,
    refresh,
  };
}
