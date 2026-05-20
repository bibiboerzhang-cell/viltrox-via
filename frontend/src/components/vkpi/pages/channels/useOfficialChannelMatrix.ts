import { useCallback, useEffect, useState } from 'react';
import { getOfficialChannelMatrix } from '../../../../services/vkpi.ui-api';
import type { ChannelContentPost, OfficialChannelAccount, OfficialChannelPlatform } from './channelTypes';

type Row = Record<string, unknown>;

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
    postedAt: text(row.posted_at || row.postedAt),
    views: numberValue(row.views),
    likes: numberValue(row.likes),
    comments: numberValue(row.comments),
    shares: numberValue(row.shares),
    accountLevel: Boolean(row.account_level || row.accountLevel),
    viewsUnavailable: Boolean(row.views_unavailable || row.viewsUnavailable),
  };
}

function mapAccount(row: Row): OfficialChannelAccount {
  return {
    id: numberValue(row.id),
    staffId: numberValue(row.staff_id ?? row.staffId),
    staffName: text(row.staff_name ?? row.staffName ?? row.staff_email ?? row.staffEmail, '未分配'),
    staffEmail: text(row.staff_email ?? row.staffEmail),
    staffAvatarUrl: text(row.staff_avatar_url ?? row.staffAvatarUrl),
    staffRole: text(row.staff_role ?? row.staffRole),
    staffActive: boolValue(row.staff_active ?? row.staffActive),
    platform: text(row.platform),
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
    totalLikes: numberValue(row.total_likes || row.totalLikes),
    totalComments: numberValue(row.total_comments || row.totalComments),
    engagementRate: numberValue(row.engagement_rate || row.engagementRate),
    posts: rows(row.posts).map(mapPost),
  };
}

function mapPlatform(row: Row): OfficialChannelPlatform {
  return {
    platform: text(row.platform, 'other'),
    label: text(row.label || row.platform, 'Other'),
    totalViews: numberValue(row.total_views || row.totalViews),
    totalPosts: numberValue(row.total_posts || row.totalPosts),
    totalFollowers: numberValue(row.total_followers || row.totalFollowers),
    followersDelta: numberValue(row.followers_delta ?? row.followersDelta),
    postsDelta: numberValue(row.posts_delta ?? row.postsDelta),
    viewsDelta: numberValue(row.views_delta ?? row.viewsDelta),
    accounts: rows(row.accounts).map(mapAccount),
  };
}

export function useOfficialChannelMatrix(apiToken?: string, viewAsStaffId?: string) {
  const [platforms, setPlatforms] = useState<OfficialChannelPlatform[]>([]);
  const [accountCount, setAccountCount] = useState(0);
  const [postCount, setPostCount] = useState(0);
  const [totalViews, setTotalViews] = useState(0);
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
    setLoading(true);
    setError('');
    try {
      const response = await getOfficialChannelMatrix(apiToken, { limit: 20, viewAsStaffId });
      setPlatforms(rows(response.platforms).map(mapPlatform));
      setAccountCount(numberValue(response.account_count));
      setPostCount(numberValue(response.post_count));
      setTotalViews(numberValue(response.total_views));
    } catch (requestError) {
      setPlatforms([]);
      setAccountCount(0);
      setPostCount(0);
      setTotalViews(0);
      setError(requestError instanceof Error ? requestError.message : '官方账号矩阵加载失败');
    } finally {
      setLoading(false);
    }
  }, [apiToken, viewAsStaffId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { platforms, accountCount, postCount, totalViews, loading, error, refresh };
}
