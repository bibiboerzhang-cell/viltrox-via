import { useState } from 'react';
import type { ChartKey, Row } from '../utils/types';
import { rowString, rowNumber, accountId, accountName, findAccountForPost, postKey } from '../utils/rowAccessors';
import { platformClass, platformDisplay, platformInitial, compact } from '../utils/platformHelpers';
import {
  formatMetric, latestSnapshotValue, metricForAccount, bestPosting, distributionByDate,
  postsForAccount,
} from '../utils/metricHelpers';
import { BigNumberCard } from '../shared/BigNumberCard';
import { DaCard } from '../shared/DaCard';
import { EmptyState } from '../shared/EmptyState';
import { TimeSeriesChart } from '../shared/TimeSeriesChart';
import { PostCard } from '../shared/PostCard';
import { proxiedImageUrl } from '../utils/mediaProxy';
import { accountAvatarUrl } from '../utils/mediaFields';

interface HomeTabProps {
  accounts: Row[];
  crossPlatform: Row[];
  posts: Row[];
  busy: boolean;
  onOpenAccount: (account: Row) => void;
  onRefreshAccount: (id: string) => void;
  onOpenFilter: () => void;
  onSetSelectedAccount: (account: Row | null) => void;
  onOpenPost: (post: Row) => void;
  selectedAccount: Row | null;
  selectedCharts: ChartKey[];
}

export function HomeTab({
  accounts, crossPlatform, posts, busy,
  onOpenAccount, onRefreshAccount, onOpenFilter,
  onSetSelectedAccount, onOpenPost, selectedAccount, selectedCharts,
}: HomeTabProps) {
  const [showAllPosts, setShowAllPosts] = useState(false);
  const visibleTopPosts = showAllPosts ? posts : posts.slice(0, 3);
  const totalFollowers = latestSnapshotValue(
    accounts,
    ['followers', 'follower_count', 'subscribers', 'subscriber_count'],
  );
  const totalViews = posts.reduce(
    (sum, post) => sum + (rowNumber(post, ['views', 'view_count', 'video_views']) || 0),
    0,
  ) || null;
  const totalEngagement = posts.reduce(
    (sum, post) => sum
      + (rowNumber(post, ['likes', 'like_count']) || 0)
      + (rowNumber(post, ['comments', 'comment_count']) || 0)
      + (rowNumber(post, ['shares', 'share_count']) || 0),
    0,
  ) || null;
  const best = bestPosting(posts);

  return (
    <>
      {/* === Overview 5 大数字卡 === */}
      <section className="da-overview-grid">
        <BigNumberCard
          title="Total Profiles"
          value={accounts.length ? compact(accounts.length) : '—'}
          delta={accounts.length ? `${accounts.length} 个账号` : '请先添加账号'}
          tone={accounts.length ? 'positive' : 'neutral'}
        />
        <BigNumberCard
          title="Followers"
          value={formatMetric(totalFollowers)}
          delta={totalFollowers ? '真实快照' : '待同步'}
          tone={totalFollowers ? 'positive' : 'neutral'}
        />
        <BigNumberCard
          title="Posts"
          value={posts.length ? compact(posts.length) : '—'}
          delta={posts.length ? '内容库已就位' : '不展示假内容'}
          tone={posts.length ? 'positive' : 'neutral'}
        />
        <BigNumberCard
          title="Engagement"
          value={formatMetric(totalEngagement)}
          delta={totalEngagement ? '内容信号' : '待同步'}
          tone={totalEngagement ? 'positive' : 'neutral'}
        />
        <BigNumberCard
          title="Views"
          value={formatMetric(totalViews)}
          delta={totalViews ? '来自帖子' : '待同步'}
          tone={totalViews ? 'positive' : 'neutral'}
        />
      </section>

      {selectedCharts.includes('top_profiles') ? (
        <DaCard
          title="Top Performing Profiles"
          eyebrow="账号矩阵"
          side={
            <button className="da-text-button" type="button" onClick={onOpenFilter}>打开筛选</button>
          }
        >
          {accounts.length ? (
            <div className="da-account-grid">
              {accounts.map((account) => {
                const followers = metricForAccount(account, crossPlatform, posts, 'followers');
                const accountPostCount = postsForAccount(posts, account).length;
                const avatarUrl = proxiedImageUrl(accountAvatarUrl(account));
                return (
                  <article
                    className="da-account-card"
                    key={accountId(account)}
                    onClick={() => onOpenAccount(account)}
                  >
                    <div className="da-account-card__top">
                      <div className={`da-account-card__avatar ${platformClass(rowString(account, ['platform']))}`}>
                        {avatarUrl ? <img src={avatarUrl} alt="" loading="lazy" /> : platformInitial(rowString(account, ['platform']))}
                      </div>
                      <div>
                        <h4>{accountName(account)}</h4>
                        <p>{platformDisplay(rowString(account, ['platform']))} · {rowString(account, ['sync_status'], 'not_configured')}</p>
                      </div>
                    </div>
                    <div className="da-account-card__strip">
                      <span>{rowString(account, ['account_role'], 'reference')}</span>
                      <span>{String(rowString(account, ['crawl_enabled'], '0')) === '1' ? 'Crawl on' : 'Crawl off'}</span>
                    </div>
                    <div className="da-account-card__meta">
                      <span>Followers<br /><strong>{formatMetric(followers)}</strong></span>
                      <span>Posts<br /><strong>{formatMetric(accountPostCount)}</strong></span>
                    </div>
                    <div className="da-account-card__actions">
                      <button
                        type="button"
                        onClick={(event) => { event.stopPropagation(); onOpenAccount(account); }}
                      >详情</button>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={(event) => { event.stopPropagation(); onRefreshAccount(accountId(account)); }}
                      >刷新</button>
                    </div>
                  </article>
                );
              })}
            </div>
          ) : (
            <EmptyState
              title="添加首个账号开始监控"
              body="创建项目后添加 Facebook / TikTok / YouTube / Instagram 等账号。抓取未配置时只显示状态,不显示假 0。"
            />
          )}
        </DaCard>
      ) : null}

      {/* === Top Posts === */}
      {selectedCharts.includes('top_posts') ? (
        <DaCard
          title={showAllPosts ? 'All Posts' : 'Top Posts'}
          eyebrow="内容信号"
          wide
          side={posts.length > 3 ? (
            <button
              className="da-text-button"
              type="button"
              onClick={() => setShowAllPosts((value) => !value)}
            >
              {showAllPosts ? '只看 Top 3' : `显示全部 ${posts.length} 条`}
            </button>
          ) : null}
        >
          {posts.length ? (
            <div className="da-post-grid">
              {visibleTopPosts.map((post, idx) => {
                const matchedAccount = findAccountForPost(post, accounts);
                return (
                <PostCard
                  key={postKey(post, idx)}
                  post={post}
                  accounts={accounts}
                  onOpenPost={onOpenPost}
                  onViewAnalytics={() => onSetSelectedAccount(matchedAccount || selectedAccount || accounts[0] || null)}
                />
                );
              })}
            </div>
          ) : (
            <EmptyState
              title="暂无真实帖子"
              body="导入 Apify 历史数据或完成平台同步后,这里会展示真实 PostCard。"
            />
          )}
        </DaCard>
      ) : null}

      {/* === Two-Column #1: Posts Distribution + Posting Signals === */}
      {selectedCharts.includes('posts_distribution') || selectedCharts.includes('posting_signals') ? (
        <section className="da-two-column">
          {selectedCharts.includes('posts_distribution') ? (
            <DaCard title="Posts Distribution" eyebrow="时间序列">
              <TimeSeriesChart data={distributionByDate(posts)} />
            </DaCard>
          ) : null}
          {selectedCharts.includes('posting_signals') ? (
            <DaCard title="Posting Signals" eyebrow="发布时段">
              <div className="da-detail-grid">
                <BigNumberCard title="发布最多日" value={best.day} delta="发布时间" />
                <BigNumberCard title="发布最多时" value={best.hour} delta="发布时间" />
                <BigNumberCard
                  title="平均日发布"
                  value={posts.length ? (posts.length / 30).toFixed(1) : '—'}
                  delta="30天估算"
                />
              </div>
            </DaCard>
          ) : null}
        </section>
      ) : null}
    </>
  );
}
