// 共享类型定义 - 数据分析板块
// 给所有 data-analysis/ 子组件使用

export type Row = Record<string, unknown>;

export type KpiKey =
  | 'followers'
  | 'followers_today'
  | 'followers_growth'
  | 'followers_growth_percent'
  | 'engagement'
  | 'posts'
  | 'views'
  | 'organic_value'
  | 'avg_eng_rate_followers'
  | 'avg_eng_rate_views'
  | 'avg_engagement'
  | 'avg_engagement_per_day'
  | 'avg_posts_per_day'
  | 'comments'
  | 'likes'
  | 'reels_views'
  | 'avg_eng_rate_impressions'
  | 'avg_eng_rate_reach'
  | 'reach'
  | 'posts_impressions'
  | 'shares'
  | 'saves';

export type SecondaryTab = 'Home' | 'Benchmarks' | 'Posts' | 'Pillars' | 'Sentiment' | 'Topic Tracking';

export type BenchmarkTab =
  | 'Cross-platform'
  | 'Brands'
  | 'Facebook'
  | 'Instagram'
  | 'TikTok'
  | 'Twitter'
  | 'YouTube'
  | 'LinkedIn'
  | '小红书';

export interface KpiOption {
  key: KpiKey;
  label: string;
  group: 'count' | 'growth' | 'engagement' | 'rate' | 'reach' | 'value';
  platforms?: string[];
}

export const SECONDARY_TABS: readonly SecondaryTab[] = [
  'Home', 'Benchmarks', 'Posts', 'Pillars', 'Sentiment', 'Topic Tracking',
] as const;

export const BENCHMARK_TABS: readonly BenchmarkTab[] = [
  'Cross-platform', 'Brands', 'Facebook', 'Instagram', 'TikTok',
  'Twitter', 'YouTube', 'LinkedIn', '小红书',
] as const;

export const ACCOUNT_TABS = [
  '执行摘要', 'Content', 'Engagement', 'Views',
  'Audience', 'Content Pillars', 'Organic Value', 'Posts', 'Compare',
] as const;
