// frontend/src/components/vkpi/pages/data-analysis/drawers/AccountDrawer.tsx
//
// R60: AccountDrawer 重构 - 7 个 Tab 实装
//
// 修改要点 (vs R59 v3.2):
//   - 加 useState<TabKey>('summary') 管理 active tab
//   - 加 onClick 切换
//   - 7 个 tab 内容组件分离 (在同目录的 tabs/ 子文件夹)
//   - 保持原有 header / actions 不变

import { useState } from 'react';

import type { Row } from '../utils/types';
import { ACCOUNT_TABS } from '../utils/types';
import { accountId, accountName, rowString } from '../utils/rowAccessors';
import { platformExternalUrl, proxiedImageUrl } from '../utils/mediaProxy';
import { accountAvatarUrl, accountProfileUrl } from '../utils/mediaFields';
import { platformClass, platformDisplay, platformInitial, prettyDate } from '../utils/platformHelpers';
import {
  AudienceTab,
  CompareTab,
  ContentTab,
  EngagementTab,
  OrganicValueTab,
  PillarsTab,
  PostsTab,
  SummaryTab,
  ViewsTab,
} from './tabs';

type TabKey = typeof ACCOUNT_TABS[number];

interface AccountDrawerProps {
  account: Row | null;
  snapshots: Row[];
  posts: Row[];
  accounts: Row[];
  platformCrawlSettings?: Row[];
  budgetSettings?: Row[];
  busy: boolean;
  onClose: () => void;
  onRefresh: (id: string) => void;
  onToggleCrawl: (id: string, enabled: boolean) => void;
  onOpenPost?: (post: Row) => void;
}

const APIFY_CRAWL_PLATFORMS = new Set([
  'instagram',
  'tiktok',
  'xiaohongshu',
  'bilibili',
  'facebook',
  'reddit',
  'x',
]);

function enabledValue(value: unknown): boolean {
  if (typeof value === 'string') {
    return ['1', 'true', 'yes', 'on', 'enabled'].includes(value.trim().toLowerCase());
  }
  return value === true || value === 1;
}

function numberValue(value: unknown): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function budgetReady(row?: Row): boolean {
  return enabledValue(row?.enabled)
    && numberValue(row?.monthly_limit_usd) > 0
    && numberValue(row?.monthly_limit_usd) > numberValue(row?.current_month_spent);
}

function statusReady(value: unknown): boolean {
  return ['configured', 'ok', 'ready', 'success', 'synced', 'historical_import'].includes(
    String(value || '').trim().toLowerCase(),
  );
}

function accountHasSuccessfulSync(account: Row): boolean {
  return statusReady(account.provider_status)
    || statusReady(account.api_status)
    || statusReady(account.sync_status)
    || Boolean(rowString(account, ['last_successful_at']));
}

export function AccountDrawer({
  account,
  snapshots,
  posts,
  accounts,
  platformCrawlSettings = [],
  budgetSettings = [],
  busy,
  onClose,
  onRefresh,
  onToggleCrawl,
  onOpenPost,
}: AccountDrawerProps) {
  // R60: tab state
  const [activeTab, setActiveTab] = useState<TabKey>('执行摘要');
  
  if (!account) {
    return <aside className="da-account-drawer" />;
  }

  const platformKey = rowString(account, ['platform'], 'other').toLowerCase();
  const crawlEnabled = enabledValue(rowString(account, ['crawl_enabled'], '0'));
  const platformSettings = platformCrawlSettings.find(
    (row) => String(row.platform || '').toLowerCase() === platformKey,
  );
  const crawlTotalBudget = budgetSettings.find(
    (row) => String(row.budget_key || '').toLowerCase() === 'crawl_total',
  );
  const apifyBudget = budgetSettings.find(
    (row) => String(row.budget_key || '').toLowerCase() === 'apify',
  );
  const platformLimitReady = numberValue(platformSettings?.daily_account_limit) > 0
    && numberValue(platformSettings?.posts_per_account) > 0;
  const apiStatus = String(platformSettings?.last_test_status || 'not_configured');
  const accountSyncReady = accountHasSuccessfulSync(account);
  const apiReady = accountSyncReady || statusReady(apiStatus);
  const apiDetail = accountSyncReady
    ? '最近同步成功'
    : (apiReady ? apiStatus : 'API 未测试或未配置');
  const crawlGateItems = [
    {
      key: 'account',
      label: '账号抓取',
      ok: crawlEnabled,
      detail: crawlEnabled ? '已开启' : '该账号未开启抓取',
    },
    {
      key: 'platform',
      label: '平台开关',
      ok: enabledValue(platformSettings?.crawl_enabled),
      detail: enabledValue(platformSettings?.crawl_enabled) ? '平台抓取已开启' : `${platformDisplay(platformKey)} 平台抓取关闭`,
    },
    {
      key: 'limit',
      label: '平台限制',
      ok: platformLimitReady,
      detail: platformLimitReady
        ? `每日 ${numberValue(platformSettings?.daily_account_limit)} 个账号 / 每号 ${numberValue(platformSettings?.posts_per_account)} 条内容`
        : '每日账号数或每号内容数为 0',
    },
    {
      key: 'platform-budget',
      label: '平台月预算',
      ok: numberValue(platformSettings?.monthly_budget_usd) > 0,
      detail: numberValue(platformSettings?.monthly_budget_usd) > 0
        ? `$${numberValue(platformSettings?.monthly_budget_usd)}`
        : '平台月预算为 0',
    },
    {
      key: 'crawl-total',
      label: '全局 crawl_total',
      ok: budgetReady(crawlTotalBudget),
      detail: budgetReady(crawlTotalBudget)
        ? `$${Math.max(numberValue(crawlTotalBudget?.monthly_limit_usd) - numberValue(crawlTotalBudget?.current_month_spent), 0)} 可用`
        : 'crawl_total 未启用或余额为 0',
    },
    ...(APIFY_CRAWL_PLATFORMS.has(platformKey) ? [{
      key: 'apify',
      label: 'Apify 预算',
      ok: budgetReady(apifyBudget),
      detail: budgetReady(apifyBudget)
        ? `$${Math.max(numberValue(apifyBudget?.monthly_limit_usd) - numberValue(apifyBudget?.current_month_spent), 0)} 可用`
        : 'Apify 预算未启用或余额为 0',
    }] : []),
    {
      key: 'api',
      label: 'API 状态',
      ok: apiReady,
      detail: apiDetail,
    },
  ];
  const firstBlockedGate = crawlGateItems.find((item) => !item.ok);
  const avatarUrl = proxiedImageUrl(accountAvatarUrl(account));
  const profileUrl = platformExternalUrl(accountProfileUrl(account));

  return (
    <aside className="da-account-drawer da-account-drawer--open">
      <header className="da-account-drawer__header">
        <div className={`da-account-drawer__avatar ${platformClass(rowString(account, ['platform']))}`}>
          {avatarUrl ? <img src={avatarUrl} alt="" loading="lazy" /> : platformInitial(rowString(account, ['platform']))}
        </div>
        <div>
          <span>账号分析</span>
          <h3>{accountName(account)}</h3>
          <p>{platformDisplay(rowString(account, ['platform']))} · /{accountName(account)}</p>
          {profileUrl ? (
            <a
              className="da-link-button"
              href={profileUrl}
              target="_blank"
              rel="noopener noreferrer"
            >打开平台主页 ↗</a>
          ) : null}
        </div>
        <button type="button" onClick={onClose} aria-label="关闭账号详情">×</button>
      </header>
      
      <div className="da-account-drawer__body">
        {/* R60: tab 切换 */}
        <nav className="da-account-tabs" role="tablist">
          {ACCOUNT_TABS.map((tab) => (
            <button
              key={tab}
              type="button"
              role="tab"
              aria-selected={activeTab === tab}
              className={`da-account-tab${activeTab === tab ? ' da-account-tab--active' : ''}`}
              onClick={() => setActiveTab(tab)}
            >
              {tab}
            </button>
          ))}
        </nav>
        
        {/* R60: 条件渲染各 tab 内容 */}
        <div className="da-account-tab-content" role="tabpanel">
          {activeTab === '执行摘要' && (
            <SummaryTab account={account} snapshots={snapshots} posts={posts} />
          )}
          {activeTab === 'Content' && (
            <ContentTab account={account} posts={posts} onOpenPost={onOpenPost} />
          )}
          {activeTab === 'Engagement' && (
            <EngagementTab account={account} snapshots={snapshots} />
          )}
          {activeTab === 'Views' && (
            <ViewsTab account={account} snapshots={snapshots} />
          )}
          {activeTab === 'Audience' && (
            <AudienceTab account={account} snapshots={snapshots} />
          )}
          {activeTab === 'Content Pillars' && (
            <PillarsTab account={account} posts={posts} />
          )}
          {activeTab === 'Organic Value' && (
            <OrganicValueTab account={account} snapshots={snapshots} />
          )}
          {activeTab === 'Posts' && (
            <PostsTab account={account} posts={posts} onOpenPost={onOpenPost} />
          )}
          {activeTab === 'Compare' && (
            <CompareTab account={account} accounts={accounts} snapshots={snapshots} />
          )}
        </div>
        
        {/* P2.24: 预算与抓取闭环,解释为什么开启账号后仍可能不触发外部抓取 */}
        <section className="da-crawl-gate-panel">
          <div className="da-crawl-gate-panel__header">
            <h4>抓取闸门</h4>
            <span className={firstBlockedGate ? 'is-blocked' : 'is-ok'}>
              {firstBlockedGate ? '未通过' : '已通过'}
            </span>
          </div>
          <p>
            {firstBlockedGate
              ? `当前阻塞: ${firstBlockedGate.detail}`
              : '账号、平台、预算和 API 状态均通过,可以刷新该账号。'}
          </p>
          <div className="da-crawl-gate-grid">
            {crawlGateItems.map((item) => (
              <div
                key={item.key}
                className={`da-crawl-gate-item${item.ok ? ' is-ok' : ' is-blocked'}`}
                title={item.detail}
              >
                <span>{item.label}</span>
                <strong>{item.ok ? '通过' : '阻塞'}</strong>
              </div>
            ))}
          </div>
        </section>

        {/* 通用元信息 (所有 tab 共享) */}
        <div className="da-intelligence-list" style={{ marginTop: 16 }}>
          <div><span>角色</span><strong>{rowString(account, ['account_role'], 'reference')}</strong></div>
          <div><span>同步状态</span><strong>{rowString(account, ['sync_status'], 'not_configured')}</strong></div>
          <div>
            <span>抓取</span>
            <strong>{crawlEnabled ? '已开启' : '已关闭'}</strong>
          </div>
          <div><span>最近成功</span><strong>{prettyDate(rowString(account, ['last_successful_at']))}</strong></div>
        </div>
        
        {/* 通用操作 (刷新 + 切换抓取) */}
        <div style={{ marginTop: 16 }}>
          <button
            className="da-black-button"
            type="button"
            disabled={busy}
            onClick={() => onRefresh(accountId(account))}
          >刷新该账号</button>
          <button
            className="da-white-button"
            type="button"
            disabled={busy}
            style={{ marginLeft: 10 }}
            onClick={() => onToggleCrawl(accountId(account), !crawlEnabled)}
          >{crawlEnabled ? '关闭账号抓取' : '开启账号抓取'}</button>
        </div>
        {!crawlEnabled ? (
          <p className="da-muted-copy" style={{ marginTop: 8 }}>
            该账号抓取关闭。开启后仍需平台抓取开关、预算和对应 API 配置通过,才会同步真实数据。
          </p>
        ) : null}
      </div>
    </aside>
  );
}
