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
import {
  ChannelPlatformMatrix,
  DEFAULT_CHANNEL_SYNC_TIMEZONE,
  isSupportedChannelSyncTimezone,
} from './channels/ChannelPlatformMatrix';
import { MyKolMatrix } from './channels/MyKolMatrix';
import { RedditAssessmentPanel } from './channels/RedditAssessmentPanel';
import { ChannelStaffProgress } from './channels/ChannelStaffProgress';
import { useOfficialChannelGaps } from './channels/useOfficialChannelGaps';
import { useOfficialChannelMatrix } from './channels/useOfficialChannelMatrix';
import type { OfficialChannelAccount, OfficialChannelPlatform } from './channels/channelTypes';
import type { VkpiDashboardData, VkpiPageKey } from '../vkpiTypes';
import { CardHeader } from '../shared/CardHeader';
import { creatorPlatformOptions } from '../shared/vkpiConstants';
import { numberFormatter } from '../shared/vkpiFormatters';
import { platformDisplay, safeNumber } from '../shared/vkpiDataUtils';
import { writeDiscoverFocus } from '../intelligence/intelligenceDiscoveryFocus';
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
  onSelectPage?: (page: VkpiPageKey) => void;
}

const CHANNEL_SYNC_TIMEZONE_STORAGE_KEY = 'vkpi.channelMatrix.syncTimezone';

function channelSyncLabel(row: Record<string, unknown>) {
  const status = String(row.last_sync_status || row.sync_status || '').trim();
  const labels: Record<string, string> = {
    configured_pending_provider: '待同步',
    no_results: '抓取无结果',
    not_configured: '待配置',
    not_supported: '未接入补抓',
    official_readonly: '只读',
    synced: '已同步',
  };
  return labels[status] || status || '待同步';
}

function channelMetricCell(row: Record<string, unknown>, key: string) {
  if (row[key] != null) return numberFormatter.format(safeNumber(row[key]));
  const status = String(row.last_sync_status || row.sync_status || '');
  return status === 'no_results' ? '无快照' : '待同步';
}

function selectedPlatformLabel(platform?: OfficialChannelPlatform, fallback = '') {
  return platform?.label || (fallback ? platformDisplay(fallback) : '全部平台');
}

function discoverQueryForPlatform(platformLabel: string, variant: 'same_platform' | 'viltrox' | 'gap') {
  if (variant === 'viltrox') return `${platformLabel} Viltrox lens review creator`;
  if (variant === 'gap') return `${platformLabel} camera lens content creator collaboration`;
  return `${platformLabel} camera gear review creator`;
}

function officialMatrixRows(platforms: OfficialChannelPlatform[]): Array<Record<string, unknown>> {
  return platforms.flatMap((platform) =>
    platform.accounts.map((account) => ({
      id: account.id,
      platform: account.platform || platform.platform,
      account_display_name: account.displayName,
      account_handle: account.handle,
      api_key_mask: '',
      status: 'official_readonly',
      last_sync_status: account.syncStatus,
      latest_followers: account.followers,
      latest_posts: account.postsCount,
      latest_views: account.totalViews,
    })),
  );
}

function ChannelDiscoveryBridge({
  platform,
  selectedPlatform,
  gapCount,
  bindingCount,
  onDiscover,
}: {
  platform?: OfficialChannelPlatform;
  selectedPlatform: string;
  gapCount: number;
  bindingCount: number;
  onDiscover: (variant: 'same_platform' | 'viltrox' | 'gap') => void;
}) {
  const label = selectedPlatformLabel(platform, selectedPlatform);
  const accountCount = platform?.accounts.length || 0;
  const postCount = platform?.totalPosts || 0;
  const views = platform?.totalViews || 0;

  return (
    <section className="vkpi-channel-discovery-bridge" aria-label="平台发现桥">
      <div className="vkpi-channel-discovery-bridge__copy">
        <span>发现入口</span>
        <h2>{selectedPlatform ? `${label} 候选发现` : '从平台池发现 KOL'}</h2>
        <p>先看已有平台和账号，再把平台上下文带到红人决策中枢。员工不用从空白搜索框开始，也不会触发外部抓取。</p>
      </div>
      <div className="vkpi-channel-discovery-bridge__metrics">
        <span><b>{numberFormatter.format(accountCount)}</b><em>当前账号</em></span>
        <span><b>{numberFormatter.format(postCount)}</b><em>内容样本</em></span>
        <span><b>{numberFormatter.format(gapCount)}</b><em>待补缺口</em></span>
        <span><b>{numberFormatter.format(bindingCount)}</b><em>绑定记录</em></span>
      </div>
      <div className="vkpi-channel-discovery-bridge__flow">
        <span className="is-active"><i>1</i><b>平台</b><em>{label}</em></span>
        <span><i>2</i><b>已有账号</b><em>{numberFormatter.format(accountCount)} 个</em></span>
        <span><i>3</i><b>候选发现</b><em>带入查询</em></span>
        <span><i>4</i><b>项目判断</b><em>证据回流</em></span>
      </div>
      <div className="vkpi-channel-discovery-bridge__actions">
        <button className="vkpi-button vkpi-button--primary" type="button" onClick={() => onDiscover('same_platform')}>发现同平台 KOL</button>
        <button className="vkpi-button" type="button" onClick={() => onDiscover('viltrox')}>找 Viltrox 相关</button>
        <button className="vkpi-button" type="button" onClick={() => onDiscover('gap')}>补项目缺口</button>
      </div>
      <small>{views ? `${numberFormatter.format(views)} 播放作为平台参考，不作为候选质量结论。` : '没有播放快照时，仍可按平台和内容方向进入发现。'}</small>
    </section>
  );
}

export function ChannelsPage({ apiToken, viewMode, data, onRefreshData, onSelectPage }: ChannelsPageProps) {
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
  const [syncTimezone, setSyncTimezone] = useState(() => {
    if (typeof window === 'undefined') return DEFAULT_CHANNEL_SYNC_TIMEZONE;
    const stored = window.localStorage.getItem(CHANNEL_SYNC_TIMEZONE_STORAGE_KEY) || '';
    return isSupportedChannelSyncTimezone(stored) ? stored : DEFAULT_CHANNEL_SYNC_TIMEZONE;
  });
  const syncUnsubscribersRef = useRef<Map<string, () => void>>(new Map());
  const { waitForTask } = useTaskCenter();
  const matrix = useOfficialChannelMatrix(apiToken);
  const gaps = useOfficialChannelGaps(apiToken);
  const matrixRows = useMemo(() => officialMatrixRows(matrix.platforms), [matrix.platforms]);
  const bindingRows = matrixRows;
  const bindingCount = matrix.accountCount;

  const selectedPlatformData = useMemo(() => {
    return matrix.platforms.find((item) => item.platform === selectedPlatform);
  }, [matrix.platforms, selectedPlatform]);
  const visiblePlatformData = useMemo<OfficialChannelPlatform | undefined>(() => {
    return selectedPlatformData;
  }, [selectedPlatformData]);
  const selectedAccount = useMemo(() => {
    return visiblePlatformData?.accounts.find((account) => account.id === selectedAccountId);
  }, [visiblePlatformData, selectedAccountId]);
  const visibleGapAccounts = useMemo(() => {
    return gaps.accounts.filter((account) => {
      const platformMatches = !selectedPlatform || account.platform === selectedPlatform;
      return platformMatches;
    });
  }, [gaps.accounts, selectedPlatform]);

  const refresh = async () => {
    if (!apiToken) return;
    const channelsResult = await listEmployeeChannels(apiToken, viewMode === 'manager' ? viewAsStaffId || undefined : undefined).catch(() => ({ channels: [] }));
    setChannelsRows(channelsResult.channels || []);
  };

  useEffect(() => {
    void refresh();
  }, [apiToken, viewAsStaffId, viewMode]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(CHANNEL_SYNC_TIMEZONE_STORAGE_KEY, syncTimezone);
  }, [syncTimezone]);

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

  const openDiscoverFromPlatform = (variant: 'same_platform' | 'viltrox' | 'gap') => {
    const platformKey = selectedPlatform || platform;
    const label = selectedPlatformLabel(visiblePlatformData, platformKey);
    writeDiscoverFocus({
      source: 'channel_platform_drilldown',
      title: `${label} 候选发现`,
      summary: '从 MY KOL 平台池进入发现；先复用已有账号、项目和平台上下文，再判断是否需要新候选。',
      query: discoverQueryForPlatform(label, variant),
      platform: platformKey || 'all',
      sourceLabel: 'MY KOL 平台池',
    });
    onSelectPage?.('discover');
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
      title={viewMode === 'manager' ? 'MY KOL / 团队矩阵' : 'MY KOL'}
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
        bindingCount={bindingCount}
        syncTimezone={syncTimezone}
        onSyncTimezoneChange={setSyncTimezone}
      />
      {viewMode === 'employee' ? (
        <ChannelDiscoveryBridge
          platform={visiblePlatformData}
          selectedPlatform={selectedPlatform}
          gapCount={visibleGapAccounts.length}
          bindingCount={bindingCount}
          onDiscover={openDiscoverFromPlatform}
        />
      ) : null}
      <ChannelAccountList
        platform={visiblePlatformData}
        selectedAccountId={selectedAccountId}
        onSelectAccount={selectAccount}
        loading={matrix.loading && !matrix.platforms.length}
      />
      <RedditAssessmentPanel account={selectedAccount} apiToken={apiToken} />
      <ChannelContentList account={selectedAccount} apiToken={apiToken} />
      <MyKolMatrix
        apiToken={apiToken}
        data={data}
        initialPlatform={selectedPlatform}
        onDiscoverPlatform={(nextPlatform) => {
          const label = platformDisplay(nextPlatform || selectedPlatform || platform);
          writeDiscoverFocus({
            source: 'my_kol_platform_card',
            title: `${label} 相似 KOL 发现`,
            summary: '从 MY KOL 平台卡进入发现；用于补充同平台、同内容方向的新候选。',
            query: discoverQueryForPlatform(label, 'same_platform'),
            platform: nextPlatform || selectedPlatform || platform || 'all',
            sourceLabel: 'MY KOL 平台卡',
          });
          onSelectPage?.('discover');
        }}
        onRefreshData={onRefreshData}
      />
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
                <span className="vkpi-channel-bindings-count">{bindingRows.length} 条</span>
                <button className="vkpi-glass-modal__close" type="button" aria-label="关闭" onClick={() => setShowBindingList(false)}>×</button>
              </div>
            </header>
            <div className="vkpi-table-wrap">
              <table className="vkpi-table">
                <thead><tr><th>平台</th><th>账号</th><th>状态</th><th>同步</th><th>粉丝</th><th>帖子</th><th>播放</th><th>操作</th></tr></thead>
                <tbody>{bindingRows.length ? bindingRows.map((row) => {
                  const rowId = String(row.id);
                  const platformKey = String(row.platform || '');
                  return <tr key={rowId}><td>{platformDisplay(row.platform)}</td><td>{String(row.account_display_name || row.account_handle || '-')}<br /><small>{row.api_key_mask ? `Key ${String(row.api_key_mask)}` : '公司官方账号 · 只读'}</small></td><td>{String(row.status || '-')}</td><td>{channelSyncLabel(row)}</td><td>{channelMetricCell(row, 'latest_followers')}</td><td>{channelMetricCell(row, 'latest_posts')}</td><td>{channelMetricCell(row, 'latest_views')}</td><td>{viewMode === 'manager' ? <button className="vkpi-mini-button" type="button" disabled={!apiToken || pendingClicks.has(rowId)} onClick={() => void runSync(row.id)}>同步</button> : <button className="vkpi-mini-button" type="button" onClick={() => { setSelectedPlatform(platformKey); setSelectedAccountId(Number(row.id) || null); setShowBindingList(false); }}>查看</button>}</td></tr>;
                }) : <tr><td className="vkpi-table-empty" colSpan={8}>暂无平台绑定。</td></tr>}</tbody>
              </table>
            </div>
          </section>
        </div>
      ) : null}
    </PageShell>
  );
}
