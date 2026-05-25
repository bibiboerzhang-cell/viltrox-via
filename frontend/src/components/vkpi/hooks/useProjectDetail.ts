import { useCallback, useEffect, useMemo, useState } from 'react';
import { getProjectDetail } from '../../../domains/projects';
import type { VkpiProjectDetail, VkpiProjectRow } from '../vkpiTypes';
import { coerceProjectStage, platformFromRaw, safeNumber, textValue } from '../shared/vkpiDataUtils';

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
  const refresh = useCallback(async () => {
    setReloadKey((key) => key + 1);
  }, []);

  return {
    ...state,
    project,
    refresh,
  };
}
