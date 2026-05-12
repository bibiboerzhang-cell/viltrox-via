import type { ChartKey, ChartOption, KpiKey, KpiOption } from './types';

export const KPI_OPTIONS: KpiOption[] = [
  { key: 'followers', label: 'Followers', group: 'count' },
  { key: 'followers_today', label: 'Followers Today', group: 'count' },
  { key: 'followers_growth', label: 'Followers Growth', group: 'growth' },
  { key: 'followers_growth_percent', label: 'Followers Growth Percent', group: 'growth' },
  { key: 'engagement', label: 'Engagement', group: 'engagement' },
  { key: 'posts', label: 'Posts', group: 'count' },
  { key: 'views', label: 'Views', group: 'reach' },
  { key: 'organic_value', label: 'Organic Value', group: 'value' },
  { key: 'avg_eng_rate_followers', label: 'Avg. Eng. Rate by Followers', group: 'rate' },
  { key: 'avg_eng_rate_views', label: 'Avg. Eng. Rate by Views', group: 'rate' },
  { key: 'avg_engagement', label: 'Avg. Engagement', group: 'engagement' },
  { key: 'avg_engagement_per_day', label: 'Avg. Engagement / Day', group: 'engagement' },
  { key: 'avg_posts_per_day', label: 'Avg. Posts / Day', group: 'count' },
  { key: 'comments', label: 'Comments', group: 'engagement' },
  { key: 'likes', label: 'Likes', group: 'engagement' },
  { key: 'reels_views', label: 'Reels Views', group: 'reach', platforms: ['instagram'] },
  { key: 'avg_eng_rate_impressions', label: 'Avg. Eng. Rate by Impressions', group: 'rate' },
  { key: 'avg_eng_rate_reach', label: 'Avg. Eng. Rate by Reach', group: 'rate' },
  { key: 'reach', label: 'Reach', group: 'reach' },
  { key: 'posts_impressions', label: 'Posts Impressions', group: 'reach' },
  { key: 'shares', label: 'Shares', group: 'engagement' },
  { key: 'saves', label: 'Saves', group: 'engagement' },
];

export const DEFAULT_KPIS: KpiKey[] = [
  'followers', 'posts', 'views', 'engagement', 'avg_eng_rate_followers',
];

export const KPI_GROUP_LABELS: Record<KpiOption['group'], string> = {
  count: '数量',
  growth: '增长',
  engagement: '互动',
  rate: '比率',
  reach: '触达',
  value: '价值',
};

export const CHART_OPTIONS: ChartOption[] = [
  { key: 'top_profiles', label: 'Top Performing Profiles', group: 'summary' },
  { key: 'top_posts', label: 'Top Posts', group: 'content' },
  { key: 'posts_distribution', label: 'Posts Distribution', group: 'content' },
  { key: 'posting_signals', label: 'Posting Signals', group: 'engagement' },
];

export const DEFAULT_CHARTS: ChartKey[] = [
  'top_profiles', 'top_posts', 'posts_distribution', 'posting_signals',
];

export const CHART_GROUP_LABELS: Record<ChartOption['group'], string> = {
  content: 'Content',
  engagement: 'Engagement',
  views: 'Views',
  audience: 'Audience',
  summary: 'Executive Summary',
};
