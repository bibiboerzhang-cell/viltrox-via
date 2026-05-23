import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  GlassFAB,
  GlassSidebar,
  GlassToast,
  GlassTopBar,
  HeroSection,
  glassVarStyle,
} from '../glass';
import { apiFetch } from '../../../services/http';
import { listKolPool, type VkpiKolPoolItem } from '../../../services/vkpi/kolPool-api';
import { getKolPoolCompetitorDashboard, getKolPoolSummary, getOfficialChannelMatrix, listBrandSignals } from '../../../services/vkpi.ui-api';
import type { VkpiPageKey } from '../vkpiTypes';
import { RealWorldMap, type CountryMapPoint } from '../glass-future/RealWorldMap';
import '../glass-future/tokens.css';
import '../glass-future/background.css';
import '../glass-future/components.css';
import '../glass-future/animations.css';
import '../glass-future/responsive.css';

export interface DashboardPremiumProps {
  apiToken?: string;
  userName?: string;
  userRole?: string;
  testId?: string;
  windowDays?: number;
  embedded?: boolean;
  onSelectPage?: (page: VkpiPageKey) => void;
}

let glassToastTimer: number | undefined;

type Row = Record<string, unknown>;
type PremiumSource = 'mock' | 'real' | 'partial';

interface PremiumKpi {
  icon: string;
  label: string;
  value: string;
  meta: string;
  trend: 'up' | 'down';
  ig: string;
  ic: string;
  sparkPath: string;
  isMock: boolean;
  mockLabel?: string;
}

interface PremiumKpiCardProps {
  item: PremiumKpi;
  expanded: boolean;
  windowDays: number;
  onToggle: () => void;
  onOpenDetail: () => void;
}

interface PremiumProductRow {
  rank: number;
  name: string;
  width: string;
  value: string;
  isMock: boolean;
  mockLabel?: string;
}

interface PremiumAlert {
  icon: string;
  title: string;
  body: string;
  time: string;
  bgc: string;
  col: string;
  isMock: boolean;
  url?: string;
  sourceLabel?: string;
  mockLabel?: string;
}

interface PremiumTask {
  title: string;
  priority: 'high' | 'mid' | 'low';
  priorityLabel: string;
  body: string;
  width: string;
  isMock: boolean;
  mockLabel?: string;
}

interface PremiumRegion {
  label: string;
  value: string;
  color: string;
  isMock: boolean;
  countryCode?: string;
  kolCount?: number;
  lat?: number;
  lng?: number;
  mockLabel?: string;
}

interface PremiumPlatform {
  icon: string;
  label: string;
  width: string;
  value: string;
  background: string;
  isMock: boolean;
  mockLabel?: string;
}

interface PremiumAgent {
  id: string;
  name: string;
  status: string;
  summary: string;
  lastOutput: string;
  isMock: boolean;
}

interface CountryDrawerState {
  region: PremiumRegion;
  items: VkpiKolPoolItem[];
  loading: boolean;
  error?: string;
}

interface PanelDrawerState {
  title: string;
  sourceLabel: string;
  rows: Array<{ label: string; value: string }>;
  actionLabel: string;
  actionPage: VkpiPageKey;
}

interface PremiumSnapshot {
  source: PremiumSource;
  failedSections: string[];
  dashboard: Row;
  trendRows: Row[];
  productRows: Row[];
  kolSummary: Row;
  kolDistribution: Row;
  officialMatrix: Row;
  competitorDashboard: Row;
  brandSignals: Row[];
  agentsStatus: Row;
  copilotBrief: Row;
  tasksStatus: Row;
  loadedAt?: string;
}

const mockKpis: PremiumKpi[] = [
  { icon: '◉', label: '总曝光量', value: '86.37M', meta: '较上周 ↑ 12.5%', trend: 'up', ig: 'rgba(27,108,255,.12)', ic: '#1b6cff', sparkPath: 'M2 18 C12 20 18 12 27 16 S44 20 53 12 70 9 81 15 98 22 118 12', isMock: true, mockLabel: '示例 KPI' },
  { icon: '▣', label: 'GMV', value: '$342.6K', meta: '较上周 ↑ 8.3%', trend: 'up', ig: 'rgba(24,199,132,.13)', ic: '#18c784', sparkPath: 'M2 18 C18 14 27 18 40 13 S60 11 74 16 94 20 118 12', isMock: true, mockLabel: '示例 KPI' },
  { icon: '♚', label: '内容数', value: '2,847', meta: '较上周 ↑ 3', trend: 'up', ig: 'rgba(139,92,246,.13)', ic: '#8b5cf6', sparkPath: 'M2 17 C15 19 22 14 35 18 S55 8 69 12 86 20 118 15', isMock: true, mockLabel: '示例 KPI' },
  { icon: '▥', label: '内容互动率', value: '3.24%', meta: '较上周 ↓ 0.4%', trend: 'down', ig: 'rgba(255,159,46,.14)', ic: '#ff9f2e', sparkPath: 'M2 12 C18 13 24 18 38 15 S58 9 70 17 92 20 118 13', isMock: true, mockLabel: '示例 KPI' },
  { icon: '▤', label: '订单量', value: '1,287', meta: '较上周 ↑ 6.7%', trend: 'up', ig: 'rgba(27,108,255,.11)', ic: '#1b6cff', sparkPath: 'M2 18 C17 13 24 17 34 15 S54 14 66 11 86 19 118 10', isMock: true, mockLabel: '示例 KPI' },
  { icon: '¥', label: '平均 ROI', value: '5.21x', meta: '较上周 ↑ 0.7x', trend: 'up', ig: 'rgba(255,77,166,.13)', ic: '#ff4da6', sparkPath: 'M2 12 C20 10 29 14 44 13 S65 19 76 17 96 12 118 9', isMock: true, mockLabel: '示例 KPI' },
];

const regionColors = ['#1b6cff', '#6aa6ff', '#18d5ff', '#8b5cf6', '#cfe0ff'];

const regions: PremiumRegion[] = [
  { label: 'United States', value: '34.4%', color: '#1b6cff', isMock: true, countryCode: 'US', kolCount: 336, lat: 39.8, lng: -98.5, mockLabel: '示例地区' },
  { label: 'United Kingdom', value: '13.0%', color: '#6aa6ff', isMock: true, countryCode: 'GB', kolCount: 127, lat: 54.0, lng: -2.0, mockLabel: '示例地区' },
  { label: 'Canada', value: '7.3%', color: '#18d5ff', isMock: true, countryCode: 'CA', kolCount: 71, lat: 56.1, lng: -106.3, mockLabel: '示例地区' },
  { label: 'Germany', value: '4.4%', color: '#8b5cf6', isMock: true, countryCode: 'DE', kolCount: 43, lat: 51.0, lng: 9.0, mockLabel: '示例地区' },
  { label: 'Italy', value: '4.1%', color: '#cfe0ff', isMock: true, countryCode: 'IT', kolCount: 40, lat: 42.8, lng: 12.8, mockLabel: '示例地区' },
];

const mockProductRows: PremiumProductRow[] = [
  { rank: 1, name: 'AF 56mm F1.2 Pro', width: '96%', value: '8.21x', isMock: true, mockLabel: '示例 ROI' },
  { rank: 2, name: 'AF 35mm F1.2 LAB', width: '82%', value: '6.74x', isMock: true, mockLabel: '示例 ROI' },
  { rank: 3, name: 'AF 135mm F1.8 LAB', width: '68%', value: '5.31x', isMock: true, mockLabel: '示例 ROI' },
  { rank: 4, name: 'AF 16mm F1.8', width: '52%', value: '4.22x', isMock: true, mockLabel: '示例 ROI' },
];

const contentTypes = [
  { label: '视频', value: '56.7%', color: '#1b6cff' },
  { label: '图集', value: '24.3%', color: '#18d5ff' },
  { label: '图文', value: '13.6%', color: '#8b5cf6' },
];

const mockAgents: PremiumAgent[] = [
  { id: 'recommendation', name: '推荐 Agent', status: 'idle', summary: '等待真实 API', lastOutput: '', isMock: true },
  { id: 'brief', name: '简报 Agent', status: 'idle', summary: '等待真实 API', lastOutput: '', isMock: true },
  { id: 'evidence', name: '证据 Agent', status: 'idle', summary: '等待真实 API', lastOutput: '', isMock: true },
  { id: 'sync_sentinel', name: '同步守护', status: 'idle', summary: '等待真实 API', lastOutput: '', isMock: true },
  { id: 'brain', name: '脑层验收', status: 'idle', summary: '等待真实 API', lastOutput: '', isMock: true },
  { id: 'kol_intel', name: 'KOL 智能', status: 'idle', summary: '等待真实 API', lastOutput: '', isMock: true },
  { id: 'market_intel', name: '市场情报', status: 'idle', summary: '等待真实 API', lastOutput: '', isMock: true },
];

const platformStyles: Record<string, { icon: string; label: string; background: string }> = {
  instagram: { icon: 'IG', label: 'Instagram', background: '#e1306c' },
  youtube: { icon: 'YT', label: 'YouTube', background: '#ff0000' },
  tiktok: { icon: 'TT', label: 'TikTok', background: '#111827' },
  facebook: { icon: 'FB', label: 'Facebook', background: '#1877f2' },
  x: { icon: 'X', label: 'X', background: '#111827' },
  media: { icon: 'MD', label: 'Media', background: '#1b6cff' },
};

const platforms: PremiumPlatform[] = [
  { icon: 'IG', label: 'Instagram', width: '100%', value: '56.57M', background: '#e1306c', isMock: true, mockLabel: '示例平台' },
  { icon: 'YT', label: 'YouTube', width: '35%', value: '19.80M', background: '#ff0000', isMock: true, mockLabel: '示例平台' },
  { icon: 'TT', label: 'TikTok', width: '11%', value: '6.21M', background: '#111827', isMock: true, mockLabel: '示例平台' },
  { icon: 'FB', label: 'Facebook', width: '5%', value: '2.41M', background: '#1877f2', isMock: true, mockLabel: '示例平台' },
];

const tasks: PremiumTask[] = [
  { title: 'DC-A1 Monitor 上市任务', priority: 'high', priorityLabel: '高', body: '52/60 KOL 已对接，剩余 8 个需本周确认。', width: '87%', isMock: true, mockLabel: '示例任务' },
  { title: '补寄任务（US 仓库）', priority: 'mid', priorityLabel: '中', body: '6 位 KOL 等待寄样，预计影响 56mm Pro 预热。', width: '0%', isMock: true, mockLabel: '示例任务' },
  { title: 'Cinegear 物料准备', priority: 'low', priorityLabel: '低', body: '剩余 4 天截止，目前进行中。', width: '55%', isMock: true, mockLabel: '示例任务' },
];

const quickActions: Array<{ icon: string; label: string; page: VkpiPageKey }> = [
  { icon: '⌁', label: '内容发布', page: 'channels' },
  { icon: '⌕', label: 'KOL 寻找', page: 'discover' },
  { icon: '▣', label: '舆情监控', page: 'dataQuality' },
  { icon: '◎', label: '竞品监控', page: 'dataQuality' },
  { icon: '▦', label: '产品管理', page: 'productBattle' },
  { icon: '▥', label: '数据报表', page: 'reports' },
];

const premiumNavTarget: Record<string, VkpiPageKey> = {
  Dashboard: 'dashboardPremium',
  Mission: 'command',
  KOL: 'channels',
  Campaign: 'projects',
  Product: 'productBattle',
  Market: 'dataAnalysis',
  Data: 'dataAnalysis',
  Settings: 'settings',
};

const EMPTY_PREMIUM_SNAPSHOT: PremiumSnapshot = {
  source: 'mock',
  failedSections: [],
  dashboard: {},
  trendRows: [],
  productRows: [],
  kolSummary: {},
  kolDistribution: {},
  officialMatrix: {},
  competitorDashboard: {},
  brandSignals: [],
  agentsStatus: {},
  copilotBrief: {},
  tasksStatus: {},
};

function localDateISO(date = new Date()): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function numberValue(value: unknown): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  const parsed = Number(String(value ?? '').replace(/[$,%\s,]/g, ''));
  return Number.isFinite(parsed) ? parsed : 0;
}

function objectValue(value: unknown): Row {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {};
}

function compact(value: number): string {
  const abs = Math.abs(value);
  const trim = (input: string) => input.replace(/\.0+$/, '').replace(/(\.\d*[1-9])0+$/, '$1');
  if (abs >= 1_000_000_000) return `${trim((value / 1_000_000_000).toFixed(abs >= 10_000_000_000 ? 1 : 2))}B`;
  if (abs >= 1_000_000) return `${trim((value / 1_000_000).toFixed(abs >= 100_000_000 ? 1 : 2))}M`;
  if (abs >= 100_000) return `${trim((value / 1_000).toFixed(0))}K`;
  if (abs >= 10_000) return `${trim((value / 1_000).toFixed(1))}K`;
  if (abs >= 1_000) return `${Math.round(value).toLocaleString('en-US')}`;
  return `${Math.round(value).toLocaleString('en-US')}`;
}

function metricMap(rows: Row[]): Map<string, Row> {
  const entries: Array<[string, Row]> = rows
    .map((row): [string, Row] => [String(row.metric_key || row.key || ''), row])
    .filter(([key]) => Boolean(key));
  return new Map(entries);
}

function metricNumber(metrics: Map<string, Row>, key: string): number {
  const row = metrics.get(key);
  return row ? numberValue(row.value_numeric ?? row.value ?? 0) : 0;
}

function badgeText(value?: string): string {
  if (!value) return '示例';
  if (value.includes('Shopify')) return '待接入';
  if (value.includes('待')) return '待数据';
  return '示例';
}

function rowsFrom(value: unknown): Row[] {
  if (Array.isArray(value)) return value as Row[];
  return [];
}

function pendingKpis(): PremiumKpi[] {
  return mockKpis.map((item) => ({
    ...item,
    value: '--',
    meta: item.label === 'GMV' || item.label === '订单量' || item.label === '平均 ROI' ? '待 Shopify 接入' : '等待真实 API',
    trend: 'up',
    isMock: true,
    mockLabel: item.label === 'GMV' || item.label === '订单量' || item.label === '平均 ROI' ? '待 Shopify 接入' : '待真实数据',
  }));
}

function officialPlatformRows(matrix: Row): Row[] {
  return rowsFrom(matrix.platforms);
}

function officialAccountRows(matrix: Row): Row[] {
  return officialPlatformRows(matrix).flatMap((platform) => rowsFrom(platform.accounts));
}

function officialTotals(matrix: Row) {
  const platforms = officialPlatformRows(matrix);
  const accounts = officialAccountRows(matrix);
  const platformTotal = (key: string) => platforms.reduce((sum, row) => sum + numberValue(row[key]), 0);
  const accountTotal = (key: string) => accounts.reduce((sum, row) => sum + numberValue(row[key]), 0);
  const views = numberValue(matrix.total_views) || platformTotal('total_views');
  const posts = numberValue(matrix.post_count) || platformTotal('total_posts');
  const followers = platformTotal('total_followers') || accountTotal('followers');
  const likes = accountTotal('total_likes');
  const comments = accountTotal('total_comments');
  const viewsDelta = platformTotal('views_delta') || accountTotal('views_delta');
  return {
    views,
    posts,
    followers,
    likes,
    comments,
    viewsDelta,
    accountCount: numberValue(matrix.account_count) || accounts.length,
  };
}

function latestTrendValue(rows: Row[], key: string): number {
  const last = rows.length ? rows[rows.length - 1] : {};
  return numberValue(last[key] ?? last.total_views ?? last.play_count ?? last.impressions);
}

function buildPremiumKpis(snapshot: PremiumSnapshot, allowMockFallback: boolean): PremiumKpi[] {
  if (snapshot.source === 'mock') return allowMockFallback ? mockKpis : pendingKpis();
  const metrics = metricMap(rowsFrom(snapshot.dashboard.metrics));
  const official = officialTotals(snapshot.officialMatrix);
  const trendTotals = snapshot.trendRows.reduce<{ views: number; likes: number; comments: number; published: number }>(
    (acc, row) => {
      acc.views += numberValue(row.views || row.total_views || row.play_count || row.impressions);
      acc.likes += numberValue(row.likes);
      acc.comments += numberValue(row.comments);
      acc.published += numberValue(row.published_content);
      return acc;
    },
    { views: 0, likes: 0, comments: 0, published: 0 },
  );
  const views = official.views || metricNumber(metrics, 'views') || trendTotals.views;
  const gmvCents = metricNumber(metrics, 'gmv');
  const orders = snapshot.trendRows.reduce((sum, row) => sum + numberValue(row.orders), 0);
  const productRois = snapshot.productRows
    .map((row) => {
      const sales = numberValue(row.sales_cents || row.revenue_cents || row.gmv_cents);
      const cost = numberValue(row.cost_cents);
      return numberValue(row.roi) || (cost ? sales / cost : 0);
    })
    .filter((value) => value > 0);
  const averageRoi = productRois.length ? productRois.reduce((sum, value) => sum + value, 0) / productRois.length : 0;
  const publishedContent = official.posts || metricNumber(metrics, 'published_content') || trendTotals.published;
  const engagementRate = official.views && (official.likes || official.comments)
    ? ((official.likes + official.comments) / official.views) * 100
    : trendTotals.views
      ? ((trendTotals.likes + trendTotals.comments) / trendTotals.views) * 100
      : 0;
  return [
    { ...mockKpis[0], value: views ? compact(views) : '--', meta: views ? `真实 API · 官方矩阵${official.viewsDelta ? ` · +${compact(official.viewsDelta)}` : ''}` : '等待真实 API', trend: 'up', isMock: !views, mockLabel: views ? undefined : '待真实数据' },
    { ...mockKpis[1], value: gmvCents ? `$${compact(gmvCents / 100)}` : '--', meta: gmvCents ? '真实 API · 归因销售' : '待 Shopify 接入', trend: 'up', isMock: !gmvCents, mockLabel: gmvCents ? undefined : '待 Shopify 接入' },
    { ...mockKpis[2], value: publishedContent ? compact(publishedContent) : '--', meta: publishedContent ? '真实 API · 官方矩阵' : '等待真实 API', trend: 'up', isMock: !publishedContent, mockLabel: publishedContent ? undefined : '待真实数据' },
    { ...mockKpis[3], value: `${engagementRate.toFixed(2)}%`, meta: '真实 API · likes/comments/views', trend: engagementRate ? 'up' : 'down', isMock: false, mockLabel: undefined },
    { ...mockKpis[4], value: orders ? compact(orders) : '--', meta: orders ? '真实 API · 归因订单' : '待 Shopify 接入', trend: 'up', isMock: !orders, mockLabel: orders ? undefined : '待 Shopify 接入' },
    { ...mockKpis[5], value: averageRoi ? `${averageRoi.toFixed(2)}x` : '--', meta: averageRoi ? '真实 API · 产品表现' : '待成本 / Shopify 接入', trend: 'up', isMock: !averageRoi, mockLabel: averageRoi ? undefined : '待成本 / Shopify 接入' },
  ];
}

function buildProductRows(rows: Row[], allowMockFallback: boolean): PremiumProductRow[] {
  const candidates = rows.slice(0, 4).map((row, index) => {
    const sales = numberValue(row.sales_cents || row.revenue_cents || row.gmv_cents);
    const cost = numberValue(row.cost_cents);
    const roi = numberValue(row.roi) || (cost ? sales / cost : 0);
    return {
      rank: index + 1,
      name: String(row.product_name || row.product_sku || row.project_name || `产品 ${index + 1}`),
      width: '0%',
      value: roi ? `${roi.toFixed(2)}x` : '待归因',
      roi,
      isMock: false,
    };
  }).filter((row) => row.roi > 0);
  if (!candidates.length) {
    return allowMockFallback
      ? mockProductRows
      : [{ rank: 1, name: '暂无真实 ROI 数据', width: '0%', value: '--', isMock: true, mockLabel: '待成本 / Shopify 接入' }];
  }
  const max = Math.max(1, ...candidates.map((row) => row.roi));
  return candidates.map(({ roi, ...row }) => ({
    ...row,
    width: `${Math.max(8, Math.round((roi / max) * 100))}%`,
  }));
}

function signalTitle(signal: Row): string {
  const brand = String(signal.brand_name || '').trim();
  const type = String(signal.signal_type || '').replace(/_/g, ' ');
  return brand ? `${brand} · ${type}` : type || '品牌信号';
}

function signalBody(signal: Row): string {
  const platform = String(signal.platform || '平台');
  const role = String(signal.brand_role || '').trim();
  const strength = String(signal.signal_strength || 'medium');
  const matched = String(signal.matched_text || '').trim();
  const context = String(signal.match_context || '').replace(/\s+/g, ' ').trim();
  const evidence = matched || context.slice(0, 72);
  return [platform, role || 'signal', strength, evidence].filter(Boolean).join(' · ');
}

function buildAlerts(signals: Row[]): PremiumAlert[] {
  if (!signals.length) return [];
  return signals.slice(0, 3).map((signal) => {
    const competitor = String(signal.brand_role || '') === 'competitor';
    return {
      icon: competitor ? '!' : '✓',
      title: signalTitle(signal),
      body: signalBody(signal),
      time: String(signal.detected_at || signal.published_at || '').slice(5, 10) || 'new',
      bgc: competitor ? '#fff7ed' : '#ecfdf3',
      col: competitor ? '#f79009' : '#12b76a',
      isMock: false,
      url: String(signal.source_url || signal.post_url || '').trim(),
      sourceLabel: String(signal.match_source || signal.source_table || 'vkpi_brand_signal').trim(),
    };
  });
}

function buildPremiumRegions(kolSummary: Row, kolDistribution: Row, allowMockFallback: boolean): PremiumRegion[] {
  const distribution = rowsFrom(kolDistribution.countries).length ? rowsFrom(kolDistribution.countries) : rowsFrom(kolSummary.country_distribution);
  const total = distribution.reduce((sum, row) => sum + numberValue(row.count || row.kol_count), 0);
  const regionRows = distribution
    .map((row, index): PremiumRegion => {
      const count = numberValue(row.count || row.kol_count);
      const share = numberValue(row.share) || (total ? (count / total) * 100 : 0);
      const countryCode = String(row.code || row.country_code || '').trim();
      return {
        label: String(row.name || row.country_name || countryCode || `国家 ${index + 1}`),
        value: share ? `${share.toFixed(1)}%` : compact(count),
        color: regionColors[index % regionColors.length],
        isMock: false,
        countryCode,
        kolCount: count,
        lat: numberValue(row.lat) || undefined,
        lng: numberValue(row.lng) || undefined,
      };
    })
    .filter((region) => Boolean(region.countryCode) && (region.kolCount || 0) > 0)
    .slice(0, 5);
  if (regionRows.length) return regionRows;
  return allowMockFallback
    ? regions
    : [{ label: '暂无国家分布', value: '--', color: '#cfe0ff', isMock: true, mockLabel: '待 KOL 国家数据' }];
}

function buildPremiumPlatforms(kolSummary: Row, officialMatrix: Row, allowMockFallback: boolean): PremiumPlatform[] {
  const officialRows = officialPlatformRows(officialMatrix)
    .map((row) => {
      const key = String(row.platform || '').trim().toLowerCase();
      const views = numberValue(row.total_views);
      const posts = numberValue(row.total_posts);
      return { key, views, posts };
    })
    .filter((row) => Boolean(row.key) && (row.views > 0 || row.posts > 0))
    .sort((a, b) => b.views - a.views || b.posts - a.posts)
    .slice(0, 5);
  if (officialRows.length) {
    const max = Math.max(1, ...officialRows.map((row) => row.views || row.posts));
    return officialRows.map((row): PremiumPlatform => {
      const style = platformStyles[row.key] || {
        icon: row.key.slice(0, 2).toUpperCase(),
        label: row.key.charAt(0).toUpperCase() + row.key.slice(1),
        background: '#1b6cff',
      };
      const value = row.views ? compact(row.views) : `${compact(row.posts)} 内容`;
      return {
        ...style,
        width: `${Math.max(8, Math.round(((row.views || row.posts) / max) * 100))}%`,
        value,
        isMock: false,
      };
    });
  }
  const rows = rowsFrom(kolSummary.by_platform)
    .map((row) => {
      const key = String(row.platform || '').trim().toLowerCase();
      const count = numberValue(row.n ?? row.count ?? row.total);
      return { key, count };
    })
    .filter((row) => Boolean(row.key) && row.count > 0)
    .sort((a, b) => b.count - a.count)
    .slice(0, 4);
  if (!rows.length) {
    return allowMockFallback
      ? platforms
      : [{ icon: '--', label: '暂无平台分布', width: '0%', value: '--', background: '#cfe0ff', isMock: true, mockLabel: '待真实数据' }];
  }
  const max = Math.max(1, ...rows.map((row) => row.count));
  return rows.map((row): PremiumPlatform => {
    const style = platformStyles[row.key] || {
      icon: row.key.slice(0, 2).toUpperCase(),
      label: row.key.charAt(0).toUpperCase() + row.key.slice(1),
      background: '#1b6cff',
    };
    return {
      ...style,
      width: `${Math.max(8, Math.round((row.count / max) * 100))}%`,
      value: `${compact(row.count)} KOL`,
      isMock: false,
    };
  });
}

function buildPremiumAgents(agentsStatus: Row, allowMockFallback: boolean): PremiumAgent[] {
  const rows = rowsFrom(agentsStatus.agents)
    .map((row): PremiumAgent => ({
      id: String(row.id || ''),
      name: String(row.name || row.id || 'Agent'),
      status: String(row.status || 'idle'),
      summary: String(row.summary || '暂无输出'),
      lastOutput: String(row.last_output || ''),
      isMock: false,
    }))
    .filter((agent) => Boolean(agent.id))
    .slice(0, 7);
  if (rows.length) return rows;
  return allowMockFallback ? mockAgents : [{ id: 'agents', name: 'Agents', status: 'idle', summary: '等待真实 API', lastOutput: '', isMock: true }];
}

function buildPremiumTasks(tasksStatus: Row): PremiumTask[] {
  return rowsFrom(tasksStatus.tasks)
    .map((row): PremiumTask => {
      const priority = String(row.priority || 'low') as PremiumTask['priority'];
      const confidence = Math.max(0, Math.min(1, numberValue(row.confidence)));
      return {
        title: String(row.title || '推荐任务'),
        priority: priority === 'high' || priority === 'mid' || priority === 'low' ? priority : 'low',
        priorityLabel: priority === 'high' ? '高' : priority === 'mid' ? '中' : '低',
        body: String(row.body || '等待人工复核'),
        width: `${Math.round(confidence * 100)}%`,
        isMock: false,
      };
    })
    .slice(0, 6);
}

function recentDateLabels(days = 6): string[] {
  const labels: string[] = [];
  const now = new Date();
  for (let index = days - 1; index >= 0; index -= 1) {
    const date = new Date(now);
    date.setDate(now.getDate() - index);
    labels.push(`${String(date.getMonth() + 1).padStart(2, '0')}/${String(date.getDate()).padStart(2, '0')}`);
  }
  return labels;
}

function trendChart(rows: Row[], allowMockFallback: boolean) {
  const fallbackPath = 'M34 196 C82 162 121 164 166 134 S235 102 285 101 363 76 411 51 458 25 500 32';
  if (!rows.length) {
    if (!allowMockFallback) {
      const emptyPath = 'M34 196 L126 196 L218 196 L310 196 L402 196 L500 196';
      return {
        path: emptyPath,
        areaPath: `${emptyPath} L500 222 L34 222 Z`,
        labels: recentDateLabels(),
        tipDate: '无数据',
        tipValue: '--',
        pointX: 500,
        pointY: 196,
      };
    }
    return {
      path: fallbackPath,
      areaPath: `${fallbackPath} L500 222 L34 222 Z`,
      labels: ['05/11', '05/12', '05/13', '05/14', '05/15', '05/16'],
      tipDate: '05/17',
      tipValue: '86.37M',
      pointX: 500,
      pointY: 32,
    };
  }
  const values = rows.map((row) => numberValue(row.views || row.total_views || row.play_count || row.impressions));
  const max = Math.max(1, ...values);
  const xMin = 34;
  const xMax = 500;
  const yMin = 32;
  const yMax = 196;
  const points = values.map((value, index) => {
    const x = values.length === 1 ? xMax : xMin + ((xMax - xMin) * index) / (values.length - 1);
    const y = yMax - ((yMax - yMin) * value) / max;
    return { x: Math.round(x), y: Math.round(y) };
  });
  const path = points.length ? `M${points.map((point) => `${point.x} ${point.y}`).join(' L')}` : fallbackPath;
  const lastPoint = points[points.length - 1] || { x: 500, y: 32 };
  return {
    path,
    areaPath: `${path} L500 222 L34 222 Z`,
    labels: rows.slice(0, 6).map((row) => String(row.date || row.day || '').slice(5).replace('-', '/') || '-'),
    tipDate: String(rows[rows.length - 1]?.date || rows[rows.length - 1]?.day || '').slice(5).replace('-', '/') || 'latest',
    tipValue: compact(latestTrendValue(rows, 'views')),
    pointX: lastPoint.x,
    pointY: lastPoint.y,
  };
}

function settledValue<T>(result: PromiseSettledResult<T>, fallback: T, failed: string[], label: string): T {
  if (result.status === 'fulfilled') return result.value;
  failed.push(label);
  return fallback;
}

function latestContentRows(officialMatrix: Row): Row[] {
  return officialAccountRows(officialMatrix)
    .flatMap((account) => rowsFrom(account.posts).map((post): Row => ({
      ...post,
      account_handle: account.handle,
      account_display_name: account.display_name,
      platform: account.platform_label || account.platform,
    })))
    .sort((a, b) => String(b.posted_at || b.published_at || '').localeCompare(String(a.posted_at || a.published_at || '')))
    .slice(0, 5);
}

function postedLabel(row: Row): string {
  const raw = String(row.posted_at || row.published_at || '');
  return raw ? raw.slice(0, 10) : '-';
}

function engagementLabel(row: Row): string {
  const views = numberValue(row.views);
  if (!views) return '--';
  const interactions = numberValue(row.likes) + numberValue(row.comments) + numberValue(row.shares);
  return `${((interactions / views) * 100).toFixed(2)}%`;
}

async function fetchPremiumSnapshot(apiToken: string, windowDays: number): Promise<PremiumSnapshot> {
  const failedSections: string[] = [];
  const [dashboardResult, trendResult, productResult, kolSummaryResult, kolDistributionResult, officialMatrixResult, competitorResult, brandSignalsResult, agentsStatusResult, copilotBriefResult, tasksStatusResult] = await Promise.allSettled([
    apiFetch<Row>(`/api/admin/vkpi/dashboard?window_days=${windowDays}`, {}, apiToken),
    apiFetch<{ rows?: Row[] }>(`/api/admin/vkpi/dashboard/revenue-trend?window_days=7`, {}, apiToken),
    apiFetch<{ rows?: Row[] }>(`/api/admin/vkpi/dashboard/product-performance?window_days=${windowDays}&limit=20`, {}, apiToken),
    getKolPoolSummary(apiToken),
    apiFetch<Row>('/api/admin/vkpi/dashboard/kol-distribution?limit=200', {}, apiToken),
    getOfficialChannelMatrix(apiToken, { limit: 20 }),
    getKolPoolCompetitorDashboard(apiToken),
    listBrandSignals(apiToken, { status: 'new', limit: 10 }),
    apiFetch<Row>('/api/admin/vkpi/dashboard/agents-status', {}, apiToken),
    apiFetch<Row>('/api/admin/vkpi/dashboard/copilot-brief', {}, apiToken),
    apiFetch<Row>('/api/admin/vkpi/dashboard/tasks?limit=6', {}, apiToken),
  ]);
  const dashboard = settledValue(dashboardResult, {}, failedSections, 'dashboard');
  const trend = settledValue(trendResult, { rows: [] }, failedSections, 'revenue-trend');
  const products = settledValue(productResult, { rows: [] }, failedSections, 'product-performance');
  const kolSummary = settledValue(kolSummaryResult, {}, failedSections, 'kol-pool-summary');
  const kolDistribution = settledValue(kolDistributionResult, {}, failedSections, 'kol-distribution');
  const officialMatrix = settledValue(officialMatrixResult, {}, failedSections, 'official-channel-matrix');
  const competitorDashboard = settledValue(competitorResult, {}, failedSections, 'competitors-dashboard');
  const brandSignals = settledValue(brandSignalsResult, { signals: [] }, failedSections, 'brand-signals');
  const agentsStatus = settledValue(agentsStatusResult, {}, failedSections, 'agents-status');
  const copilotBrief = settledValue(copilotBriefResult, {}, failedSections, 'copilot-brief');
  const tasksStatus = settledValue(tasksStatusResult, {}, failedSections, 'tasks');
  return {
    source: failedSections.length ? 'partial' : 'real',
    failedSections,
    dashboard,
    trendRows: rowsFrom(trend.rows),
    productRows: rowsFrom(products.rows),
    kolSummary,
    kolDistribution,
    officialMatrix,
    competitorDashboard,
    brandSignals: rowsFrom(brandSignals.signals),
    agentsStatus,
    copilotBrief,
    tasksStatus,
    loadedAt: new Date().toISOString(),
  };
}

function kpiStatusLabel(item: PremiumKpi): string {
  if (!item.isMock) return '真实 API';
  return item.mockLabel?.includes('Shopify') || item.mockLabel?.includes('待') ? '待接入' : '示例';
}

function PremiumKpiCard({ item, expanded, windowDays, onToggle, onOpenDetail }: PremiumKpiCardProps) {
  const statusLabel = kpiStatusLabel(item);
  return (
    <div
      className={`glass-card kpi${expanded ? ' is-expanded' : ''}${item.isMock ? ' is-pending' : ' is-real'}`}
      style={glassVarStyle({ '--ig': item.ig, '--ic': item.ic })}
      title={item.mockLabel}
      role="button"
      tabIndex={0}
      onClick={onToggle}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') onToggle();
      }}
    >
      <div className="topline"><div className="icon">{item.icon}</div><span className={`tag ${item.isMock ? '' : 'tag-real'}`}>{item.isMock ? badgeText(item.mockLabel) : '真实'}</span></div>
      <div className="label">{item.label}</div>
      <div className="value">{item.value}</div>
      <div className={`meta ${item.trend}`}>{item.meta}</div>
      <svg className="spark" viewBox="0 0 120 28"><path d={item.sparkPath} fill="none" stroke={item.ic} strokeWidth="3" strokeLinecap="round" /></svg>
      {expanded ? (
        <div className="kpi-detail">
          <div className="kpi-detail-head"><b>{statusLabel}</b><span>{item.isMock ? item.mockLabel || '示例数据' : '来源已接入'}</span></div>
          <div className="kpi-detail-grid">
            <span>当前窗口</span><b>近 {windowDays} 天</b>
            <span>数据来源</span><b>{item.isMock ? item.mockLabel || '示例数据' : item.meta}</b>
          </div>
          <button
            className="kpi-detail-link"
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              onOpenDetail();
            }}
          >
            {item.isMock ? '查看接入状态' : '查看完整归因'}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function PremiumKpiSkeletons() {
  return (
    <>
      {Array.from({ length: 6 }).map((_, index) => (
        <div className="glass-card kpi vkpi-premium-kpi-skeleton" key={index} aria-hidden="true">
          <div className="topline">
            <span className="vkpi-skeleton vkpi-skeleton-avatar" />
            <span className="vkpi-skeleton vkpi-skeleton-pill" />
          </div>
          <span className="vkpi-skeleton vkpi-skeleton-line is-medium" />
          <span className="vkpi-skeleton vkpi-skeleton-line is-short" />
          <span className="vkpi-skeleton vkpi-skeleton-line is-long" />
        </div>
      ))}
    </>
  );
}

function PremiumDashboardSkeleton() {
  return (
    <div className="content-grid vkpi-premium-loading-skeleton" aria-hidden="true">
      <div>
        <div className="left-grid">
          <div className="glass-card panel">
            <div className="panel-head">
              <span className="vkpi-skeleton vkpi-skeleton-line is-medium" />
              <span className="vkpi-skeleton vkpi-skeleton-pill" />
            </div>
            <div className="vkpi-premium-skeleton-map">
              <span className="vkpi-skeleton vkpi-skeleton-line is-long" />
              <span className="vkpi-skeleton vkpi-skeleton-line is-medium" />
            </div>
            <div className="vkpi-premium-skeleton-regions">
              {Array.from({ length: 5 }).map((_, index) => <span className="vkpi-skeleton vkpi-skeleton-pill" key={index} />)}
            </div>
          </div>
          <div className="glass-card panel">
            <div className="panel-head">
              <span className="vkpi-skeleton vkpi-skeleton-line is-medium" />
              <span className="vkpi-skeleton vkpi-skeleton-pill" />
            </div>
            <div className="vkpi-premium-skeleton-chart">
              <span />
              <span />
              <span />
              <span />
            </div>
          </div>
          <div className="lower">
            {Array.from({ length: 3 }).map((_, index) => (
              <div className="glass-card mini vkpi-premium-mini-skeleton" key={index}>
                <span className="vkpi-skeleton vkpi-skeleton-line is-medium" />
                <span className="vkpi-skeleton vkpi-skeleton-line is-long" />
                <span className="vkpi-skeleton vkpi-skeleton-line is-long" />
                <span className="vkpi-skeleton vkpi-skeleton-line is-short" />
              </div>
            ))}
          </div>
        </div>
        <div className="glass-card latest">
          <div className="panel-head">
            <span className="vkpi-skeleton vkpi-skeleton-line is-medium" />
            <span className="vkpi-skeleton vkpi-skeleton-pill" />
          </div>
          <div className="vkpi-premium-table-skeleton">
            {Array.from({ length: 5 }).map((_, index) => (
              <div key={index}>
                <span className="vkpi-skeleton vkpi-skeleton-avatar" />
                <span className="vkpi-skeleton vkpi-skeleton-line is-long" />
                <span className="vkpi-skeleton vkpi-skeleton-line is-medium" />
                <span className="vkpi-skeleton vkpi-skeleton-pill" />
              </div>
            ))}
          </div>
        </div>
      </div>
      <aside className="rail">
        {Array.from({ length: 4 }).map((_, index) => (
          <div className="glass-card rail-card vkpi-premium-rail-skeleton" key={index}>
            <span className="vkpi-skeleton vkpi-skeleton-line is-medium" />
            <span className="vkpi-skeleton vkpi-skeleton-line is-long" />
            <span className="vkpi-skeleton vkpi-skeleton-line is-long" />
          </div>
        ))}
      </aside>
    </div>
  );
}

export function DashboardPremium({ apiToken, userName = 'Jianbo', userRole = 'Marketing Director', testId = 'vkpi-dashboard-premium', windowDays = 30, embedded = false, onSelectPage }: DashboardPremiumProps) {
  const [toast, setToast] = useState('已触发');
  const [toastVisible, setToastVisible] = useState(false);
  const [activeNav, setActiveNav] = useState('Dashboard');
  const [activeSegment, setActiveSegment] = useState('曝光量');
  const [snapshot, setSnapshot] = useState<PremiumSnapshot>(EMPTY_PREMIUM_SNAPSHOT);
  const [loadingData, setLoadingData] = useState(false);
  const [countryDrawer, setCountryDrawer] = useState<CountryDrawerState | null>(null);
  const [panelDrawer, setPanelDrawer] = useState<PanelDrawerState | null>(null);
  const [expandedKpi, setExpandedKpi] = useState<string | null>(null);

  useEffect(() => {
    if (!apiToken) {
      setSnapshot(EMPTY_PREMIUM_SNAPSHOT);
      setLoadingData(false);
      return undefined;
    }
    let cancelled = false;
    setLoadingData(true);
    fetchPremiumSnapshot(apiToken, windowDays)
      .then((nextSnapshot) => {
        if (!cancelled) setSnapshot(nextSnapshot);
      })
      .catch(() => {
        if (!cancelled) {
          setSnapshot({ ...EMPTY_PREMIUM_SNAPSHOT, source: 'partial', failedSections: ['premium-dashboard'] });
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingData(false);
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken, windowDays]);

  const allowMockFallback = !embedded;
  const premiumKpis = useMemo(() => buildPremiumKpis(snapshot, allowMockFallback), [allowMockFallback, snapshot]);
  const premiumProductRows = useMemo(() => buildProductRows(snapshot.productRows, allowMockFallback), [allowMockFallback, snapshot.productRows]);
  const premiumAlerts = useMemo(() => buildAlerts(snapshot.brandSignals), [snapshot.brandSignals]);
  const premiumTrend = useMemo(() => trendChart(snapshot.trendRows, allowMockFallback), [allowMockFallback, snapshot.trendRows]);
  const premiumRegions = useMemo(() => buildPremiumRegions(snapshot.kolSummary, snapshot.kolDistribution, allowMockFallback), [allowMockFallback, snapshot.kolDistribution, snapshot.kolSummary]);
  const premiumMapPoints = useMemo<CountryMapPoint[]>(
    () =>
      premiumRegions
        .filter((region) => Boolean(region.countryCode) && typeof region.lat === 'number' && typeof region.lng === 'number')
        .map((region) => ({
          code: String(region.countryCode || '').toUpperCase(),
          name: region.label,
          count: Number(region.kolCount || 0),
          lat: Number(region.lat),
          lng: Number(region.lng),
          color: region.color,
        })),
    [premiumRegions],
  );
  const premiumPlatforms = useMemo(() => buildPremiumPlatforms(snapshot.kolSummary, snapshot.officialMatrix, allowMockFallback), [allowMockFallback, snapshot.kolSummary, snapshot.officialMatrix]);
  const premiumAgents = useMemo(() => buildPremiumAgents(snapshot.agentsStatus, allowMockFallback), [allowMockFallback, snapshot.agentsStatus]);
  const premiumTasks = useMemo(() => buildPremiumTasks(snapshot.tasksStatus), [snapshot.tasksStatus]);
  const contentRows = useMemo(() => latestContentRows(snapshot.officialMatrix), [snapshot.officialMatrix]);
  const official = useMemo(() => officialTotals(snapshot.officialMatrix), [snapshot.officialMatrix]);
  const contentTypeRows = allowMockFallback
    ? contentTypes
    : official.posts
      ? [{ label: '未分类', value: compact(official.posts), color: '#1b6cff' }]
      : [{ label: '暂无', value: '--', color: '#cfe0ff' }];
  const competitorTiers = objectValue(snapshot.competitorDashboard.tier_counts);
  const riskCount = numberValue(competitorTiers.avoid) + numberValue(competitorTiers.caution);
  const kolTotal = numberValue(snapshot.kolSummary.total || snapshot.kolSummary.candidate_asset_count);
  const activeAgentCount = premiumAgents.filter((agent) => agent.status === 'active').length;
  const copilotSummary = objectValue(snapshot.copilotBrief.summary);
  const copilotIsReal = Boolean(snapshot.copilotBrief.is_real);
  const copilotHeadline = String(copilotSummary.headline || snapshot.copilotBrief.headline || '行动卡');
  const copilotBody = copilotIsReal
    ? `Brief Agent · ${String(snapshot.copilotBrief.last_output || 'latest')}`
    : '推荐、风险、任务已汇总。';
  const copilotInsight = copilotIsReal
    ? `真实 brief · ${numberValue(copilotSummary.brief_item_count)} 条情报 · ${numberValue(copilotSummary.next_action_count)} 个动作`
    : '示例 · 待接 LLM';
  const heroMissions = useMemo(() => [
    { value: snapshot.source === 'mock' && allowMockFallback ? '7' : compact(official.accountCount), suffix: snapshot.source === 'mock' && allowMockFallback ? 'actions' : 'accounts', label: snapshot.source === 'mock' && allowMockFallback ? '今日关键动作' : '官方账号' },
    { value: snapshot.source === 'mock' && allowMockFallback ? '3' : compact(official.posts), suffix: snapshot.source === 'mock' && allowMockFallback ? 'risks' : 'contents', label: snapshot.source === 'mock' && allowMockFallback ? '项目 / 竞品风险' : '已抓取内容' },
    { value: snapshot.source === 'mock' && allowMockFallback ? '12' : compact(kolTotal), suffix: 'KOL', label: snapshot.source === 'mock' && allowMockFallback ? '新候选待评估' : 'KOL 池总量' },
  ], [allowMockFallback, kolTotal, official.accountCount, official.posts, snapshot.source]);
  const syncLabel = loadingData
    ? '加载真实 API…'
    : snapshot.source === 'mock'
      ? allowMockFallback ? '示例 · 待接入真实状态' : '等待真实 API'
      : snapshot.failedSections.length
        ? `部分真实 · ${snapshot.failedSections.length} 项失败`
        : '真实 API 已接入';

  const showToast = useCallback((message: string) => {
    setToast(message);
    setToastVisible(true);
    window.clearTimeout(glassToastTimer);
    glassToastTimer = window.setTimeout(() => setToastVisible(false), 1600);
  }, []);

  const openCountryDrawer = useCallback((region: PremiumRegion) => {
    if (region.isMock || !region.countryCode) {
      showToast('地区分布 · 示例数据');
      return;
    }
    if (!apiToken) {
      showToast('登录后查看国家 KOL 列表');
      return;
    }
    setCountryDrawer({ region, items: [], loading: true });
    listKolPool(apiToken, { country: region.countryCode, limit: 20 })
      .then((response) => setCountryDrawer({ region, items: response.items || [], loading: false }))
      .catch(() => setCountryDrawer({ region, items: [], loading: false, error: '国家 KOL 列表加载失败' }));
  }, [apiToken, showToast]);

  const handleNavSelect = (key: string) => {
    setActiveNav(key);
    const target = premiumNavTarget[key];
    if (target) {
      if (target !== 'dashboardPremium' && onSelectPage) {
        onSelectPage(target);
      }
      return;
    }
    showToast(`${key} · 暂无可用页面`);
  };

  const handleSegmentSelect = (key: string) => {
    setActiveSegment(key);
    showToast(`切换：${key}`);
  };

  const goToWorkspacePage = useCallback((page: VkpiPageKey, fallbackLabel: string) => {
    if (onSelectPage) {
      onSelectPage(page);
      return;
    }
    showToast(`${fallbackLabel} · 可接真实路由`);
  }, [onSelectPage, showToast]);

  const handleKpiDetail = useCallback((item: PremiumKpi) => {
    if (item.isMock) {
      showToast(item.mockLabel || '该指标等待真实数据接入');
      return;
    }
    goToWorkspacePage('dataAnalysis', item.label);
  }, [goToWorkspacePage, showToast]);

  const openTrendDrawer = useCallback(() => {
    setPanelDrawer({
      title: '曝光趋势',
      sourceLabel: snapshot.source === 'mock' ? '示例趋势' : 'revenue-trend API',
      rows: [
        { label: '当前指标', value: activeSegment },
        { label: '最近日期', value: premiumTrend.tipDate },
        { label: '最新值', value: premiumTrend.tipValue },
        { label: '样本点', value: `${premiumTrend.labels.filter(Boolean).length} 天` },
      ],
      actionLabel: '查看完整趋势',
      actionPage: 'dataAnalysis',
    });
  }, [activeSegment, premiumTrend.labels, premiumTrend.tipDate, premiumTrend.tipValue, snapshot.source]);

  const openProductDrawer = useCallback(() => {
    setPanelDrawer({
      title: '产品 ROI 排行',
      sourceLabel: premiumProductRows.some((row) => !row.isMock) ? 'product-performance API' : '待成本 / Shopify',
      rows: premiumProductRows.map((row) => ({
        label: `#${row.rank} ${row.name}`,
        value: row.value,
      })),
      actionLabel: '进入产品作战',
      actionPage: 'productBattle',
    });
  }, [premiumProductRows]);

  const openContentTypeDrawer = useCallback(() => {
    setPanelDrawer({
      title: '内容类型分布',
      sourceLabel: allowMockFallback ? '示例分布' : 'official matrix',
      rows: contentTypeRows.map((row) => ({ label: row.label, value: row.value })),
      actionLabel: '进入内容中心',
      actionPage: 'channels',
    });
  }, [allowMockFallback, contentTypeRows]);

  const openPlatformDrawer = useCallback(() => {
    setPanelDrawer({
      title: 'KOL 平台分布',
      sourceLabel: premiumPlatforms.some((platform) => !platform.isMock) ? 'kol-pool summary / official matrix' : '待真实数据',
      rows: premiumPlatforms.map((platform) => ({
        label: platform.label,
        value: platform.value,
      })),
      actionLabel: '进入账号矩阵',
      actionPage: 'channels',
    });
  }, [premiumPlatforms]);

  const openContentUrl = useCallback((url: unknown) => {
    const href = typeof url === 'string' ? url.trim() : '';
    if (!href) {
      showToast('暂无内容链接');
      return;
    }
    window.open(href, '_blank', 'noopener,noreferrer');
  }, [showToast]);

  const dashboardContent = (
    <>
      {!embedded ? (
        <GlassTopBar
            actions={[
              { label: localDateISO(), onClick: () => showToast('今日日期 · 本地时区') },
              { label: syncLabel, variant: 'sync', onClick: () => showToast(snapshot.source === 'mock' ? '同步状态 · 开发占位' : `数据源：${snapshot.source}`) },
              { label: '导出', onClick: () => goToWorkspacePage('reports', '报表导出') },
              { label: '生成周报', variant: 'primary', onClick: () => goToWorkspacePage('reports', '生成周报') },
            ]}
        />
      ) : null}
	          <HeroSection missions={heroMissions} />
	          <section className="kpis">
	            {loadingData && snapshot.source === 'mock' && !snapshot.loadedAt
	              ? <PremiumKpiSkeletons />
	              : premiumKpis.map((item) => (
	                <PremiumKpiCard
	                  key={item.label}
	                  item={item}
	                  expanded={expandedKpi === item.label}
	                  windowDays={windowDays}
	                  onToggle={() => setExpandedKpi((current) => current === item.label ? null : item.label)}
	                  onOpenDetail={() => handleKpiDetail(item)}
	                />
	              ))}
	          </section>
	          {loadingData && snapshot.source === 'mock' && !snapshot.loadedAt ? (
	            <PremiumDashboardSkeleton />
	          ) : (
	          <div className="content-grid">
            <div>
              <div className="left-grid">
                <div className="glass-card panel">
                  <div className="panel-head"><h3>全球 KOL 分布</h3><button className="link" type="button" onClick={() => goToWorkspacePage('dataAnalysis', 'Country Map')}>Country Map</button></div>
                  <div className="holo-map">
                    <div className="zoom"><span>+</span><span>−</span></div>
                    <RealWorldMap points={premiumMapPoints} onCountryClick={(point) => {
                      const region = premiumRegions.find((item) => String(item.countryCode || '').toUpperCase() === point.code);
                      if (region) openCountryDrawer(region);
                    }} />
                  </div>
                  <div className="region-list">
                    {premiumRegions.map((region) => <div className="region" style={glassVarStyle({ '--c': region.color })} key={region.label} role="button" tabIndex={0} title={region.mockLabel || `${region.kolCount || 0} KOL`} onClick={() => openCountryDrawer(region)} onKeyDown={(event) => { if (event.key === 'Enter') openCountryDrawer(region); }}><span><i></i>{region.label}{region.isMock ? <em>{badgeText(region.mockLabel)}</em> : null}</span><b>{region.value}</b></div>)}
                  </div>
                </div>
                <div className="glass-card panel">
                  <div className="panel-head"><h3>曝光趋势（近 7 天）</h3><div className="segment">{['曝光量', '互动量', '销售额'].map((segment) => <button className={activeSegment === segment ? 'active' : ''} data-seg={segment} onClick={() => handleSegmentSelect(segment)} type="button" key={segment}>{segment}</button>)}</div></div>
                  <div className="linechart" role="button" tabIndex={0} onClick={openTrendDrawer} onKeyDown={(event) => { if (event.key === 'Enter') openTrendDrawer(); }}><svg viewBox="0 0 520 250" preserveAspectRatio="none"><defs><linearGradient id="area" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#1b6cff" /><stop offset="1" stopColor="#1b6cff" stopOpacity="0" /></linearGradient></defs><g stroke="rgba(92,130,190,.16)"><line x1="26" y1="30" x2="500" y2="30" /><line x1="26" y1="80" x2="500" y2="80" /><line x1="26" y1="130" x2="500" y2="130" /><line x1="26" y1="180" x2="500" y2="180" /></g><path d={premiumTrend.path} fill="none" stroke="#1b6cff" strokeWidth="4" strokeLinecap="round" /><path d={premiumTrend.areaPath} fill="url(#area)" opacity=".25" /><circle cx={premiumTrend.pointX} cy={premiumTrend.pointY} r="8" fill="#1b6cff" stroke="#fff" strokeWidth="4" /><text x="34" y="238" fontSize="12" fill="#667085">{premiumTrend.labels[0] || '-'}</text><text x="116" y="238" fontSize="12" fill="#667085">{premiumTrend.labels[1] || '-'}</text><text x="198" y="238" fontSize="12" fill="#667085">{premiumTrend.labels[2] || '-'}</text><text x="280" y="238" fontSize="12" fill="#667085">{premiumTrend.labels[3] || '-'}</text><text x="362" y="238" fontSize="12" fill="#667085">{premiumTrend.labels[4] || '-'}</text><text x="444" y="238" fontSize="12" fill="#667085">{premiumTrend.labels[5] || '-'}</text></svg><div className="float-tip">{premiumTrend.tipDate}<b>{premiumTrend.tipValue}</b></div></div>
                </div>
                <div className="lower">
                  <div className="glass-card mini">
                    <div className="panel-head"><h3>产品 ROI 排行</h3><button className="link" type="button" onClick={() => goToWorkspacePage('productBattle', '产品 ROI')}>查看全部</button></div>
                    {premiumProductRows.map((row) => <div className="row" title={row.mockLabel} key={row.rank} role="button" tabIndex={0} onClick={openProductDrawer} onKeyDown={(event) => { if (event.key === 'Enter') openProductDrawer(); }}><span className="rank">{row.rank}</span><div><b>{row.name}{row.isMock ? <span className="tag">{badgeText(row.mockLabel)}</span> : null}</b><div className="bar"><span style={glassVarStyle({ '--w': row.width })}></span></div></div><small>{row.value}</small></div>)}
                  </div>
                  <div className="glass-card mini">
                    <div className="panel-head"><h3>内容类型分布</h3><span className="tag">{allowMockFallback ? '示例' : '官方矩阵'}</span></div>
                    <div className="donut-wrap" role="button" tabIndex={0} onClick={openContentTypeDrawer} onKeyDown={(event) => { if (event.key === 'Enter') openContentTypeDrawer(); }}><div className="donut"></div><div className="donut-label"><span>总内容</span><b>{official.posts ? compact(official.posts) : '--'}</b></div></div>
                    <div className="region-list" style={{ gridTemplateColumns: 'repeat(3,1fr)' }}>{contentTypeRows.map((item) => <div className="region" style={glassVarStyle({ '--c': item.color })} key={item.label} role="button" tabIndex={0} onClick={openContentTypeDrawer} onKeyDown={(event) => { if (event.key === 'Enter') openContentTypeDrawer(); }}><span><i></i>{item.label}</span><b>{item.value}</b></div>)}</div>
                  </div>
                  <div className="glass-card mini">
                    <div className="panel-head"><h3>KOL 平台分布</h3><button className="link" type="button" onClick={() => goToWorkspacePage('channels', 'KOL 平台分布')}>查看全部</button></div>
                    {premiumPlatforms.map((platform) => <div className="platform" title={platform.mockLabel} key={platform.label} role="button" tabIndex={0} onClick={openPlatformDrawer} onKeyDown={(event) => { if (event.key === 'Enter') openPlatformDrawer(); }}><span className="picon" style={{ background: platform.background }}>{platform.icon}</span><div><b>{platform.label}{platform.isMock ? <span className="tag">{badgeText(platform.mockLabel)}</span> : null}</b><div className="bar"><span style={glassVarStyle({ '--w': platform.width })}></span></div></div><small>{platform.value}</small></div>)}
                  </div>
                </div>
              </div>
              <div className="glass-card latest"><div className="panel-head"><h3>最新内容表现</h3><button className="link" type="button" onClick={() => goToWorkspacePage('channels', '内容中心')}>进入内容中心</button></div><table className="table"><thead><tr><th>内容</th><th>账号 / 平台</th><th>发布平台</th><th>发布于</th><th>曝光量</th><th>互动率</th><th>操作</th></tr></thead><tbody>{contentRows.length ? contentRows.map((row, index) => <tr key={`${row.id || row.url || index}`}><td><div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}><div className="thumb"></div><div><b>{String(row.title || '官方内容')}</b><br /><span className="tag">真实</span></div></div></td><td>@{String(row.account_handle || row.account_display_name || '-')}<br /><span style={{ color: '#667085' }}>{String(row.platform || '-')}</span></td><td>{String(row.platform || '-')}</td><td>{postedLabel(row)}</td><td><b>{compact(numberValue(row.views))}</b></td><td>{engagementLabel(row)}</td><td><button type="button" title="查看账号矩阵" onClick={() => goToWorkspacePage('channels', '内容中心')}>⌁</button> <button type="button" title="打开原始内容" onClick={() => openContentUrl(row.url)}>↗</button> <button type="button" title="查看数据分析" onClick={() => goToWorkspacePage('dataAnalysis', '内容分析')}>…</button></td></tr>) : <tr><td colSpan={7}><div className="empty-real">暂无真实最新内容明细</div></td></tr>}</tbody></table></div>
            </div>
            <aside className="rail">
              <div className="glass-card rail-card copilot"><div className="ai-kicker">V-KPI Copilot</div><h3>{copilotHeadline}</h3><p>{copilotBody}</p><div className="insight">{copilotInsight}</div></div>
              <div className="glass-card rail-card agents-room">
                <div className="panel-head"><h3>Agents 战情室</h3><span className="tag">{activeAgentCount} / {premiumAgents.length} 在线</span></div>
                <div className="agents-list">
                  {premiumAgents.map((agent) => (
                    <div className="agent-row" key={agent.id} title={agent.lastOutput || agent.summary}>
                      <span className={`agent-dot ${agent.status}`}></span>
                      <div><b>{agent.name}{agent.isMock ? <span className="tag">示例</span> : null}</b><p>{agent.summary}</p></div>
                    </div>
                  ))}
                </div>
                <button className="link agents-link" type="button" onClick={() => showToast('Agent Inbox 下一小块接入')}>Agent Inbox</button>
              </div>
              <div className="glass-card rail-card">
                <div className="panel-head">
                  <h3>重要提醒</h3>
                  <span className="tag">Brand Signal</span>
                  <button className="link" type="button" onClick={() => goToWorkspacePage('dataQuality', '重要提醒')}>查看全部</button>
                </div>
                {premiumAlerts.length ? premiumAlerts.map((alert) => (
                  <div className="alert" title={alert.sourceLabel} key={`${alert.title}-${alert.time}`}>
                    <div className="alert-ic" style={glassVarStyle({ '--bgc': alert.bgc, '--col': alert.col })}>{alert.icon}</div>
                    <div>
                      <b>{alert.title}<span className="tag">真实</span></b>
                      <p>{alert.body}</p>
                    </div>
                    <span className="time">{alert.time}</span>
                    {alert.url ? <button className="link" type="button" title="打开原始内容" onClick={() => openContentUrl(alert.url)}>↗</button> : null}
                  </div>
                )) : <div className="empty-real">暂无真实品牌信号</div>}
              </div>
              <div className="glass-card rail-card"><div className="panel-head"><h3>本周关键任务</h3><button className="link" type="button" onClick={() => goToWorkspacePage('projects', '本周关键任务')}>查看全部</button></div>{premiumTasks.length ? premiumTasks.map((task) => <div className="task" title={task.mockLabel} key={task.title}><div className="task-head"><b>{task.title}{task.isMock ? <span className="tag">示例</span> : null}</b><span className={`priority ${task.priority}`}>{task.priorityLabel}</span></div><p>{task.body}</p><div className="progress"><span style={glassVarStyle({ '--w': task.width })}></span></div></div>) : <div className="empty-real">{snapshot.tasksStatus.is_real ? '暂无真实推荐任务' : '等待真实任务 API'}</div>}</div>
              <div className="glass-card rail-card"><div className="panel-head"><h3>快捷入口</h3></div><div className="quick">{quickActions.map((action) => <button key={action.label} type="button" onClick={() => goToWorkspacePage(action.page, action.label)}><b>{action.icon}</b><span>{action.label}</span></button>)}</div></div>
            </aside>
	          </div>
	          )}
	    </>
	  );

  return (
    <div className={`vkpi-glass-shell${embedded ? ' vkpi-glass-shell--embedded' : ''}`} data-testid={testId}>
      {embedded ? (
        <main className="main">{dashboardContent}</main>
      ) : (
        <>
          <div className="browser"><div className="traffic"><span className="t-dot red"></span><span className="t-dot yellow"></span><span className="t-dot green"></span></div><div className="browser-title">viltroxtest.com · V-KPI Glass Intelligence</div><div className="browser-icons">◉ ⇧</div></div>
          <div className="app">
            <GlassSidebar activeKey={activeNav} onSelectNav={handleNavSelect} profileInitial={userName.slice(0, 1).toUpperCase()} profileName={userName} profileRole={userRole} />
            <main className="main">{dashboardContent}</main>
          </div>
        </>
      )}
      {countryDrawer ? (
        <div className="country-drawer" role="dialog" aria-label={`${countryDrawer.region.label} KOL`}>
          <div className="country-drawer-head">
            <div><span>国家 KOL</span><b>{countryDrawer.region.label}</b></div>
            <button type="button" onClick={() => setCountryDrawer(null)}>×</button>
          </div>
          <div className="country-drawer-meta">
            <span>{countryDrawer.region.countryCode || '-'}</span>
            <span>{compact(countryDrawer.region.kolCount || countryDrawer.items.length)} KOL</span>
          </div>
          {countryDrawer.loading ? <div className="country-drawer-empty">加载中…</div> : null}
          {!countryDrawer.loading && countryDrawer.error ? <div className="country-drawer-empty">{countryDrawer.error}</div> : null}
          {!countryDrawer.loading && !countryDrawer.error && countryDrawer.items.length === 0 ? <div className="country-drawer-empty">暂无 KOL</div> : null}
          {!countryDrawer.loading && !countryDrawer.error ? (
            <div className="country-drawer-list">
              {countryDrawer.items.slice(0, 12).map((item) => (
                <div className="country-kol" key={item.id}>
                  <b>{item.display_name || item.handle || `KOL ${item.id}`}</b>
                  <span>{item.platform || '-'} · {compact(numberValue(item.followers))} followers</span>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
      {panelDrawer ? (
        <div className="panel-drawer" role="dialog" aria-label={panelDrawer.title}>
          <div className="country-drawer-head">
            <div><span>{panelDrawer.sourceLabel}</span><b>{panelDrawer.title}</b></div>
            <button type="button" onClick={() => setPanelDrawer(null)}>×</button>
          </div>
          <div className="panel-drawer-list">
            {panelDrawer.rows.map((row) => (
              <div className="panel-drawer-row" key={row.label}>
                <span>{row.label}</span>
                <b>{row.value}</b>
              </div>
            ))}
          </div>
          <button className="panel-drawer-action" type="button" onClick={() => goToWorkspacePage(panelDrawer.actionPage, panelDrawer.title)}>
            {panelDrawer.actionLabel}
          </button>
        </div>
      ) : null}
      {embedded ? null : <GlassFAB onClick={() => goToWorkspacePage('dataQuality', '反馈 / 报错')} />}
      <GlassToast show={toastVisible}>{toast}</GlassToast>
    </div>
  );
}

export default DashboardPremium;
