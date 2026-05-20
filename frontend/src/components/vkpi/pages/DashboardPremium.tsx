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
import { getKolPoolCompetitorDashboard, getKolPoolSummary, listBrandSignals } from '../../../services/vkpi.ui-api';
import type { VkpiPageKey } from '../vkpiTypes';
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

interface CountryDrawerState {
  region: PremiumRegion;
  items: VkpiKolPoolItem[];
  loading: boolean;
  error?: string;
}

interface PremiumSnapshot {
  source: PremiumSource;
  failedSections: string[];
  dashboard: Row;
  trendRows: Row[];
  productRows: Row[];
  kolSummary: Row;
  competitorDashboard: Row;
  brandSignals: Row[];
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

const mapPointPositions = [
  { x: 170, y: 106, r: 8 },
  { x: 332, y: 123, r: 7 },
  { x: 486, y: 166, r: 6 },
  { x: 250, y: 165, r: 6 },
  { x: 424, y: 92, r: 5 },
];

const regions: PremiumRegion[] = [
  { label: '北美', value: '67.2%', color: '#1b6cff', isMock: true, mockLabel: '示例地区' },
  { label: '欧洲', value: '21.8%', color: '#6aa6ff', isMock: true, mockLabel: '示例地区' },
  { label: '亚太', value: '8.6%', color: '#18d5ff', isMock: true, mockLabel: '示例地区' },
  { label: '南美', value: '1.6%', color: '#8b5cf6', isMock: true, mockLabel: '示例地区' },
  { label: '其他', value: '0.8%', color: '#cfe0ff', isMock: true, mockLabel: '示例地区' },
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

const mockAlerts: PremiumAlert[] = [
  { icon: '!', title: 'Sigma 35mm F1.4 EX 发布', body: '竞品发布导致相关流量下降 8%', time: '2h', bgc: '#fff1f0', col: '#f04438', isMock: true, mockLabel: '示例提醒' },
  { icon: '!', title: 'Z50II AF 问题讨论增加', body: '新增 18 条负面舆情', time: '5h', bgc: '#fff7ed', col: '#f79009', isMock: true, mockLabel: '示例提醒' },
  { icon: '✓', title: '35mm F1.2 LAB 互动创新高', body: '互动率较上周增长 23%', time: '1d', bgc: '#ecfdf3', col: '#12b76a', isMock: true, mockLabel: '示例提醒' },
];

const tasks: PremiumTask[] = [
  { title: 'DC-A1 Monitor 上市任务', priority: 'high', priorityLabel: '高', body: '52/60 KOL 已对接，剩余 8 个需本周确认。', width: '87%', isMock: true, mockLabel: '示例任务' },
  { title: '补寄任务（US 仓库）', priority: 'mid', priorityLabel: '中', body: '6 位 KOL 等待寄样，预计影响 56mm Pro 预热。', width: '0%', isMock: true, mockLabel: '示例任务' },
  { title: 'Cinegear 物料准备', priority: 'low', priorityLabel: '低', body: '剩余 4 天截止，目前进行中。', width: '55%', isMock: true, mockLabel: '示例任务' },
];

const quickActions = [
  { icon: '⌁', label: '内容发布' },
  { icon: '⌕', label: 'KOL 寻找' },
  { icon: '▣', label: '舆情监控' },
  { icon: '◎', label: '竞品监控' },
  { icon: '▦', label: '产品管理' },
  { icon: '▥', label: '数据报表' },
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
  competitorDashboard: {},
  brandSignals: [],
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
  if (abs >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`;
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
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

function rowsFrom(value: unknown): Row[] {
  if (Array.isArray(value)) return value as Row[];
  return [];
}

function latestTrendValue(rows: Row[], key: string): number {
  const last = rows.length ? rows[rows.length - 1] : {};
  return numberValue(last[key] ?? last.total_views ?? last.play_count ?? last.impressions);
}

function buildPremiumKpis(snapshot: PremiumSnapshot): PremiumKpi[] {
  if (snapshot.source === 'mock') return mockKpis;
  const metrics = metricMap(rowsFrom(snapshot.dashboard.metrics));
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
  const views = metricNumber(metrics, 'views') || trendTotals.views;
  const publishedContent = metricNumber(metrics, 'published_content') || trendTotals.published;
  const engagementRate = trendTotals.views ? ((trendTotals.likes + trendTotals.comments) / trendTotals.views) * 100 : 0;
  return [
    { ...mockKpis[0], value: compact(views), meta: `真实 API · 近 ${snapshot.dashboard.window_days || 30} 天`, trend: 'up', isMock: false, mockLabel: undefined },
    { ...mockKpis[1], meta: 'Mock · 等 Shopify', isMock: true, mockLabel: 'Mock · 等 Shopify' },
    { ...mockKpis[2], value: compact(publishedContent), meta: '真实 API · 内容表', trend: 'up', isMock: false, mockLabel: undefined },
    { ...mockKpis[3], value: `${engagementRate.toFixed(2)}%`, meta: '真实 API · likes/comments/views', trend: engagementRate ? 'up' : 'down', isMock: false, mockLabel: undefined },
    { ...mockKpis[4], meta: 'Mock · 等 Shopify', isMock: true, mockLabel: 'Mock · 等 Shopify' },
    { ...mockKpis[5], meta: 'Mock · 等 Shopify', isMock: true, mockLabel: 'Mock · 等 Shopify' },
  ];
}

function buildProductRows(rows: Row[]): PremiumProductRow[] {
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
  if (!candidates.length) return mockProductRows;
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
  return `${platform} · ${role || 'signal'} · ${strength}`;
}

function buildAlerts(signals: Row[]): PremiumAlert[] {
  if (!signals.length) return mockAlerts;
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
    };
  });
}

function buildPremiumRegions(kolSummary: Row): PremiumRegion[] {
  const distribution = rowsFrom(kolSummary.country_distribution);
  const total = distribution.reduce((sum, row) => sum + numberValue(row.kol_count), 0);
  const regionRows = distribution
    .map((row, index): PremiumRegion => {
      const count = numberValue(row.kol_count);
      const share = numberValue(row.share) || (total ? (count / total) * 100 : 0);
      const countryCode = String(row.country_code || '').trim();
      return {
        label: String(row.country_name || countryCode || `国家 ${index + 1}`),
        value: share ? `${share.toFixed(1)}%` : compact(count),
        color: regionColors[index % regionColors.length],
        isMock: false,
        countryCode,
        kolCount: count,
      };
    })
    .filter((region) => Boolean(region.countryCode) && (region.kolCount || 0) > 0)
    .slice(0, 5);
  return regionRows.length ? regionRows : regions;
}

function buildPremiumPlatforms(kolSummary: Row): PremiumPlatform[] {
  const rows = rowsFrom(kolSummary.by_platform)
    .map((row) => {
      const key = String(row.platform || '').trim().toLowerCase();
      const count = numberValue(row.n ?? row.count ?? row.total);
      return { key, count };
    })
    .filter((row) => Boolean(row.key) && row.count > 0)
    .sort((a, b) => b.count - a.count)
    .slice(0, 4);
  if (!rows.length) return platforms;
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

function trendChart(rows: Row[]) {
  const fallbackPath = 'M34 196 C82 162 121 164 166 134 S235 102 285 101 363 76 411 51 458 25 500 32';
  if (!rows.length) {
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

async function fetchPremiumSnapshot(apiToken: string, windowDays: number): Promise<PremiumSnapshot> {
  const failedSections: string[] = [];
  const [dashboardResult, trendResult, productResult, kolSummaryResult, competitorResult, brandSignalsResult] = await Promise.allSettled([
    apiFetch<Row>(`/api/admin/vkpi/dashboard?window_days=${windowDays}`, {}, apiToken),
    apiFetch<{ rows?: Row[] }>(`/api/admin/vkpi/dashboard/revenue-trend?window_days=7`, {}, apiToken),
    apiFetch<{ rows?: Row[] }>(`/api/admin/vkpi/dashboard/product-performance?window_days=${windowDays}&limit=20`, {}, apiToken),
    getKolPoolSummary(apiToken),
    getKolPoolCompetitorDashboard(apiToken),
    listBrandSignals(apiToken, { status: 'new', limit: 10 }),
  ]);
  const dashboard = settledValue(dashboardResult, {}, failedSections, 'dashboard');
  const trend = settledValue(trendResult, { rows: [] }, failedSections, 'revenue-trend');
  const products = settledValue(productResult, { rows: [] }, failedSections, 'product-performance');
  const kolSummary = settledValue(kolSummaryResult, {}, failedSections, 'kol-pool-summary');
  const competitorDashboard = settledValue(competitorResult, {}, failedSections, 'competitors-dashboard');
  const brandSignals = settledValue(brandSignalsResult, { signals: [] }, failedSections, 'brand-signals');
  return {
    source: failedSections.length ? 'partial' : 'real',
    failedSections,
    dashboard,
    trendRows: rowsFrom(trend.rows),
    productRows: rowsFrom(products.rows),
    kolSummary,
    competitorDashboard,
    brandSignals: rowsFrom(brandSignals.signals),
    loadedAt: new Date().toISOString(),
  };
}

function PremiumKpiCard({ item }: { item: PremiumKpi }) {
  return (
    <div className="glass-card kpi" style={glassVarStyle({ '--ig': item.ig, '--ic': item.ic })} title={item.mockLabel}>
      <div className="topline"><div className="icon">{item.icon}</div>{item.isMock ? <span className="tag">示例</span> : null}</div>
      <div className="label">{item.label}</div>
      <div className="value">{item.value}</div>
      <div className={`meta ${item.trend}`}>{item.meta}</div>
      <svg className="spark" viewBox="0 0 120 28"><path d={item.sparkPath} fill="none" stroke={item.ic} strokeWidth="3" strokeLinecap="round" /></svg>
    </div>
  );
}

export function DashboardPremium({ apiToken, userName = 'Jianbo', userRole = 'Marketing Director', testId = 'vkpi-dashboard-premium', windowDays = 30, onSelectPage }: DashboardPremiumProps) {
  const [toast, setToast] = useState('已触发');
  const [toastVisible, setToastVisible] = useState(false);
  const [activeNav, setActiveNav] = useState('Dashboard');
  const [activeSegment, setActiveSegment] = useState('曝光量');
  const [snapshot, setSnapshot] = useState<PremiumSnapshot>(EMPTY_PREMIUM_SNAPSHOT);
  const [loadingData, setLoadingData] = useState(false);
  const [countryDrawer, setCountryDrawer] = useState<CountryDrawerState | null>(null);

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

  const premiumKpis = useMemo(() => buildPremiumKpis(snapshot), [snapshot]);
  const premiumProductRows = useMemo(() => buildProductRows(snapshot.productRows), [snapshot.productRows]);
  const premiumAlerts = useMemo(() => buildAlerts(snapshot.brandSignals), [snapshot.brandSignals]);
  const premiumTrend = useMemo(() => trendChart(snapshot.trendRows), [snapshot.trendRows]);
  const premiumRegions = useMemo(() => buildPremiumRegions(snapshot.kolSummary), [snapshot.kolSummary]);
  const premiumPlatforms = useMemo(() => buildPremiumPlatforms(snapshot.kolSummary), [snapshot.kolSummary]);
  const competitorTiers = objectValue(snapshot.competitorDashboard.tier_counts);
  const riskCount = numberValue(competitorTiers.avoid) + numberValue(competitorTiers.caution);
  const kolTotal = numberValue(snapshot.kolSummary.total || snapshot.kolSummary.candidate_asset_count);
  const heroMissions = useMemo(() => [
    { value: snapshot.source === 'mock' ? '7' : String(Math.max(1, riskCount || snapshot.failedSections.length || 1)), suffix: snapshot.source === 'mock' ? 'actions' : 'signals', label: snapshot.source === 'mock' ? '今日关键动作' : '竞品 / 风险信号' },
    { value: snapshot.source === 'mock' ? '3' : String(snapshot.failedSections.length), suffix: snapshot.source === 'mock' ? 'risks' : 'failed', label: snapshot.source === 'mock' ? '项目 / 竞品风险' : 'API 分区失败' },
    { value: snapshot.source === 'mock' ? '12' : compact(kolTotal), suffix: 'KOL', label: snapshot.source === 'mock' ? '新候选待评估' : 'KOL 池总量' },
  ], [kolTotal, riskCount, snapshot.failedSections.length, snapshot.source]);
  const syncLabel = loadingData
    ? '加载真实 API…'
    : snapshot.source === 'mock'
      ? '示例 · 待接入真实状态'
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
    if (target && target !== 'dashboardPremium' && onSelectPage) {
      onSelectPage(target);
      return;
    }
    showToast(`${key} · 高级玻璃方向占位`);
  };

  const handleSegmentSelect = (key: string) => {
    setActiveSegment(key);
    showToast(`切换：${key}`);
  };

  return (
    <div className="vkpi-glass-shell" data-testid={testId}>
      <div className="browser"><div className="traffic"><span className="t-dot red"></span><span className="t-dot yellow"></span><span className="t-dot green"></span></div><div className="browser-title">viltroxtest.com · V-KPI Glass Intelligence</div><div className="browser-icons">◉ ⇧</div></div>
      <div className="app">
        <GlassSidebar activeKey={activeNav} onSelectNav={handleNavSelect} profileInitial={userName.slice(0, 1).toUpperCase()} profileName={userName} profileRole={userRole} />
        <main className="main">
          <GlassTopBar
            actions={[
              { label: localDateISO(), onClick: () => showToast('今日日期 · 本地时区') },
              { label: syncLabel, variant: 'sync', onClick: () => showToast(snapshot.source === 'mock' ? '同步状态 · 开发占位' : `数据源：${snapshot.source}`) },
              { label: '导出', onClick: () => showToast('原型交互 · 可接真实路由') },
              { label: '生成周报', variant: 'primary', onClick: () => showToast('原型交互 · 可接真实路由') },
            ]}
          />
          <HeroSection missions={heroMissions} />
          <section className="kpis">
            {premiumKpis.map((item) => <PremiumKpiCard key={item.label} item={item} />)}
          </section>
          <div className="content-grid">
            <div>
              <div className="left-grid">
                <div className="glass-card panel">
                  <div className="panel-head"><h3>全球 KOL 分布</h3><span className="link" onClick={() => showToast('原型交互 · 可接真实路由')}>Country Map</span></div>
                  <div className="holo-map">
                    <div className="zoom"><span>+</span><span>−</span></div>
                    <svg viewBox="0 0 620 240" preserveAspectRatio="none">
                      <defs><linearGradient id="mg" x1="0" x2="1"><stop offset="0" stopColor="#1b6cff" /><stop offset="1" stopColor="#18d5ff" /></linearGradient><filter id="gl"><feGaussianBlur stdDeviation="5" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter></defs>
                      <path d="M92 82c40-22 94-9 121 2 27 11 68 3 92 15 30 15 9 39-28 40-58 3-125 15-161-2-35-16-61-39-24-55z" fill="url(#mg)" opacity=".93" filter="url(#gl)" />
                      <path d="M272 82c68-32 128-19 184-4 42 11 77 30 120 21 34-7 65 13 62 35-4 22-49 30-91 24-57-8-99-3-148 16-52 20-120 7-136-30-8-18-14-43 9-62z" fill="#9dc4ff" opacity=".66" />
                      <path d="M184 154c50-14 87 1 119 13 44 17 86 9 121 23 27 11 25 33-5 41-42 12-88-2-126-10-47-10-91 6-124-14-30-17-34-48 15-53z" fill="#6aa6ff" opacity=".50" />
                      <path d="M457 150c43-10 86 4 110 23 24 19 9 41-35 39-50-2-98-14-111-35-10-15 7-23 36-27z" fill="#cfe0ff" opacity=".70" />
                      {premiumRegions.slice(0, 5).map((region, index) => {
                        const point = mapPointPositions[index % mapPointPositions.length];
                        const label = region.countryCode || region.label.slice(0, 2);
                        return (
                          <g className="map-point" key={region.label} role="button" tabIndex={0} onClick={() => openCountryDrawer(region)} onKeyDown={(event) => { if (event.key === 'Enter') openCountryDrawer(region); }}>
                            <circle cx={point.x} cy={point.y} r={point.r} fill={region.color} />
                            <circle cx={point.x} cy={point.y} r={point.r * 3.4} fill={region.color} opacity=".12" />
                            <text x={point.x + 12} y={point.y + 4} fill="#344054" fontSize="11" fontWeight="800">{label}</text>
                          </g>
                        );
                      })}
                    </svg>
                  </div>
                  <div className="region-list">
                    {premiumRegions.map((region) => <div className="region" style={glassVarStyle({ '--c': region.color })} key={region.label} role="button" tabIndex={0} title={region.mockLabel || `${region.kolCount || 0} KOL`} onClick={() => openCountryDrawer(region)} onKeyDown={(event) => { if (event.key === 'Enter') openCountryDrawer(region); }}><span><i></i>{region.label}{region.isMock ? <em>示例</em> : null}</span><b>{region.value}</b></div>)}
                  </div>
                </div>
                <div className="glass-card panel">
                  <div className="panel-head"><h3>曝光趋势（近 7 天）</h3><div className="segment">{['曝光量', '互动量', '销售额'].map((segment) => <button className={activeSegment === segment ? 'active' : ''} data-seg={segment} onClick={() => handleSegmentSelect(segment)} type="button" key={segment}>{segment}</button>)}</div></div>
                  <div className="linechart"><svg viewBox="0 0 520 250" preserveAspectRatio="none"><defs><linearGradient id="area" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#1b6cff" /><stop offset="1" stopColor="#1b6cff" stopOpacity="0" /></linearGradient></defs><g stroke="rgba(92,130,190,.16)"><line x1="26" y1="30" x2="500" y2="30" /><line x1="26" y1="80" x2="500" y2="80" /><line x1="26" y1="130" x2="500" y2="130" /><line x1="26" y1="180" x2="500" y2="180" /></g><path d={premiumTrend.path} fill="none" stroke="#1b6cff" strokeWidth="4" strokeLinecap="round" /><path d={premiumTrend.areaPath} fill="url(#area)" opacity=".25" /><circle cx={premiumTrend.pointX} cy={premiumTrend.pointY} r="8" fill="#1b6cff" stroke="#fff" strokeWidth="4" /><text x="34" y="238" fontSize="12" fill="#667085">{premiumTrend.labels[0] || '-'}</text><text x="116" y="238" fontSize="12" fill="#667085">{premiumTrend.labels[1] || '-'}</text><text x="198" y="238" fontSize="12" fill="#667085">{premiumTrend.labels[2] || '-'}</text><text x="280" y="238" fontSize="12" fill="#667085">{premiumTrend.labels[3] || '-'}</text><text x="362" y="238" fontSize="12" fill="#667085">{premiumTrend.labels[4] || '-'}</text><text x="444" y="238" fontSize="12" fill="#667085">{premiumTrend.labels[5] || '-'}</text></svg><div className="float-tip">{premiumTrend.tipDate}<b>{premiumTrend.tipValue}</b></div></div>
                </div>
                <div className="lower">
                  <div className="glass-card mini">
                    <div className="panel-head"><h3>产品 ROI 排行</h3><span className="link" onClick={() => showToast('原型交互 · 可接真实路由')}>查看全部</span></div>
                    {premiumProductRows.map((row) => <div className="row" title={row.mockLabel} key={row.rank}><span className="rank">{row.rank}</span><div><b>{row.name}{row.isMock ? <span className="tag">示例</span> : null}</b><div className="bar"><span style={glassVarStyle({ '--w': row.width })}></span></div></div><small>{row.value}</small></div>)}
                  </div>
                  <div className="glass-card mini">
                    <div className="panel-head"><h3>内容类型分布</h3><span className="tag">示例</span></div>
                    <div className="donut-wrap"><div className="donut"></div><div className="donut-label"><span>总内容</span><b>2,847</b></div></div>
                    <div className="region-list" style={{ gridTemplateColumns: 'repeat(3,1fr)' }}>{contentTypes.map((item) => <div className="region" style={glassVarStyle({ '--c': item.color })} key={item.label}><span><i></i>{item.label}</span><b>{item.value}</b></div>)}</div>
                  </div>
                  <div className="glass-card mini">
                    <div className="panel-head"><h3>KOL 平台分布</h3><span className="link" onClick={() => showToast('原型交互 · 可接真实路由')}>查看全部</span></div>
                    {premiumPlatforms.map((platform) => <div className="platform" title={platform.mockLabel} key={platform.label}><span className="picon" style={{ background: platform.background }}>{platform.icon}</span><div><b>{platform.label}{platform.isMock ? <span className="tag">示例</span> : null}</b><div className="bar"><span style={glassVarStyle({ '--w': platform.width })}></span></div></div><small>{platform.value}</small></div>)}
                  </div>
                </div>
              </div>
              <div className="glass-card latest"><div className="panel-head"><h3>最新内容表现</h3><span className="link" onClick={() => showToast('原型交互 · 可接真实路由')}>进入内容中心</span></div><table className="table"><thead><tr><th>内容</th><th>KOL / 平台</th><th>发布平台</th><th>发布于</th><th>曝光量</th><th>互动率</th><th>操作</th></tr></thead><tbody><tr><td><div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}><div className="thumb"></div><div><b>Viltrox 35mm F1.2 LAB Real World Review</b><br /><span className="tag">视频</span> <span className="tag">测评</span> <span className="tag">示例</span></div></div></td><td>@vor_ject<br /><span style={{ color: '#667085' }}>Instagram</span></td><td>Instagram</td><td>2 小时前</td><td><b>1.24M</b></td><td>4.62%</td><td><button type="button" onClick={() => showToast('原型交互 · 可接真实路由')}>⌁</button> <button type="button" onClick={() => showToast('原型交互 · 可接真实路由')}>↗</button> <button type="button" onClick={() => showToast('原型交互 · 可接真实路由')}>…</button></td></tr></tbody></table></div>
            </div>
            <aside className="rail">
              <div className="glass-card rail-card copilot"><div className="ai-kicker">V-KPI Copilot</div><h3>系统正在把推荐、风险、任务压缩成 7 张行动卡。</h3><p>今日重点：处理 4 条推荐反馈、补齐 35mm LAB 项目 KOL 缺口、检查 Sigma 竞品内容。</p><div className="insight">示例 · 置信度 91% · 证据 18 条 · 数据新鲜度 4h</div></div>
              <div className="glass-card rail-card"><div className="panel-head"><h3>重要提醒</h3><span className="link" onClick={() => showToast('原型交互 · 可接真实路由')}>查看全部</span></div>{premiumAlerts.map((alert) => <div className="alert" title={alert.mockLabel} key={alert.title}><div className="alert-ic" style={glassVarStyle({ '--bgc': alert.bgc, '--col': alert.col })}>{alert.icon}</div><div><b>{alert.title}{alert.isMock ? <span className="tag">示例</span> : null}</b><p>{alert.body}</p></div><span className="time">{alert.time}</span></div>)}</div>
              <div className="glass-card rail-card"><div className="panel-head"><h3>本周关键任务</h3><span className="link" onClick={() => showToast('原型交互 · 可接真实路由')}>查看全部</span></div>{tasks.map((task) => <div className="task" title={task.mockLabel} key={task.title}><div className="task-head"><b>{task.title}{task.isMock ? <span className="tag">示例</span> : null}</b><span className={`priority ${task.priority}`}>{task.priorityLabel}</span></div><p>{task.body}</p><div className="progress"><span style={glassVarStyle({ '--w': task.width })}></span></div></div>)}</div>
              <div className="glass-card rail-card"><div className="panel-head"><h3>快捷入口</h3></div><div className="quick">{quickActions.map((action) => <div key={action.label} onClick={() => showToast('原型交互 · 可接真实路由')}><b>{action.icon}</b><span>{action.label}</span></div>)}</div></div>
            </aside>
          </div>
        </main>
      </div>
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
      <GlassFAB onClick={() => showToast('原型交互 · 可接真实路由')} />
      <GlassToast show={toastVisible}>{toast}</GlassToast>
    </div>
  );
}

export default DashboardPremium;
