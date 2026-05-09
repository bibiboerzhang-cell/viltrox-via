import type { KpiKey, Row } from './types';
import { accountId, accountName, postTitle, rowNumber, rowString } from './rowAccessors';
import { normalizePlatform, shortDate } from './platformHelpers';

/** 数字格式化 - 按 KPI 类型决定单位 */
export function formatMetric(value: number | null, key?: KpiKey): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  if (key?.includes('rate') || key?.includes('percent')) return `${value.toFixed(value >= 10 ? 0 : 1)}%`;
  if (key === 'organic_value') {
    return `$${new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(value)}`;
  }
  return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(value);
}

export function latestSnapshotValue(accounts: Row[], keyCandidates: string[]): number | null {
  const values = accounts.map((row) => rowNumber(row, keyCandidates)).filter((value): value is number => value !== null);
  if (!values.length) return null;
  return values.reduce((sum, value) => sum + value, 0);
}

export function postsForAccount(posts: Row[], account: Row): Row[] {
  const id = accountId(account);
  const handle = accountName(account).toLowerCase();
  return posts.filter((post) => {
    const postAccount = rowString(post, ['account_id', 'industry_account_id']);
    const postHandle = rowString(post, ['handle', 'account_handle', 'username', 'display_name']).toLowerCase().replace(/^@/, '');
    return postAccount === id || postHandle === handle;
  });
}

export function crossPlatformFor(crossPlatform: Row[], account: Row): Row | undefined {
  const platform = normalizePlatform(rowString(account, ['platform']));
  const id = accountId(account);
  const handle = accountName(account).toLowerCase();
  return crossPlatform.find((row) => {
    const rowPlatform = normalizePlatform(rowString(row, ['platform']));
    const rowAccount = rowString(row, ['account_id', 'id', 'profile_id']);
    const rowHandle = rowString(row, ['handle', 'display_name', 'name']).toLowerCase().replace(/^@/, '');
    return rowAccount === id || rowHandle === handle || rowPlatform === platform;
  });
}

/** 给某账号取某 KPI 的真实数值,数据来源优先级: snapshot → cross-platform → 帖子聚合 */
export function metricForAccount(account: Row, crossPlatform: Row[], posts: Row[], key: KpiKey): number | null {
  const platformSummary = crossPlatformFor(crossPlatform, account);
  const accountPosts = postsForAccount(posts, account);
  const postSum = (keys: string[]) => accountPosts.reduce((sum, post) => sum + (rowNumber(post, keys) || 0), 0);
  const postCount = accountPosts.length;
  const followers = rowNumber(account, ['followers', 'follower_count', 'subscribers', 'subscriber_count'])
    || rowNumber(platformSummary, ['followers', 'followers_total']);
  const views = rowNumber(platformSummary, ['views_30d', 'views', 'video_views', 'reels_views'])
    ?? postSum(['views', 'view_count', 'video_views']);
  const likes = rowNumber(platformSummary, ['likes', 'likes_30d']) ?? postSum(['likes', 'like_count']);
  const comments = rowNumber(platformSummary, ['comments', 'comments_30d']) ?? postSum(['comments', 'comment_count']);
  const shares = rowNumber(platformSummary, ['shares', 'shares_30d']) ?? postSum(['shares', 'share_count']);
  const saves = rowNumber(platformSummary, ['saves', 'saves_30d']) ?? postSum(['saves', 'save_count']);
  const engagement = rowNumber(platformSummary, ['engagement', 'engagement_total_30d']) ?? likes + comments + shares + saves;

  switch (key) {
    case 'followers': return followers;
    case 'followers_today': return rowNumber(account, ['followers_today']) ?? followers;
    case 'followers_growth': return rowNumber(platformSummary, ['followers_growth', 'followers_growth_30d']);
    case 'followers_growth_percent': return rowNumber(platformSummary, ['followers_growth_percent', 'followers_growth_pct']);
    case 'posts': return rowNumber(platformSummary, ['posts', 'post_count', 'posts_30d']) ?? postCount;
    case 'views': return views || null;
    case 'likes': return likes || null;
    case 'comments': return comments || null;
    case 'shares': return shares || null;
    case 'saves': return saves || null;
    case 'engagement': return engagement || null;
    case 'avg_engagement': return postCount && engagement ? engagement / postCount : rowNumber(platformSummary, ['avg_engagement']);
    case 'avg_engagement_per_day': return rowNumber(platformSummary, ['avg_engagement_per_day']) ?? (engagement ? engagement / 30 : null);
    case 'avg_posts_per_day': return rowNumber(platformSummary, ['avg_posts_per_day']) ?? (postCount ? postCount / 30 : null);
    case 'avg_eng_rate_followers': return rowNumber(platformSummary, ['avg_eng_rate_followers', 'engagement_rate_followers']) ?? (followers && engagement ? (engagement / followers) * 100 : null);
    case 'avg_eng_rate_views': return rowNumber(platformSummary, ['avg_eng_rate_views', 'engagement_rate_views']) ?? (views && engagement ? (engagement / views) * 100 : null);
    case 'reels_views': return rowNumber(platformSummary, ['reels_views']) ?? postSum(['reels_views']);
    case 'avg_eng_rate_impressions': return rowNumber(platformSummary, ['avg_eng_rate_impressions']);
    case 'avg_eng_rate_reach': return rowNumber(platformSummary, ['avg_eng_rate_reach']);
    case 'reach': return rowNumber(platformSummary, ['reach', 'reach_30d']);
    case 'posts_impressions': return rowNumber(platformSummary, ['posts_impressions', 'impressions', 'impressions_30d']);
    case 'organic_value': return rowNumber(platformSummary, ['organic_value']);
    default: return null;
  }
}

export function averageNumbers(values: Array<number | null>): number | null {
  const nums = values.filter((value): value is number => value !== null && Number.isFinite(value));
  if (!nums.length) return null;
  return nums.reduce((sum, value) => sum + value, 0) / nums.length;
}

export function extractHashtags(posts: Row[]): Array<{ label: string; value: number }> {
  const counts = new Map<string, number>();
  for (const post of posts) {
    const caption = postTitle(post);
    const tags = caption.match(/#[\p{L}\p{N}_-]+/gu) || [];
    for (const tag of tags) {
      const key = tag.replace('#', '').toLowerCase();
      counts.set(key, (counts.get(key) || 0) + 1);
    }
  }
  return Array.from(counts.entries())
    .map(([label, value]) => ({ label, value }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 8);
}

export function distributionByDate(posts: Row[]): Array<{ label: string; value: number; tooltip: string }> {
  const byDate = new Map<string, number>();
  for (const post of posts) {
    const time = rowString(post, ['published_at', 'posted_at', 'created_at']);
    if (!time) continue;
    const date = new Date(time);
    if (Number.isNaN(date.getTime())) continue;
    const key = date.toISOString().slice(0, 10);
    byDate.set(key, (byDate.get(key) || 0) + 1);
  }
  return Array.from(byDate.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .slice(-30)
    .map(([key, value]) => ({ label: shortDate(key), value, tooltip: `${shortDate(key)} · ${value} posts` }));
}

export function bestPosting(posts: Row[]): { day: string; hour: string } {
  if (!posts.length) return { day: '—', hour: '—' };
  const dayCount = new Map<number, number>();
  const hourCount = new Map<number, number>();
  for (const post of posts) {
    const time = rowString(post, ['published_at', 'posted_at', 'created_at']);
    if (!time) continue;
    const date = new Date(time);
    if (Number.isNaN(date.getTime())) continue;
    dayCount.set(date.getDay(), (dayCount.get(date.getDay()) || 0) + 1);
    hourCount.set(date.getHours(), (hourCount.get(date.getHours()) || 0) + 1);
  }
  const dayWinner = [...dayCount.entries()].sort((a, b) => b[1] - a[1])[0];
  const hourWinner = [...hourCount.entries()].sort((a, b) => b[1] - a[1])[0];
  const dayLabels = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
  return {
    day: dayWinner ? dayLabels[dayWinner[0]] : '—',
    hour: hourWinner ? `${hourWinner[0]}:00` : '—',
  };
}

export function contentPillars(posts: Row[]): Array<{ label: string; value: number }> {
  const counts = new Map<string, number>();
  for (const post of posts) {
    const pillar = rowString(post, ['content_pillar', 'pillar', 'category', 'topic'], '未分类');
    counts.set(pillar, (counts.get(pillar) || 0) + 1);
  }
  return Array.from(counts.entries())
    .map(([label, value]) => ({ label, value }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 6);
}

export function postTypes(posts: Row[]): { primary: string; total: number } {
  const counts = new Map<string, number>();
  for (const post of posts) {
    const type = rowString(post, ['post_type', 'media_type', 'type'], 'video');
    counts.set(type, (counts.get(type) || 0) + 1);
  }
  const winner = [...counts.entries()].sort((a, b) => b[1] - a[1])[0];
  return { primary: winner ? winner[0] : 'video', total: posts.length };
}
