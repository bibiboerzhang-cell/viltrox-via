import { useCallback, useEffect, useState } from 'react';
import { getOfficialChannelMatrix } from '../../../../domains/channels';
import type { ChannelContentPost, OfficialChannelAccount, OfficialChannelPlatform } from './channelTypes';

type Row = Record<string, unknown>;
type MatrixSnapshot = {
  platforms: OfficialChannelPlatform[];
  accountCount: number;
  postCount: number;
  totalViews: number;
  fetchedAt: number;
};

const MATRIX_STALE_MS = 30_000;
const MATRIX_STORAGE_TTL_MS = 24 * 60 * 60 * 1000;
const MATRIX_POST_SAMPLE_LIMIT = 4;
const MATRIX_STORAGE_PREFIX = 'vkpi:mykol:official-matrix:v2:';
const MATRIX_CACHE = new Map<string, MatrixSnapshot>();

function matrixCacheKey(apiToken?: string, viewAsStaffId?: string) {
  return `${viewAsStaffId || 'all'}:${apiToken ? apiToken.slice(-10) : 'no-token'}`;
}

function readStoredMatrix(cacheKey: string): MatrixSnapshot | undefined {
  if (typeof window === 'undefined') return undefined;
  try {
    const raw = window.localStorage.getItem(`${MATRIX_STORAGE_PREFIX}${cacheKey}`);
    if (!raw) return undefined;
    const parsed = JSON.parse(raw) as MatrixSnapshot;
    if (!parsed || !Array.isArray(parsed.platforms) || !parsed.fetchedAt) return undefined;
    if (Date.now() - parsed.fetchedAt > MATRIX_STORAGE_TTL_MS) return undefined;
    MATRIX_CACHE.set(cacheKey, parsed);
    return parsed;
  } catch {
    return undefined;
  }
}

function writeStoredMatrix(cacheKey: string, snapshot: MatrixSnapshot) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(`${MATRIX_STORAGE_PREFIX}${cacheKey}`, JSON.stringify(snapshot));
  } catch {
    // Local storage can be full or disabled; memory cache still keeps the page responsive.
  }
}

function text(value: unknown, fallback = '') {
  return String(value ?? fallback).trim();
}

function numberValue(value: unknown) {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function boolValue(value: unknown, fallback = true) {
  if (value === undefined || value === null || value === '') return fallback;
  return value === true || value === 'true' || value === 1 || value === '1';
}

function timeValue(value: string) {
  if (!value) return 0;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 0 : date.getTime();
}

function rows(value: unknown): Row[] {
  return Array.isArray(value) ? value.filter((item): item is Row => Boolean(item) && typeof item === 'object') : [];
}

function textList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((item) => text(item)).filter(Boolean);
  if (typeof value === 'string') {
    const raw = value.trim();
    if (!raw) return [];
    if (raw.startsWith('[')) {
      try {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) return parsed.map((item) => text(item)).filter(Boolean);
      } catch {
        return [raw];
      }
    }
    return [raw];
  }
  return [];
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function isRedditPlatform(value: unknown) {
  return text(value).toLowerCase() === 'reddit';
}

function viewsUnavailableReason(platform: unknown, fallback: unknown) {
  const explicit = text(fallback);
  if (explicit) return explicit;
  if (isRedditPlatform(platform)) return 'Reddit 不公开帖子播放量；今年分析使用点赞、评论和站内评分。';
  return '';
}

function mapPost(row: Row): ChannelContentPost {
  return {
    id: text(row.id || row.url),
    sourceId: text(row.source_id || row.sourceId),
    title: text(row.title, '内容快照'),
    url: text(row.url),
    mediaUrl: text(row.media_url || row.mediaUrl),
    videoUrl: text(row.video_url || row.videoUrl),
    imageUrls: textList(row.image_urls || row.imageUrls),
    mediaUrls: textList(row.media_urls || row.mediaUrls),
    mediaType: text(row.media_type || row.mediaType),
    mediaKind: text(row.media_kind || row.mediaKind),
    mediaStatus: text(row.media_status || row.mediaStatus),
    mediaStatusLabel: text(row.media_status_label || row.mediaStatusLabel),
    mediaStatusReason: text(row.media_status_reason || row.mediaStatusReason),
    mediaQuality: text(row.media_quality || row.mediaQuality),
    postedAt: text(row.posted_at || row.postedAt),
    views: numberValue(row.views),
    likes: numberValue(row.likes),
    comments: numberValue(row.comments),
    shares: numberValue(row.shares),
    accountLevel: Boolean(row.account_level || row.accountLevel),
    viewsUnavailable: Boolean(row.views_unavailable || row.viewsUnavailable),
    viewsMetricLabel: text(row.views_metric_label || row.viewsMetricLabel),
    viewsUnavailableReason: text(row.views_unavailable_reason || row.viewsUnavailableReason),
  };
}

function mapAccount(row: Row): OfficialChannelAccount {
  const platform = text(row.platform);
  const viewsUnavailable = Boolean(row.views_unavailable || row.viewsUnavailable || isRedditPlatform(platform));
  return {
    id: numberValue(row.id),
    staffId: numberValue(row.staff_id ?? row.staffId),
    staffName: text(row.staff_name ?? row.staffName ?? row.staff_email ?? row.staffEmail, '未分配'),
    staffEmail: text(row.staff_email ?? row.staffEmail),
    staffAvatarUrl: text(row.staff_avatar_url ?? row.staffAvatarUrl),
    staffRole: text(row.staff_role ?? row.staffRole),
    staffActive: boolValue(row.staff_active ?? row.staffActive),
    platform,
    platformLabel: text(row.platform_label || row.platformLabel || row.platform),
    handle: text(row.handle),
    displayName: text(row.display_name || row.displayName || row.handle, '官方账号'),
    accountUrl: text(row.account_url || row.accountUrl),
    avatarUrl: text(row.avatar_url || row.avatarUrl),
    syncStatus: text(row.sync_status || row.syncStatus, 'not_configured'),
    lastSyncAt: text(row.last_sync_at || row.lastSyncAt),
    lastSyncError: text(row.last_sync_error || row.lastSyncError),
    followers: numberValue(row.followers),
    followersDelta: numberValue(row.followers_delta ?? row.followersDelta),
    postsCount: numberValue(row.posts_count || row.postsCount),
    postsDelta: numberValue(row.posts_delta ?? row.postsDelta),
    totalViews: numberValue(row.total_views || row.totalViews),
    viewsDelta: numberValue(row.views_delta ?? row.viewsDelta),
    viewsUnavailable,
    viewsMetricLabel: text(row.views_metric_label || row.viewsMetricLabel, viewsUnavailable ? '公开播放' : '播放'),
    viewsUnavailableReason: viewsUnavailableReason(platform, row.views_unavailable_reason || row.viewsUnavailableReason),
    totalLikes: numberValue(row.total_likes || row.totalLikes),
    totalComments: numberValue(row.total_comments || row.totalComments),
    engagementRate: numberValue(row.engagement_rate || row.engagementRate),
    baselineProtected: boolValue(row.baseline_protected ?? row.baselineProtected, false),
    baselineProtectedLabel: text(row.baseline_protected_label || row.baselineProtectedLabel),
    baselineProtectedReason: text(row.baseline_protected_reason || row.baselineProtectedReason),
    baselineProtectedFields: textList(row.baseline_protected_fields || row.baselineProtectedFields),
    baselineProtectedDetail: recordValue(row.baseline_protected_detail || row.baselineProtectedDetail),
    posts: rows(row.posts).map(mapPost),
  };
}

function mapPlatform(row: Row): OfficialChannelPlatform {
  const platform = text(row.platform, 'other');
  const accounts = rows(row.accounts).map(mapAccount);
  const viewsUnavailable = Boolean(row.views_unavailable || row.viewsUnavailable || isRedditPlatform(platform) || (accounts.length > 0 && accounts.every((account) => account.viewsUnavailable)));
  const latestSyncAt = accounts
    .map((account) => account.lastSyncAt)
    .filter(Boolean);
  const platformLastSyncAt = latestSyncAt.reduce(
    (latest, value) => (timeValue(value) > timeValue(latest) ? value : latest),
    text(row.last_sync_at || row.lastSyncAt),
  );
  return {
    platform,
    label: text(row.label || row.platform, 'Other'),
    totalViews: numberValue(row.total_views || row.totalViews),
    totalPosts: numberValue(row.total_posts || row.totalPosts),
    totalFollowers: numberValue(row.total_followers || row.totalFollowers),
    lastSyncAt: platformLastSyncAt,
    followersDelta: numberValue(row.followers_delta ?? row.followersDelta),
    postsDelta: numberValue(row.posts_delta ?? row.postsDelta),
    viewsDelta: numberValue(row.views_delta ?? row.viewsDelta),
    viewsUnavailable,
    viewsMetricLabel: text(row.views_metric_label || row.viewsMetricLabel, viewsUnavailable ? '公开播放' : '播放'),
    viewsUnavailableReason: viewsUnavailableReason(platform, row.views_unavailable_reason || row.viewsUnavailableReason),
    baselineProtected: boolValue(row.baseline_protected ?? row.baselineProtected, false) || accounts.some((account) => account.baselineProtected),
    baselineProtectedLabel: text(row.baseline_protected_label || row.baselineProtectedLabel),
    baselineProtectedReason: text(row.baseline_protected_reason || row.baselineProtectedReason),
    baselineProtectedAccounts: numberValue(row.baseline_protected_accounts || row.baselineProtectedAccounts),
    baselineProtectedFields: textList(row.baseline_protected_fields || row.baselineProtectedFields),
    baselineProtectedDetail: recordValue(row.baseline_protected_detail || row.baselineProtectedDetail),
    accounts,
  };
}

export function useOfficialChannelMatrix(apiToken?: string, viewAsStaffId?: string) {
  const cacheKey = matrixCacheKey(apiToken, viewAsStaffId);
  const initialSnapshot = apiToken ? MATRIX_CACHE.get(cacheKey) || readStoredMatrix(cacheKey) : undefined;
  const [platforms, setPlatforms] = useState<OfficialChannelPlatform[]>(initialSnapshot?.platforms || []);
  const [accountCount, setAccountCount] = useState(initialSnapshot?.accountCount || 0);
  const [postCount, setPostCount] = useState(initialSnapshot?.postCount || 0);
  const [totalViews, setTotalViews] = useState(initialSnapshot?.totalViews || 0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    if (!apiToken) {
      setPlatforms([]);
      setAccountCount(0);
      setPostCount(0);
      setTotalViews(0);
      setError('');
      return;
    }
    const cached = MATRIX_CACHE.get(cacheKey);
    if (cached) {
      setPlatforms(cached.platforms);
      setAccountCount(cached.accountCount);
      setPostCount(cached.postCount);
      setTotalViews(cached.totalViews);
    }
    setLoading(!cached);
    setError('');
    if (cached && Date.now() - cached.fetchedAt < MATRIX_STALE_MS) return;
    const startedAt = typeof performance !== 'undefined' ? performance.now() : Date.now();
    try {
      const response = await getOfficialChannelMatrix(apiToken, { limit: MATRIX_POST_SAMPLE_LIMIT, viewAsStaffId });
      const nextSnapshot = {
        platforms: rows(response.platforms).map(mapPlatform),
        accountCount: numberValue(response.account_count),
        postCount: numberValue(response.post_count),
        totalViews: numberValue(response.total_views),
        fetchedAt: Date.now(),
      };
      MATRIX_CACHE.set(cacheKey, nextSnapshot);
      writeStoredMatrix(cacheKey, nextSnapshot);
      setPlatforms(nextSnapshot.platforms);
      setAccountCount(nextSnapshot.accountCount);
      setPostCount(nextSnapshot.postCount);
      setTotalViews(nextSnapshot.totalViews);
      const viteMeta = import.meta as unknown as { env?: { DEV?: boolean } };
      if (viteMeta.env?.DEV) {
        const elapsed = Math.round((typeof performance !== 'undefined' ? performance.now() : Date.now()) - startedAt);
        console.info(`[V-KPI] official-matrix loaded in ${elapsed}ms`, {
          accounts: nextSnapshot.accountCount,
          posts: nextSnapshot.postCount,
          platforms: nextSnapshot.platforms.length,
        });
      }
    } catch (requestError) {
      if (!cached) {
        setPlatforms([]);
        setAccountCount(0);
        setPostCount(0);
        setTotalViews(0);
        setError(requestError instanceof Error ? requestError.message : '官方账号矩阵加载失败');
      } else {
        // Stale-while-revalidate: keep cached data visible and avoid replacing a usable page with a transient timeout.
        setError('');
      }
    } finally {
      setLoading(false);
    }
  }, [apiToken, cacheKey, viewAsStaffId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { platforms, accountCount, postCount, totalViews, loading, error, refresh };
}
