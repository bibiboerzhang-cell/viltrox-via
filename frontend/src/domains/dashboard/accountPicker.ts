import type { KpiCard } from './dashboardModel';
import { compact } from './dashboardModel';

export type DashboardAccountType = 'all' | 'kol' | 'media' | 'company';

export interface DashboardAccountCounts {
  all?: number;
  kol?: number;
  media?: number;
  company?: number;
}

export interface DashboardAccountKpi {
  account_type: DashboardAccountType;
  selected_count?: number;
  active_roster: number;
  total_roster: number;
  active_30d: number;
  total_exposure: number;
  total_followers: number;
  engagement_rate: number;
  attributed_gmv: number;
  avg_roi: number | null;
  counts?: DashboardAccountCounts;
  metric_source?: Record<string, string>;
  totals?: {
    likes?: number;
    comments?: number;
    shares?: number;
    interactions?: number;
  };
}

export interface DashboardAccountRow {
  id: string;
  dashboard_id: string;
  source_id: number;
  source_table: string;
  handle: string;
  name: string;
  platform: string;
  country: string;
  tier: string;
  account_type: DashboardAccountType;
  followers: number;
  posts_count: number;
  total_views: number;
  total_likes: number;
  total_comments: number;
  total_shares: number;
  engagement_rate: number;
  profile_url: string;
  avatar_url: string;
  is_active: boolean;
  interactions: number;
}

export interface DashboardAccountList {
  account_type: DashboardAccountType;
  total: number;
  page: number;
  page_size: number;
  kols: DashboardAccountRow[];
  counts?: DashboardAccountCounts;
}

export const dashboardAccountTypeOptions: Array<{ key: DashboardAccountType; label: string; detail: string }> = [
  { key: 'all', label: 'All', detail: '公司账号 + KOL + Media 合并口径' },
  { key: 'kol', label: 'KOL', detail: '只看 KOL 候选池口径' },
  { key: 'media', label: 'Media', detail: '只看媒体/平台类账号口径' },
  { key: 'company', label: '公司账号', detail: '只看官方账号矩阵口径' },
];

function dashForZero(value: number): string {
  return value ? compact(value) : '--';
}

export function accountDisplayName(row: DashboardAccountRow): string {
  return row.name || row.handle || `Account ${row.source_id || row.id}`;
}

function money(value: number): string {
  if (!value) return '--';
  if (value >= 1000) return `$${compact(value)}`;
  return `$${Math.round(value).toLocaleString('en-US')}`;
}

function roi(value: number | null | undefined): string {
  return value ? `${Number(value).toFixed(1)}x` : '--';
}

function realOrEmpty(hasValue: boolean): Pick<KpiCard, 'status' | 'pending'> {
  return hasValue ? { status: '真实' } : { status: '待接入', pending: true };
}

export function buildDashboardAccountKpiCards(payload: DashboardAccountKpi | null): KpiCard[] {
  if (!payload) {
    return [
      { icon: '◉', label: 'Active Roster', value: '--', meta: '等待 Dashboard KOL API', tone: 'blue', status: '待接入', pending: true },
      { icon: '▣', label: 'Active 30D', value: '--', meta: '等待 Dashboard KOL API', tone: 'green', status: '待接入', pending: true },
      { icon: '▥', label: 'Total Exposure', value: '--', meta: '等待播放/曝光数据', tone: 'purple', status: '待接入', pending: true },
      { icon: '◇', label: 'Engagement R.', value: '--', meta: '等待互动数据', tone: 'orange', status: '待接入', pending: true },
      { icon: '$', label: 'Attributed GMV', value: '--', meta: '待 Shopify 接入', tone: 'green', status: '待接入', pending: true },
      { icon: '¥', label: 'Avg ROI', value: '--', meta: '待成本与订单接入', tone: 'pink', status: '待接入', pending: true },
    ];
  }

  const total = Number(payload.total_roster || 0);
  const active = Number(payload.active_roster || 0);
  const exposure = Number(payload.total_exposure || 0);
  const engagement = Number(payload.engagement_rate || 0);
  return [
    { icon: '◉', label: 'Active Roster', value: compact(active), meta: `${compact(total)} total · 有内容/播放`, tone: 'blue', status: '真实' },
    { icon: '▣', label: 'Active 30D', value: compact(payload.active_30d || 0), meta: '30 天内同步且有内容数据', tone: 'green', status: '真实' },
    { icon: '▥', label: 'Total Exposure', value: dashForZero(exposure), meta: exposure ? 'v_dashboard_account_pool.total_views' : '当前口径暂无播放数据', tone: 'purple', ...realOrEmpty(exposure > 0) },
    { icon: '◇', label: 'Engagement R.', value: engagement ? `${engagement.toFixed(2)}%` : '--', meta: 'likes/comments/shares ÷ views', tone: 'orange', ...realOrEmpty(engagement > 0) },
    { icon: '$', label: 'Attributed GMV', value: money(payload.attributed_gmv || 0), meta: '待 Shopify 归因接入', tone: 'green', status: '待接入', pending: true },
    { icon: '¥', label: 'Avg ROI', value: roi(payload.avg_roi), meta: '待成本与订单接入', tone: 'pink', status: '待接入', pending: true },
  ];
}
