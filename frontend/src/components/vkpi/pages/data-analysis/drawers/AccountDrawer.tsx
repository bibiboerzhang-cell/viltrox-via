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
  busy: boolean;
  onClose: () => void;
  onRefresh: (id: string) => void;
  onToggleCrawl: (id: string, enabled: boolean) => void;
}

export function AccountDrawer({
  account,
  snapshots,
  posts,
  accounts,
  busy,
  onClose,
  onRefresh,
  onToggleCrawl,
}: AccountDrawerProps) {
  // R60: tab state
  const [activeTab, setActiveTab] = useState<TabKey>('执行摘要');
  
  if (!account) {
    return <aside className="da-account-drawer" />;
  }

  const crawlEnabled = String(rowString(account, ['crawl_enabled'], '0')) === '1'
    || rowString(account, ['crawl_enabled'], '') === 'true';

  return (
    <aside className="da-account-drawer da-account-drawer--open">
      <header className="da-account-drawer__header">
        <div className={`da-account-drawer__avatar ${platformClass(rowString(account, ['platform']))}`}>
          {platformInitial(rowString(account, ['platform']))}
        </div>
        <div>
          <span>账号分析</span>
          <h3>{accountName(account)}</h3>
          <p>{platformDisplay(rowString(account, ['platform']))} · /{accountName(account)}</p>
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
            <ContentTab account={account} posts={posts} />
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
            <PostsTab account={account} posts={posts} />
          )}
          {activeTab === 'Compare' && (
            <CompareTab account={account} accounts={accounts} snapshots={snapshots} />
          )}
        </div>
        
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
