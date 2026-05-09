// frontend/src/components/vkpi/pages/data-analysis/drawers/tabs/index.tsx
//
// R60: 9 个 Tab 组件统一文件
//
// 设计:
//   - 2 个完整模板 (SummaryTab + ContentTab) - 你 / 团队按这个风格做剩余 7 个
//   - 7 个骨架 (Engagement / Views / Audience / Pillars / OrganicValue / Posts / Compare)
//     接收相同的 props,可以直接渲染 placeholder 占位
//     标记 TODO,留团队按需求填充
//
// 共同接口:
//   - account: 当前账号 Row
//   - snapshots: 最近 30 天 snapshot
//   - posts: 最近 50 条 posts
//   - accounts: 所有账号 (Compare tab 用)
//
// 数据来源 (统一,不需要改后端):
//   GET /api/admin/vkpi/industry-data/accounts/{id}
//   返回: {account, snapshots[30], posts[50]}

import type { Row } from '../../utils/types';
import { rowNumber, rowString } from '../../utils/rowAccessors';
import { formatMetric } from '../../utils/metricHelpers';
import { prettyDate } from '../../utils/platformHelpers';
import { BigNumberCard } from '../../shared/BigNumberCard';

// 共享 props 接口
interface BaseTabProps {
  account: Row;
  snapshots?: Row[];
  posts?: Row[];
  accounts?: Row[];
}

function stablePostKey(post: Row, index: number): string {
  const id = rowString(post, ['id', 'post_id', 'source_ref', 'url', 'post_url']);
  return id || `post-${index}`;
}


// ═══════════════════════════════════════════════
// 完整模板 1: SummaryTab (执行摘要)
// ═══════════════════════════════════════════════

export function SummaryTab({ account, snapshots = [], posts = [] }: BaseTabProps) {
  const latest = snapshots[0];
  const previous = snapshots[1];
  
  const followers = rowNumber(latest, ['followers', 'follower_count'])
    ?? rowNumber(account, ['followers', 'follower_count']);
  const followersGrowth = rowNumber(latest, ['followers_growth_30d']);
  
  const views = rowNumber(latest, ['views_30d', 'views']);
  const previousViews = rowNumber(previous, ['views_30d', 'views']);
  const viewsTrend = previousViews && views
    ? `${views > previousViews ? '↑' : '↓'} ${Math.abs(((views - previousViews) / previousViews) * 100).toFixed(1)}%`
    : '—';
  
  const engagement = rowNumber(latest, ['engagement_total_30d', 'engagement']);
  const engagementRate = rowNumber(latest, ['engagement_rate']);
  
  const postsCount = posts.length;
  const lastSuccess = rowString(account, ['last_successful_at']);
  
  return (
    <div className="da-tab-summary">
      <div className="da-detail-grid">
        <BigNumberCard
          title="Followers"
          value={formatMetric(followers)}
          delta={followersGrowth ? `+${formatMetric(followersGrowth)} (30d)` : '真实快照'}
          tone={followersGrowth && followersGrowth > 0 ? 'positive' : 'neutral'}
        />
        <BigNumberCard
          title="Views (30d)"
          value={formatMetric(views)}
          delta={viewsTrend}
          tone={views ? 'positive' : 'neutral'}
        />
        <BigNumberCard
          title="Engagement"
          value={formatMetric(engagement)}
          delta={engagementRate ? `${engagementRate.toFixed(2)}%` : '—'}
          tone={engagement ? 'positive' : 'neutral'}
        />
        <BigNumberCard
          title="Posts (snapshot)"
          value={String(postsCount)}
          delta={postsCount > 0 ? `近 ${postsCount} 条` : '待同步'}
          tone={postsCount > 0 ? 'positive' : 'neutral'}
        />
      </div>
      
      <div className="da-summary-footer" style={{ marginTop: 16, fontSize: 13, color: 'var(--vkpi-color-text-muted)' }}>
        <p>最近成功同步: {prettyDate(lastSuccess)}</p>
        <p>快照数: {snapshots.length} (近 30 天)</p>
      </div>
    </div>
  );
}


// ═══════════════════════════════════════════════
// 完整模板 2: ContentTab (内容列表)
// ═══════════════════════════════════════════════

export function ContentTab({ posts = [] }: BaseTabProps) {
  if (posts.length === 0) {
    return <div className="vkpi-empty">暂无 post 数据。需要先抓取或开启平台抓取。</div>;
  }
  
  return (
    <div className="da-tab-content">
      <div className="da-content-grid">
        {posts.slice(0, 24).map((post, index) => {
          const thumbnail = rowString(post, ['thumbnail_url', 'image_url', 'cover_url']);
          const title = rowString(post, ['title', 'caption'], '(无标题)');
          const views = rowNumber(post, ['views', 'views_count', 'play_count']);
          const likes = rowNumber(post, ['likes', 'likes_count']);
          const comments = rowNumber(post, ['comments', 'comments_count']);
          const publishedAt = rowString(post, ['published_at', 'created_at']);
          
          return (
            <div key={stablePostKey(post, index)} className="da-post-card">
              {thumbnail ? (
                <img src={thumbnail} alt="" className="da-post-thumbnail" loading="lazy" />
              ) : (
                <div className="da-post-thumbnail da-post-thumbnail--placeholder">📷</div>
              )}
              <div className="da-post-meta">
                <div className="da-post-title" title={title}>{title.slice(0, 60)}</div>
                <div className="da-post-stats">
                  <span>👁 {formatMetric(views)}</span>
                  <span>👍 {formatMetric(likes)}</span>
                  <span>💬 {formatMetric(comments)}</span>
                </div>
                <div className="da-post-date">{prettyDate(publishedAt)}</div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}


// ═══════════════════════════════════════════════
// 骨架 3: EngagementTab
// TODO: 加 recharts 折线图,X 轴 snapshot_date,Y 轴 engagement_rate
// ═══════════════════════════════════════════════

export function EngagementTab({ snapshots = [] }: BaseTabProps) {
  const recentRate = rowNumber(snapshots[0], ['engagement_rate']);
  
  return (
    <div className="da-tab-engagement">
      <div className="da-detail-grid">
        <BigNumberCard
          title="Engagement Rate"
          value={recentRate ? `${recentRate.toFixed(2)}%` : '—'}
          delta="近 30 天"
          tone="neutral"
        />
      </div>
      <p className="da-muted-copy" style={{ marginTop: 16 }}>
        TODO: 折线图 (recharts) - X: snapshot_date, Y: engagement_rate
      </p>
      <details>
        <summary>原始数据 ({snapshots.length} 条 snapshot)</summary>
        <pre style={{ fontSize: 11, maxHeight: 300, overflow: 'auto' }}>
          {JSON.stringify(snapshots.slice(0, 5).map((s) => ({
            date: rowString(s, ['snapshot_date']),
            engagement_rate: rowNumber(s, ['engagement_rate']),
            engagement_total_30d: rowNumber(s, ['engagement_total_30d']),
          })), null, 2)}
        </pre>
      </details>
    </div>
  );
}


// ═══════════════════════════════════════════════
// 骨架 4: ViewsTab
// TODO: 折线图 - views_30d / reach_total_30d / impressions_total_30d
// ═══════════════════════════════════════════════

export function ViewsTab({ snapshots = [] }: BaseTabProps) {
  const latest = snapshots[0];
  const views30d = rowNumber(latest, ['views_30d']);
  const reach30d = rowNumber(latest, ['reach_total_30d']);
  const impressions30d = rowNumber(latest, ['impressions_total_30d']);
  
  return (
    <div className="da-tab-views">
      <div className="da-detail-grid">
        <BigNumberCard title="Views (30d)" value={formatMetric(views30d)} delta="—" tone="neutral" />
        <BigNumberCard title="Reach (30d)" value={formatMetric(reach30d)} delta="—" tone="neutral" />
        <BigNumberCard title="Impressions (30d)" value={formatMetric(impressions30d)} delta="—" tone="neutral" />
      </div>
      <p className="da-muted-copy" style={{ marginTop: 16 }}>
        TODO: 折线图 - X: snapshot_date, Y: views/reach/impressions
      </p>
    </div>
  );
}


// ═══════════════════════════════════════════════
// 骨架 5: AudienceTab (受众画像)
// TODO: 接 audience_graph API (R-Phase3 范围),先占位
// ═══════════════════════════════════════════════

export function AudienceTab({ account }: BaseTabProps) {
  return (
    <div className="da-tab-audience">
      <p className="da-muted-copy">
        TODO: 受众画像 (audience graph)
      </p>
      <ul style={{ fontSize: 13, color: 'var(--vkpi-color-text-muted)' }}>
        <li>地理分布 (国家 / 城市)</li>
        <li>年龄段</li>
        <li>性别比例</li>
        <li>设备类型</li>
        <li>活跃时段</li>
      </ul>
      <p className="da-muted-copy" style={{ fontSize: 12 }}>
        数据来源: vkpi_audience_estimate 表 (R-Phase3 接入 audience_graph API)
      </p>
    </div>
  );
}


// ═══════════════════════════════════════════════
// 骨架 6: PillarsTab (内容支柱)
// TODO: LLM 分类 (R-Phase3 范围) 把 posts 按 pillar 归类
// ═══════════════════════════════════════════════

export function PillarsTab({ posts = [] }: BaseTabProps) {
  return (
    <div className="da-tab-pillars">
      <p className="da-muted-copy">
        TODO: Content Pillar 自动归类 (R-Phase3 LLM Gateway)
      </p>
      <p style={{ fontSize: 13, color: 'var(--vkpi-color-text-muted)' }}>
        当前 posts: {posts.length} 条<br />
        计划: 用 LLM 给每条 post 打 pillar 标签 (educational / lifestyle / comedy / review / vlog / ...),
        然后按 pillar 聚合统计
      </p>
    </div>
  );
}


// ═══════════════════════════════════════════════
// 骨架 7: OrganicValueTab (organic value)
// ═══════════════════════════════════════════════

export function OrganicValueTab({ snapshots = [] }: BaseTabProps) {
  const latest = snapshots[0];
  const orgValueCents = rowNumber(latest, ['estimated_organic_value_cents']);
  const orgValueUSD = orgValueCents ? (orgValueCents / 100) : 0;
  
  return (
    <div className="da-tab-organic-value">
      <div className="da-detail-grid">
        <BigNumberCard
          title="Organic Value (USD)"
          value={`$${orgValueUSD.toFixed(2)}`}
          delta="估算"
          tone={orgValueUSD > 0 ? 'positive' : 'neutral'}
        />
      </div>
      <p className="da-muted-copy" style={{ marginTop: 16 }}>
        TODO: 折线图历史 organic value 走势 + 拆分公式说明
      </p>
    </div>
  );
}


// ═══════════════════════════════════════════════
// 骨架 8: PostsTab (post 详细列表 + 排序)
// ContentTab 是网格,PostsTab 是表格 + 排序
// TODO: 加排序 / 筛选
// ═══════════════════════════════════════════════

export function PostsTab({ posts = [] }: BaseTabProps) {
  if (posts.length === 0) {
    return <div className="vkpi-empty">暂无 post 数据</div>;
  }
  
  return (
    <div className="da-tab-posts">
      <table className="vkpi-table" style={{ fontSize: 13 }}>
        <thead>
          <tr>
            <th>标题</th>
            <th>发布时间</th>
            <th>Views</th>
            <th>Likes</th>
            <th>Comments</th>
            <th>Engagement</th>
          </tr>
        </thead>
        <tbody>
          {posts.slice(0, 50).map((post, index) => (
            <tr key={stablePostKey(post, index)}>
              <td>{rowString(post, ['title', 'caption'], '(无标题)').slice(0, 40)}</td>
              <td>{prettyDate(rowString(post, ['published_at']))}</td>
              <td>{formatMetric(rowNumber(post, ['views']))}</td>
              <td>{formatMetric(rowNumber(post, ['likes']))}</td>
              <td>{formatMetric(rowNumber(post, ['comments']))}</td>
              <td>{formatMetric(rowNumber(post, ['engagement_total']))}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}


// ═══════════════════════════════════════════════
// 骨架 9: CompareTab (账号对比)
// TODO: 选 1-2 个其他账号对比关键指标
// ═══════════════════════════════════════════════

export function CompareTab({ account, accounts = [] }: BaseTabProps) {
  const otherAccounts = accounts.filter((a) => a.id !== account.id).slice(0, 5);
  
  return (
    <div className="da-tab-compare">
      <p className="da-muted-copy">
        TODO: 账号对比 (followers / engagement / views 横向对比)
      </p>
      <p style={{ fontSize: 13, color: 'var(--vkpi-color-text-muted)' }}>
        当前账号: {rowString(account, ['account_name'])}<br />
        可对比账号: {otherAccounts.length} 个
      </p>
    </div>
  );
}
