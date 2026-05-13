import { useMemo, useState } from 'react';

import type { Row } from '../utils/types';
import { ACCOUNT_TABS } from '../utils/types';
import { accountId, accountName, rowNumber, rowString } from '../utils/rowAccessors';
import { platformExternalUrl, proxiedImageUrl } from '../utils/mediaProxy';
import { accountAvatarUrl, accountProfileUrl } from '../utils/mediaFields';
import {
  compact, normalizePlatform, platformClass, platformDisplay, platformInitial, prettyDate,
} from '../utils/platformHelpers';
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
} from '../drawers/tabs';

type AccountTabKey = typeof ACCOUNT_TABS[number];

interface ProfileDashboardProps {
  account: Row;
  snapshots: Row[];
  posts: Row[];
  accounts: Row[];
  platformCrawlSettings: Row[];
  budgetSettings: Row[];
  busy: boolean;
  onBack: () => void;
  onRefresh: (id: string) => void;
  onToggleCrawl: (id: string, enabled: boolean) => void;
  onOpenPost: (post: Row) => void;
}

const PAID_CRAWL_PLATFORMS = new Set([
  'instagram',
  'tiktok',
  'facebook',
  'x',
  'twitter',
  'xiaohongshu',
  'xhs',
  'bilibili',
  'reddit',
]);

function enabledValue(value: unknown): boolean {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'number') return value !== 0;
  const normalized = String(value || '').trim().toLowerCase();
  return ['1', 'true', 'yes', 'on', 'enabled'].includes(normalized);
}

function numberValue(value: unknown): number {
  if (value === null || value === undefined || value === '') return 0;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function platformHomeUrl(platform: string, handle: string): string {
  const clean = handle.replace(/^@/, '').replace(/^\/+/, '');
  if (!clean) return '';
  if (platform === 'instagram') return `https://www.instagram.com/${clean}/`;
  if (platform === 'tiktok') return `https://www.tiktok.com/@${clean}`;
  if (platform === 'youtube') return clean.startsWith('@') ? `https://www.youtube.com/${clean}` : `https://www.youtube.com/@${clean}`;
  if (platform === 'facebook') return `https://www.facebook.com/${clean}`;
  if (platform === 'x' || platform === 'twitter') return `https://x.com/${clean}`;
  if (platform === 'reddit') return clean.startsWith('r/') ? `https://www.reddit.com/${clean}` : `https://www.reddit.com/user/${clean}`;
  return '';
}

function budgetByKey(budgets: Row[], keys: string[]): Row | null {
  return budgets.find((budget) => keys.includes(String(budget.budget_key || budget.key || '').toLowerCase())) || null;
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
    || Boolean(rowString(account, ['last_successful_at', 'last_crawled_at']));
}

function tabLabel(tab: AccountTabKey): string {
  if (tab === '执行摘要') return 'Summary';
  if (tab === 'Content Pillars') return 'Pillars';
  if (tab === 'Organic Value') return 'Organic Value';
  return tab;
}

export function ProfileDashboard({
  account,
  snapshots,
  posts,
  accounts,
  platformCrawlSettings,
  budgetSettings,
  busy,
  onBack,
  onRefresh,
  onToggleCrawl,
  onOpenPost,
}: ProfileDashboardProps) {
  const [activeTab, setActiveTab] = useState<AccountTabKey>('执行摘要');
  const accountKey = accountId(account);
  const platform = normalizePlatform(rowString(account, ['platform']));
  const handle = accountName(account);
  const avatarUrl = proxiedImageUrl(accountAvatarUrl(account));
  const profileUrl = platformExternalUrl(accountProfileUrl(account)) || platformHomeUrl(platform, handle);
  const latest = snapshots[0] || {};
  const followers = rowNumber(latest, ['followers', 'follower_count'])
    ?? rowNumber(account, ['followers', 'follower_count']);
  const views = rowNumber(latest, ['views_30d', 'views', 'video_views']);
  const engagementRate = rowNumber(latest, ['engagement_rate', 'avg_eng_rate_by_followers']);
  const lastSuccess = rowString(account, ['last_successful_at', 'last_crawled_at', 'updated_at']);
  const crawlEnabled = enabledValue(account.crawl_enabled);

  const crawlConfig = useMemo(() => {
    const setting = platformCrawlSettings.find((item) => normalizePlatform(item.platform) === platform) || {};
    const paidPlatform = PAID_CRAWL_PLATFORMS.has(platform);
    const platformEnabled = enabledValue(setting.crawl_enabled);
    const accountLimit = numberValue(setting.daily_account_limit);
    const postsPerAccount = numberValue(setting.posts_per_account);
    const platformBudget = numberValue(setting.monthly_budget_usd);
    const platformTestStatus = String(setting.last_test_status || '').toLowerCase();
    const accountSyncReady = accountHasSuccessfulSync(account);
    const apiReady = !paidPlatform || accountSyncReady || statusReady(platformTestStatus);
    const apiHint = accountSyncReady
      ? '最近同步成功'
      : (apiReady ? (platformTestStatus || '通过') : '未测试或未配置');
    const platformBudgetReady = !paidPlatform || platformBudget > 0;
    const globalCrawlBudget = budgetByKey(budgetSettings, ['crawl_total', 'total', 'crawl']);
    const apifyBudget = budgetByKey(budgetSettings, ['apify']);
    const globalBudgetReady = numberValue(globalCrawlBudget?.monthly_limit_usd) > numberValue(globalCrawlBudget?.current_month_spent);
    const apifyBudgetReady = !paidPlatform || numberValue(apifyBudget?.monthly_limit_usd) > numberValue(apifyBudget?.current_month_spent);
    const gateItems = [
      { label: '账号抓取', ok: crawlEnabled, hint: crawlEnabled ? '已开启' : '右上角可开启' },
      { label: '平台开关', ok: platformEnabled, hint: platformEnabled ? '已开启' : '系统设置未开启' },
      { label: '每日账号', ok: accountLimit > 0, hint: accountLimit > 0 ? `${accountLimit}/day` : '未配置' },
      { label: '每号内容', ok: postsPerAccount > 0, hint: postsPerAccount > 0 ? `${postsPerAccount}/account` : '未配置' },
      { label: '平台月预算', ok: platformBudgetReady, hint: platformBudgetReady ? `$${platformBudget}` : '未配置' },
      { label: '全局预算', ok: globalBudgetReady, hint: globalBudgetReady ? '通过' : '未配置' },
      { label: 'API 状态', ok: apiReady, hint: apiHint },
      { label: 'Apify 预算', ok: apifyBudgetReady, hint: apifyBudgetReady ? '通过' : '未配置' },
    ];
    const blocked = gateItems.find((item) => !item.ok);
    return {
      blockedReason: blocked ? `${blocked.label}: ${blocked.hint}` : '',
      gateItems,
      ready: !blocked,
    };
  }, [account, budgetSettings, crawlEnabled, platform, platformCrawlSettings]);

  const renderTab = () => {
    const props = { account, snapshots, posts, accounts, onOpenPost };
    switch (activeTab) {
      case '执行摘要':
        return <SummaryTab {...props} />;
      case 'Content':
        return <ContentTab {...props} />;
      case 'Engagement':
        return <EngagementTab {...props} />;
      case 'Views':
        return <ViewsTab {...props} />;
      case 'Audience':
        return <AudienceTab {...props} />;
      case 'Content Pillars':
        return <PillarsTab {...props} />;
      case 'Organic Value':
        return <OrganicValueTab {...props} />;
      case 'Posts':
        return <PostsTab {...props} />;
      case 'Compare':
        return <CompareTab {...props} />;
      default:
        return null;
    }
  };

  return (
    <section className="da-profile-shell">
      <header className="da-profile-header">
        <button className="da-profile-back" type="button" onClick={onBack}>← 账号矩阵</button>
        <div className="da-profile-identity">
          <div className={`da-profile-avatar ${platformClass(platform)}`}>
            {avatarUrl ? <img src={avatarUrl} alt="" loading="lazy" /> : platformInitial(platform)}
          </div>
          <div>
            <span className="da-kicker da-kicker--profile">账号分析 · {platformDisplay(platform)}</span>
            <h1>{handle}</h1>
            <p>
              {profileUrl ? (
                <button type="button" className="da-link-button" onClick={() => window.open(profileUrl, '_blank', 'noopener,noreferrer')}>
                  打开平台主页 ↗
                </button>
              ) : '平台主页待补充'}
              <span> · 最近成功: {prettyDate(lastSuccess)}</span>
            </p>
          </div>
        </div>
        <div className="da-profile-actions">
          <button
            className="da-white-button"
            type="button"
            disabled={busy}
            onClick={() => onRefresh(accountKey)}
          >刷新该账号</button>
          <button
            className={crawlEnabled ? 'da-danger-button' : 'da-black-button'}
            type="button"
            disabled={busy}
            onClick={() => onToggleCrawl(accountKey, !crawlEnabled)}
          >{crawlEnabled ? '关闭抓取' : '开启抓取'}</button>
        </div>
      </header>

      <div className="da-profile-status-row">
        <div><span>Followers</span><strong>{compact(followers)}</strong></div>
        <div><span>Posts</span><strong>{compact(posts.length)}</strong></div>
        <div><span>Views</span><strong>{compact(views)}</strong></div>
        <div><span>Engagement</span><strong>{engagementRate !== null && engagementRate !== undefined ? `${engagementRate.toFixed(2)}%` : '—'}</strong></div>
      </div>

      <div className={`da-profile-gate-summary${crawlConfig.ready ? ' is-ok' : ' is-blocked'}`}>
        <strong>{crawlConfig.ready ? '抓取链路已通过' : '抓取链路未通过'}</strong>
        <span>{crawlConfig.ready ? '平台开关、预算、API 与账号抓取均可用。' : `当前阻塞: ${crawlConfig.blockedReason}`}</span>
      </div>

      <nav className="da-profile-tabs" aria-label="账号分析模块">
        {ACCOUNT_TABS.map((tab) => (
          <button
            key={tab}
            type="button"
            className={activeTab === tab ? 'is-active' : ''}
            onClick={() => setActiveTab(tab)}
          >{tabLabel(tab)}</button>
        ))}
      </nav>

      <div className="da-profile-content">
        {renderTab()}
      </div>

      <details className="da-profile-advanced">
        <summary>高级诊断 · 抓取闸门 / 预算 / API 状态</summary>
        <div className="da-crawl-gate-grid da-crawl-gate-grid--compact">
          {crawlConfig.gateItems.map((item) => (
            <div key={item.label} className={`da-crawl-gate-item ${item.ok ? 'is-ok' : 'is-blocked'}`}>
              <span>{item.label}</span>
              <strong>{item.ok ? '通过' : '阻塞'}</strong>
              <em>{item.hint}</em>
            </div>
          ))}
        </div>
      </details>
    </section>
  );
}
