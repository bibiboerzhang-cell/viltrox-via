import { useMemo, useState } from 'react';
import type { Row } from '../../utils/types';
import { accountId, accountName, rowNumber, rowString } from '../../utils/rowAccessors';
import { bestPosting, contentPillars, formatMetric } from '../../utils/metricHelpers';
import { platformExternalUrl, proxiedImageUrl, proxiedVideoUrl, redirectedVideoUrl } from '../../utils/mediaProxy';
import { postPlatformUrl, postThumbnailUrl, postVideoUrl } from '../../utils/mediaFields';
import { prettyDate } from '../../utils/platformHelpers';
import { BigNumberCard } from '../../shared/BigNumberCard';

interface BaseTabProps {
  account: Row;
  snapshots?: Row[];
  posts?: Row[];
  accounts?: Row[];
}

function stablePostKey(post: Row, index: number): string {
  return rowString(post, ['id', 'post_id', 'source_ref', 'url', 'post_url']) || `post-${index}`;
}

function postEngagement(post: Row): number {
  return (rowNumber(post, ['likes', 'like_count']) || 0)
    + (rowNumber(post, ['comments', 'comment_count']) || 0)
    + (rowNumber(post, ['shares', 'share_count']) || 0)
    + (rowNumber(post, ['saves', 'save_count']) || 0);
}

function newestSnapshot(snapshots: Row[]): Row | undefined {
  return snapshots[0];
}

function currencyFromCents(value: number | null): string {
  if (value === null) return '$0';
  return `$${new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(value / 100)}`;
}

function MiniSnapshotTable({ snapshots }: { snapshots: Row[] }) {
  if (!snapshots.length) return <div className="vkpi-empty">暂无历史快照。</div>;
  return (
    <div className="da-table-wrap" style={{ marginTop: 16 }}>
      <table className="da-table">
        <thead>
          <tr>
            <th>日期</th>
            <th>Followers</th>
            <th>Views</th>
            <th>Engagement</th>
            <th>Rate</th>
          </tr>
        </thead>
        <tbody>
          {snapshots.slice(0, 10).map((snapshot, index) => (
            <tr key={`${rowString(snapshot, ['snapshot_date'])}-${index}`}>
              <td>{prettyDate(rowString(snapshot, ['snapshot_date']))}</td>
              <td>{formatMetric(rowNumber(snapshot, ['followers']))}</td>
              <td>{formatMetric(rowNumber(snapshot, ['views_30d', 'views']))}</td>
              <td>{formatMetric(rowNumber(snapshot, ['engagement_total_30d', 'engagement']))}</td>
              <td>{formatMetric(rowNumber(snapshot, ['engagement_rate']), 'avg_eng_rate_followers')}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SummaryTab({ account, snapshots = [], posts = [] }: BaseTabProps) {
  const latest = newestSnapshot(snapshots);
  const previous = snapshots[1];
  const followers = rowNumber(latest, ['followers', 'follower_count'])
    ?? rowNumber(account, ['followers', 'follower_count']);
  const views = rowNumber(latest, ['views_30d', 'views'])
    ?? posts.reduce((sum, post) => sum + (rowNumber(post, ['views', 'view_count', 'video_views']) || 0), 0);
  const previousViews = rowNumber(previous, ['views_30d', 'views']);
  const viewsTrend = previousViews && views !== null
    ? `${views >= previousViews ? '上升' : '下降'} ${Math.abs(((views - previousViews) / previousViews) * 100).toFixed(1)}%`
    : '暂无对比';
  const engagement = rowNumber(latest, ['engagement_total_30d', 'engagement'])
    ?? posts.reduce((sum, post) => sum + postEngagement(post), 0);
  const engagementRate = rowNumber(latest, ['engagement_rate']);
  const best = bestPosting(posts);

  return (
    <div className="da-tab-summary">
      <div className="da-detail-grid">
        <BigNumberCard title="Followers" value={formatMetric(followers)} delta={latest ? '真实快照' : '账号字段'} tone="neutral" />
        <BigNumberCard title="Views (30d)" value={formatMetric(views)} delta={viewsTrend} tone={views ? 'positive' : 'neutral'} />
        <BigNumberCard title="Engagement" value={formatMetric(engagement)} delta={engagementRate !== null ? `${engagementRate.toFixed(2)}%` : '内容合计'} tone={engagement ? 'positive' : 'neutral'} />
        <BigNumberCard title="Posts" value={String(posts.length)} delta={posts.length ? '已载入内容' : '待同步'} tone={posts.length ? 'positive' : 'neutral'} />
        <BigNumberCard title="发布最多日" value={best.day} delta="基于已载入内容" />
        <BigNumberCard title="发布最多时" value={best.hour} delta="基于已载入内容" />
      </div>
      <MiniSnapshotTable snapshots={snapshots} />
      <div className="da-summary-footer" style={{ marginTop: 16, fontSize: 13, color: 'var(--vkpi-color-text-muted)' }}>
        <p>最近成功同步: {prettyDate(rowString(account, ['last_successful_at']))}</p>
        <p>快照数: {snapshots.length} · 已载入帖子: {posts.length}</p>
      </div>
    </div>
  );
}

export function ContentTab({ posts = [] }: BaseTabProps) {
  const [showAll, setShowAll] = useState(false);
  const [videoFallbackPosts, setVideoFallbackPosts] = useState<Record<string, boolean>>({});
  const sortedPosts = useMemo(
    () => [...posts].sort((a, b) => rowString(b, ['published_at', 'created_at']).localeCompare(rowString(a, ['published_at', 'created_at']))),
    [posts],
  );
  const visiblePosts = showAll ? sortedPosts : sortedPosts.slice(0, 24);

  if (posts.length === 0) {
    return <div className="vkpi-empty">暂无 post 数据。需要先抓取或开启平台抓取。</div>;
  }

  return (
    <div className="da-tab-content">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <span style={{ fontSize: 13, color: 'var(--vkpi-color-text-muted)' }}>显示 {visiblePosts.length} / {sortedPosts.length} 条内容</span>
        {sortedPosts.length > 24 ? (
          <button className="da-text-button" type="button" onClick={() => setShowAll((value) => !value)}>
            {showAll ? '只看 Top 24' : `显示全部 ${sortedPosts.length} 条`}
          </button>
        ) : null}
      </div>
      <div className="da-content-grid">
        {visiblePosts.map((post, index) => {
          const thumbnail = proxiedImageUrl(postThumbnailUrl(post));
          const rawVideoUrl = postVideoUrl(post);
          const proxiedUrl = proxiedVideoUrl(rawVideoUrl);
          const fallbackUrl = redirectedVideoUrl(rawVideoUrl);
          const postKey = stablePostKey(post, index);
          const videoUrl = videoFallbackPosts[postKey] && fallbackUrl ? fallbackUrl : proxiedUrl;
          const postUrl = platformExternalUrl(postPlatformUrl(post));
          const title = rowString(post, ['title', 'caption'], '(无标题)');
          const views = rowNumber(post, ['views', 'view_count', 'video_views', 'play_count']);
          const likes = rowNumber(post, ['likes', 'like_count']);
          const comments = rowNumber(post, ['comments', 'comment_count']);
          const publishedAt = rowString(post, ['published_at', 'created_at']);

          return (
            <div key={postKey} className="da-post-card">
              {videoUrl ? (
                <video
                  className="da-post-thumbnail"
                  src={videoUrl}
                  poster={thumbnail || undefined}
                  controls
                  preload="metadata"
                  playsInline
                  onError={() => {
                    if (fallbackUrl && fallbackUrl !== videoUrl) {
                      setVideoFallbackPosts((state) => ({ ...state, [postKey]: true }));
                    }
                  }}
                />
              ) : thumbnail ? (
                <img src={thumbnail} alt="" className="da-post-thumbnail" loading="lazy" />
              ) : (
                <div className="da-post-thumbnail da-post-thumbnail--placeholder">Media</div>
              )}
              <div className="da-post-meta">
                <div className="da-post-title" title={title}>{title.slice(0, 80)}</div>
                <div className="da-post-stats">
                  <span>Views {formatMetric(views)}</span>
                  <span>Likes {formatMetric(likes)}</span>
                  <span>Comments {formatMetric(comments)}</span>
                </div>
                <div className="da-post-date">{prettyDate(publishedAt)}</div>
                {postUrl ? (
                  <button className="da-link-button" type="button" onClick={() => window.open(postUrl, '_blank', 'noopener,noreferrer')}>
                    打开平台
                  </button>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function EngagementTab({ snapshots = [], posts = [] }: BaseTabProps) {
  const latest = newestSnapshot(snapshots);
  const engagement = rowNumber(latest, ['engagement_total_30d', 'engagement'])
    ?? posts.reduce((sum, post) => sum + postEngagement(post), 0);
  const recentRate = rowNumber(latest, ['engagement_rate']);
  const avgPerDay = rowNumber(latest, ['avg_engagement_per_day']) ?? (engagement ? engagement / 30 : null);
  return (
    <div className="da-tab-engagement">
      <div className="da-detail-grid">
        <BigNumberCard title="Engagement" value={formatMetric(engagement)} delta="互动总量" tone={engagement ? 'positive' : 'neutral'} />
        <BigNumberCard title="Engagement Rate" value={formatMetric(recentRate, 'avg_eng_rate_followers')} delta="按粉丝估算" tone={recentRate ? 'positive' : 'neutral'} />
        <BigNumberCard title="Avg Engagement / Day" value={formatMetric(avgPerDay)} delta="30 天口径" />
      </div>
      <MiniSnapshotTable snapshots={snapshots} />
    </div>
  );
}

export function ViewsTab({ snapshots = [], posts = [] }: BaseTabProps) {
  const latest = newestSnapshot(snapshots);
  const views30d = rowNumber(latest, ['views_30d', 'views'])
    ?? posts.reduce((sum, post) => sum + (rowNumber(post, ['views', 'view_count', 'video_views']) || 0), 0);
  const reach30d = rowNumber(latest, ['reach_total_30d', 'reach_30d']);
  const impressions30d = rowNumber(latest, ['impressions_total_30d', 'impressions_30d']);
  const reelsViews = rowNumber(latest, ['reels_views_30d', 'reels_views']);
  return (
    <div className="da-tab-views">
      <div className="da-detail-grid">
        <BigNumberCard title="Views (30d)" value={formatMetric(views30d)} delta="快照或帖子合计" tone={views30d ? 'positive' : 'neutral'} />
        <BigNumberCard title="Reach (30d)" value={formatMetric(reach30d)} delta="平台可用时显示" />
        <BigNumberCard title="Impressions (30d)" value={formatMetric(impressions30d)} delta="平台可用时显示" />
        <BigNumberCard title="Reels Views" value={formatMetric(reelsViews)} delta="短视频口径" />
      </div>
      <MiniSnapshotTable snapshots={snapshots} />
    </div>
  );
}

export function AudienceTab({ account }: BaseTabProps) {
  return (
    <div className="da-tab-audience">
      <div className="da-detail-grid">
        <BigNumberCard title="Region" value={rowString(account, ['region'], '—')} delta="账号字段" />
        <BigNumberCard title="Category" value={rowString(account, ['category'], '—')} delta="账号字段" />
        <BigNumberCard title="Role" value={rowString(account, ['account_role'], 'reference')} delta="矩阵角色" />
      </div>
      <p className="da-muted-copy" style={{ marginTop: 16 }}>
        当前平台未返回完整受众画像时,只展示公开账号字段。后续 audience API 接入后会在这里显示国家、城市、年龄、性别和活跃时段。
      </p>
    </div>
  );
}

export function PillarsTab({ posts = [] }: BaseTabProps) {
  const pillars = contentPillars(posts);
  return (
    <div className="da-tab-pillars">
      {pillars.length ? (
        <div className="da-table-wrap">
          <table className="da-table">
            <thead><tr><th>Pillar</th><th>Posts</th><th>占比</th></tr></thead>
            <tbody>
              {pillars.map((pillar) => (
                <tr key={pillar.label}>
                  <td>{pillar.label}</td>
                  <td>{pillar.value}</td>
                  <td>{posts.length ? `${((pillar.value / posts.length) * 100).toFixed(1)}%` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="vkpi-empty">暂无内容支柱数据。帖子导入后会按已有 pillar/category 字段聚合。</div>
      )}
    </div>
  );
}

export function OrganicValueTab({ snapshots = [] }: BaseTabProps) {
  const latest = newestSnapshot(snapshots);
  const orgValueCents = rowNumber(latest, ['estimated_organic_value_cents']);
  const avgViews = rowNumber(latest, ['avg_views', 'views_30d']);
  return (
    <div className="da-tab-organic-value">
      <div className="da-detail-grid">
        <BigNumberCard title="Organic Value" value={currencyFromCents(orgValueCents)} delta="估算值" tone={orgValueCents ? 'positive' : 'neutral'} />
        <BigNumberCard title="Avg Views" value={formatMetric(avgViews)} delta="估算公式输入" />
      </div>
      <MiniSnapshotTable snapshots={snapshots} />
    </div>
  );
}

export function PostsTab({ posts = [] }: BaseTabProps) {
  const [showAll, setShowAll] = useState(false);
  const sortedPosts = useMemo(
    () => [...posts].sort((a, b) => rowString(b, ['published_at', 'created_at']).localeCompare(rowString(a, ['published_at', 'created_at']))),
    [posts],
  );
  const visiblePosts = showAll ? sortedPosts : sortedPosts.slice(0, 50);

  if (posts.length === 0) {
    return <div className="vkpi-empty">暂无 post 数据。</div>;
  }

  return (
    <div className="da-tab-posts">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <span style={{ fontSize: 13, color: 'var(--vkpi-color-text-muted)' }}>显示 {visiblePosts.length} / {sortedPosts.length} 条</span>
        {sortedPosts.length > 50 ? (
          <button className="da-text-button" type="button" onClick={() => setShowAll((value) => !value)}>
            {showAll ? '只看前 50' : `显示全部 ${sortedPosts.length} 条`}
          </button>
        ) : null}
      </div>
      <table className="vkpi-table" style={{ fontSize: 13 }}>
        <thead>
          <tr>
            <th>标题</th>
            <th>发布时间</th>
            <th>Views</th>
            <th>Likes</th>
            <th>Comments</th>
            <th>Engagement</th>
            <th>平台</th>
          </tr>
        </thead>
        <tbody>
          {visiblePosts.map((post, index) => {
            const postUrl = platformExternalUrl(postPlatformUrl(post));
            return (
              <tr key={stablePostKey(post, index)}>
                <td>{rowString(post, ['title', 'caption'], '(无标题)').slice(0, 60)}</td>
                <td>{prettyDate(rowString(post, ['published_at', 'created_at']))}</td>
                <td>{formatMetric(rowNumber(post, ['views', 'view_count', 'video_views']))}</td>
                <td>{formatMetric(rowNumber(post, ['likes', 'like_count']))}</td>
                <td>{formatMetric(rowNumber(post, ['comments', 'comment_count']))}</td>
                <td>{formatMetric(postEngagement(post))}</td>
                <td>{postUrl ? <button className="da-link-button" type="button" onClick={() => window.open(postUrl, '_blank', 'noopener,noreferrer')}>打开</button> : '—'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function CompareTab({ account, accounts = [] }: BaseTabProps) {
  const otherAccounts = accounts.filter((a) => accountId(a) !== accountId(account)).slice(0, 8);
  return (
    <div className="da-tab-compare">
      {otherAccounts.length ? (
        <div className="da-table-wrap">
          <table className="da-table">
            <thead><tr><th>账号</th><th>平台</th><th>状态</th><th>角色</th></tr></thead>
            <tbody>
              <tr className="da-table__benchmark-row">
                <td>{accountName(account)}</td>
                <td>{rowString(account, ['platform'])}</td>
                <td>{rowString(account, ['sync_status'], 'not_configured')}</td>
                <td>{rowString(account, ['account_role'], 'reference')}</td>
              </tr>
              {otherAccounts.map((item) => (
                <tr key={accountId(item)}>
                  <td>{accountName(item)}</td>
                  <td>{rowString(item, ['platform'])}</td>
                  <td>{rowString(item, ['sync_status'], 'not_configured')}</td>
                  <td>{rowString(item, ['account_role'], 'reference')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="vkpi-empty">当前项目内没有可对比账号。添加同平台或竞品账号后会展示横向对比。</div>
      )}
    </div>
  );
}
