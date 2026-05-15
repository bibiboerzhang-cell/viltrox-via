import React, { useEffect, useMemo, useState } from 'react';
import {
  addIndustryAccount,
  createIndustryProject,
  getIndustryAccount,
  getIndustryCrossPlatform,
  importIndustryApifyHistory,
  listBudgetSettings,
  listIndustryAccounts,
  listIndustryPosts,
  listIndustryProjects,
  listPlatformCrawlSettings,
  refreshIndustryAccount,
  updateIndustryAccount,
  analyzeDataAnalysisPostUrl,
} from '../../../../services/vkpi.ui-api';
import { creatorPlatformOptions } from '../../shared/vkpiConstants';

import type { ChartKey, KpiKey, Row, SecondaryTab } from './utils/types';
import { SECONDARY_TABS } from './utils/types';
import { DEFAULT_CHARTS, DEFAULT_KPIS } from './utils/kpiOptions';
import { accountId, accountName, rowString } from './utils/rowAccessors';
import { compact, normalizePlatform, platformClass, platformDisplay, platformInitial } from './utils/platformHelpers';
import { metricForAccount, postsForAccount } from './utils/metricHelpers';

import { FilterDrawer } from './drawers/FilterDrawer';
import { PostDetailDrawer } from './drawers/PostDetailDrawer';
import { ProfileDashboard } from './profile/ProfileDashboard';

import { HomeTab } from './tabs/HomeTab';
import { BenchmarksTab } from './tabs/BenchmarksTab';
import { PostsTab } from './tabs/PostsTab';
import { PillarsTab } from './tabs/PillarsTab';
import { SentimentTab } from './tabs/SentimentTab';
import { TopicTrackingTab } from './tabs/TopicTrackingTab';

interface CrossPlatformPanelProps {
  apiToken?: string;
  busy: boolean;
  onBusyChange: (busy: boolean) => void;
  onMessage: (message: string) => void;
  viewMode?: 'manager' | 'employee';
}

type DatePreset = '7d' | '30d' | '90d' | 'this_month' | 'all';
type CompareMode = 'previous_period' | 'none';
type GroupBy = 'day' | 'week' | 'month';

const DATE_PRESETS: Array<{ key: DatePreset; label: string; days?: number }> = [
  { key: '7d', label: '近 7 天', days: 7 },
  { key: '30d', label: '近 30 天', days: 30 },
  { key: '90d', label: '近 90 天', days: 90 },
  { key: 'this_month', label: '本月' },
  { key: 'all', label: '全部历史' },
];

function dateLabel(preset: DatePreset): string {
  return DATE_PRESETS.find((item) => item.key === preset)?.label || '近 30 天';
}

function parsePostTime(post: Row): number | null {
  const raw = rowString(post, ['published_at', 'posted_at', 'created_at', 'timestamp', 'taken_at', 'date']);
  if (!raw) return null;
  const time = new Date(raw).getTime();
  return Number.isFinite(time) ? time : null;
}

function startForPreset(preset: DatePreset): number | null {
  const now = new Date();
  if (preset === 'all') return null;
  if (preset === 'this_month') {
    return new Date(now.getFullYear(), now.getMonth(), 1).getTime();
  }
  const config = DATE_PRESETS.find((item) => item.key === preset);
  const days = config?.days || 30;
  return now.getTime() - days * 24 * 60 * 60 * 1000;
}

function readyStatus(value: unknown): boolean {
  return ['synced', 'success', 'configured', 'ready', 'historical_import', 'ok'].includes(
    String(value || '').trim().toLowerCase(),
  );
}

function boolish(value: unknown): boolean {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'number') return value !== 0;
  return ['1', 'true', 'yes', 'on', 'enabled'].includes(String(value || '').trim().toLowerCase());
}

function accountHasUsableData(account: Row, loadedPosts: Row[]): boolean {
  const crawlEnabled = boolish(account.crawl_enabled);
  if (
    readyStatus(account.sync_status)
    || readyStatus(account.provider_status)
    || readyStatus(account.api_status)
    || (crawlEnabled && rowString(account, ['last_successful_at', 'last_crawled_at']))
  ) {
    return true;
  }
  const id = accountId(account);
  const handle = accountName(account).toLowerCase().replace(/^@/, '');
  return loadedPosts.some((post) => {
    const postAccount = rowString(post, ['account_id', 'industry_account_id', 'profile_id']);
    const postHandle = rowString(post, ['handle', 'account_handle', 'username', 'display_name']).toLowerCase().replace(/^@/, '');
    return (postAccount && postAccount === id) || (handle && postHandle === handle);
  });
}

function defaultSelectedAccountIds(loadedAccounts: Row[], loadedPosts: Row[]): string[] {
  const usable = loadedAccounts.filter((account) => accountHasUsableData(account, loadedPosts));
  return (usable.length ? usable : loadedAccounts).map((account) => accountId(account));
}

function csvEscape(value: unknown): string {
  const text = String(value ?? '');
  if (/[",\n]/.test(text)) return `"${text.replace(/"/g, '""')}"`;
  return text;
}

function downloadTextFile(filename: string, content: string, mimeType: string): void {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function CrossPlatformPanel({
  apiToken, busy, onBusyChange, onMessage, viewMode: _viewMode = 'manager',
}: CrossPlatformPanelProps) {
  const [projects, setProjects] = useState<Row[]>([]);
  const [accounts, setAccounts] = useState<Row[]>([]);
  const [crossPlatform, setCrossPlatform] = useState<Row[]>([]);
  const [posts, setPosts] = useState<Row[]>([]);
  const [snapshots, setSnapshots] = useState<Row[]>([]);
  const [accountPosts, setAccountPosts] = useState<Row[]>([]);
  const [platformCrawlSettings, setPlatformCrawlSettings] = useState<Row[]>([]);
  const [budgetSettings, setBudgetSettings] = useState<Row[]>([]);
  const [selectedAccount, setSelectedAccount] = useState<Row | null>(null);
  const [selectedPost, setSelectedPost] = useState<Row | null>(null);
  const [postAnalysis, setPostAnalysis] = useState<Row | null>(null);
  const [postAnalysisBusy, setPostAnalysisBusy] = useState(false);
  const [postAnalysisError, setPostAnalysisError] = useState('');

  const [projectName, setProjectName] = useState('');
  const [projectId, setProjectId] = useState('');
  const [handle, setHandle] = useState('');
  const [platform, setPlatform] = useState('youtube');
  const [historyJson, setHistoryJson] = useState('');
  const [historySourceRef, setHistorySourceRef] = useState('');

  const [selectedProfileIds, setSelectedProfileIds] = useState<string[]>([]);
  const [selectedKpis, setSelectedKpis] = useState<KpiKey[]>(DEFAULT_KPIS);
  const [selectedCharts, setSelectedCharts] = useState<ChartKey[]>(DEFAULT_CHARTS);
  const [datePreset, setDatePreset] = useState<DatePreset>('30d');
  const [compareMode, setCompareMode] = useState<CompareMode>('previous_period');
  const [groupBy, setGroupBy] = useState<GroupBy>('day');
  const [includeBenchmark, setIncludeBenchmark] = useState(false);
  const [isFilterOpen, setFilterOpen] = useState(false);
  const [activeSecondaryTab, setActiveSecondaryTab] = useState<SecondaryTab>('Home');

  const refresh = async (nextProjectId = projectId) => {
    if (!apiToken) return;
    const [projectResult, crawlSettingsResult, budgetSettingsResult] = await Promise.all([
      listIndustryProjects(apiToken).catch(() => ({ projects: [] })),
      listPlatformCrawlSettings(apiToken).catch(() => ({ platforms: [] })),
      listBudgetSettings(apiToken).catch(() => ({ budgets: [] })),
    ]);
    setPlatformCrawlSettings(crawlSettingsResult.platforms || []);
    setBudgetSettings(budgetSettingsResult.budgets || []);
    const loadedProjects = projectResult.projects || [];
    setProjects(loadedProjects);
    const resolvedProjectId = String(nextProjectId || (loadedProjects[0]?.id ?? ''));
    setProjectId(resolvedProjectId);
    if (!resolvedProjectId) {
      setAccounts([]); setCrossPlatform([]); setPosts([]);
      setSelectedAccount(null); setSnapshots([]); setAccountPosts([]); setSelectedProfileIds([]);
      return;
    }
    const [accountResult, crossResult, postResult] = await Promise.all([
      listIndustryAccounts(apiToken, resolvedProjectId).catch(() => ({ accounts: [] })),
      getIndustryCrossPlatform(apiToken, resolvedProjectId).catch(() => ({ platforms: [] })),
      listIndustryPosts(apiToken, resolvedProjectId, 500).catch(() => ({ posts: [] })),
    ]);
    const loadedAccounts = accountResult.accounts || [];
    const loadedPosts = postResult.posts || [];
    setAccounts(loadedAccounts);
    setCrossPlatform((crossResult.platforms || []) as Row[]);
    setPosts(loadedPosts);
    setSelectedProfileIds(defaultSelectedAccountIds(loadedAccounts, loadedPosts));
    if (loadedAccounts.length) {
      const targetId = String(selectedAccount?.id || loadedAccounts[0].id || '');
      if (targetId) await loadAccount(targetId);
    } else {
      setSelectedAccount(null); setSnapshots([]); setAccountPosts([]);
    }
  };

  const loadAccount = async (accountIdValue: string) => {
    if (!apiToken || !accountIdValue) return;
    const result = await getIndustryAccount(apiToken, accountIdValue)
      .catch(() => ({ account: null, snapshots: [], posts: [] }));
    setSelectedAccount((result.account || null) as Row | null);
    setSnapshots(result.snapshots || []);
    setAccountPosts(result.posts || []);
  };

  useEffect(() => { void refresh(); }, [apiToken]);

  const submitProject = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!apiToken || !projectName.trim()) return;
    onBusyChange(true);
    try {
      const response = await createIndustryProject(apiToken, {
        name: projectName.trim(),
        project_type: 'brand_monitor',
        monitoring_frequency: 'daily',
      });
      const project = (response.project || {}) as Row;
      const nextProjectId = String(project.id || '');
      setProjectName('');
      setProjectId(nextProjectId);
      onMessage('数据分析项目已创建。');
      await refresh(nextProjectId);
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '项目创建失败');
    } finally {
      onBusyChange(false);
    }
  };

  const submitAccount = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!apiToken || !projectId || !handle.trim()) return;
    onBusyChange(true);
    try {
      await addIndustryAccount(apiToken, projectId, {
        platform,
        handle: handle.trim().replace(/^@/, ''),
        account_role: 'reference',
        crawl_enabled: false,
        sync_status: 'not_configured',
      });
      setHandle('');
      onMessage('账号已加入数据分析矩阵。抓取默认关闭,不展示假数据。');
      await refresh(projectId);
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '账号添加失败');
    } finally {
      onBusyChange(false);
    }
  };

  const refreshAccount = async (accountIdValue: string) => {
    if (!apiToken || !accountIdValue) return;
    onBusyChange(true);
    onMessage('正在刷新该账号,真实抓取可能需要 10-60 秒。');
    try {
      const response = await refreshIndustryAccount(apiToken, accountIdValue);
      const accountPayload = (response.account || {}) as Row;
      if (accountPayload.id) setSelectedAccount(accountPayload);
      const nextStatus = rowString(accountPayload, ['sync_status'], String(response.sync_status || 'not_configured'));
      onMessage(String(response.message || `账号刷新完成: ${nextStatus}`));
      await loadAccount(accountIdValue);
      await refresh(projectId);
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '账号刷新失败');
    } finally {
      onBusyChange(false);
    }
  };

  const toggleAccountCrawl = async (accountIdValue: string, enabled: boolean) => {
    if (!apiToken || !accountIdValue) return;
    onBusyChange(true);
    const previousAccount = selectedAccount && accountId(selectedAccount) === accountIdValue ? selectedAccount : null;
    if (previousAccount) {
      setSelectedAccount({
        ...previousAccount,
        crawl_enabled: enabled ? 1 : 0,
        sync_status: enabled ? 'queued' : 'not_configured',
      });
    }
    onMessage(enabled ? '正在开启账号抓取...' : '正在关闭账号抓取...');
    try {
      const response = await updateIndustryAccount(apiToken, accountIdValue, {
        crawl_enabled: enabled,
      });
      onMessage(enabled ? '该账号抓取已开启。刷新时会继续校验平台开关、预算和 API。' : '该账号抓取已关闭。');
      const accountPayload = (response.account || {}) as Row;
      if (accountPayload.id) setSelectedAccount(accountPayload);
      await loadAccount(accountIdValue);
      await refresh(projectId);
    } catch (error) {
      if (previousAccount) setSelectedAccount(previousAccount);
      onMessage(error instanceof Error ? error.message : '账号抓取开关更新失败');
    } finally {
      onBusyChange(false);
    }
  };

  const importHistoricalJson = async () => {
    if (!apiToken || !projectId || !historyJson.trim()) return;
    onBusyChange(true);
    try {
      const parsed = JSON.parse(historyJson) as unknown;
      const parsedRow = parsed as Row;
      const items = Array.isArray(parsed)
        ? parsed as Row[]
        : (Array.isArray(parsedRow.items) ? parsedRow.items as Row[] : []);
      if (!items.length) throw new Error('JSON 必须是数组,或包含 items 数组。');
      const response = await importIndustryApifyHistory(apiToken, projectId, {
        source_type: 'apify_json',
        source_ref: historySourceRef.trim() || 'manual_paste',
        items,
      });
      onMessage(`历史数据导入完成: 账号 ${String(response.imported || 0)},快照 ${String(response.snapshots_written || 0)},帖子 ${String(response.posts_written || 0)}。`);
      setHistoryJson('');
      await refresh(projectId);
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '历史数据导入失败');
    } finally {
      onBusyChange(false);
    }
  };

  const visibleAccounts = useMemo(
    () => accounts.filter((account) => selectedProfileIds.length === 0 || selectedProfileIds.includes(accountId(account))),
    [accounts, selectedProfileIds],
  );
  const visiblePosts = useMemo(() => {
    const selectedIds = new Set(visibleAccounts.map((account) => accountId(account)));
    const selectedHandles = new Set(visibleAccounts.map((account) => accountName(account).toLowerCase().replace(/^@/, '')));
    const filterByAccount = selectedProfileIds.length > 0 && visibleAccounts.length > 0;
    const start = startForPreset(datePreset);
    const hasDatedPosts = posts.some((post) => parsePostTime(post) !== null);
    return posts.filter((post) => {
      if (filterByAccount) {
        const postAccount = rowString(post, ['account_id', 'industry_account_id', 'profile_id']);
        const postHandle = rowString(post, ['handle', 'account_handle', 'username', 'display_name']).toLowerCase().replace(/^@/, '');
        if (postAccount && !selectedIds.has(postAccount) && !selectedHandles.has(postHandle)) return false;
        if (!postAccount && postHandle && !selectedHandles.has(postHandle)) return false;
      }
      if (start !== null && hasDatedPosts) {
        const time = parsePostTime(post);
        if (time === null || time < start) return false;
      }
      return true;
    });
  }, [datePreset, posts, selectedProfileIds.length, visibleAccounts]);
  const accountCountByPlatform = useMemo(
    () => visibleAccounts.reduce<Record<string, number>>((acc, account) => {
      const key = normalizePlatform(rowString(account, ['platform']));
      acc[key] = (acc[key] || 0) + 1;
      return acc;
    }, {}),
    [visibleAccounts],
  );

  const toggleProfile = (id: string) => setSelectedProfileIds(
    (items) => items.includes(id) ? items.filter((item) => item !== id) : [...items, id],
  );
  const toggleKpi = (key: KpiKey) => setSelectedKpis(
    (items) => items.includes(key) ? items.filter((item) => item !== key) : [...items, key],
  );
  const toggleChart = (key: ChartKey) => setSelectedCharts(
    (items) => items.includes(key) ? items.filter((item) => item !== key) : [...items, key],
  );
  const openAccount = (account: Row) => {
    setSelectedAccount(account);
    void loadAccount(accountId(account));
  };
  const openPost = (post: Row) => {
    setSelectedPost(post);
    setPostAnalysis(null);
    setPostAnalysisError('');
  };
  const analyzePost = async (post: Row) => {
    if (!apiToken) return;
    const matchedAccount = accounts.find((account) => {
      const id = accountId(account);
      const postAccount = rowString(post, ['account_id', 'industry_account_id', 'profile_id']);
      const postHandle = rowString(post, ['handle', 'account_handle', 'username', 'display_name'])
        .toLowerCase()
        .replace(/^@/, '');
      const accountHandle = accountName(account).toLowerCase().replace(/^@/, '');
      return (postAccount && postAccount === id) || (postHandle && accountHandle && postHandle === accountHandle);
    });
    const url = rowString(post, ['post_url', 'permalink_url', 'webVideoUrl', 'external_url', 'url']);
    if (!url) {
      setPostAnalysisError('该内容没有原帖 URL，无法运行真实单帖分析。');
      return;
    }
    setPostAnalysisBusy(true);
    setPostAnalysisError('');
    try {
      const result = await analyzeDataAnalysisPostUrl(apiToken, {
        url,
        platform: normalizePlatform(rowString(post, ['platform'], rowString(matchedAccount, ['platform']))),
        creatorHandle: rowString(post, ['handle', 'account_handle', 'username'], accountName(matchedAccount || {})),
      });
      setPostAnalysis(result as Row);
      onMessage('单帖分析完成。');
    } catch (error) {
      const message = error instanceof Error ? error.message : '单帖分析失败';
      setPostAnalysisError(message);
      onMessage(message);
    } finally {
      setPostAnalysisBusy(false);
    }
  };
  const selectAccountForAnalysis = (account: Row | null) => {
    if (account) {
      openAccount(account);
      return;
    }
    setSelectedAccount(null);
  };
  const onImportKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') void importHistoricalJson();
  };

  const exportDashboardCsv = () => {
    const rows = visibleAccounts.map((account) => ({
      project_id: projectId,
      profile: accountName(account),
      platform: platformDisplay(rowString(account, ['platform'])),
      account_role: rowString(account, ['account_role'], 'reference'),
      crawl_enabled: rowString(account, ['crawl_enabled'], '0'),
      sync_status: rowString(account, ['sync_status'], 'not_configured'),
      followers: metricForAccount(account, crossPlatform, visiblePosts, 'followers') ?? '',
      posts_loaded: postsForAccount(visiblePosts, account).length,
      views: metricForAccount(account, crossPlatform, visiblePosts, 'views') ?? '',
      engagement: metricForAccount(account, crossPlatform, visiblePosts, 'engagement') ?? '',
      avg_eng_rate_followers: metricForAccount(account, crossPlatform, visiblePosts, 'avg_eng_rate_followers') ?? '',
      last_successful_at: rowString(account, ['last_successful_at', 'last_crawled_at', 'updated_at']),
    }));
    const headers = [
      'project_id', 'profile', 'platform', 'account_role', 'crawl_enabled', 'sync_status',
      'followers', 'posts_loaded', 'views', 'engagement', 'avg_eng_rate_followers', 'last_successful_at',
    ];
    const csv = [
      headers.join(','),
      ...rows.map((row) => headers.map((header) => csvEscape(row[header as keyof typeof row])).join(',')),
    ].join('\n');
    const suffix = new Date().toISOString().slice(0, 10);
    downloadTextFile(`vkpi-data-analysis-${suffix}.csv`, csv, 'text/csv;charset=utf-8');
    onMessage(`已导出 ${rows.length} 个账号、${visiblePosts.length} 条内容的 CSV。`);
  };

  // === Tab 内容路由 ===
  const renderTab = () => {
    switch (activeSecondaryTab) {
      case 'Home':
        return (
          <HomeTab
            accounts={visibleAccounts}
            crossPlatform={crossPlatform}
            posts={visiblePosts}
            busy={busy}
            onOpenAccount={openAccount}
            onOpenPost={openPost}
            onRefreshAccount={refreshAccount}
            onOpenFilter={() => setFilterOpen(true)}
            onOpenPostsTab={() => setActiveSecondaryTab('Posts')}
            onSetSelectedAccount={selectAccountForAnalysis}
            selectedCharts={selectedCharts}
          />
        );
      case 'Benchmarks':
        return (
          <BenchmarksTab
            accounts={accounts}
            visibleAccounts={visibleAccounts}
            crossPlatform={crossPlatform}
            posts={visiblePosts}
            selectedKpis={selectedKpis}
            includeBenchmark={includeBenchmark}
            onOpenAccount={openAccount}
            onOpenFilter={() => setFilterOpen(true)}
          />
        );
      case 'Posts':
        return (
          <PostsTab
            accounts={visibleAccounts}
            posts={visiblePosts}
            onSetSelectedAccount={selectAccountForAnalysis}
            onOpenPost={openPost}
          />
        );
      case 'Pillars':
        return <PillarsTab posts={visiblePosts} />;
      case 'Sentiment':
        return <SentimentTab apiToken={apiToken} />;
      case 'Topic Tracking':
        return <TopicTrackingTab />;
      default:
        return null;
    }
  };

  return (
    <div className="da-shell">
      <main className={`da-main${selectedAccount ? ' da-main--profile' : ''}`}>
        {selectedAccount ? (
          <ProfileDashboard
            account={selectedAccount}
            snapshots={snapshots}
            posts={accountPosts}
            accounts={accounts}
            platformCrawlSettings={platformCrawlSettings}
            budgetSettings={budgetSettings}
            busy={busy}
            onOpenPost={openPost}
            onBack={() => setSelectedAccount(null)}
            onRefresh={(id) => void refreshAccount(id)}
            onToggleCrawl={(id, enabled) => void toggleAccountCrawl(id, enabled)}
          />
        ) : (
          <>
            {/* === Hero === */}
            <section className="da-hero">
              <div className="da-hero__glass">
                <div className="da-hero__copy">
                  <span className="da-kicker">Viltrox Marketing · 数据分析</span>
                  <h1>数据分析中心</h1>
                  <p>行业 / 竞品 / 自有账号矩阵监控。本页以项目、账号、帖子、KPI 与图表为主体,不展示字段说明书。</p>
                  <div className="da-platform-strip">
                    {Object.entries(accountCountByPlatform).map(([key, count]) => (
                      <span className="da-platform-chip" key={key}>
                        <span
                          className={`da-account-card__avatar ${platformClass(key)}`}
                          style={{ width: 18, height: 18, fontSize: 10 }}
                        >{platformInitial(key)}</span>
                        {platformDisplay(key)} <strong>{count}</strong>
                      </span>
                    ))}
                    {!Object.keys(accountCountByPlatform).length ? (
                      <span className="da-platform-chip">尚未添加平台账号</span>
                    ) : null}
                  </div>
                  <p style={{ fontSize: 12, opacity: 0.85 }}>
                    {apiToken ? '真实 API 已接入;未同步数据时保持真实空态。' : '登录后加载真实数据。'}
                  </p>
                  <div className="da-hero__actions">
                    <button className="da-black-button" type="button" onClick={() => setFilterOpen(true)}>» Filters</button>
                    <button className="da-white-button" type="button" onClick={() => setActiveSecondaryTab('Pillars')}>✨ Content Pillars</button>
                    <button
                      className="da-white-button"
                      type="button"
                      disabled={!visibleAccounts.length}
                      onClick={exportDashboardCsv}
                    >⬇ Export CSV</button>
                  </div>
                </div>
              </div>
            </section>

            <section className="da-analysis-toolbar" aria-label="数据分析控制台">
              <div className="da-analysis-toolbar__field">
                <span>数据范围</span>
                <select value={datePreset} onChange={(event) => setDatePreset(event.target.value as DatePreset)}>
                  {DATE_PRESETS.map((item) => (
                    <option key={item.key} value={item.key}>{item.label}</option>
                  ))}
                </select>
              </div>
              <div className="da-analysis-toolbar__field">
                <span>Compare with</span>
                <select value={compareMode} onChange={(event) => setCompareMode(event.target.value as CompareMode)}>
                  <option value="previous_period">Previous period</option>
                  <option value="none">不对比</option>
                </select>
              </div>
              <div className="da-analysis-toolbar__field">
                <span>Group values by</span>
                <select value={groupBy} onChange={(event) => setGroupBy(event.target.value as GroupBy)}>
                  <option value="day">Day</option>
                  <option value="week">Week</option>
                  <option value="month">Month</option>
                </select>
              </div>
              <button className="da-toolbar-button" type="button" onClick={() => setFilterOpen(true)}>
                Select KPIs / Charts
              </button>
              <div className="da-analysis-toolbar__summary">
                {dateLabel(datePreset)} · {visibleAccounts.length || accounts.length} profiles{accounts.length > visibleAccounts.length ? ` / 已隐藏 ${accounts.length - visibleAccounts.length} 个未同步账号` : ''} · {visiblePosts.length} posts
                {compareMode === 'previous_period' ? ' · 对比上一周期' : ''}
                {groupBy !== 'day' ? ` · ${groupBy}` : ''}
              </div>
            </section>

            {/* === 横向二级 Tab === */}
            <nav className="da-secondary-tabs" aria-label="数据分析子模块">
              {SECONDARY_TABS.map((tab) => (
                <button
                  key={tab}
                  type="button"
                  className={activeSecondaryTab === tab ? 'is-active' : ''}
                  onClick={() => setActiveSecondaryTab(tab)}
                >{tab}</button>
              ))}
            </nav>

            {/* === Control Row (所有 Tab 共享) === */}
            <section className="da-control-row">
              <form className="da-control-card" onSubmit={submitProject}>
                <label>项目</label>
                <div>
                  <input
                    value={projectName}
                    onChange={(event) => setProjectName(event.target.value)}
                    placeholder="例如 Viltrox 35mm Market Watch"
                  />
                  <button
                    className="da-black-button"
                    disabled={busy || !apiToken || !projectName.trim()}
                    type="submit"
                  >Create</button>
                </div>
              </form>
              <form className="da-control-card" onSubmit={submitAccount}>
                <label>添加账号</label>
                <div>
                  <select value={platform} onChange={(event) => setPlatform(event.target.value)}>
                    {creatorPlatformOptions.map((option) => (
                      <option key={option.value} value={option.value}>{option.label}</option>
                    ))}
                  </select>
                  <input
                    value={handle}
                    onChange={(event) => setHandle(event.target.value)}
                    placeholder="@handle / brand account"
                  />
                  <button
                    className="da-black-button"
                    disabled={busy || !apiToken || !projectId || !handle.trim()}
                    type="submit"
                  >Add</button>
                </div>
              </form>
              <div className="da-control-card da-control-card--select">
                <label>项目切换</label>
                <select value={projectId} onChange={(event) => void refresh(event.target.value)}>
                  <option value="">选择数据分析项目</option>
                  {projects.map((project) => (
                    <option key={String(project.id)} value={String(project.id)}>
                      {rowString(project, ['name', 'id'])}
                    </option>
                  ))}
                </select>
              </div>
            </section>

            {/* === Tab 内容 === */}
            {renderTab()}

            {/* === Apify 历史导入 (Advanced 折叠区,所有 Tab 都可见) === */}
            <details className="da-import-box">
              <summary>Advanced · Apify 历史数据导入 / KPI Schema</summary>
              <div className="da-import-box__body">
                <span>这里保留原有导入能力,但不再作为主视觉。粘贴已有 JSON,不主动调用外部付费抓取。</span>
                <input
                  value={historySourceRef}
                  onChange={(event) => setHistorySourceRef(event.target.value)}
                  placeholder="Dataset ID / 文件名 / 来源备注"
                />
                <textarea
                  value={historyJson}
                  onChange={(event) => setHistoryJson(event.target.value)}
                  onKeyDown={onImportKeyDown}
                  rows={5}
                  placeholder='粘贴 Apify 导出的 JSON 数组,例如 [{"platform":"youtube","handle":"creator","followers":12000,"videos":[...]}]'
                />
                <button
                  className="da-black-button"
                  disabled={busy || !apiToken || !projectId || !historyJson.trim()}
                  type="button"
                  onClick={() => void importHistoricalJson()}
                >导入历史数据</button>
              </div>
            </details>
          </>
        )}
      </main>

      {/* === Filter Toggle === */}
      {!selectedAccount ? (
        <button
          className={`da-filter-toggle${isFilterOpen ? ' is-open' : ''}`}
          type="button"
          onClick={() => setFilterOpen((open) => !open)}
          aria-label={isFilterOpen ? '关闭筛选' : '打开筛选'}
        >{isFilterOpen ? '关闭筛选' : '筛选'}</button>
      ) : null}

      <FilterDrawer
        isOpen={isFilterOpen}
        accounts={accounts}
        selectedProfileIds={selectedProfileIds}
        selectedKpis={selectedKpis}
        selectedCharts={selectedCharts}
        includeBenchmark={includeBenchmark}
        onClose={() => setFilterOpen(false)}
        onProfileToggle={toggleProfile}
        onKpiToggle={toggleKpi}
        onChartToggle={toggleChart}
        onBenchmarkToggle={() => setIncludeBenchmark((value) => !value)}
        onUpdate={() => { setFilterOpen(false); onMessage('筛选已更新。'); }}
      />

      <PostDetailDrawer
        post={selectedPost}
        accounts={accounts}
        analysis={postAnalysis}
        analysisBusy={postAnalysisBusy}
        analysisError={postAnalysisError}
        onClose={() => setSelectedPost(null)}
        onAnalyze={(post) => void analyzePost(post)}
      />

    </div>
  );
}
