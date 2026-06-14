import type { VkpiPageKey } from '../../components/vkpi/vkpiTypes';

export type Row = Record<string, unknown>;
export type DashboardSource = 'mock' | 'real' | 'partial';
export type DashboardScope = 'official' | 'kol' | 'no_kol' | 'combined';
export type TrendSegment = '曝光量' | '互动量' | '销售额';
export type IntelligenceTab = 'today' | 'platform' | 'market' | 'competitor';
type TrendTotals = { views: number; likes: number; comments: number; posts: number };

export interface Snapshot {
  source: DashboardSource;
  failedSections: string[];
  dashboard: Row;
  trendRows: Row[];
  kolSummary: Row;
  kolDistribution: Row;
  officialMatrix: Row;
  competitorDashboard: Row;
  brandSignals: Row[];
  recentContent: Row[];
  agentsStatus: Row;
  copilotBrief: Row;
  tasksStatus: Row;
  recommendationBacklog: Row;
  loadedAt?: string;
}

export interface KpiCard {
  icon: string;
  label: string;
  value: string;
  meta: string;
  tone: 'blue' | 'green' | 'purple' | 'orange' | 'pink' | 'slate';
  status: '真实' | '部分' | '待接入';
  pending?: boolean;
}

export interface RegionRow {
  label: string;
  code: string;
  count: number;
  share: number;
  lat?: number;
  lng?: number;
}

export interface PanelRow {
  label: string;
  value: string;
}

export interface PanelDrawer {
  title: string;
  sourceLabel: string;
  rows: PanelRow[];
  actionLabel: string;
  actionPage?: VkpiPageKey;
}

export const EMPTY_SNAPSHOT: Snapshot = {
  source: 'mock',
  failedSections: [],
  dashboard: {},
  trendRows: [],
  kolSummary: {},
  kolDistribution: {},
  officialMatrix: {},
  competitorDashboard: {},
  brandSignals: [],
  recentContent: [],
  agentsStatus: {},
  copilotBrief: {},
  tasksStatus: {},
  recommendationBacklog: {},
};

export const scopeOptions: Array<{ key: DashboardScope; label: string; detail: string }> = [
  { key: 'official', label: '官方 18 号', detail: '下方 KPI 仅看官方账号矩阵' },
  { key: 'kol', label: 'KOL', detail: '下方 KPI 仅看 KOL 口径' },
  { key: 'no_kol', label: '不含 KOL', detail: '下方 KPI 排除 KOL 口径' },
  { key: 'combined', label: '官方 + KOL', detail: '下方 KPI 使用合并口径' },
];

export const trendSegments: TrendSegment[] = ['曝光量', '互动量', '销售额'];
export const intelligenceTabs: Array<{ key: IntelligenceTab; label: string }> = [
  { key: 'today', label: '今日判断' },
  { key: 'platform', label: '平台建议' },
  { key: 'market', label: '市场雷达' },
  { key: 'competitor', label: '竞品预警' },
];

export const toneStyle: Record<KpiCard['tone'], { icon: string; text: string }> = {
  blue: { icon: 'rgba(27,108,255,.12)', text: '#1b6cff' },
  green: { icon: 'rgba(24,199,132,.13)', text: '#18c784' },
  purple: { icon: 'rgba(139,92,246,.13)', text: '#8b5cf6' },
  orange: { icon: 'rgba(255,159,46,.14)', text: '#ff9f2e' },
  pink: { icon: 'rgba(255,77,166,.13)', text: '#ff4da6' },
  slate: { icon: 'rgba(71,85,105,.12)', text: '#475569' },
};

export function localDateISO(date = new Date()): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

export function rows(value: unknown): Row[] {
  return Array.isArray(value) ? value.filter((item): item is Row => Boolean(item) && typeof item === 'object' && !Array.isArray(item)) : [];
}

export function record(value: unknown): Row {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {};
}

export function num(value: unknown): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  const parsed = Number(String(value ?? '').replace(/[$,%\s,]/g, ''));
  return Number.isFinite(parsed) ? parsed : 0;
}

export function compact(value: number): string {
  const abs = Math.abs(value);
  const trim = (input: string) => input.replace(/\.0+$/, '').replace(/(\.\d*[1-9])0+$/, '$1');
  if (abs >= 1_000_000_000) return `${trim((value / 1_000_000_000).toFixed(2))}B`;
  if (abs >= 1_000_000) return `${trim((value / 1_000_000).toFixed(2))}M`;
  if (abs >= 10_000) return `${trim((value / 1_000).toFixed(abs >= 100_000 ? 0 : 1))}K`;
  return Math.round(value).toLocaleString('en-US');
}

export function dateLabel(value?: string): string {
  if (!value) return '本次会话';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

export function officialPlatformRows(matrix: Row): Row[] {
  return rows(matrix.platforms);
}

export function officialAccountRows(matrix: Row): Row[] {
  return officialPlatformRows(matrix).flatMap((platform) => rows(platform.accounts));
}

function dashboardOfficialSummary(dashboard: Row): Row {
  const direct = record(dashboard.official_matrix_summary);
  if (Object.keys(direct).length) return direct;
  return record(dashboard.summary);
}

export function mergeDashboardOfficialSummary(matrix: Row, dashboard: Row): Row {
  const summary = dashboardOfficialSummary(dashboard);
  const accountCount = num(matrix.account_count) || num(summary.account_count || summary.official_account_count);
  const postCount = num(matrix.post_count) || num(summary.post_count || summary.official_post_count);
  const totalViews = num(matrix.total_views) || num(summary.total_views || summary.official_total_views);
  if (!accountCount && !postCount && !totalViews) return matrix;
  return {
    ...matrix,
    account_count: accountCount,
    post_count: postCount,
    total_views: totalViews,
  };
}

export function officialTotals(matrix: Row) {
  const platforms = officialPlatformRows(matrix);
  const accounts = officialAccountRows(matrix);
  const platformTotal = (key: string) => platforms.reduce((sum, row) => sum + num(row[key]), 0);
  const accountTotal = (key: string) => accounts.reduce((sum, row) => sum + num(row[key]), 0);
  return {
    views: num(matrix.total_views) || platformTotal('total_views'),
    posts: num(matrix.post_count) || platformTotal('total_posts'),
    likes: accountTotal('total_likes') || platformTotal('total_likes'),
    comments: accountTotal('total_comments') || platformTotal('total_comments'),
    viewsDelta: platformTotal('views_delta') || accountTotal('views_delta'),
    accountCount: num(matrix.account_count) || accounts.length,
  };
}

function metricValue(row: Row, segment: TrendSegment): number {
  if (segment === '销售额') return num(row.gmv_cents || row.sales_cents || row.revenue_cents) / 100;
  if (segment === '互动量') return num(row.likes) + num(row.comments);
  return num(row.views || row.total_views || row.play_count || row.impressions);
}

export function platformName(value: unknown): string {
  const key = String(value || '').trim().toLowerCase();
  const map: Record<string, string> = { instagram: 'Instagram', youtube: 'YouTube', tiktok: 'TikTok', facebook: 'Facebook', x: 'X' };
  return map[key] || (key ? key.charAt(0).toUpperCase() + key.slice(1) : '未知平台');
}

export function countryFlag(code?: string): string {
  const clean = String(code || '').trim().toUpperCase();
  if (!/^[A-Z]{2}$/.test(clean)) return '•';
  return Array.from(clean).map((letter) => String.fromCodePoint(127397 + letter.charCodeAt(0))).join('');
}

export function settledValue<T>(result: PromiseSettledResult<T>, fallback: T, failed: string[], label: string): T {
  if (result.status === 'fulfilled') return result.value;
  failed.push(label);
  return fallback;
}

export interface KpiAttributionInput {
  /** Confirmed Shopify GMV in USD (vkpi_sales_attributions.revenue_cents / 100). */
  attributedGmv?: number;
  /** (GMV - cost) / cost, may be negative; null when no cost basis. */
  avgRoi?: number | null;
  /** Backend metric_source.gmv: real source | 'awaiting_data' | 'not_connected'. */
  gmvSource?: string;
  /** Backend metric_source.roi. */
  roiSource?: string;
}

export function buildKpis(snapshot: Snapshot, scope: DashboardScope, attribution?: KpiAttributionInput | null): KpiCard[] {
  const official = officialTotals(snapshot.officialMatrix);
  const trendTotals = snapshot.trendRows.reduce<TrendTotals>((acc, row) => {
    acc.views += num(row.views || row.total_views || row.play_count || row.impressions);
    acc.likes += num(row.likes);
    acc.comments += num(row.comments);
    acc.posts += num(row.published_content);
    return acc;
  }, { views: 0, likes: 0, comments: 0, posts: 0 });
  const scopedPending = scope === 'kol' || scope === 'combined';
  const scopeSuffix = scope === 'no_kol' ? ' · 不含 KOL' : '';
  const views = official.views || trendTotals.views;
  const posts = official.posts || trendTotals.posts;
  const engagementBase = official.views || trendTotals.views;
  const interactions = official.likes + official.comments || trendTotals.likes + trendTotals.comments;
  const engagement = engagementBase ? (interactions / engagementBase) * 100 : 0;
  const pendingLabel = scope === 'kol' ? 'KOL 口径待接入' : '官方 + KOL 合并口径待接入';
  const maybePending = (card: KpiCard): KpiCard => scopedPending ? { ...card, value: '--', meta: pendingLabel, status: '待接入', pending: true } : card;

  // GMV / ROI: show real numbers only when the attribution ledger actually has
  // confirmed Shopify revenue. No payload (or zero) → honest 待接入.
  const gmv = Number(attribution?.attributedGmv || 0);
  const roi = attribution?.avgRoi;
  const gmvAwaiting = attribution?.gmvSource === 'awaiting_data';
  const roiAwaiting = attribution?.roiSource === 'awaiting_data';
  const gmvCard: KpiCard = gmv > 0
    ? { icon: '▣', label: 'GMV', value: `$${compact(gmv)}`, meta: 'Shopify 已确认归因', tone: 'green', status: '真实' }
    : { icon: '▣', label: 'GMV', value: '--', meta: gmvAwaiting ? '已连接 · 等待确认归因订单' : '待 Shopify 订单接入', tone: 'green', status: '待接入', pending: true };
  const roiCard: KpiCard = (gmv > 0 && roi !== null && roi !== undefined)
    ? { icon: '¥', label: '平均 ROI', value: `${Number(roi).toFixed(1)}x`, meta: '(GMV-成本)/成本', tone: 'pink', status: '真实' }
    : { icon: '¥', label: '平均 ROI', value: '--', meta: roiAwaiting ? '已连接 · 等待确认归因订单' : '待成本与订单接入', tone: 'pink', status: '待接入', pending: true };

  return [
    maybePending({ icon: '◉', label: '总曝光量', value: views ? compact(views) : '--', meta: views ? `官方矩阵${official.viewsDelta ? ` · +${compact(official.viewsDelta)}` : ''}${scopeSuffix}` : '等待真实 API', tone: 'blue', status: views ? '真实' : '待接入', pending: !views }),
    gmvCard,
    maybePending({ icon: '♚', label: '内容数', value: posts ? compact(posts) : '--', meta: posts ? `官方矩阵${scopeSuffix}` : '等待真实 API', tone: 'purple', status: posts ? '真实' : '待接入', pending: !posts }),
    maybePending({ icon: '▥', label: '内容互动率', value: engagementBase ? `${engagement.toFixed(2)}%` : '--', meta: engagementBase ? `likes/comments/views${scopeSuffix}` : '等待互动分母', tone: 'orange', status: engagementBase ? '真实' : '待接入', pending: !engagementBase }),
    { icon: '▤', label: '订单量', value: '--', meta: '待 Shopify 订单 webhook', tone: 'blue', status: '待接入', pending: true },
    roiCard,
  ];
}

export function buildTrend(rowsList: Row[], segment: TrendSegment) {
  const values = rowsList.map((row) => metricValue(row, segment));
  const labels = rowsList.map((row) => String(row.date || row.day || '').slice(5).replace('-', '/') || '-').slice(-6);
  if (!values.length || !values.some(Boolean)) {
    const emptyPath = 'M34 196 L126 196 L218 196 L310 196 L402 196 L500 196';
    return { path: emptyPath, areaPath: `${emptyPath} L500 222 L34 222 Z`, labels: labels.length ? labels : ['-', '-', '-', '-', '-', '-'], tipDate: '无数据', tipValue: '--', pointX: 500, pointY: 196 };
  }
  const max = Math.max(1, ...values);
  const points = values.map((value, index) => {
    const x = values.length === 1 ? 500 : 34 + ((500 - 34) * index) / (values.length - 1);
    const y = 196 - ((196 - 32) * value) / max;
    return { x: Math.round(x), y: Math.round(y) };
  });
  const path = `M${points.map((point) => `${point.x} ${point.y}`).join(' L')}`;
  const lastPoint = points[points.length - 1] || { x: 500, y: 196 };
  const latest = values[values.length - 1] || 0;
  return {
    path,
    areaPath: `${path} L500 222 L34 222 Z`,
    labels,
    tipDate: labels[labels.length - 1] || 'latest',
    tipValue: segment === '销售额' ? `$${compact(latest)}` : compact(latest),
    pointX: lastPoint.x,
    pointY: lastPoint.y,
  };
}

export function buildRegions(snapshot: Snapshot): RegionRow[] {
  const distribution = rows(snapshot.kolDistribution.countries).length ? rows(snapshot.kolDistribution.countries) : rows(snapshot.kolSummary.country_distribution);
  const total = distribution.reduce((sum, row) => sum + num(row.count || row.kol_count), 0);
  return distribution
    .map((row): RegionRow => {
      const count = num(row.count || row.kol_count);
      return {
        label: String(row.name || row.country_name || row.country_code || '未知国家'),
        code: String(row.code || row.country_code || '').toUpperCase(),
        count,
        share: total ? (count / total) * 100 : num(row.share),
        lat: num(row.lat) || undefined,
        lng: num(row.lng) || undefined,
      };
    })
    .filter((row) => row.code && row.count > 0)
    .sort((a, b) => b.count - a.count)
    .slice(0, 12);
}

export function buildPlatforms(snapshot: Snapshot) {
  const official = officialPlatformRows(snapshot.officialMatrix)
    .map((row) => ({ label: platformName(row.platform), value: num(row.total_views) || num(row.total_posts), meta: num(row.total_views) ? '曝光' : '内容' }))
    .filter((row) => row.value > 0);
  const source = official.length ? official : rows(snapshot.kolSummary.by_platform)
    .map((row) => ({ label: platformName(row.platform), value: num(row.n ?? row.count ?? row.total), meta: 'KOL' }))
    .filter((row) => row.value > 0);
  const max = Math.max(1, ...source.map((row) => row.value));
  return source.sort((a, b) => b.value - a.value).slice(0, 5).map((row) => ({
    ...row,
    width: `${Math.max(8, Math.round((row.value / max) * 100))}%`,
  }));
}

export function latestContent(snapshot: Snapshot): Row[] {
  const recent = rows(snapshot.recentContent);
  if (recent.length) return recent.slice(0, 8);
  return officialAccountRows(snapshot.officialMatrix).flatMap((account) => rows(account.posts).map((post): Row => ({
    ...post,
    platform: account.platform_label || account.platform,
    account_handle: account.handle || account.display_name,
  }))).slice(0, 8);
}

export function contentTitle(row: Row): string {
  return String(row.title || row.caption || row.text || row.summary || '未命名内容').replace(/https?:\/\/\S+/g, '').replace(/\s+/g, ' ').trim().slice(0, 86);
}

export function contentUrl(row: Row): string {
  return String(row.url || row.post_url || row.source_url || row.permalink || row.content_url || '').trim();
}
