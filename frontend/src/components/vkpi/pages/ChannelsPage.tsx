import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  bindEmployeeChannel,
  listEmployeeChannels,
  syncEmployeeChannel,
} from '../../../domains/channels';
import { useTaskCenter } from '../../tasks/TaskCenter';
import { ChannelAccountList } from './channels/ChannelAccountList';
import { ChannelContentList } from './channels/ChannelContentList';
import { ChannelGapPanel } from './channels/ChannelGapPanel';
import { ChannelPlatformMatrix } from './channels/ChannelPlatformMatrix';
import { MyKolMatrix } from './channels/MyKolMatrix';
import { RedditAssessmentPanel } from './channels/RedditAssessmentPanel';
import { ChannelStaffProgress } from './channels/ChannelStaffProgress';
import { useOfficialChannelGaps } from './channels/useOfficialChannelGaps';
import { useOfficialChannelMatrix } from './channels/useOfficialChannelMatrix';
import type { OfficialChannelAccount, OfficialChannelPlatform } from './channels/channelTypes';
import type { VkpiDashboardData } from '../vkpiTypes';
import { CardHeader } from '../shared/CardHeader';
import { creatorPlatformOptions } from '../shared/vkpiConstants';
import { numberFormatter } from '../shared/vkpiFormatters';
import { platformDisplay, safeNumber } from '../shared/vkpiDataUtils';
import { PageShell } from './PageShell';
import './channels/channels.css';
import './channels/channelStaff.css';
import './channels/channelAccounts.css';
import './channels/channelContent.css';
import './channels/channelGaps.css';

interface ChannelsPageProps {
  apiToken?: string;
  viewMode: 'manager' | 'employee';
  data: VkpiDashboardData;
  onRefreshData?: () => void;
}

function channelSyncLabel(row: Record<string, unknown>) {
  const status = String(row.last_sync_status || row.sync_status || '').trim();
  const labels: Record<string, string> = {
    configured_pending_provider: '待同步',
    no_results: '抓取无结果',
    not_configured: '待配置',
    not_supported: '未接入补抓',
    synced: '已同步',
  };
  return labels[status] || status || '待同步';
}

function channelMetricCell(row: Record<string, unknown>, key: string) {
  if (row[key] != null) return numberFormatter.format(safeNumber(row[key]));
  const status = String(row.last_sync_status || row.sync_status || '');
  return status === 'no_results' ? '无快照' : '待同步';
}

export function ChannelsPage({ apiToken, viewMode, data, onRefreshData }: ChannelsPageProps) {
  const [channelsRows, setChannelsRows] = useState<Array<Record<string, unknown>>>([]);
  const [platform, setPlatform] = useState('youtube');
  const [handle, setHandle] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [viewAsStaffId, setViewAsStaffId] = useState('');
  const [selectedPlatform, setSelectedPlatform] = useState('');
  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(null);
  const [selectedStaffId, setSelectedStaffId] = useState<number | null>(null);
  const [showBindingList, setShowBindingList] = useState(false);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [pendingClicks, setPendingClicks] = useState<Set<string>>(new Set());
  const syncUnsubscribersRef = useRef<Map<string, () => void>>(new Map());
  const { waitForTask } = useTaskCenter();
  const matrix = useOfficialChannelMatrix(apiToken, viewMode === 'manager' ? viewAsStaffId || undefined : undefined);
  const gaps = useOfficialChannelGaps(apiToken, viewMode === 'manager' ? viewAsStaffId || undefined : undefined);

  const selectedPlatformData = useMemo(() => {
    return matrix.platforms.find((item) => item.platform === selectedPlatform);
  }, [matrix.platforms, selectedPlatform]);
  const visiblePlatformData = useMemo<OfficialChannelPlatform | undefined>(() => {
    if (!selectedPlatformData || selectedStaffId == null) return selectedPlatformData;
    const accounts = selectedPlatformData.accounts.filter((account) => account.staffId === selectedStaffId);
    return {
      ...selectedPlatformData,
      accounts,
      totalViews: accounts.reduce((sum, account) => sum + account.totalViews, 0),
      totalPosts: accounts.reduce((sum, account) => sum + account.postsCount, 0),
      totalFollowers: accounts.reduce((sum, account) => sum + account.followers, 0),
      followersDelta: accounts.reduce((sum, account) => sum + (account.followersDelta || 0), 0),
      postsDelta: accounts.reduce((sum, account) => sum + (account.postsDelta || 0), 0),
      viewsDelta: accounts.reduce((sum, account) => sum + (account.viewsDelta || 0), 0),
    };
  }, [selectedPlatformData, selectedStaffId]);
  const selectedAccount = useMemo(() => {
    return visiblePlatformData?.accounts.find((account) => account.id === selectedAccountId);
  }, [visiblePlatformData, selectedAccountId]);
  const visibleGapAccounts = useMemo(() => {
    return gaps.accounts.filter((account) => {
      const platformMatches = !selectedPlatform || account.platform === selectedPlatform;
      const staffMatches = selectedStaffId == null || account.staffId === selectedStaffId;
      return platformMatches && staffMatches;
    });
  }, [gaps.accounts, selectedPlatform, selectedStaffId]);

  const refresh = async () => {
    if (!apiToken) return;
    const channelsResult = await listEmployeeChannels(apiToken, viewMode === 'manager' ? viewAsStaffId || undefined : undefined).catch(() => ({ channels: [] }));
    setChannelsRows(channelsResult.channels || []);
  };

  useEffect(() => {
    void refresh();
  }, [apiToken, viewAsStaffId, viewMode]);

  useEffect(() => {
    return () => {
      syncUnsubscribersRef.current.forEach((unsubscribe) => unsubscribe());
      syncUnsubscribersRef.current.clear();
    };
  }, []);

  useEffect(() => {
    setSelectedStaffId(null);
    setSelectedAccountId(null);
  }, [viewAsStaffId]);

  useEffect(() => {
    if (!visiblePlatformData) {
      setSelectedAccountId(null);
      return;
    }
    const hasSelected = visiblePlatformData.accounts.some((account) => account.id === selectedAccountId);
    if (!hasSelected) {
      setSelectedAccountId(visiblePlatformData.accounts[0]?.id ?? null);
    }
  }, [visiblePlatformData, selectedAccountId]);

  const selectPlatform = (nextPlatform: string) => {
    setSelectedPlatform(nextPlatform);
    setSelectedAccountId(null);
  };

  const selectAccount = (account: OfficialChannelAccount) => {
    setSelectedAccountId(account.id);
  };

  const selectStaff = (staffId: number | null) => {
    setSelectedStaffId(staffId);
    setSelectedAccountId(null);
  };

  const submitChannel = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!apiToken || !handle.trim()) return;
    setBusy(true);
    try {
      await bindEmployeeChannel(apiToken, { platform, account_handle: handle.trim(), account_display_name: displayName.trim() || handle.trim(), api_key: apiKey.trim() || undefined }, viewMode === 'manager' ? viewAsStaffId || undefined : undefined);
      setHandle('');
      setApiKey('');
      setDisplayName('');
      setMessage('平台账号已绑定。API Key 已由后端加密存储；当前前端不会读取完整 Key。');
      await refresh();
      await matrix.refresh();
      await gaps.refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '平台绑定失败');
    } finally {
      setBusy(false);
    }
  };

  const clearPendingClick = (channelId: string) => {
    setPendingClicks((previous) => {
      const next = new Set(previous);
      next.delete(channelId);
      return next;
    });
  };

  const refreshAfterSync = () => {
    void refresh();
    void matrix.refresh();
    void gaps.refresh();
    onRefreshData?.();
  };

  const runSync = async (id: unknown) => {
    if (!apiToken || !id) return;
    const channelId = String(id);
    if (pendingClicks.has(channelId)) return;
    setPendingClicks((previous) => new Set(previous).add(channelId));
    try {
      const response = await syncEmployeeChannel(apiToken, channelId);
      const taskId = response.task_id ? String(response.task_id) : '';
      if (taskId) {
        setMessage('已加入队列,任务在后台执行');
        clearPendingClick(channelId);
        syncUnsubscribersRef.current.get(channelId)?.();
        const unsubscribe = waitForTask(taskId, {
          onDone: () => {
            setMessage('同步完成');
            refreshAfterSync();
            syncUnsubscribersRef.current.delete(channelId);
          },
          onFailed: (task) => {
            setMessage(`同步失败: ${task.error || '未知错误'}`);
            syncUnsubscribersRef.current.delete(channelId);
          },
          onCancelled: () => {
            setMessage('同步已取消');
            syncUnsubscribersRef.current.delete(channelId);
          },
        });
        syncUnsubscribersRef.current.set(channelId, unsubscribe);
        return;
      }
      setMessage(String(response.message || '同步完成'));
      refreshAfterSync();
    } catch (error) {
      setMessage(error instanceof Error ? `提交失败: ${error.message}` : '提交失败');
    } finally {
      clearPendingClick(channelId);
    }
  };

  return (
    <PageShell
      title={viewMode === 'manager' ? 'KOL/账号管理 / 团队矩阵' : '我的平台'}
      eyebrow={null}
      headingExtra={(
        <ChannelGapPanel
          accounts={visibleGapAccounts}
          summary={gaps.summary}
          loading={gaps.loading}
          error={gaps.error}
          onRefresh={() => void gaps.refresh()}
          compactMode
        />
      )}
    >
      <section className="vkpi-card-grid vkpi-card-grid--forms">
        <section className="vkpi-card vkpi-action-card">
          <CardHeader title="绑定平台账号" />
          <form className="vkpi-form-stack" onSubmit={submitChannel}>
            {viewMode === 'manager' ? <select value={viewAsStaffId} onChange={(event) => setViewAsStaffId(event.target.value)}><option value="">当前账号 / 全部</option>{data.staffMembers.map((member) => <option key={member.id} value={member.id}>{member.name} · {member.email}</option>)}</select> : null}
            <select value={platform} onChange={(event) => setPlatform(event.target.value)}>{creatorPlatformOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select>
            <input value={handle} onChange={(event) => setHandle(event.target.value)} placeholder="@handle / 主页 ID" />
            <input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="显示名称（可选）" />
            <input value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="API Key（可选，后端加密，前端不回显）" />
            <button className="vkpi-button vkpi-button--primary" disabled={busy || !apiToken} type="submit">绑定平台</button>
          </form>
        </section>
        {viewMode === 'manager' ? <section className="vkpi-card vkpi-action-card vkpi-action-card--team"><ChannelStaffProgress platforms={matrix.platforms} selectedStaffId={selectedStaffId} onSelectStaff={selectStaff} compactMode /></section> : null}
      </section>
      <ChannelPlatformMatrix
        platforms={matrix.platforms}
        selectedPlatform={selectedPlatform}
        loading={matrix.loading}
        error={matrix.error}
        accountCount={matrix.accountCount}
        postCount={matrix.postCount}
        totalViews={matrix.totalViews}
        onSelectPlatform={selectPlatform}
        onOpenBindings={() => setShowBindingList(true)}
        bindingCount={channelsRows.length}
      />
      <ChannelAccountList
        platform={visiblePlatformData}
        selectedAccountId={selectedAccountId}
        onSelectAccount={selectAccount}
        loading={matrix.loading && !matrix.platforms.length}
      />
      <RedditAssessmentPanel account={selectedAccount} apiToken={apiToken} />
      <ChannelContentList account={selectedAccount} apiToken={apiToken} />
      {viewMode === 'employee' ? <MyKolMatrix apiToken={apiToken} data={data} onRefreshData={onRefreshData} /> : null}
      {message ? <div className="vkpi-inline-message">{message}</div> : null}
      {showBindingList ? (
        <div className="vkpi-glass-modal" role="dialog" aria-modal="true" aria-label="平台绑定列表">
          <button className="vkpi-glass-modal__backdrop" type="button" aria-label="关闭" onClick={() => setShowBindingList(false)} />
          <section className="vkpi-glass-modal__panel vkpi-channel-bindings-modal">
            <header className="vkpi-glass-modal__header">
              <div>
                <span>官方账号矩阵</span>
                <h2>{viewMode === 'manager' ? '平台绑定列表' : '我的平台列表'}</h2>
              </div>
              <div>
                <span className="vkpi-channel-bindings-count">{channelsRows.length} 条</span>
                <button className="vkpi-glass-modal__close" type="button" aria-label="关闭" onClick={() => setShowBindingList(false)}>×</button>
              </div>
            </header>
            <div className="vkpi-table-wrap">
              <table className="vkpi-table">
                <thead><tr><th>平台</th><th>账号</th><th>状态</th><th>同步</th><th>粉丝</th><th>帖子</th><th>播放</th><th>操作</th></tr></thead>
                <tbody>{channelsRows.length ? channelsRows.map((row) => {
                  const rowId = String(row.id);
                  return <tr key={rowId}><td>{platformDisplay(row.platform)}</td><td>{String(row.account_display_name || row.account_handle || '-')}<br /><small>{row.api_key_mask ? `Key ${String(row.api_key_mask)}` : '未填写 API Key'}</small></td><td>{String(row.status || '-')}</td><td>{channelSyncLabel(row)}</td><td>{channelMetricCell(row, 'latest_followers')}</td><td>{channelMetricCell(row, 'latest_posts')}</td><td>{channelMetricCell(row, 'latest_views')}</td><td><button className="vkpi-mini-button" type="button" disabled={!apiToken || pendingClicks.has(rowId)} onClick={() => void runSync(row.id)}>同步</button></td></tr>;
                }) : <tr><td className="vkpi-table-empty" colSpan={8}>暂无平台绑定。</td></tr>}</tbody>
              </table>
            </div>
          </section>
        </div>
      ) : null}
    </PageShell>
  );
}
