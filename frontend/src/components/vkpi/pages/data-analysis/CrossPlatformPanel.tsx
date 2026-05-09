import React, { useEffect, useMemo, useState } from 'react';
import {
  addIndustryAccount,
  createIndustryProject,
  getIndustryAccount,
  getIndustryCrossPlatform,
  importIndustryApifyHistory,
  listIndustryAccounts,
  listIndustryPosts,
  listIndustryProjects,
  refreshIndustryAccount,
  updateIndustryAccount,
} from '../../../../services/vkpi.ui-api';
import { creatorPlatformOptions } from '../../shared/vkpiConstants';

import type { KpiKey, Row, SecondaryTab } from './utils/types';
import { SECONDARY_TABS } from './utils/types';
import { DEFAULT_KPIS } from './utils/kpiOptions';
import { accountId, accountName, rowString } from './utils/rowAccessors';
import { compact, normalizePlatform, platformClass, platformDisplay, platformInitial } from './utils/platformHelpers';

import { FilterDrawer } from './drawers/FilterDrawer';
import { AccountDrawer } from './drawers/AccountDrawer';

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

export function CrossPlatformPanel({
  apiToken, busy, onBusyChange, onMessage, viewMode: _viewMode = 'manager',
}: CrossPlatformPanelProps) {
  const [projects, setProjects] = useState<Row[]>([]);
  const [accounts, setAccounts] = useState<Row[]>([]);
  const [crossPlatform, setCrossPlatform] = useState<Row[]>([]);
  const [posts, setPosts] = useState<Row[]>([]);
  const [snapshots, setSnapshots] = useState<Row[]>([]);
  const [selectedAccount, setSelectedAccount] = useState<Row | null>(null);

  const [projectName, setProjectName] = useState('');
  const [projectId, setProjectId] = useState('');
  const [handle, setHandle] = useState('');
  const [platform, setPlatform] = useState('youtube');
  const [historyJson, setHistoryJson] = useState('');
  const [historySourceRef, setHistorySourceRef] = useState('');

  const [selectedProfileIds, setSelectedProfileIds] = useState<string[]>([]);
  const [selectedKpis, setSelectedKpis] = useState<KpiKey[]>(DEFAULT_KPIS);
  const [includeBenchmark, setIncludeBenchmark] = useState(false);
  const [isFilterOpen, setFilterOpen] = useState(false);
  const [activeSecondaryTab, setActiveSecondaryTab] = useState<SecondaryTab>('Home');

  const refresh = async (nextProjectId = projectId) => {
    if (!apiToken) return;
    const projectResult = await listIndustryProjects(apiToken).catch(() => ({ projects: [] }));
    const loadedProjects = projectResult.projects || [];
    setProjects(loadedProjects);
    const resolvedProjectId = String(nextProjectId || (loadedProjects[0]?.id ?? ''));
    setProjectId(resolvedProjectId);
    if (!resolvedProjectId) {
      setAccounts([]); setCrossPlatform([]); setPosts([]);
      setSelectedAccount(null); setSnapshots([]); setSelectedProfileIds([]);
      return;
    }
    const [accountResult, crossResult, postResult] = await Promise.all([
      listIndustryAccounts(apiToken, resolvedProjectId).catch(() => ({ accounts: [] })),
      getIndustryCrossPlatform(apiToken, resolvedProjectId).catch(() => ({ platforms: [] })),
      listIndustryPosts(apiToken, resolvedProjectId, 100).catch(() => ({ posts: [] })),
    ]);
    const loadedAccounts = accountResult.accounts || [];
    setAccounts(loadedAccounts);
    setCrossPlatform((crossResult.platforms || []) as Row[]);
    setPosts(postResult.posts || []);
    setSelectedProfileIds(loadedAccounts.map((a: Row) => accountId(a)));
    if (loadedAccounts.length) {
      const targetId = String(selectedAccount?.id || loadedAccounts[0].id || '');
      if (targetId) await loadAccount(targetId);
    } else {
      setSelectedAccount(null); setSnapshots([]);
    }
  };

  const loadAccount = async (accountIdValue: string) => {
    if (!apiToken || !accountIdValue) return;
    const result = await getIndustryAccount(apiToken, accountIdValue)
      .catch(() => ({ account: null, snapshots: [], posts: [] }));
    setSelectedAccount((result.account || null) as Row | null);
    setSnapshots(result.snapshots || []);
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
    try {
      const response = await refreshIndustryAccount(apiToken, accountIdValue);
      onMessage(String(response.message || `账号刷新状态: ${response.sync_status || 'not_configured'}`));
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
  const accountCountByPlatform = useMemo(
    () => accounts.reduce<Record<string, number>>((acc, account) => {
      const key = normalizePlatform(rowString(account, ['platform']));
      acc[key] = (acc[key] || 0) + 1;
      return acc;
    }, {}),
    [accounts],
  );

  const toggleProfile = (id: string) => setSelectedProfileIds(
    (items) => items.includes(id) ? items.filter((item) => item !== id) : [...items, id],
  );
  const toggleKpi = (key: KpiKey) => setSelectedKpis(
    (items) => items.includes(key) ? items.filter((item) => item !== key) : [...items, key],
  );
  const openAccount = (account: Row) => {
    setSelectedAccount(account);
    void loadAccount(accountId(account));
  };
  const onImportKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') void importHistoricalJson();
  };

  // === Tab 内容路由 ===
  const renderTab = () => {
    switch (activeSecondaryTab) {
      case 'Home':
        return (
          <HomeTab
            accounts={accounts}
            crossPlatform={crossPlatform}
            posts={posts}
            busy={busy}
            onOpenAccount={openAccount}
            onRefreshAccount={refreshAccount}
            onOpenFilter={() => setFilterOpen(true)}
            onSetSelectedAccount={setSelectedAccount}
            selectedAccount={selectedAccount}
          />
        );
      case 'Benchmarks':
        return (
          <BenchmarksTab
            accounts={accounts}
            visibleAccounts={visibleAccounts}
            crossPlatform={crossPlatform}
            posts={posts}
            selectedKpis={selectedKpis}
            includeBenchmark={includeBenchmark}
            onOpenAccount={openAccount}
            onOpenFilter={() => setFilterOpen(true)}
          />
        );
      case 'Posts':
        return <PostsTab accounts={accounts} posts={posts} onSetSelectedAccount={setSelectedAccount} />;
      case 'Pillars':
        return <PillarsTab posts={posts} />;
      case 'Sentiment':
        return <SentimentTab />;
      case 'Topic Tracking':
        return <TopicTrackingTab />;
      default:
        return null;
    }
  };

  const selectedProject = projects.find((project) => String(project.id || '') === projectId);

  return (
    <div className="da-shell">
      <main className="da-main">

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
                <button className="da-white-button" type="button" disabled>✨ Content Pillars</button>
                <button className="da-white-button" type="button" disabled>⬇ Download</button>
              </div>
            </div>
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

      </main>

      {/* === Filter Toggle === */}
      <button
        className={`da-filter-toggle${isFilterOpen ? ' is-open' : ''}`}
        type="button"
        onClick={() => setFilterOpen((open) => !open)}
        aria-label={isFilterOpen ? '关闭筛选' : '打开筛选'}
      >{isFilterOpen ? '关闭筛选' : '筛选'}</button>

      <FilterDrawer
        isOpen={isFilterOpen}
        accounts={accounts}
        selectedProfileIds={selectedProfileIds}
        selectedKpis={selectedKpis}
        includeBenchmark={includeBenchmark}
        onClose={() => setFilterOpen(false)}
        onProfileToggle={toggleProfile}
        onKpiToggle={toggleKpi}
        onBenchmarkToggle={() => setIncludeBenchmark((value) => !value)}
        onUpdate={() => { setFilterOpen(false); onMessage('筛选已更新。'); }}
      />

      <AccountDrawer
        account={selectedAccount}
        snapshots={snapshots}
        posts={posts}
        accounts={accounts}
        busy={busy}
        onClose={() => setSelectedAccount(null)}
        onRefresh={(id) => void refreshAccount(id)}
        onToggleCrawl={(id, enabled) => void toggleAccountCrawl(id, enabled)}
      />
    </div>
  );
}
