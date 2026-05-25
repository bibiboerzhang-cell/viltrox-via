import { useCallback, useEffect, useState } from 'react';
import { getOfficialChannelGapReport } from '../../../../domains/channels';
import type { ChannelGapAccount, ChannelGapIssue } from './channelTypes';

type Row = Record<string, unknown>;

function text(value: unknown, fallback = '') {
  return String(value ?? fallback).trim();
}

function numberValue(value: unknown) {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function rows(value: unknown): Row[] {
  return Array.isArray(value) ? value.filter((item): item is Row => Boolean(item) && typeof item === 'object') : [];
}

function mapIssue(row: Row): ChannelGapIssue {
  return {
    key: text(row.key, 'unknown'),
    label: text(row.label, '数据缺口'),
    priority: numberValue(row.priority),
  };
}

function mapAccount(row: Row): ChannelGapAccount {
  return {
    id: numberValue(row.id),
    platform: text(row.platform),
    platformLabel: text(row.platform_label || row.platformLabel || row.platform),
    displayName: text(row.display_name || row.displayName || row.handle, '官方账号'),
    handle: text(row.handle),
    accountUrl: text(row.account_url || row.accountUrl),
    staffId: numberValue(row.staff_id || row.staffId),
    staffName: text(row.staff_name || row.staffName, '未分配'),
    followers: numberValue(row.followers),
    postsCount: numberValue(row.posts_count || row.postsCount),
    totalViews: numberValue(row.total_views || row.totalViews),
    postSampleCount: numberValue(row.post_sample_count || row.postSampleCount),
    provider: text(row.provider, 'manual'),
    providerReady: Boolean(row.provider_ready || row.providerReady),
    autoRefillSupported: Boolean(row.auto_refill_supported || row.autoRefillSupported),
    syncStatus: text(row.sync_status || row.syncStatus),
    lastSyncAt: text(row.last_sync_at || row.lastSyncAt),
    lastSyncError: text(row.last_sync_error || row.lastSyncError),
    recommendedAction: text(row.recommended_action || row.recommendedAction),
    issues: rows(row.issues).map(mapIssue),
  };
}

export function useOfficialChannelGaps(apiToken?: string, viewAsStaffId?: string) {
  const [accounts, setAccounts] = useState<ChannelGapAccount[]>([]);
  const [summary, setSummary] = useState<Row>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    if (!apiToken) {
      setAccounts([]);
      setSummary({});
      setError('');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const response = await getOfficialChannelGapReport(apiToken, { limit: 50, viewAsStaffId });
      setAccounts(rows(response.accounts).map(mapAccount));
      setSummary((response.summary || {}) as Row);
    } catch (requestError) {
      setAccounts([]);
      setSummary({});
      setError(requestError instanceof Error ? requestError.message : '账号缺口清单加载失败');
    } finally {
      setLoading(false);
    }
  }, [apiToken, viewAsStaffId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { accounts, summary, loading, error, refresh };
}
