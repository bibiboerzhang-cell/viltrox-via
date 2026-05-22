import React, { useEffect, useMemo, useRef, useState } from 'react';
import type { VkpiDashboardData, VkpiKolLookupResult, VkpiKolOption, VkpiKolProfile, VkpiPlatform, VkpiProjectRow } from '../vkpiTypes';
import { KolPoolPanel } from '../panels/KolPoolPanel';
import { Avatar } from '../shared/Avatar';
import { creatorPlatformOptions, platformLabels, stageLabels } from '../shared/vkpiConstants';
import { arrayValue, compactCount, contactLinksFrom, objectValue, platformFromRaw, safeNumber, textValue } from '../shared/vkpiDataUtils';
import { PageShell } from './PageShell';
import {
  addKolContact,
  batchEnrichKolPool,
  enrichKolPoolItem,
  getKolAssessment,
  getKolPoolCompetitors,
  getKolPoolDimensions11,
  getKolPoolItem,
  getKolPosts,
  getKolProductFit,
  getKolProfile,
  listKolContacts,
  listKolPool,
  listMarketingKols,
  listProductRecommendations,
  promoteKolPoolToMain,
  productRecommendationAction,
  runProductRecommendations,
  searchMarketingKolsNatural,
  searchPlatformKols,
} from '../../../services/vkpi.ui-api';
import type { VkpiKolAssessmentResponse, VkpiKolProductFitResponse } from '../../../services/vkpi.ui-api';
import './discover/discoverDecision.css';

interface DiscoverPageProps {
  data: VkpiDashboardData;
  onLookupKol?: (payload: { platform: string; handleOrUrl: string; createIfMissing?: boolean; email?: string; contactEmail?: string; notes?: string; scanAccount?: boolean; maxPosts?: number; productSku?: string }) => Promise<VkpiKolLookupResult>;
  onScanKolAccount?: (kolId: string, maxPosts?: number) => Promise<Record<string, unknown>>;
  onClaimKol?: (kolId: string) => Promise<void>;
  onUpdateKol?: (kolId: string, payload: { avatarUrl?: string; profileUrl?: string; contactEmail?: string; contactPhone?: string; notes?: string; contactLinks?: Array<{ label?: string; value?: string; url?: string }> }) => Promise<void>;
  onCreateProject?: (payload: { projectName: string; kolId?: string; productSku?: string; productName?: string; productSkus?: string[]; products?: Array<{ productSku: string; productName?: string }>; platform?: string; marketplace?: string; note?: string }) => Promise<void>;
  apiToken?: string;
}

type DiscoverTab = 'search' | 'recommendations' | 'pool';
type MessageTone = 'info' | 'warn' | 'error';
type SearchStepStatus = 'pending' | 'active' | 'done' | 'error';

interface SearchProgressStep {
  key: string;
  label: string;
  detail: string;
  status: SearchStepStatus;
}

interface SearchProgressState {
  visible: boolean;
  title: string;
  percent: number;
  steps: SearchProgressStep[];
}

interface SearchHistoryItem {
  id: string;
  query: string;
  platform: string;
  mode: string;
  resultCount: number;
  status: string;
  searchedAt: string;
}

interface UiKol {
  id: string;
  name: string;
  handle: string;
  platform: VkpiPlatform;
  avatar?: string;
  profileUrl?: string;
  contactEmail?: string;
  contactPhone?: string;
  followerCount: number;
  followerLabel: string;
  contentCount: number;
  contentCountLabel: string;
  score: number;
  grade: string;
  country: string;
  topic: string;
  claimOwner: string;
  status: string;
  freshness: string;
  contactCount: number;
  projectCount: number;
  hasCollaboration: boolean;
  riskLabel: string;
  sourceKind?: 'kol' | 'platform_search' | 'kol_pool';
  raw: Record<string, unknown>;
}

interface ContactItem {
  id: string;
  type: string;
  value: string;
  layer: number;
  source: string;
  confidence?: number;
  evidence?: string;
  verified?: boolean;
}

type ProductFitItem = NonNullable<VkpiKolProductFitResponse['items']>[number];
type RecommendationAction = 'shortlist' | 'reject' | 'claim';
type CompetitorRelation = Record<string, unknown>;
type Dimensions11Payload = Record<string, unknown>;

interface DirectionChip {
  label: string;
  query: string;
  source: string;
}

interface SmartRecommendation {
  id: string;
  kolId: string;
  handle: string;
  displayName: string;
  platform: string;
  score: number;
  rank: number;
  status: string;
  reason: string;
  source: string;
  competitorRiskTier: string;
  competitorRiskScore: number;
  competitorBrand: string;
  raw: Record<string, unknown>;
}

const tabLabels: Array<{ key: DiscoverTab; label: string; hint: string }> = [
  { key: 'search', label: '主动搜索', hint: '真实查重 / 抓取 / 画像' },
  { key: 'recommendations', label: '智能推荐', hint: 'Product Analysis 真实推荐' },
  { key: 'pool', label: '候选池', hint: '复用 KOL Pool' },
];

const dimensionLabels = [
  { key: 'audience', label: '受众', source: 'audience_fit' },
  { key: 'engagement', label: '互动', source: 'engagement_rate' },
  { key: 'content', label: '内容', source: 'account_score' },
  { key: 'consistency', label: '稳定', source: 'scan_status' },
  { key: 'safety', label: '安全', source: 'risk_level' },
  { key: 'growth', label: '增长', source: 'backend Module 1' },
  { key: 'professional', label: '专业', source: 'product_fit' },
  { key: 'authenticity', label: '真实', source: 'backend Module 1' },
];

const searchStepDefinitions = [
  { key: 'candidate', label: '推荐方向', detail: '先确认搜索意图' },
  { key: 'source', label: '数据源', detail: '平台搜索 / 已有档案' },
  { key: 'profile', label: '账号资料', detail: '头像、粉丝、链接' },
  { key: 'posts', label: '最近内容', detail: '样本内容或 posts' },
  { key: 'decision', label: '可分析', detail: '建档 / 抓取 / 产品适配' },
];

const SEARCH_HISTORY_STORAGE_KEY = 'vkpi.discover.searchHistory.v1';
const MAX_SEARCH_HISTORY = 12;

const idleSearchProgress: SearchProgressState = {
  visible: false,
  title: '',
  percent: 0,
  steps: searchStepDefinitions.map((step) => ({ ...step, status: 'pending' })),
};

function searchProgressState(
  title: string,
  percent: number,
  activeKey: string,
  doneKeys: string[] = [],
  errorKey = '',
): SearchProgressState {
  const done = new Set(doneKeys);
  return {
    visible: true,
    title,
    percent: Math.max(0, Math.min(100, Math.round(percent))),
    steps: searchStepDefinitions.map((step) => ({
      ...step,
      status: errorKey === step.key ? 'error' : done.has(step.key) ? 'done' : activeKey === step.key ? 'active' : 'pending',
    })),
  };
}

function platformInputValue(platformLabel: string): string {
  const normalized = String(platformLabel || '').toLowerCase();
  return creatorPlatformOptions.find((option) => option.value === normalized || option.label.toLowerCase() === normalized)?.value || normalized || 'other';
}

function loadSearchHistory(): SearchHistoryItem[] {
  if (typeof window === 'undefined') return [];
  try {
    const parsed = JSON.parse(window.localStorage.getItem(SEARCH_HISTORY_STORAGE_KEY) || '[]');
    if (!Array.isArray(parsed)) return [];
    return parsed.map((item) => {
      const row = objectValue(item);
      const query = textValue(row.query, '');
      if (!query) return null;
      return {
        id: textValue(row.id, `${textValue(row.platform, 'all')}:${query.toLowerCase()}`),
        query,
        platform: textValue(row.platform, 'all'),
        mode: textValue(row.mode, 'search'),
        resultCount: safeNumber(row.resultCount),
        status: textValue(row.status, ''),
        searchedAt: textValue(row.searchedAt, new Date().toISOString()),
      };
    }).filter(Boolean).slice(0, MAX_SEARCH_HISTORY) as SearchHistoryItem[];
  } catch {
    return [];
  }
}

function saveSearchHistory(items: SearchHistoryItem[]) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(SEARCH_HISTORY_STORAGE_KEY, JSON.stringify(items.slice(0, MAX_SEARCH_HISTORY)));
  } catch {
    // Ignore private-mode storage failures; search itself should still work.
  }
}

function formatHistoryTime(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  const deltaSeconds = Math.max(0, Math.floor((Date.now() - parsed.getTime()) / 1000));
  if (deltaSeconds < 60) return '刚刚';
  const minutes = Math.floor(deltaSeconds / 60);
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${parsed.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })} ${parsed.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`;
}

function searchHistoryPlatformLabel(value: string): string {
  return value === 'all' ? '全部平台' : platformLabels[platformFromRaw(value)] || value;
}

function kolPoolIdForCompetitors(kol?: UiKol): string {
  if (!kol) return '';
  const raw = objectValue(kol.raw);
  const historyMatch = objectValue(raw.historical_match || raw.history_match);
  const id = raw.kol_pool_id || raw.history_kol_pool_id || historyMatch.kol_pool_id || historyMatch.id;
  if (id) return String(id);
  return kol.sourceKind === 'kol_pool' && /^\d+$/.test(kol.id) ? kol.id : '';
}

function competitorTierLabel(value: unknown): string {
  const tier = textValue(value, 'opportunity');
  if (tier === 'avoid') return '高风险';
  if (tier === 'caution') return '谨慎';
  if (tier === 'safe') return '可合作';
  return '机会';
}

function competitorTone(value: unknown): string {
  const tier = textValue(value, 'opportunity');
  if (tier === 'avoid') return 'is-risk-avoid';
  if (tier === 'caution') return 'is-risk-caution';
  if (tier === 'safe') return 'is-risk-safe';
  return 'is-risk-opportunity';
}

function visibleCompetitorRelations(relations: CompetitorRelation[]): CompetitorRelation[] {
  return relations
    .filter((relation) => safeNumber(relation.risk_score) > 0 || textValue(relation.collaboration_depth, 'none') !== 'none')
    .sort((a, b) => safeNumber(b.risk_score) - safeNumber(a.risk_score))
    .slice(0, 4);
}

function normalizeHandle(value: unknown): string {
  const raw = String(value || '').trim();
  if (!raw) return '-';
  return raw.startsWith('@') ? raw : `@${raw}`;
}

function scoreToGrade(score: number): string {
  if (score >= 90) return 'S';
  if (score >= 80) return 'A';
  if (score >= 65) return 'B';
  if (score >= 50) return 'C';
  return 'D';
}

function clampScore(value: unknown): number {
  const parsed = safeNumber(value);
  if (!parsed) return 0;
  return Math.max(0, Math.min(100, Math.round(parsed)));
}

function confidenceValue(block: Record<string, unknown>, key: string, fallback = 0): number {
  const confidence = objectValue(block.confidence);
  const parsed = safeNumber(confidence[key]);
  if (parsed) return Math.max(0, Math.min(1, parsed));
  return Math.max(0, Math.min(1, fallback));
}

function pendingByConfidence(value: number, confidence: number): boolean {
  if (confidence >= 0.35) return false;
  return !value || confidence === 0;
}

function scoreFromRaw(raw: Record<string, unknown>): number {
  return clampScore(
    raw.account_score ||
      raw.score ||
      raw.priority_score ||
      raw.avg_ai_quality_score ||
      raw.product_fit ||
      raw.audience_fit ||
      raw.viltrox_fit_score,
  );
}

function compactLabel(value: unknown, fallback = '-'): string {
  const parsed = safeNumber(value);
  return parsed ? compactCount(parsed) : fallback;
}

function productFitMetaLine(product: Record<string, unknown>): string {
  const catalog = objectValue(product.catalog_product || product.matched_catalog_product);
  const specs = objectValue(product.specs || catalog.specs);
  const mount = textValue(product.mount || catalog.mount, '');
  const price = safeNumber(product.price_usd || catalog.price_usd);
  const focal = textValue(specs.focal_length, '');
  const aperture = textValue(specs.aperture, '');
  return [
    mount,
    price ? `$${price.toLocaleString('en-US')}` : '',
    focal,
    aperture,
  ].filter(Boolean).join(' · ');
}

function usableCandidateText(...values: unknown[]): string {
  for (const value of values) {
    const text = textValue(value, '');
    if (text && !/^unknown\s+creator$/i.test(text)) return text;
  }
  return '';
}

function candidateKey(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9@._-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 80) || 'candidate';
}

function queryHandleSeed(term: string): string {
  const clean = term.trim();
  if (!clean) return 'candidate';
  const withoutProtocol = clean.replace(/^https?:\/\//i, '').replace(/^www\./i, '');
  const urlParts = withoutProtocol.split(/[/?#]/).filter(Boolean);
  const lastPart = urlParts[urlParts.length - 1] || withoutProtocol;
  return lastPart.replace(/^@/, '') || clean.replace(/^@/, '');
}

function contactCountFromRaw(raw: Record<string, unknown>): number {
  let total = 0;
  if (textValue(raw.contact_email, '')) total += 1;
  if (textValue(raw.contact_phone, '')) total += 1;
  total += contactLinksFrom(raw.contact_links_json).length;
  total += contactLinksFrom(raw.contact_links).length;
  return total;
}

function topicFromRaw(raw: Record<string, unknown>): string {
  return textValue(raw.niche || raw.primary_topic || raw.channel_tags || raw.promoted_product || raw.category, '待归类');
}

function kolOptionToUiKol(kol: VkpiKolOption): UiKol {
  const raw: Record<string, unknown> = {
    id: kol.id,
    media_name: kol.name,
    channel_name: kol.handle,
    platform: kol.platform,
    avatar_url: kol.avatar,
    profile_url: kol.profileUrl,
    contact_email: kol.contactEmail,
    contact_phone: kol.contactPhone,
    contact_links: kol.contactLinks || [],
    follower_count: kol.followerLabel,
    content_count: kol.contentCountLabel,
    active_claim_id: kol.activeClaimId,
    claim_staff_name: kol.claimOwner,
    snapshot_scan_status: kol.scanStatus,
  };
  const score = scoreFromRaw(raw);
  return {
    id: kol.id,
    name: kol.name || kol.handle || 'KOL',
    handle: normalizeHandle(kol.handle || kol.name),
    platform: kol.platform,
    avatar: kol.avatar,
    profileUrl: kol.profileUrl,
    contactEmail: kol.contactEmail,
    contactPhone: kol.contactPhone,
    followerCount: safeNumber(kol.followerLabel),
    followerLabel: kol.followerLabel || '-',
    contentCount: safeNumber(kol.contentCountLabel),
    contentCountLabel: kol.contentCountLabel || '-',
    score,
    grade: score ? scoreToGrade(score) : '-',
    country: '-',
    topic: '已建档红人',
    claimOwner: kol.claimOwner || '',
    status: kol.scanStatus || 'known_profile',
    freshness: kol.scanStatus ? '已同步' : '待同步',
    contactCount: (kol.contactEmail ? 1 : 0) + (kol.contactPhone ? 1 : 0) + (kol.contactLinks?.length || 0),
    projectCount: 0,
    hasCollaboration: false,
    riskLabel: '',
    raw,
  };
}

function rawToUiKol(raw: Record<string, unknown>): UiKol {
  const historyMatch = objectValue(raw.historical_match || raw.history_match);
  const historyCooperationCount = safeNumber(historyMatch.cooperation_count || raw.cooperation_count || raw.history_cooperation_count);
  const sourceKind = textValue(raw.source_kind, '') === 'kol_pool' ? 'kol_pool' : undefined;
  const score = Math.max(scoreFromRaw(raw), historyCooperationCount ? 70 : 0);
  const snapshotFollowers = raw.snapshot_follower_count || raw.follower_count || raw.followers || raw.subscriber_count;
  const snapshotContent = raw.snapshot_content_count || raw.content_count || raw.video_count || raw.posts_count;
  const platform = platformFromRaw(raw.platform);
  const handle = normalizeHandle(sourceKind === 'kol_pool' ? raw.handle || raw.channel_name : raw.channel_name || raw.handle || raw.username || raw.owner_name || raw.media_name);
  const projectCount = safeNumber(raw.project_count || raw.campaign_count || raw.cooperation_count || historyCooperationCount);
  return {
    id: String(raw.id || raw.kol_id || raw.linked_main_kol_id || handle),
    name: textValue(raw.media_name || raw.creator_name || raw.display_name || raw.owner_name || handle, handle),
    handle,
    platform,
    avatar: textValue(raw.avatar_url || raw.profile_pic_url || raw.profilePicUrl, ''),
    profileUrl: textValue(raw.profile_url || raw.channel_url || raw.url, ''),
    contactEmail: textValue(raw.contact_email, ''),
    contactPhone: textValue(raw.contact_phone, ''),
    followerCount: safeNumber(snapshotFollowers),
    followerLabel: compactLabel(snapshotFollowers),
    contentCount: safeNumber(snapshotContent),
    contentCountLabel: compactLabel(snapshotContent),
    score,
    grade: score ? scoreToGrade(score) : '-',
    country: textValue(raw.country || raw.country_code || raw.region, '-'),
    topic: topicFromRaw(raw),
    claimOwner: textValue(raw.claim_staff_name || raw.assigned_staff_name || raw.staff_name, ''),
    status: sourceKind === 'kol_pool'
      ? (historyCooperationCount ? `历史合作 ${historyCooperationCount} 条` : '历史档案 / 待深扫')
      : textValue(raw.snapshot_scan_status || raw.scan_status || raw.sync_status || raw.contact_status, 'known_profile'),
    freshness: textValue(raw.snapshot_scanned_at || raw.scanned_at || raw.updated_at, '待刷新'),
    contactCount: contactCountFromRaw(raw),
    projectCount,
    hasCollaboration: projectCount > 0 || safeNumber(raw.revenue_cents) > 0,
    riskLabel: textValue(raw.risk_level || raw.risk_label, safeNumber(historyMatch.risk_rows) ? '历史风险待核' : ''),
    sourceKind,
    raw,
  };
}

function lookupToUiKol(result: VkpiKolLookupResult): UiKol | null {
  const raw = objectValue(result.kol);
  if (!raw.id && !raw.channel_name && !raw.media_name) return null;
  const merged = {
    ...raw,
    ...objectValue(result.dossier?.snapshot),
    ...objectValue(result.dossier?.report),
  };
  return rawToUiKol(merged);
}

function candidatePostsFromRaw(raw: Record<string, unknown>): Array<Record<string, unknown>> {
  const rows = [
    ...arrayValue(raw.posts),
    ...arrayValue(raw.items),
    ...arrayValue(raw.videos),
    ...arrayValue(raw.latest_posts),
    ...arrayValue(raw.latestPosts),
  ].map(objectValue);
  const sampleTitle = textValue(raw.sample_title || raw.title || raw.caption || raw.text, '');
  const sampleUrl = textValue(raw.source_url || raw.post_url || raw.url || raw.content_url, '');
  const sampleViews = safeNumber(raw.views || raw.avg_views || raw.view_count || raw.play_count);
  if (sampleTitle || sampleUrl) {
    rows.unshift({
      id: textValue(raw.source_url || raw.url || raw.candidate_id || raw.search_query, 'platform-sample'),
      title: sampleTitle || '平台搜索样本内容',
      post_url: sampleUrl,
      url: sampleUrl,
      views: sampleViews,
      likes: safeNumber(raw.likes || raw.like_count),
      comments: safeNumber(raw.comments || raw.comment_count),
      published: textValue(raw.published || raw.searched_at, ''),
      source_kind: 'platform_search_sample',
    });
  }
  const seen = new Set<string>();
  return rows
    .map((row, index) => {
      const url = textValue(row.source_url || row.post_url || row.url || row.content_url || row.permalink, '');
      const title = textValue(row.sample_title || row.title || row.caption || row.text || url, '');
      return {
        ...row,
        id: textValue(row.id || row.post_uid || row.shortCode || row.shortcode || url || title, `candidate-post-${index}`),
        title,
        post_url: url,
        url,
        views: safeNumber(row.views || row.avg_views || row.view_count || row.play_count),
      };
    })
    .filter((row) => {
      const key = textValue(row.post_url || row.url || row.title || row.id, '');
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 6);
}

function platformSearchItemToUiKol(raw: Record<string, unknown>, index: number, candidateId?: number): UiKol {
  const historyMatch = objectValue(raw.historical_match || raw.history_match);
  const historyCooperationCount = safeNumber(historyMatch.cooperation_count || raw.history_cooperation_count);
  const hasHistoryMatch = Boolean(historyMatch.matched || historyMatch.kol_pool_id || raw.history_kol_pool_id);
  const searchQuery = usableCandidateText(raw.search_query);
  const handleSource = usableCandidateText(
    raw.handle,
    raw.username,
    raw.ownerUsername,
    raw.channel_name,
    raw.display_name,
    raw.ownerFullName,
    searchQuery,
  ) || `candidate-${index + 1}`;
  const displayName = usableCandidateText(
    raw.channel_name,
    raw.display_name,
    raw.media_name,
    raw.creator_name,
    raw.ownerFullName,
    raw.owner_name,
    handleSource,
    searchQuery,
  ) || handleSource;
  const platform = platformFromRaw(raw.platform);
  const views = safeNumber(raw.views || raw.avg_views);
  const baseScore = Math.max(35, Math.min(78, Math.round(35 + Math.log10(Math.max(views, 1)) * 8)));
  const score = Math.max(baseScore, historyCooperationCount ? 76 : hasHistoryMatch ? 64 : 0);
  const samplePosts = candidatePostsFromRaw(raw);
  const contentCount = safeNumber(raw.content_count || raw.post_count || raw.posts_count) || samplePosts.length || 1;
  const followerSource = raw.follower_count || raw.followers || historyMatch.followers;
  return {
    id: `candidate:${candidateId || index}:${platform}:${candidateKey(handleSource)}`,
    name: displayName,
    handle: normalizeHandle(handleSource),
    platform,
    avatar: textValue(
      raw.avatar_url ||
        raw.profile_pic_url ||
        raw.profilePicUrl ||
        raw.profilePictureUrl ||
        raw.ownerProfilePicUrl ||
        raw.thumbnail_url ||
        raw.thumbnail ||
	        raw.thumbnailUrl ||
	        raw.image ||
	        raw.picture ||
	        historyMatch.avatar_url,
	      '',
	    ),
    profileUrl: textValue(raw.channel_url || raw.profile_url || historyMatch.profile_url || raw.url, ''),
    contactEmail: '',
    contactPhone: '',
    followerCount: safeNumber(followerSource),
    followerLabel: compactLabel(followerSource),
    contentCount,
    contentCountLabel: compactCount(contentCount),
    score,
    grade: scoreToGrade(score),
    country: textValue(raw.market || raw.country, '-'),
    topic: textValue(raw.sample_title || raw.search_query, '平台实时搜索结果'),
    claimOwner: '',
    status: historyCooperationCount ? `已合作历史 ${historyCooperationCount} 条 / 可复用` : hasHistoryMatch ? '历史档案 / 未深扫 / 可分析' : '候选 / 未深扫 / 可分析',
    freshness: textValue(raw.published || raw.searched_at, '刚搜索'),
    contactCount: 0,
    projectCount: historyCooperationCount,
    hasCollaboration: historyCooperationCount > 0,
    riskLabel: safeNumber(historyMatch.risk_rows) ? '历史风险待核' : '',
    sourceKind: 'platform_search',
    raw: { ...raw, candidate_id: candidateId, source_kind: 'platform_search' },
  };
}

function instantCandidateToUiKol(term: string, selectedPlatform: string): UiKol {
  const seed = queryHandleSeed(term);
  const platform = platformFromRaw(selectedPlatform === 'all' ? '' : selectedPlatform);
  const handle = normalizeHandle(seed);
  return {
    id: `candidate:instant:${platform}:${candidateKey(seed)}`,
    name: term.trim() || handle,
    handle,
    platform,
    avatar: '',
    profileUrl: '',
    contactEmail: '',
    contactPhone: '',
    followerCount: 0,
    followerLabel: '读取中',
    contentCount: 0,
    contentCountLabel: '读取中',
    score: 0,
    grade: '-',
    country: '-',
    topic: '搜索候选 / 等待平台返回',
    claimOwner: '',
    status: '候选生成中',
    freshness: '刚开始',
    contactCount: 0,
    projectCount: 0,
    hasCollaboration: false,
    riskLabel: '',
    sourceKind: 'platform_search',
    raw: { source_kind: 'instant_candidate', search_query: term.trim(), platform: selectedPlatform },
  };
}

function isCandidateKol(kol?: UiKol) {
  return Boolean(kol?.sourceKind === 'platform_search' || kol?.sourceKind === 'kol_pool' || String(kol?.id || '').startsWith('candidate:') || String(kol?.id || '').startsWith('pool:'));
}

function isPlatformSearchCandidate(kol?: UiKol) {
  return Boolean(kol?.sourceKind === 'platform_search' || String(kol?.id || '').startsWith('candidate:'));
}

function searchKolMergeKey(kol: UiKol): string {
  const handle = normalizeHandle(kol.handle || kol.profileUrl || kol.name);
  if (handle) return `${platformInputValue(kol.platform)}:${handle}`;
  return `${platformInputValue(kol.platform)}:${candidateKey(kol.id || kol.name)}`;
}

function mergeSearchKols(...groups: UiKol[][]): UiKol[] {
  const merged = new Map<string, UiKol>();
  groups.flat().forEach((kol) => {
    const key = searchKolMergeKey(kol);
    const current = merged.get(key);
    if (!current) {
      merged.set(key, kol);
      return;
    }
    const raw = { ...current.raw, ...kol.raw };
    const projectCount = Math.max(current.projectCount || 0, kol.projectCount || 0);
    const next: UiKol = {
      ...current,
      ...kol,
      id: kol.sourceKind === 'platform_search' ? kol.id : current.id,
      avatar: kol.avatar || current.avatar,
      profileUrl: kol.profileUrl || current.profileUrl,
      contactEmail: kol.contactEmail || current.contactEmail,
      contactPhone: kol.contactPhone || current.contactPhone,
      followerCount: Math.max(current.followerCount || 0, kol.followerCount || 0),
      followerLabel: kol.followerCount ? kol.followerLabel : current.followerLabel,
      contentCount: Math.max(current.contentCount || 0, kol.contentCount || 0),
      contentCountLabel: kol.contentCount ? kol.contentCountLabel : current.contentCountLabel,
      score: Math.max(current.score || 0, kol.score || 0),
      projectCount,
      hasCollaboration: current.hasCollaboration || kol.hasCollaboration || projectCount > 0,
      riskLabel: kol.riskLabel || current.riskLabel,
      status: projectCount ? `已合作历史 ${projectCount} 条 / 可复用` : (kol.status || current.status),
      sourceKind: kol.sourceKind === 'platform_search' ? kol.sourceKind : current.sourceKind,
      raw,
    };
    merged.set(key, next);
  });
  return Array.from(merged.values()).sort((a, b) => {
    const collabDelta = Number(b.hasCollaboration) - Number(a.hasCollaboration);
    if (collabDelta) return collabDelta;
    const sourceDelta = Number(b.sourceKind === 'kol_pool') - Number(a.sourceKind === 'kol_pool');
    if (sourceDelta) return sourceDelta;
    const scoreDelta = (b.score || 0) - (a.score || 0);
    if (scoreDelta) return scoreDelta;
    return (b.followerCount || 0) - (a.followerCount || 0);
  });
}

function formatAssessmentMethod(method?: string) {
  const clean = String(method || '').trim();
  if (!clean) return '等待深度评估';
  if (clean.includes('local_assessment')) return '本地旧评估；建议刷新';
  return clean.replace(/_/g, ' ');
}

function formatProductFitSource(hasFits: boolean, method?: string) {
  if (!hasFits) return '等待产品适配';
  if (String(method || '').includes('local_product_fit')) return '本地规则估算';
  if (String(method || '').includes('rule_dimensions_11')) return '11维规则产品适配';
  return String(method || '产品适配').replace(/_/g, ' ');
}

function cleanProductLabel(value: unknown) {
  let label = textValue(value, '未命名产品');
  label = label.replace(/^viltrox\s+/i, '').replace(/\s+/g, ' ').trim();
  label = label.replace(/\s+(FE|E|Z|X|L|RF)$/i, ' ($1)');
  return label;
}

function searchIntentTags(query: unknown): Array<{ label: string; detail: string }> {
  const raw = textValue(query, '').trim();
  if (!raw) return [];
  const lower = raw.toLowerCase();
  const tags: Array<{ label: string; detail: string }> = [];
  const add = (label: string, detail: string) => {
    if (!tags.some((item) => item.label === label)) tags.push({ label, detail });
  };
  if (/35\s*mm|35mm|evo|f1[.\s]?8|1[.\s]?8/.test(lower)) add('35mm EVO / F1.8', '镜头样片、街拍、人像、测评优先');
  if (/街拍|street/.test(raw) || lower.includes('street')) add('街拍内容', '看真实外拍频率和画面风格');
  if (/测评|评测|review|test/.test(lower)) add('测评账号', '优先看近期评测样片和器材可信度');
  if (/youtube|视频|video|拍摄|creator/.test(lower)) add('视频创作者', '重点看近期内容、播放和频道匹配');
  if (/人像|portrait/.test(lower)) add('人像方向', '检查肤色、人像样片和镜头表达');
  if (/旅行|travel|vlog/.test(lower)) add('旅行 / Vlog', '检查轻量化设备和连续更新能力');
  if (!tags.length) add('搜索关键词', raw.length > 28 ? `${raw.slice(0, 28)}...` : raw);
  return tags.slice(0, 5);
}

function filterKols(kols: UiKol[], filters: { platform: string; level: string; grade: string; collab: string; risk: string; freshness: string }, query: string): UiKol[] {
  const needle = query.trim().toLowerCase().replace(/^@/, '');
  return kols.filter((kol) => {
    if (filters.platform !== 'all' && platformInputValue(kol.platform) !== filters.platform) return false;
    if (filters.level === 'top' && kol.followerCount < 500000) return false;
    if (filters.level === 'mid' && (kol.followerCount < 50000 || kol.followerCount >= 500000)) return false;
    if (filters.level === 'tail' && kol.followerCount >= 50000) return false;
    if (filters.grade !== 'all' && kol.grade !== filters.grade) return false;
    if (filters.collab === 'yes' && !kol.hasCollaboration) return false;
    if (filters.collab === 'no' && kol.hasCollaboration) return false;
    if (filters.risk === 'has' && !kol.riskLabel) return false;
    if (filters.risk === 'clean' && kol.riskLabel) return false;
    if (filters.freshness === 'stale' && !String(kol.status || kol.freshness).toLowerCase().includes('refresh')) return false;
    if (!needle) return true;
    return [kol.name, kol.handle, kol.topic, kol.country, kol.platform].join(' ').toLowerCase().includes(needle);
  });
}

function dimensions11Bars(payload: Dimensions11Payload | null) {
  if (!payload) return [];
  const block1 = objectValue(payload.block1_content);
  const block2 = objectValue(payload.block2_performance);
  const block3 = objectValue(payload.block3_business);
  const block4 = objectValue(payload.block4_specialty);
  if (!Object.keys(block1).length && !Object.keys(block2).length && !Object.keys(block3).length && !Object.keys(block4).length) return [];
  const specialty = objectValue(block1.content_specialty);
  const productFit = objectValue(block4.product_fit);
  const productFitConfidence = objectValue(block4.product_fit_confidence);
  const specialtyScore = Math.max(0, ...Object.values(specialty).map(safeNumber));
  const productFitScore = Math.max(
    0,
    ...Object.entries(productFit)
      .filter(([sku]) => confidenceValue({ confidence: productFitConfidence }, sku) >= 0.35)
      .map(([, score]) => safeNumber(score)),
  );
  const clusters = arrayValue(block4.industry_cluster).filter(Boolean);
  const postingFrequency = clampScore(block1.posting_frequency_score);
  const contentDiversity = clampScore(block1.content_diversity_score);
  const followersTier = clampScore(block2.followers_tier_score);
  const engagementQuality = clampScore(block2.engagement_quality_score);
  const growthVelocity = clampScore(block2.growth_velocity_score);
  const cooperationHistory = clampScore(block3.cooperation_history_score);
  const contactReachability = clampScore(block3.contact_reachability_score);
  const competitorRisk = clampScore(block3.competitor_risk_score);
  const industryConfidence = confidenceValue(block4, 'industry_cluster');
  const productFitBlockConfidence = confidenceValue(block4, 'product_fit');
  return [
    { key: 'posting_frequency', label: '发布活跃', source: textValue(block1.source, '11维规则'), value: postingFrequency, pending: pendingByConfidence(postingFrequency, confidenceValue(block1, 'posting_frequency_score')) },
    { key: 'content_diversity', label: '内容多样', source: textValue(block1.source, '11维规则'), value: contentDiversity, pending: pendingByConfidence(contentDiversity, confidenceValue(block1, 'content_diversity_score')) },
    { key: 'content_specialty', label: '内容特长', source: Object.keys(specialty).slice(0, 3).join(' / ') || '待接内容证据', value: clampScore(specialtyScore), pending: pendingByConfidence(clampScore(specialtyScore), confidenceValue(block1, 'content_specialty')) },
    { key: 'followers_tier', label: '粉丝规模', source: textValue(block2.source, '11维规则'), value: followersTier, pending: pendingByConfidence(followersTier, confidenceValue(block2, 'followers_tier_score')) },
    { key: 'engagement_quality', label: '互动质量', source: textValue(block2.source, '11维规则'), value: engagementQuality, pending: pendingByConfidence(engagementQuality, confidenceValue(block2, 'engagement_quality_score')) },
    { key: 'growth_velocity', label: '增长趋势', source: textValue(block2.source, '11维规则'), value: growthVelocity, pending: pendingByConfidence(growthVelocity, confidenceValue(block2, 'growth_velocity_score')) },
    { key: 'cooperation_history', label: '合作历史', source: textValue(block3.source, '11维规则'), value: cooperationHistory, pending: pendingByConfidence(cooperationHistory, confidenceValue(block3, 'cooperation_history_score')) },
    { key: 'contact_reachability', label: '联系可达', source: textValue(block3.source, '11维规则'), value: contactReachability, pending: pendingByConfidence(contactReachability, confidenceValue(block3, 'contact_reachability_score')) },
    { key: 'competitor_risk', label: '竞品风险', source: textValue(block3.competitor_risk_tier, 'opportunity'), value: competitorRisk, pending: confidenceValue(block3, 'competitor_risk_score') < 0.35 },
    { key: 'industry_cluster', label: '行业归属', source: clusters.join(' / ') || '待接行业证据', value: clusters.length ? 82 : 0, pending: pendingByConfidence(clusters.length ? 82 : 0, industryConfidence) },
    { key: 'product_fit', label: '产品适配', source: textValue(block4.source, '11维规则'), value: clampScore(productFitScore), pending: pendingByConfidence(clampScore(productFitScore), productFitBlockConfidence) },
  ];
}

function productFitsFromDimensions11(payload: Dimensions11Payload | null): ProductFitItem[] {
  if (!payload) return [];
  const block4 = objectValue(payload.block4_specialty);
  const productFit = objectValue(block4.product_fit);
  const productFitConfidence = objectValue(block4.product_fit_confidence);
  const clusters = arrayValue(block4.industry_cluster).map((item) => textValue(item, '')).filter(Boolean);
  return Object.entries(productFit)
    .filter(([sku]) => confidenceValue({ confidence: productFitConfidence }, sku) >= 0.35)
    .map(([sku, score]) => ({
      product_sku: sku,
      product_name: cleanProductLabel(sku),
      score: safeNumber(score),
      method: textValue(payload.method, 'rule_dimensions_11_v0'),
      reasons: clusters.length ? [`行业 ${clusters.slice(0, 3).join(' / ')}`] : ['来自 11 维规则画像'],
      evidence: ['vkpi_kol_pool cached profile/posts'],
    }))
    .filter((item) => item.score)
    .sort((a, b) => safeNumber(b.score) - safeNumber(a.score))
    .slice(0, 5);
}

function dimensionsFromProfile(profile: VkpiKolProfile | null, selected: UiKol | undefined, assessment: VkpiKolAssessmentResponse | null, dimensions11: Dimensions11Payload | null) {
  const ruleDimensions = dimensions11Bars(dimensions11);
  if (ruleDimensions.length) return ruleDimensions;
  const assessmentDimensions = objectValue(assessment?.dimensions);
  if (Object.keys(assessmentDimensions).length) {
    return dimensionLabels.map((item) => {
      const dimension = objectValue(assessmentDimensions[item.key]);
      const value = clampScore(dimension.score);
      const status = textValue(dimension.status, value ? 'ready' : 'missing');
      return {
        ...item,
        value,
        source: textValue(dimension.source, item.source),
        pending: status === 'missing' || !value,
      };
    });
  }
  const summary = profile?.summary || {};
  const riskLevel = String(summary.risk_level || selected?.riskLabel || '').toLowerCase();
  const engagementRaw = safeNumber(summary.engagement_rate || selected?.raw.snapshot_engagement_rate);
  const engagementScore = engagementRaw ? Math.min(100, Math.round(engagementRaw * 10000)) : 0;
  return dimensionLabels.map((item) => {
    let value = 0;
    let pending = false;
    if (item.key === 'audience') value = clampScore(summary.audience_fit || selected?.raw.audience_fit);
    if (item.key === 'engagement') value = engagementScore;
    if (item.key === 'content') value = clampScore(summary.account_score || selected?.score);
    if (item.key === 'consistency') value = selected?.status && !selected.status.includes('error') ? 72 : 0;
    if (item.key === 'safety') value = riskLevel && !['low', 'none', ''].includes(riskLevel) ? 48 : riskLevel ? 82 : 0;
    if (item.key === 'professional') value = clampScore(summary.product_fit || selected?.raw.product_fit || selected?.score);
    if (item.key === 'growth' || item.key === 'authenticity') pending = true;
    return { ...item, value, pending: pending || !value };
  });
}

function apiContactsToItems(rows: Array<Record<string, unknown>> | undefined): ContactItem[] {
  return (rows || []).map((row, index) => ({
    id: textValue(row.id, `contact-${index}`),
    type: textValue(row.contact_type || row.type, 'Contact'),
    value: textValue(row.contact_value || row.value, ''),
    layer: safeNumber(row.layer || row.discovered_layer) || 1,
    source: textValue(row.source || row.discovery_source, 'contacts endpoint'),
    confidence: safeNumber(row.confidence),
    evidence: textValue(row.evidence, ''),
    verified: Boolean(row.verified || row.verified_at),
  })).filter((item) => item.value);
}

function contactsFromProfile(profile: VkpiKolProfile | null, selected: UiKol | undefined): ContactItem[] {
  const contacts = objectValue(profile?.contacts);
  const result: ContactItem[] = [];
  const email = textValue(contacts.email || selected?.contactEmail, '');
  const phone = textValue(contacts.phone || selected?.contactPhone, '');
  if (email) result.push({ id: 'email', type: 'Email', value: email, layer: 1, source: 'profile_business_email', confidence: 95, verified: true });
  if (phone) result.push({ id: 'phone', type: 'Phone / WhatsApp', value: phone, layer: 1, source: 'profile_contact_phone', confidence: 80 });
  const links = [
    ...contactLinksFrom(contacts.links),
    ...contactLinksFrom(selected?.raw.contact_links_json),
    ...contactLinksFrom(selected?.raw.contact_links),
  ];
  links.forEach((link, index) => {
    result.push({
      id: `link-${index}`,
      type: link.label || 'Link',
      value: link.value || link.url || '',
      layer: 1,
      source: 'profile_contact_links',
      confidence: 70,
      evidence: link.url,
    });
  });
  return result.filter((item, index, all) => item.value && all.findIndex((candidate) => candidate.value === item.value) === index);
}

function profileProjects(profile: VkpiKolProfile | null, data: VkpiDashboardData, selected: UiKol | undefined): VkpiProjectRow[] {
  if (!selected) return [];
  const direct = data.projects.filter((project) => project.kolId && String(project.kolId) === selected.id);
  if (direct.length) return direct;
  const handle = selected.handle.replace(/^@/, '').toLowerCase();
  return data.projects.filter((project) => [project.kolHandle, project.kolName].join(' ').toLowerCase().includes(handle)).slice(0, 5);
}

function recentPosts(profile: VkpiKolProfile | null, fallbackPosts: Array<Record<string, unknown>>, selected?: UiKol) {
  const rows = [
    ...arrayValue(profile?.posts).map(objectValue),
    ...arrayValue(profile?.content_posts).map(objectValue),
    ...fallbackPosts,
    ...(selected && isCandidateKol(selected) ? candidatePostsFromRaw(selected.raw) : []),
  ];
  return rows.filter((row, index) => row.id || index < 6).slice(0, 6);
}

function jsonObjectValue(value: unknown): Record<string, unknown> {
  if (value && typeof value === 'object' && !Array.isArray(value)) return value as Record<string, unknown>;
  try {
    const parsed = JSON.parse(String(value || '{}'));
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

function recommendationToSmart(row: Record<string, unknown>, index: number): SmartRecommendation {
  const explanation = jsonObjectValue(row.explanation_json);
  const scoringBreakdown = jsonObjectValue(row.scoring_breakdown_json);
  const competitor = objectValue(explanation.competitor || scoringBreakdown.competitor);
  const strengths = arrayValue(explanation.strengths).map((item) => String(item || '').trim()).filter(Boolean);
  const concerns = arrayValue(explanation.concerns).map((item) => String(item || '').trim()).filter(Boolean);
  const score = clampScore(row.score);
  const handle = normalizeHandle(row.handle || row.display_name || `recommendation-${row.id || index + 1}`);
  const reasonParts = [
    strengths[0],
    concerns.length ? `注意：${concerns[0]}` : '',
    textValue(row.status, ''),
  ].filter(Boolean);
  return {
    id: textValue(row.id || row.recommendation_id || row.recommendation_uid, `rec-${index + 1}`),
    kolId: textValue(row.linked_main_kol_id || row.kol_id, ''),
    handle,
    displayName: textValue(row.display_name || row.handle, handle),
    platform: textValue(row.platform, '-'),
    score,
    rank: safeNumber(row.rank) || index + 1,
    status: textValue(row.status, 'recommended'),
    reason: reasonParts.join(' · ') || '规则推荐已生成；解释证据可在后端 evidence 接口继续展开。',
    source: textValue(row.recommendation_uid || row.kol_pool_id || row.run_id, 'product-analysis'),
    competitorRiskTier: textValue(competitor.risk_tier, ''),
    competitorRiskScore: safeNumber(competitor.risk_score),
    competitorBrand: textValue(competitor.brand, ''),
    raw: row,
  };
}

function recommendationToUiKol(row: SmartRecommendation): UiKol {
  return rawToUiKol({
    ...row.raw,
    id: row.kolId || row.handle,
    linked_main_kol_id: row.kolId || undefined,
    channel_name: row.handle.replace(/^@/, ''),
    media_name: row.displayName,
    handle: row.handle,
    platform: row.platform,
    score: row.score,
  });
}

function rankedValues(values: string[]): string[] {
  const counts = new Map<string, number>();
  values.map((value) => value.trim()).filter(Boolean).forEach((value) => {
    counts.set(value, (counts.get(value) || 0) + 1);
  });
  return Array.from(counts.entries()).sort((a, b) => b[1] - a[1]).map(([value]) => value);
}

function addDirection(chips: DirectionChip[], chip: DirectionChip) {
  if (chips.some((item) => item.label === chip.label || item.query === chip.query)) return;
  chips.push(chip);
}

function buildDirectionChips(kols: UiKol[], productLaunches: VkpiDashboardData['productLaunches']): DirectionChip[] {
  const chips: DirectionChip[] = [];
  const products = productLaunches
    .map((product) => cleanProductLabel(product.productName || product.productSku || product.launchName))
    .filter(Boolean);
  const topics = rankedValues(kols.flatMap((kol) => kol.topic.split(/[\/,，;；|]/)).filter((topic) => !['待归类', '已建档红人'].includes(topic.trim())));
  const countries = rankedValues(kols.map((kol) => kol.country).filter((country) => country !== '-'));
  const platforms = rankedValues(kols.map((kol) => platformLabels[kol.platform] || kol.platform));
  const withContacts = kols.filter((kol) => kol.contactCount > 0).length;
  const withCollab = kols.filter((kol) => kol.hasCollaboration).length;
  const highScore = kols.filter((kol) => kol.score >= 80).length;

  if (products[0]) {
    addDirection(chips, {
      label: `${products[0]} 匹配方向`,
      query: `${products[0]} review portrait street`,
      source: '来自产品上市数据',
    });
  }
  if (topics[0]) {
    addDirection(chips, {
      label: `${topics[0]} 内容补人`,
      query: `找${countries[0] || ''}${topics[0]}${platforms[0] ? ` ${platforms[0]}` : ''} 中腰部红人`,
      source: '来自当前红人主题分布',
    });
  }
  if (countries[0] && platforms[0]) {
    addDirection(chips, {
      label: `${countries[0]} ${platforms[0]} 增量`,
      query: `${countries[0]} ${platforms[0]} 测评 街拍 portrait`,
      source: '来自地区和平台占比',
    });
  }
  if (withContacts) {
    addDirection(chips, {
      label: `优先可联系 ${withContacts}`,
      query: '有联系方式 高评分 可合作 红人',
      source: '来自联系方式完整度',
    });
  }
  if (withCollab || highScore) {
    addDirection(chips, {
      label: `高分复用 ${withCollab || highScore}`,
      query: '历史合作 高评分 ROI 可复用',
      source: '来自合作和评分信号',
    });
  }
  if (products[1]) {
    addDirection(chips, {
      label: `${products[1]} 新市场`,
      query: `${products[1]} YouTube Instagram 新市场`,
      source: '来自产品排期数据',
    });
  }

  if (!chips.length) {
    return [
      { label: '新品上市匹配方向', query: '找新品上市可合作红人', source: '等待真实数据后自动替换' },
      { label: '内容缺口补人方向', query: '找测评 街拍 portrait 中腰部红人', source: '等待真实数据后自动替换' },
      { label: '竞品对比方向', query: 'Sigma Tamron 对比内容 红人', source: '等待真实数据后自动替换' },
      { label: '可联系优先方向', query: '有联系方式 可合作 高评分红人', source: '等待真实数据后自动替换' },
    ];
  }

  return chips.slice(0, 5);
}

export function DiscoverPage({ data, onLookupKol, onScanKolAccount, onClaimKol, onUpdateKol, onCreateProject, apiToken }: DiscoverPageProps) {
  const baseKols = useMemo(() => data.kolOptions.map(kolOptionToUiKol), [data.kolOptions]);
  const [activeTab, setActiveTab] = useState<DiscoverTab>('search');
  const [query, setQuery] = useState('');
  const [localQuery, setLocalQuery] = useState('');
  const [platform, setPlatform] = useState('youtube');
  const [createIfMissing, setCreateIfMissing] = useState(true);
  const [scanAccount, setScanAccount] = useState(true);
  const [filters, setFilters] = useState({ platform: 'all', level: 'all', grade: 'all', collab: 'all', risk: 'all', freshness: 'all' });
  const [searchKols, setSearchKols] = useState<UiKol[]>([]);
  const [activeSearchQuery, setActiveSearchQuery] = useState('');
  const [selectedKolId, setSelectedKolId] = useState('');
  const [lookupResult, setLookupResult] = useState<VkpiKolLookupResult | null>(null);
  const [selectedProfile, setSelectedProfile] = useState<VkpiKolProfile | null>(null);
  const [selectedAssessment, setSelectedAssessment] = useState<VkpiKolAssessmentResponse | null>(null);
  const [selectedProductFits, setSelectedProductFits] = useState<ProductFitItem[]>([]);
  const [selectedContacts, setSelectedContacts] = useState<ContactItem[]>([]);
  const [selectedCompetitors, setSelectedCompetitors] = useState<CompetitorRelation[]>([]);
  const [competitorLoading, setCompetitorLoading] = useState(false);
  const [selectedDimensions11, setSelectedDimensions11] = useState<Dimensions11Payload | null>(null);
  const [dimensions11Loading, setDimensions11Loading] = useState(false);
  const [smartRecommendations, setSmartRecommendations] = useState<SmartRecommendation[]>([]);
  const [recommendationLoading, setRecommendationLoading] = useState(false);
  const [recommendationMessage, setRecommendationMessage] = useState('');
  const [profilePosts, setProfilePosts] = useState<Array<Record<string, unknown>>>([]);
  const [profileLoading, setProfileLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [scanBusy, setScanBusy] = useState(false);
  const [searchProgress, setSearchProgress] = useState<SearchProgressState>(idleSearchProgress);
  const [searchHistory, setSearchHistory] = useState<SearchHistoryItem[]>(loadSearchHistory);
  const [message, setMessage] = useState('');
  const [messageTone, setMessageTone] = useState<MessageTone>('info');
  const [contactModalOpen, setContactModalOpen] = useState(false);
  const [projectModalOpen, setProjectModalOpen] = useState(false);
  const [contactType, setContactType] = useState('email');
  const [contactValue, setContactValue] = useState('');
  const [contactEvidence, setContactEvidence] = useState('');
  const [projectProductSku, setProjectProductSku] = useState('');
  const [projectNote, setProjectNote] = useState('');
  const [internalNote, setInternalNote] = useState('');
  const searchRunRef = useRef(0);

  const combinedKols = useMemo(() => {
    const map = new Map<string, UiKol>();
    [...searchKols, ...baseKols].forEach((kol) => {
      if (kol.id) map.set(kol.id, kol);
    });
    return Array.from(map.values());
  }, [baseKols, searchKols]);

  const searchOnly = Boolean(activeSearchQuery || searchProgress.visible);
  const displayedKols = useMemo(() => searchOnly ? searchKols : combinedKols, [combinedKols, searchKols, searchOnly]);
  const visibleKols = useMemo(() => filterKols(displayedKols, filters, localQuery), [displayedKols, filters, localQuery]);
  const selectedKol = useMemo(
    () => displayedKols.find((kol) => kol.id === selectedKolId) || visibleKols[0] || (searchOnly ? undefined : combinedKols[0]),
    [combinedKols, displayedKols, searchOnly, selectedKolId, visibleKols],
  );
  const selectedProjects = useMemo(() => profileProjects(selectedProfile, data, selectedKol), [data, selectedKol, selectedProfile]);
  const contacts = useMemo(() => selectedContacts.length ? selectedContacts : contactsFromProfile(selectedProfile, selectedKol), [selectedContacts, selectedProfile, selectedKol]);
  const dimensions = useMemo(() => dimensionsFromProfile(selectedProfile, selectedKol, selectedAssessment, selectedDimensions11), [selectedAssessment, selectedDimensions11, selectedProfile, selectedKol]);
  const posts = useMemo(() => recentPosts(selectedProfile, profilePosts, selectedKol), [profilePosts, selectedKol, selectedProfile]);
  const localCandidateRecommendations = useMemo(() => visibleKols.filter((kol) => kol.score >= 65 || kol.contactCount || kol.hasCollaboration).slice(0, 8), [visibleKols]);
  const selectedKolPoolId = useMemo(() => kolPoolIdForCompetitors(selectedKol), [selectedKol]);

  useEffect(() => {
    if (!selectedKolId && visibleKols[0]?.id) setSelectedKolId(visibleKols[0].id);
  }, [selectedKolId, visibleKols]);

  useEffect(() => {
    if (!selectedKol?.id || !apiToken) {
      setSelectedProfile(null);
      setSelectedAssessment(null);
      setSelectedProductFits([]);
      setSelectedContacts([]);
      setProfilePosts([]);
      return;
    }
    if (isPlatformSearchCandidate(selectedKol)) {
      setSelectedProfile(null);
      setSelectedAssessment(null);
      setSelectedProductFits([]);
      setSelectedContacts([]);
      setProfilePosts([]);
      setProfileLoading(false);
      return;
    }
    let cancelled = false;
    setProfileLoading(true);
    void Promise.allSettled([
      getKolProfile(apiToken, selectedKol.id),
      getKolPosts(apiToken, selectedKol.id, { limit: 50 }),
      getKolAssessment(apiToken, selectedKol.id),
      getKolProductFit(apiToken, selectedKol.id, 5),
      listKolContacts(apiToken, selectedKol.id),
    ]).then(([profileResult, postsResult, assessmentResult, productFitResult, contactsResult]) => {
      if (cancelled) return;
      if (profileResult.status === 'fulfilled') setSelectedProfile(profileResult.value);
      else setSelectedProfile(null);
      if (postsResult.status === 'fulfilled') setProfilePosts(postsResult.value.items || []);
      else setProfilePosts([]);
      if (assessmentResult.status === 'fulfilled') setSelectedAssessment(assessmentResult.value);
      else setSelectedAssessment(null);
      if (productFitResult.status === 'fulfilled') setSelectedProductFits(productFitResult.value.items || []);
      else setSelectedProductFits([]);
      if (contactsResult.status === 'fulfilled') setSelectedContacts(apiContactsToItems(contactsResult.value.contacts || []));
      else setSelectedContacts([]);
    }).finally(() => {
      if (!cancelled) setProfileLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [apiToken, selectedKol?.id]);

  useEffect(() => {
    if (!apiToken || !selectedKolPoolId) {
      setSelectedCompetitors([]);
      setCompetitorLoading(false);
      setSelectedDimensions11(null);
      setDimensions11Loading(false);
      return;
    }
    let cancelled = false;
    setCompetitorLoading(true);
    setDimensions11Loading(true);
    void getKolPoolCompetitors(apiToken, selectedKolPoolId)
      .then((result) => {
        if (cancelled) return;
        setSelectedCompetitors(arrayValue(result.relations).map(objectValue));
      })
      .catch(() => {
        if (!cancelled) setSelectedCompetitors([]);
      })
      .finally(() => {
        if (!cancelled) setCompetitorLoading(false);
      });
    void getKolPoolDimensions11(apiToken, selectedKolPoolId)
      .then((result) => {
        if (cancelled) return;
        setSelectedDimensions11(objectValue(result));
      })
      .catch(() => {
        if (!cancelled) setSelectedDimensions11(null);
      })
      .finally(() => {
        if (!cancelled) setDimensions11Loading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken, selectedKolPoolId]);

  const setNotice = (text: string, tone: MessageTone = 'info') => {
    setMessage(text);
    setMessageTone(tone);
  };

  const revealSearchKols = async (items: UiKol[], runId: number) => {
    if (!items.length || searchRunRef.current !== runId) return;
    setSearchKols([items[0]]);
    setSelectedKolId(items[0].id);
    for (let index = 1; index < items.length; index += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, index < 8 ? 80 : 45));
      if (searchRunRef.current !== runId) return;
      setSearchKols(items.slice(0, index + 1));
    }
  };

  const loadKols = async (searchText: string, platformFilter = filters.platform): Promise<number> => {
    if (!apiToken) {
      setNotice('当前未登录，列表使用已加载的本地真实数据。', 'warn');
      return 0;
    }
    const result = await listMarketingKols(apiToken, {
      search: searchText || undefined,
      platform: platformFilter !== 'all' ? platformFilter : undefined,
      limit: 100,
    });
    const rows = (result.kols || []).map(rawToUiKol);
    setSearchKols(rows);
    return rows.length;
  };

  const runNaturalSearch = async (searchText: string, platformFilter = filters.platform): Promise<number> => {
    const clean = searchText.trim();
    if (!apiToken) {
      const count = await loadKols(clean, platformFilter);
      setLocalQuery(clean);
      return count;
    }
    const result = await searchMarketingKolsNatural(apiToken, {
      query: clean,
      platform: platformFilter !== 'all' ? platformFilter : undefined,
      limit: 100,
    });
    const nextKols = (result.items || []).map(rawToUiKol);
    setSearchKols(nextKols);
    setLocalQuery('');
    if (nextKols[0]?.id) setSelectedKolId(nextKols[0].id);
    const parsed = objectValue(result.parsed);
    const parsedBits = [
      textValue(parsed.platform, ''),
      textValue(parsed.country, ''),
      textValue(parsed.level, ''),
      arrayValue(parsed.keywords).slice(0, 3).join('/'),
    ].filter(Boolean);
    setNotice(nextKols.length
      ? `已用真实规则解析搜索命中 ${nextKols.length} 个红人${parsedBits.length ? `：${parsedBits.join(' · ')}` : ''}。`
      : '规则解析搜索没有命中真实红人；没有伪造结果，可放宽关键词或先补候选池。', nextKols.length ? 'info' : 'warn');
    return nextKols.length;
  };

  const loadSmartRecommendations = async (silent = false) => {
    if (!apiToken) {
      setSmartRecommendations([]);
      setRecommendationMessage('当前未登录，无法读取 Product Analysis 推荐表。');
      return;
    }
    setRecommendationLoading(true);
    if (!silent) setRecommendationMessage('');
    try {
      const result = await listProductRecommendations(apiToken, { limit: 80 });
      const rows = (result.recommendations || []).map(recommendationToSmart);
      setSmartRecommendations(rows);
      setRecommendationMessage(rows.length ? `已读取 ${rows.length} 条真实推荐。` : '推荐表暂无记录，可用“生成推荐”从 KOL Pool 跑一次规则推荐。');
    } catch (error) {
      setRecommendationMessage(error instanceof Error ? error.message : '推荐列表读取失败');
    } finally {
      setRecommendationLoading(false);
    }
  };

  useEffect(() => {
    if (activeTab === 'recommendations') void loadSmartRecommendations(true);
  }, [activeTab, apiToken]);

  const regenerateSmartRecommendations = async () => {
    if (!apiToken) {
      setRecommendationMessage('当前未登录，无法生成推荐。');
      return;
    }
    setRecommendationLoading(true);
    setRecommendationMessage('正在调用 Product Analysis 规则推荐器。');
    try {
      const result = await runProductRecommendations(apiToken, { limit: 80 });
      const generated = arrayValue(result.recommendations).length;
      await loadSmartRecommendations(true);
      setRecommendationMessage(generated ? `已生成 ${generated} 条真实推荐。` : '推荐器已运行，但 KOL Pool 暂无可推荐候选。');
    } catch (error) {
      setRecommendationMessage(error instanceof Error ? error.message : '推荐生成失败');
    } finally {
      setRecommendationLoading(false);
    }
  };

  const handleRecommendationAction = async (recommendation: SmartRecommendation, action: RecommendationAction) => {
    if (!apiToken) return;
    setRecommendationLoading(true);
    try {
      const result = await productRecommendationAction(apiToken, recommendation.id, action, { source: 'discover_page' });
      const kol = objectValue(result.kol);
      if (kol.id) {
        const next = rawToUiKol(kol);
        setSearchKols((previous) => [next, ...previous.filter((item) => item.id !== next.id)]);
        setSelectedKolId(next.id);
      } else if (recommendation.kolId) {
        const next = recommendationToUiKol(recommendation);
        setSearchKols((previous) => [next, ...previous.filter((item) => item.id !== next.id)]);
        setSelectedKolId(next.id);
      }
      await loadSmartRecommendations(true);
      setNotice(action === 'reject' ? '已记录推荐拒绝。' : action === 'claim' ? '已通过推荐 action 建档/认领。' : '已将推荐标记为入选。');
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '推荐 action 失败', 'error');
    } finally {
      setRecommendationLoading(false);
    }
  };

  const recordSearchHistory = (entry: Omit<SearchHistoryItem, 'id' | 'searchedAt'>) => {
    const cleanQuery = entry.query.trim();
    if (!cleanQuery) return;
    const normalizedPlatform = entry.platform || 'all';
    const nextItem: SearchHistoryItem = {
      ...entry,
      id: `${normalizedPlatform}:${cleanQuery.toLowerCase()}`,
      query: cleanQuery,
      platform: normalizedPlatform,
      searchedAt: new Date().toISOString(),
    };
    setSearchHistory((previous) => {
      const next = [nextItem, ...previous.filter((item) => item.id !== nextItem.id)].slice(0, MAX_SEARCH_HISTORY);
      saveSearchHistory(next);
      return next;
    });
  };

  const runSearch = async (termInput = query, platformInput = platform) => {
    const term = termInput.trim();
    const selectedPlatform = platformInput || platform;
    setQuery(term);
    setPlatform(selectedPlatform);
    if (!term) {
      setActiveSearchQuery('');
      setSearchProgress(idleSearchProgress);
      await loadKols('', filters.platform);
      setNotice('已刷新红人列表。');
      return;
    }
    const looksLikeHandle = term.startsWith('@') || term.includes('instagram.com') || term.includes('youtube.com') || term.includes('tiktok.com') || term.includes('/');
    const shouldUsePlatformSearch = !looksLikeHandle && selectedPlatform !== 'all' && scanAccount && Boolean(apiToken);
    const instantCandidate = shouldUsePlatformSearch ? null : instantCandidateToUiKol(term, selectedPlatform);
    const runId = searchRunRef.current + 1;
    searchRunRef.current = runId;
    setActiveTab('search');
    setActiveSearchQuery(term);
    setLocalQuery('');
    if (instantCandidate) {
      setSearchKols((previous) => [instantCandidate, ...previous.filter((kol) => kol.id !== instantCandidate.id)]);
      setSelectedKolId(instantCandidate.id);
      setSearchProgress(searchProgressState('已生成账号候选，正在进入真实数据源。', 18, 'source', ['candidate']));
    } else {
      setSearchKols([]);
      setSelectedKolId('');
      setSearchProgress(searchProgressState(`正在按「${term}」推荐候选账号。`, 24, 'source', ['candidate']));
    }
    setBusy(true);
    setNotice('');
    try {
      if (shouldUsePlatformSearch && apiToken) {
        let warmKols: UiKol[] = [];
        setSearchProgress(searchProgressState('先检索已有 KOL 和 1012 历史合作池。', 30, 'source', ['candidate']));
        try {
          const warmResult = await searchMarketingKolsNatural(apiToken, {
            query: term,
            platform: selectedPlatform,
            limit: 30,
          });
          warmKols = (warmResult.items || []).map(rawToUiKol);
          if (warmKols.length && searchRunRef.current === runId) {
            setSearchKols(warmKols);
            setSelectedKolId(warmKols[0].id);
            setNotice(`先命中 ${warmKols.length} 个已有/历史档案；平台实时搜索继续补头像、最近内容和新候选。`);
          }
        } catch (error) {
          console.warn('warm natural search failed', error);
        }
        setSearchProgress(searchProgressState(
          warmKols.length ? '已有档案已先显示；平台实时搜索继续补新候选。' : '平台实时搜索中；结果会逐条变成候选卡片。',
          warmKols.length ? 48 : 38,
          'source',
          ['candidate'],
        ));
        if (!warmKols.length) setNotice('正在调用平台真实搜索；不会把输入文字伪造成账号。');
        const result = await searchPlatformKols(apiToken, { query: term, platform: selectedPlatform, maxResults: 25 });
        setSearchProgress(searchProgressState('平台已返回，正在整理头像、账号资料和样本内容。', 68, 'profile', ['candidate', 'source']));
        const candidateIds = Array.isArray(result.candidate_ids) ? result.candidate_ids : [];
        const nextKols = (result.items || []).map((item, index) => platformSearchItemToUiKol(objectValue(item), index, candidateIds[index]));
        const mergedKols = mergeSearchKols(warmKols, nextKols);
        const hasCandidatePosts = mergedKols.some((kol) => candidatePostsFromRaw(kol.raw).length > 0);
        setLocalQuery('');
        if (!mergedKols.length) {
          setSearchKols([]);
          setSelectedKolId('');
          setSearchProgress(searchProgressState('平台没有返回候选；没有生成假账号，可换关键词或平台重试。', 100, 'decision', ['candidate', 'source', 'profile', 'decision']));
          setNotice(result.message || '平台真实搜索和历史池都没有返回候选；未用本地库伪造结果。', 'warn');
          recordSearchHistory({ query: term, platform: selectedPlatform, mode: 'platform_search', resultCount: 0, status: '无候选' });
          return;
        }
        setSearchProgress(searchProgressState('平台已返回，正在合并历史档案并逐条显示推荐候选。', hasCandidatePosts ? 84 : 76, 'posts', ['candidate', 'source', 'profile']));
        await revealSearchKols(mergedKols, runId);
        if (searchRunRef.current !== runId) return;
        setSearchProgress(searchProgressState(
          hasCandidatePosts ? '已回填候选和最近内容；可以选择账号后建档或深扫。' : '已返回候选；最近内容需要建档抓取后补齐。',
          hasCandidatePosts ? 100 : 82,
          hasCandidatePosts ? 'decision' : 'posts',
          hasCandidatePosts ? ['candidate', 'source', 'profile', 'posts', 'decision'] : ['candidate', 'source', 'profile'],
        ));
        const historyCount = warmKols.length;
        const message = result.message || `已合并 ${historyCount} 个历史/已有档案 + ${nextKols.length} 个平台实时候选；历史合作会优先标出。`;
        setNotice(message, mergedKols.length ? 'info' : 'warn');
        recordSearchHistory({ query: term, platform: selectedPlatform, mode: 'platform_search', resultCount: mergedKols.length, status: hasCandidatePosts ? '含最近内容' : (historyCount ? '含历史档案' : '候选已返回') });
      } else if (looksLikeHandle && onLookupKol) {
        setSearchProgress(searchProgressState('正在查重或建档账号，先保留即时候选。', 45, 'profile', ['candidate', 'source']));
        const result = await onLookupKol({
          platform: selectedPlatform,
          handleOrUrl: term,
          createIfMissing,
          scanAccount: false,
          maxPosts: 24,
        });
        setLookupResult(result || null);
        const found = result ? lookupToUiKol(result) : null;
        if (found) {
          setSearchKols((previous) => [found, ...previous.filter((kol) => kol.id !== found.id)]);
          setSelectedKolId(found.id);
        }
        const kolId = found?.id || String(result?.kol?.id || '');
        if (scanAccount && kolId && onScanKolAccount) {
          setBusy(false);
          setScanBusy(true);
          setSearchProgress(searchProgressState('账号已建档，正在抓取最近内容。', 76, 'posts', ['candidate', 'source', 'profile']));
          setNotice('查重完成，正在用真实接口抓取账号数据。');
          await onScanKolAccount(kolId, 24);
          if (onLookupKol) {
            const refreshed = await onLookupKol({ platform: selectedPlatform, handleOrUrl: term, createIfMissing: false, scanAccount: false, maxPosts: 24 });
            setLookupResult(refreshed || result);
            const refreshedKol = refreshed ? lookupToUiKol(refreshed) : null;
            if (refreshedKol) {
              setSearchKols((previous) => [refreshedKol, ...previous.filter((kol) => kol.id !== refreshedKol.id)]);
              setSelectedKolId(refreshedKol.id);
            }
          }
          setSearchProgress(searchProgressState('账号抓取完成；右侧画像和最近内容会刷新。', 100, 'decision', ['candidate', 'source', 'profile', 'posts', 'decision']));
          setNotice('账号抓取完成；右侧画像会读取最新 profile / posts。');
          recordSearchHistory({ query: term, platform: selectedPlatform, mode: 'account_lookup', resultCount: 1, status: '已抓取' });
        } else {
          setSearchProgress(searchProgressState(
            found ? '已命中已有档案；可继续抓取或加入项目。' : '查重完成，但没有可展示档案。',
            found ? 100 : 72,
            found ? 'decision' : 'profile',
            found ? ['candidate', 'source', 'profile', 'decision'] : ['candidate', 'source'],
          ));
          setNotice(found ? '查重完成，已打开红人画像。' : '查重完成，但没有返回可展示的红人档案。', found ? 'info' : 'warn');
          recordSearchHistory({ query: term, platform: selectedPlatform, mode: 'account_lookup', resultCount: found ? 1 : 0, status: found ? '已命中' : '未命中' });
        }
      } else {
        setSearchProgress(searchProgressState('正在检索已有 KOL 档案。', 46, 'source', ['candidate']));
        const count = await runNaturalSearch(term, filters.platform);
        setSearchProgress(searchProgressState('已有档案检索完成；未命中时不会伪造数据。', 100, 'decision', ['candidate', 'source', 'profile', 'decision']));
        recordSearchHistory({ query: term, platform: filters.platform, mode: 'local_rules', resultCount: count, status: count ? '已有档案' : '未命中' });
      }
    } catch (error) {
      setSearchProgress(searchProgressState(
        instantCandidate ? '搜索失败；已保留账号候选，方便换词重试。' : '搜索失败；没有生成假账号，换关键词或平台重试。',
        100,
        'source',
        ['candidate'],
        'source',
      ));
      setNotice(error instanceof Error ? error.message : '红人搜索失败', 'error');
      recordSearchHistory({ query: term, platform: selectedPlatform, mode: shouldUsePlatformSearch ? 'platform_search' : looksLikeHandle ? 'account_lookup' : 'local_rules', resultCount: 0, status: '失败' });
    } finally {
      setBusy(false);
      setScanBusy(false);
    }
  };

  const handleSearch = async (event?: React.FormEvent) => {
    event?.preventDefault();
    await runSearch();
  };

  const rerunHistorySearch = (item: SearchHistoryItem) => {
    setActiveTab('search');
    void runSearch(item.query, item.platform);
  };

  const handleClaim = async () => {
    if (!selectedKol?.id || !onClaimKol) return;
    setBusy(true);
    try {
      await onClaimKol(selectedKol.id);
      setNotice('红人已绑定到当前员工账号。');
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '认领失败', 'error');
    } finally {
      setBusy(false);
    }
  };

  const handleDeepScan = async () => {
    if (!selectedKol?.id || !onScanKolAccount) return;
    setScanBusy(true);
    setNotice('正在运行真实深度抓取；UI 进度不伪造，完成后刷新画像。');
    try {
      await onScanKolAccount(selectedKol.id, 50);
      if (apiToken) {
        const [profileResult, assessmentResult, productFitResult, contactsResult] = await Promise.allSettled([
          getKolProfile(apiToken, selectedKol.id),
          getKolAssessment(apiToken, selectedKol.id),
          getKolProductFit(apiToken, selectedKol.id, 5),
          listKolContacts(apiToken, selectedKol.id),
        ]);
        if (profileResult.status === 'fulfilled') setSelectedProfile(profileResult.value);
        if (assessmentResult.status === 'fulfilled') setSelectedAssessment(assessmentResult.value);
        if (productFitResult.status === 'fulfilled') setSelectedProductFits(productFitResult.value.items || []);
        if (contactsResult.status === 'fulfilled') setSelectedContacts(apiContactsToItems(contactsResult.value.contacts || []));
      }
      setNotice('深度抓取完成。');
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '深度抓取失败', 'error');
    } finally {
      setScanBusy(false);
    }
  };

  const saveManualContact = async () => {
    if (!selectedKol?.id || !contactValue.trim()) return;
    const nextLink = { label: contactType, value: contactValue.trim(), url: contactValue.trim().startsWith('http') ? contactValue.trim() : undefined };
    setBusy(true);
    try {
      if (apiToken) {
        const result = await addKolContact(apiToken, selectedKol.id, {
          contactType,
          contactValue: contactValue.trim(),
          evidence: contactEvidence.trim() || undefined,
          layer: 5,
          source: 'manual_input',
        });
        setSelectedContacts(apiContactsToItems(result.contacts || []));
      } else if (onUpdateKol) {
        await onUpdateKol(selectedKol.id, {
          contactEmail: contactType === 'email' ? contactValue.trim() : undefined,
          contactPhone: contactType === 'phone' || contactType === 'whatsapp' ? contactValue.trim() : undefined,
          contactLinks: [nextLink],
          notes: contactEvidence.trim() || undefined,
        });
      } else {
        return;
      }
      setContactModalOpen(false);
      setContactValue('');
      setContactEvidence('');
      setNotice('联系方式已通过真实补录接口保存；当前使用现有 KOL 字段桥接 5 层漏斗。');
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '联系方式保存失败', 'error');
    } finally {
      setBusy(false);
    }
  };

  const createProjectForKol = async () => {
    if (!selectedKol?.id || !onCreateProject) return;
    setBusy(true);
    try {
      await onCreateProject({
        projectName: `${selectedKol.handle} · KOL 合作`,
        kolId: selectedKol.id,
        productSku: projectProductSku.trim() || undefined,
        platform: platformInputValue(selectedKol.platform),
        note: projectNote.trim() || '从红人决策中枢加入项目',
      });
      setProjectModalOpen(false);
      setProjectProductSku('');
      setProjectNote('');
      setNotice('已通过真实项目接口创建 KOL 项目。');
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '加入项目失败', 'error');
    } finally {
      setBusy(false);
    }
  };

  const heroMetrics = [
    { label: 'KOL 池', value: compactCount(combinedKols.length), hint: '当前可见真实档案' },
    { label: '今日推荐', value: smartRecommendations.length ? String(smartRecommendations.length) : '-', hint: 'Product Analysis 返回' },
    { label: '已分析内容', value: compactCount(posts.length || selectedKol?.contentCount || 0), hint: 'profile/posts 返回' },
    { label: '联系方式', value: compactCount(contacts.length), hint: 'Layer 1 真实字段' },
  ];
  const directionChips = useMemo(() => buildDirectionChips(combinedKols, data.productLaunches), [combinedKols, data.productLaunches]);

  return (
    <PageShell
      title="红人决策中枢"
      description="主动搜索、智能推荐、深度分析共用 kol_pool：搜 -> 查 -> 评 -> 决 -> 录。"
      eyebrow={null}
      headingExtra={(
        <div className="vkpi-discover-heading-metrics">
          {heroMetrics.map((metric) => (
            <div className="vkpi-discover-metric" key={metric.label}>
              <strong>{metric.value}</strong>
              <span>{metric.label}</span>
            </div>
          ))}
        </div>
      )}
    >
      <div className="vkpi-discover-v2">
        {message ? <div className={`vkpi-discover-notice is-${messageTone}`}>{message}</div> : null}

        <div className="vkpi-discover-intent-chips" aria-label="数据搜索方向">
          <span className="vkpi-discover-intent-chips__label">数据方向</span>
          {directionChips.map((chip) => (
	            <button
	              key={chip.label}
	              type="button"
	              title={chip.source}
	              onClick={() => {
	                setQuery(chip.query);
	                void runSearch(chip.query, platform);
	              }}
	            >
              {chip.label}
            </button>
          ))}
        </div>

        <nav className="vkpi-discover-tabs" aria-label="红人搜索工作区">
          {tabLabels.map((tab) => (
            <button key={tab.key} type="button" className={activeTab === tab.key ? 'is-active' : ''} onClick={() => setActiveTab(tab.key)}>
              <strong>{tab.label}</strong>
              <span>{tab.hint}</span>
            </button>
          ))}
        </nav>

        <section className="vkpi-discover-command">
          <form className="vkpi-discover-command__search" onSubmit={(event) => void handleSearch(event)}>
            <span>⌘K</span>
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="粘贴 URL / @handle / 关键词，例如：美国 35mm 街拍 IG 红人" />
            <select value={platform} onChange={(event) => setPlatform(event.target.value)} aria-label="搜索平台">
              <option value="all">全部平台</option>
              {creatorPlatformOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
            <button className="vkpi-discover-btn is-primary" type="submit" disabled={busy || scanBusy || (!apiToken && !onLookupKol)}>
              {busy || scanBusy ? '处理中' : '搜索'}
            </button>
          </form>
          <div className="vkpi-discover-command__actions">
            <label><input type="checkbox" checked={createIfMissing} onChange={(event) => setCreateIfMissing(event.target.checked)} /> 自动建档</label>
            <label><input type="checkbox" checked={scanAccount} onChange={(event) => setScanAccount(event.target.checked)} /> 抓取账号</label>
	            <button
	              className="vkpi-discover-btn"
	              type="button"
	              onClick={() => {
	                setActiveSearchQuery('');
	                setSearchProgress(idleSearchProgress);
	                void loadKols('', filters.platform);
	              }}
	              disabled={!apiToken || busy}
	            >
	              刷新
	            </button>
          </div>
        </section>

        <SearchProgress progress={searchProgress} />

        {activeTab === 'pool' ? (
          <KolPoolPanel
            apiToken={apiToken || ''}
            onListPool={(params) => {
              if (!apiToken) return Promise.reject(new Error('未登录'));
              return listKolPool(apiToken, params);
            }}
            onGetItem={(kolPoolId) => {
              if (!apiToken) return Promise.reject(new Error('未登录'));
              return getKolPoolItem(apiToken, kolPoolId);
            }}
            onEnrichItem={(kolPoolId, maxPosts) => {
              if (!apiToken) return Promise.reject(new Error('未登录'));
              return enrichKolPoolItem(apiToken, kolPoolId, maxPosts);
            }}
            onBatchEnrich={(payload) => {
              if (!apiToken) return Promise.reject(new Error('未登录'));
              return batchEnrichKolPool(apiToken, payload);
            }}
            onPromoteToMain={(kolPoolId) => {
              if (!apiToken) return Promise.reject(new Error('未登录'));
              return promoteKolPoolToMain(apiToken, kolPoolId);
            }}
            onOpenImport={() => setActiveTab('search')}
          />
        ) : (
          <section className="vkpi-discover-workgrid">
            <div className="vkpi-discover-left">
              {activeTab === 'search' ? (
                <SearchPanel
                  localQuery={localQuery}
                  setLocalQuery={setLocalQuery}
                  filters={filters}
                  setFilters={setFilters}
	                  visibleKols={visibleKols}
	                  selectedKolId={selectedKol?.id || ''}
	                  searchProgress={searchProgress}
	                  searchHistory={searchHistory}
	                  onSelect={setSelectedKolId}
	                  onHistorySelect={rerunHistorySearch}
	                />
              ) : (
                <RecommendationPanel
                  recommendations={smartRecommendations}
                  loading={recommendationLoading}
                  message={recommendationMessage}
                  localFallbackCount={localCandidateRecommendations.length}
                  selectedKolId={selectedKol?.id || ''}
                  onSelect={(recommendation) => {
                    if (!recommendation.kolId) {
                      setNotice('这条推荐还没有 linked_main_kol_id；请先点“认领”把它接入主 KOL 表。', 'warn');
                      return;
                    }
                    const next = recommendationToUiKol(recommendation);
                    setSearchKols((previous) => [next, ...previous.filter((item) => item.id !== next.id)]);
                    setSelectedKolId(next.id);
                    setActiveTab('search');
                  }}
                  onRefresh={() => void loadSmartRecommendations()}
                  onRegenerate={() => void regenerateSmartRecommendations()}
                  onAction={(recommendation, action) => void handleRecommendationAction(recommendation, action)}
                />
              )}
            </div>

            <aside className="vkpi-discover-profile">
              <ProfilePanel
                selectedKol={selectedKol}
                selectedProfile={selectedProfile}
                selectedAssessment={selectedAssessment}
                selectedDimensions11={selectedDimensions11}
                lookupResult={lookupResult}
                contacts={contacts}
                dimensions={dimensions}
                competitorRelations={selectedCompetitors}
                competitorLoading={competitorLoading}
                dimensions11Loading={dimensions11Loading}
                productFits={selectedProductFits}
                posts={posts}
                projects={selectedProjects}
                productLaunches={data.productLaunches}
                profileLoading={profileLoading}
                scanBusy={scanBusy}
                busy={busy}
                internalNote={internalNote}
                setInternalNote={setInternalNote}
                onClaim={handleClaim}
                onDeepScan={handleDeepScan}
                onOpenContact={() => setContactModalOpen(true)}
                onOpenProject={() => setProjectModalOpen(true)}
                canClaim={Boolean(onClaimKol && selectedKol && !selectedKol.claimOwner && !isCandidateKol(selectedKol))}
                canScan={Boolean(onScanKolAccount && selectedKol && !isCandidateKol(selectedKol))}
                canUpdate={Boolean((apiToken || onUpdateKol) && selectedKol && !isCandidateKol(selectedKol))}
                canCreateProject={Boolean(onCreateProject && selectedKol && !isCandidateKol(selectedKol))}
              />
            </aside>
          </section>
        )}
      </div>

      {contactModalOpen ? (
        <div className="vkpi-discover-modal" role="dialog" aria-modal="true" aria-label="手动添加联系方式">
          <div className="vkpi-discover-modal__box">
            <h3>手动添加联系方式</h3>
            <p>UI 预留 5 层漏斗；当前保存仍走现有红人补录接口。</p>
            <label>类型<select value={contactType} onChange={(event) => setContactType(event.target.value)}>
              <option value="email">Email</option>
              <option value="phone">Phone</option>
              <option value="whatsapp">WhatsApp</option>
              <option value="manager_email">经纪人 Email</option>
              <option value="linkedin">LinkedIn</option>
            </select></label>
            <label>联系方式<input value={contactValue} onChange={(event) => setContactValue(event.target.value)} placeholder="business@example.com" /></label>
            <label>证据 / 备注<textarea value={contactEvidence} onChange={(event) => setContactEvidence(event.target.value)} placeholder="人工从官网 / DM / 名片确认" /></label>
            <div className="vkpi-discover-modal__actions">
              <button className="vkpi-discover-btn" type="button" onClick={() => setContactModalOpen(false)}>取消</button>
              <button className="vkpi-discover-btn is-primary" type="button" onClick={() => void saveManualContact()} disabled={busy || !contactValue.trim() || !(apiToken || onUpdateKol)}>保存</button>
            </div>
          </div>
        </div>
      ) : null}

      {projectModalOpen ? (
        <div className="vkpi-discover-modal" role="dialog" aria-modal="true" aria-label="加入推广项目">
          <div className="vkpi-discover-modal__box">
            <h3>加入推广项目</h3>
            <p>当前先用现有项目创建接口建立 KOL 项目；Campaign 聚合保持由「我的项目」页面负责。</p>
            <label>产品 SKU<input value={projectProductSku} onChange={(event) => setProjectProductSku(event.target.value)} placeholder="AF 35mm F1.2 LAB FE" /></label>
            <label>备注<textarea value={projectNote} onChange={(event) => setProjectNote(event.target.value)} placeholder="从红人决策中枢加入项目" /></label>
            <div className="vkpi-discover-modal__actions">
              <button className="vkpi-discover-btn" type="button" onClick={() => setProjectModalOpen(false)}>取消</button>
              <button className="vkpi-discover-btn is-primary" type="button" onClick={() => void createProjectForKol()} disabled={busy || !onCreateProject}>创建项目</button>
            </div>
          </div>
        </div>
      ) : null}
    </PageShell>
  );
}

function SearchProgress({ progress }: { progress: SearchProgressState }) {
  if (!progress.visible) return null;
  return (
    <section className="vkpi-discover-search-progress" aria-live="polite">
      <div className="vkpi-discover-search-progress__head">
        <strong>{progress.title}</strong>
        <span>{progress.percent}%</span>
      </div>
      <div className="vkpi-discover-search-progress__bar"><i style={{ width: `${progress.percent}%` }} /></div>
      <div className="vkpi-discover-search-progress__steps">
        {progress.steps.map((step) => (
          <span className={`is-${step.status}`} key={step.key} title={step.detail}>
            <b>{step.label}</b>
            <em>{step.detail}</em>
          </span>
        ))}
      </div>
    </section>
  );
}

function DiscoverCandidateSkeletons({ count = 5 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }).map((_, index) => (
        <div className="vkpi-discover-kol vkpi-discover-kol--skeleton" key={index} aria-hidden="true">
          <span className="vkpi-skeleton vkpi-skeleton-avatar is-round" />
          <div className="vkpi-discover-kol__main">
            <span className="vkpi-skeleton vkpi-skeleton-line is-medium" />
            <span className="vkpi-skeleton vkpi-skeleton-line is-long" />
            <div>
              <span className="vkpi-skeleton vkpi-skeleton-pill" />
              <span className="vkpi-skeleton vkpi-skeleton-pill" />
              <span className="vkpi-skeleton vkpi-skeleton-pill" />
            </div>
          </div>
          <div className="vkpi-discover-kol__score">
            <span className="vkpi-skeleton vkpi-skeleton-line is-short" />
            <span className="vkpi-skeleton vkpi-skeleton-pill" />
          </div>
        </div>
      ))}
    </>
  );
}

function DiscoverRecommendationSkeletons() {
  return (
    <>
      {[0, 1, 2].map((item) => (
        <div className="vkpi-discover-rec vkpi-discover-rec--skeleton" key={item} aria-hidden="true">
          <div>
            <span className="vkpi-skeleton vkpi-skeleton-line is-medium" />
            <span className="vkpi-skeleton vkpi-skeleton-line is-long" />
            <span className="vkpi-skeleton vkpi-skeleton-line is-long" />
            <div className="vkpi-discover-rec__actions">
              <span className="vkpi-skeleton vkpi-skeleton-pill" />
              <span className="vkpi-skeleton vkpi-skeleton-pill" />
              <span className="vkpi-skeleton vkpi-skeleton-pill" />
            </div>
          </div>
          <span className="vkpi-skeleton vkpi-skeleton-line is-short" />
        </div>
      ))}
    </>
  );
}

function SearchPanel({
  localQuery,
  setLocalQuery,
  filters,
  setFilters,
  visibleKols,
  selectedKolId,
  searchProgress,
  searchHistory,
  onSelect,
  onHistorySelect,
}: {
  localQuery: string;
  setLocalQuery: (value: string) => void;
  filters: { platform: string; level: string; grade: string; collab: string; risk: string; freshness: string };
  setFilters: React.Dispatch<React.SetStateAction<{ platform: string; level: string; grade: string; collab: string; risk: string; freshness: string }>>;
  visibleKols: UiKol[];
  selectedKolId: string;
  searchProgress: SearchProgressState;
  searchHistory: SearchHistoryItem[];
  onSelect: (kolId: string) => void;
  onHistorySelect: (item: SearchHistoryItem) => void;
}) {
  const setFilter = (key: keyof typeof filters, value: string) => setFilters((previous) => ({ ...previous, [key]: value }));
  const runningSearch = searchProgress.visible && searchProgress.percent < 100;
  return (
    <section className="vkpi-discover-panel">
      <div className="vkpi-discover-panel__header">
        <div>
          <h3>主动搜索结果</h3>
          <span>{runningSearch ? '平台搜索运行中，候选会逐条出现。' : `${visibleKols.length} 个档案/候选；平台搜索结果需建档后才有完整画像`}</span>
        </div>
      </div>
      {searchHistory.length ? (
        <div className="vkpi-discover-search-history" aria-label="搜索历史">
          <div className="vkpi-discover-search-history__head">
            <strong>搜索历史</strong>
            <span>点一下复搜</span>
          </div>
          <div className="vkpi-discover-search-history__items">
            {searchHistory.slice(0, 6).map((item) => (
              <button key={item.id} type="button" onClick={() => onHistorySelect(item)} title={item.query}>
                <b>{item.query}</b>
                <span>{searchHistoryPlatformLabel(item.platform)} · {item.resultCount} 条 · {formatHistoryTime(item.searchedAt)}</span>
              </button>
            ))}
          </div>
        </div>
      ) : null}
      <div className="vkpi-discover-searchbox">
        <input value={localQuery} onChange={(event) => setLocalQuery(event.target.value)} placeholder="在当前结果内筛选 handle / 国家 / 主题" />
        <div className="vkpi-discover-quick">
          {['35mm 街拍', 'YouTube Review', '美国', '联系方式'].map((item) => (
            <button key={item} type="button" onClick={() => setLocalQuery(item)}>{item}</button>
          ))}
        </div>
      </div>
      <div className="vkpi-discover-filters">
        <select value={filters.platform} onChange={(event) => setFilter('platform', event.target.value)} aria-label="平台筛选">
          <option value="all">全部平台</option>
          {creatorPlatformOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
        <select value={filters.level} onChange={(event) => setFilter('level', event.target.value)} aria-label="红人层级">
          <option value="all">全部层级</option>
          <option value="top">头部</option>
          <option value="mid">中腰部</option>
          <option value="tail">长尾</option>
        </select>
        <select value={filters.grade} onChange={(event) => setFilter('grade', event.target.value)} aria-label="评分">
          <option value="all">全部评分</option>
          {['S', 'A', 'B', 'C', 'D'].map((grade) => <option key={grade} value={grade}>Grade {grade}</option>)}
        </select>
        <select value={filters.collab} onChange={(event) => setFilter('collab', event.target.value)} aria-label="合作状态">
          <option value="all">合作状态</option>
          <option value="yes">合作过</option>
          <option value="no">未合作</option>
        </select>
        <select value={filters.risk} onChange={(event) => setFilter('risk', event.target.value)} aria-label="风险">
          <option value="all">风险状态</option>
          <option value="clean">无风险</option>
          <option value="has">有风险</option>
        </select>
        <select value={filters.freshness} onChange={(event) => setFilter('freshness', event.target.value)} aria-label="数据新鲜度">
          <option value="all">全部新鲜度</option>
          <option value="stale">需刷新</option>
        </select>
      </div>
      <div className="vkpi-discover-list">
        {visibleKols.length ? (
          <>
            {visibleKols.map((kol) => (
              <KolCard key={kol.id} kol={kol} active={selectedKolId === kol.id} onClick={() => onSelect(kol.id)} />
            ))}
            {runningSearch ? <DiscoverCandidateSkeletons count={Math.max(1, Math.min(3, 5 - visibleKols.length))} /> : null}
          </>
        ) : runningSearch ? (
          <DiscoverCandidateSkeletons />
        ) : <div className="vkpi-discover-empty">当前筛选下没有红人。请用顶部搜索从真实接口加载，或切到候选池。</div>}
      </div>
    </section>
  );
}

function RecommendationPanel({
  recommendations,
  loading,
  message,
  localFallbackCount,
  selectedKolId,
  onSelect,
  onRefresh,
  onRegenerate,
  onAction,
}: {
  recommendations: SmartRecommendation[];
  loading: boolean;
  message: string;
  localFallbackCount: number;
  selectedKolId: string;
  onSelect: (recommendation: SmartRecommendation) => void;
  onRefresh: () => void;
  onRegenerate: () => void;
  onAction: (recommendation: SmartRecommendation, action: RecommendationAction) => void;
}) {
  return (
    <section className="vkpi-discover-panel">
      <div className="vkpi-discover-panel__header">
        <div>
          <h3>智能推荐</h3>
          <span>读取 Product Analysis 推荐表；操作回写推荐 action / outcome。</span>
        </div>
        <div className="vkpi-discover-rec-toolbar">
          <button type="button" onClick={onRefresh} disabled={loading}>刷新</button>
          <button type="button" onClick={onRegenerate} disabled={loading}>生成推荐</button>
        </div>
      </div>
      <div className="vkpi-discover-rec-filter">
        {['Campaign 补人', '新市场发现', '竞品对比', '入选', '认领', '拒绝'].map((item) => <span key={item}>{item}</span>)}
      </div>
      <div className="vkpi-discover-list">
        {message ? <div className="vkpi-discover-empty is-compact">{message}</div> : null}
        {recommendations.length ? recommendations.map((recommendation) => {
          const activeId = recommendation.kolId || recommendationToUiKol(recommendation).id;
          const competitorTier = recommendation.competitorRiskTier;
          const competitorLabel = competitorTier
            ? `${recommendation.competitorBrand ? recommendation.competitorBrand.toUpperCase() : '竞品'} ${competitorTier}${recommendation.competitorRiskScore ? ` ${recommendation.competitorRiskScore.toFixed(1)}` : ''}`
            : '';
          return (
          <div
            className={`vkpi-discover-rec ${selectedKolId === activeId ? 'is-active' : ''} ${competitorTier ? `is-risk-${competitorTier}` : ''}`}
            role="button"
            tabIndex={0}
            key={recommendation.id}
            onClick={() => onSelect(recommendation)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') onSelect(recommendation);
            }}
          >
            <div>
              <strong>{recommendation.rank}. {recommendation.handle}</strong>
              <p>{platformLabels[platformFromRaw(recommendation.platform)] || recommendation.platform} · {recommendation.status} · {recommendation.source}</p>
              {competitorLabel ? <span className={`vkpi-discover-rec__risk is-${competitorTier}`}>{competitorLabel}</span> : null}
              <em>{recommendation.reason}</em>
              <div className="vkpi-discover-rec__actions" onClick={(event) => event.stopPropagation()}>
                <button type="button" onClick={() => onAction(recommendation, 'shortlist')} disabled={loading}>入选</button>
                <button type="button" onClick={() => onAction(recommendation, 'claim')} disabled={loading}>认领</button>
                <button type="button" onClick={() => onAction(recommendation, 'reject')} disabled={loading}>拒绝</button>
              </div>
            </div>
            <b>{recommendation.score || '-'}</b>
          </div>
        );}) : loading ? (
          <DiscoverRecommendationSkeletons />
        ) : <div className="vkpi-discover-empty">推荐表暂无真实记录。本地候选可推荐 {localFallbackCount} 个，但这里不再用前端排序冒充智能推荐。</div>}
      </div>
    </section>
  );
}

function KolCard({ kol, active, onClick }: { kol: UiKol; active: boolean; onClick: () => void }) {
  const level = kol.followerCount >= 500000 ? 'top' : kol.followerCount >= 50000 ? 'mid' : 'tail';
  return (
    <button className={`vkpi-discover-kol ${active ? 'is-active' : ''}`} type="button" onClick={onClick}>
      <Avatar name={kol.name || kol.handle} src={kol.avatar} size="sm" />
      <div className="vkpi-discover-kol__main">
        <strong>{kol.handle}</strong>
        <span>{platformLabels[kol.platform] || kol.platform} · {kol.followerLabel} 粉 · {kol.country} · {kol.topic}</span>
        <div>
	          <em>{level}</em>
	          {kol.sourceKind === 'kol_pool' ? <em className="is-purple">历史档案</em> : isCandidateKol(kol) ? <em className="is-good">平台搜索</em> : null}
	          <em>{kol.status}</em>
          {kol.hasCollaboration ? <em className="is-good">合作过</em> : <em>未合作</em>}
          {kol.contactCount ? <em className="is-purple">联系方式 {kol.contactCount}</em> : null}
          {kol.riskLabel ? <em className="is-warn">{kol.riskLabel}</em> : null}
        </div>
      </div>
      <div className="vkpi-discover-kol__score">
        <strong>{kol.score || '-'}</strong>
        <span>{kol.grade}</span>
      </div>
    </button>
  );
}

function ProfilePanel({
  selectedKol,
  selectedProfile,
  selectedAssessment,
  selectedDimensions11,
  lookupResult,
  contacts,
  dimensions,
  competitorRelations,
  competitorLoading,
  dimensions11Loading,
  productFits,
  posts,
  projects,
  productLaunches,
  profileLoading,
  scanBusy,
  busy,
  internalNote,
  setInternalNote,
  onClaim,
  onDeepScan,
  onOpenContact,
  onOpenProject,
  canClaim,
  canScan,
  canUpdate,
  canCreateProject,
}: {
  selectedKol?: UiKol;
  selectedProfile: VkpiKolProfile | null;
  selectedAssessment: VkpiKolAssessmentResponse | null;
  selectedDimensions11: Dimensions11Payload | null;
  lookupResult: VkpiKolLookupResult | null;
  contacts: ContactItem[];
  dimensions: Array<{ key: string; label: string; source: string; value: number; pending: boolean }>;
  competitorRelations: CompetitorRelation[];
  competitorLoading: boolean;
  dimensions11Loading: boolean;
  productFits: ProductFitItem[];
  posts: Array<Record<string, unknown>>;
  projects: VkpiProjectRow[];
  productLaunches: VkpiDashboardData['productLaunches'];
  profileLoading: boolean;
  scanBusy: boolean;
  busy: boolean;
  internalNote: string;
  setInternalNote: (value: string) => void;
  onClaim: () => Promise<void>;
  onDeepScan: () => Promise<void>;
  onOpenContact: () => void;
  onOpenProject: () => void;
  canClaim: boolean;
  canScan: boolean;
  canUpdate: boolean;
  canCreateProject: boolean;
}) {
  if (!selectedKol) return <div className="vkpi-discover-empty">选择一个 KOL 查看右侧完整画像。</div>;
  const summary = selectedProfile?.summary || {};
  const assessmentScore = safeNumber(selectedAssessment?.score) || selectedKol.score;
  const assessmentGrade = textValue(selectedAssessment?.grade, selectedKol.grade);
  const candidateOnly = isPlatformSearchCandidate(selectedKol);
  const historicalMatch = objectValue(selectedKol.raw.historical_match || selectedKol.raw.history_match);
  const historicalCooperationCount = safeNumber(historicalMatch.cooperation_count || selectedKol.raw.cooperation_count || selectedKol.raw.history_cooperation_count);
  const historicalRecentCooperations = arrayValue(historicalMatch.recent_cooperations).map(objectValue);
  const candidateIntentTags = searchIntentTags(selectedKol.raw.search_query || selectedKol.topic || selectedKol.raw.sample_title);
  const candidateFocus = candidateIntentTags.length ? candidateIntentTags.map((item) => item.label).join(' / ') : '近期内容';
  const candidateSampleCount = candidatePostsFromRaw(selectedKol.raw).length;
  const dimensions11ProductFits = productFitsFromDimensions11(selectedDimensions11);
  const productRows: Array<Record<string, unknown>> = productFits.length ? productFits as unknown as Array<Record<string, unknown>> : dimensions11ProductFits as unknown as Array<Record<string, unknown>>;
  const productFitMethod = textValue(productRows[0]?.method, '');
  const productFitScore = safeNumber(summary.product_fit || selectedKol.raw.product_fit) || safeNumber(productRows[0]?.score);
  const decision = candidateOnly
    ? (historicalCooperationCount
      ? `历史合作命中 ${historicalCooperationCount} 条；优先核对最近内容、负责人和产品线后复用。`
      : candidateSampleCount
      ? `可观察；平台已返回样本内容，先围绕 ${candidateFocus} 建档抓取。`
      : `待抓取；这是平台候选，建议先抓取账号再评估 ${candidateFocus}。`)
    : textValue(selectedAssessment?.recommended_action || summary.recommended_action, assessmentScore >= 80 ? '优先合作；建议补齐产品适配和联系方式证据。' : assessmentScore ? '可观察；先补齐近期内容与联系方式。' : '待评估；请先运行真实抓取。');
  const profileStatus = historicalCooperationCount ? `历史合作 ${historicalCooperationCount} 条` : candidateOnly ? '候选 / 未深扫 / 可分析' : profileLoading ? '画像加载中' : selectedProfile ? '真实 profile 已加载' : lookupResult ? '查重结果已加载' : '列表档案';
  const hasDimensions11 = Boolean(selectedDimensions11 && textValue(selectedDimensions11.method, ''));
  const overallDimensionsScore = safeNumber(selectedDimensions11?.overall_score);
  const competitorRows = visibleCompetitorRelations(competitorRelations);
  return (
    <div className="vkpi-discover-profile__stack">
      <section className="vkpi-discover-profile-head">
        <Avatar name={selectedKol.name || selectedKol.handle} src={selectedKol.avatar} size="lg" />
        <div>
          <strong>{selectedKol.handle}</strong>
          <span>{platformLabels[selectedKol.platform] || selectedKol.platform} · {selectedKol.followerLabel} 粉 · {selectedKol.country}</span>
          <div>
            <em>{profileStatus}</em>
            <em>内容 {selectedKol.contentCountLabel}</em>
          </div>
        </div>
        <div className="vkpi-discover-profile-score">
          <b>{assessmentScore || '-'}</b>
          <span className={`is-${assessmentGrade}`}>{assessmentGrade}</span>
        </div>
      </section>

      <section className="vkpi-discover-card is-decision">
        <b>建议：{decision}</b>
        <span>产品适配 {productFitScore ? `${productFitScore}/100` : '待接 Product Fit'} · 风险 {textValue(summary.risk_level || selectedKol.riskLabel, '暂无高风险')}</span>
      </section>

      <section className="vkpi-discover-card">
        <div className="vkpi-discover-card__title">
          <b>竞品关系</b>
          <span>{competitorLoading ? '读取历史池' : competitorRows.length ? `${competitorRows.length} 条命中` : '历史池规则检测'}</span>
        </div>
        {competitorLoading ? (
          <>
            <span className="vkpi-skeleton vkpi-skeleton-line is-long" />
            <span className="vkpi-skeleton vkpi-skeleton-line is-medium" />
          </>
        ) : competitorRows.length ? competitorRows.map((relation) => (
          <div className={`vkpi-discover-fit is-competitor-risk ${competitorTone(relation.risk_tier)}`} key={`${relation.competitor_brand}-${relation.risk_score}`}>
            <div>
              <strong>{textValue(relation.competitor_brand, 'competitor').toUpperCase()} · {competitorTierLabel(relation.risk_tier)}</strong>
              <span>
                {textValue(relation.collaboration_depth, 'mentioned')} · 90 天 {safeNumber(relation.collaboration_count_90d)} 条 ·
                历史 {safeNumber(relation.collaboration_count_total)} 条
              </span>
            </div>
            <b>{safeNumber(relation.risk_score).toFixed(1)}</b>
          </div>
        )) : (
          <div className="vkpi-discover-empty is-compact">1012 历史池和已缓存资料暂无竞品证据；后续 deep scan 会补最近内容。</div>
        )}
      </section>

      {scanBusy ? (
        <section className="vkpi-discover-card">
          <b>真实抓取运行中</b>
          <span>后端接口同步返回前不伪造 6 步进度。完成后会刷新 profile / posts。</span>
          <div className="vkpi-discover-progress"><i /></div>
        </section>
      ) : null}

      {profileLoading ? (
        <section className="vkpi-discover-card vkpi-discover-profile-loading" aria-live="polite">
          <div className="vkpi-discover-card__title"><b>正在读取画像</b><span>资料 / 近期内容 / 历史合作</span></div>
          <span className="vkpi-skeleton vkpi-skeleton-line is-long" />
          <span className="vkpi-skeleton vkpi-skeleton-line is-medium" />
          <div className="vkpi-discover-mini-grid">
            <span className="vkpi-skeleton vkpi-skeleton-line" />
            <span className="vkpi-skeleton vkpi-skeleton-line" />
            <span className="vkpi-skeleton vkpi-skeleton-line" />
            <span className="vkpi-skeleton vkpi-skeleton-line" />
          </div>
        </section>
      ) : null}

      <section className="vkpi-discover-card">
        <div className="vkpi-discover-card__title">
          <b>{hasDimensions11 ? '11 维评估' : '8 维评估'}</b>
          <span>{dimensions11Loading ? '读取规则画像' : hasDimensions11 ? `规则画像 ${overallDimensionsScore || '-'} · ${formatAssessmentMethod(textValue(selectedDimensions11?.method, ''))}` : formatAssessmentMethod(selectedAssessment?.method)}</span>
        </div>
        <div className="vkpi-discover-bars">
          {dimensions.map((dimension) => (
            <div className={`vkpi-discover-bar ${dimension.pending ? 'is-pending' : ''}`} key={dimension.key}>
              <span>{dimension.label}</span>
              <div><i style={{ width: `${dimension.pending ? 48 : dimension.value}%` }} /></div>
              <b>{dimension.pending ? '待接' : dimension.value}</b>
            </div>
          ))}
        </div>
      </section>

      <section className="vkpi-discover-card">
        <div className="vkpi-discover-card__title"><b>Top 5 产品适配</b><span>{formatProductFitSource(Boolean(productRows.length), productFitMethod)}</span></div>
        {productRows.length ? productRows.map((product, index) => {
          const metaLine = productFitMetaLine(product);
          return (
            <div className="vkpi-discover-fit" key={String(product.launch_id || product.id || product.product_sku || product.productSku || index)}>
              <div>
                <strong>{cleanProductLabel(product.product_name || product.productName || product.product_sku || product.productSku)}</strong>
                <span>{Array.isArray(product.reasons) && product.reasons.length ? textValue(product.reasons[0], '') : textValue(product.launch_name || product.launchName || product.category, 'Viltrox 产品')}</span>
                {metaLine ? <small>{metaLine}</small> : null}
              </div>
              <b>{safeNumber(product.score) || productFitScore || '待接'}</b>
            </div>
          );
        }) : candidateOnly && candidateIntentTags.length ? (
          <div className="vkpi-discover-fit is-query">
            <div>
              <strong>搜索方向</strong>
              <span>{candidateIntentTags.map((item) => `${item.label}：${item.detail}`).join(' / ')}</span>
            </div>
            <b>待抓取</b>
          </div>
        ) : <div className="vkpi-discover-empty is-compact">暂无真实产品适配。先建档并运行深度评估，再选择具体产品方向。</div>}
      </section>

      <section className="vkpi-discover-card">
        <div className="vkpi-discover-card__title">
          <b>5 层联系方式</b>
          <button type="button" onClick={onOpenContact} disabled={!canUpdate}>+ 手动添加</button>
        </div>
        {contacts.length ? contacts.map((contact) => (
          <div className="vkpi-discover-contact" key={contact.id}>
            <div>
              <span>Layer {contact.layer}</span>
              <b>{contact.type}</b>
              <em>{contact.verified ? '已验证' : contact.confidence ? `置信 ${contact.confidence}%` : '待验证'}</em>
              <strong>{contact.value}</strong>
              <small>{contact.source}{contact.evidence ? ` · ${contact.evidence}` : ''}</small>
            </div>
          </div>
        )) : <div className="vkpi-discover-empty is-compact">暂无真实联系方式。可先运行抓取或手动补录。</div>}
      </section>

      <section className="vkpi-discover-card">
        <div className="vkpi-discover-card__title"><b>全部视频深度分析</b><span>读取 profile/posts 真实返回</span></div>
        <div className="vkpi-discover-mini-grid">
          <div><strong>{compactCount(posts.length)}</strong><span>内容条数</span></div>
          <div><strong>{compactCount(summary.total_views || selectedKol.raw.total_views)}</strong><span>总播放</span></div>
          <div><strong>{compactCount(summary.total_likes || selectedKol.raw.total_likes)}</strong><span>总点赞</span></div>
          <div><strong>{summary.user_persona ? '已识别' : '待接'}</strong><span>受众画像</span></div>
        </div>
        {summary.user_persona ? <p className="vkpi-discover-muted">{String(summary.user_persona)}</p> : null}
      </section>

      <section className="vkpi-discover-card">
        <div className="vkpi-discover-card__title"><b>Viltrox 合作历史</b><span>{projects.length || historicalCooperationCount} 个记录</span></div>
        {projects.length ? projects.slice(0, 4).map((project) => (
          <div className="vkpi-discover-history" key={project.id}>
            <strong>{project.campaign}</strong>
            <span>{stageLabels[project.stage] || project.stage} · {project.productName || project.productSku || '未绑定产品'} · ROI {project.roi ?? '-'}</span>
          </div>
        )) : historicalCooperationCount ? (
          <div className="vkpi-discover-history is-legacy">
            <div>
              <strong>{textValue(historicalMatch.display_name || selectedKol.name || selectedKol.handle, selectedKol.handle)}</strong>
              <span>
                {textValue(historicalMatch.source_type, 'vkpi_kol_pool')} · {historicalCooperationCount} 条历史合作
                {safeNumber(historicalMatch.profile_rows) ? ` · ${safeNumber(historicalMatch.profile_rows)} 条档案证据` : ''}
              </span>
              {historicalRecentCooperations.length ? (
                <small>
                  {historicalRecentCooperations.slice(0, 2).map((row) => textValue(row.product || row.project || row.status, '')).filter(Boolean).join(' / ')}
                </small>
              ) : null}
            </div>
            <b>可复用</b>
          </div>
        ) : <div className="vkpi-discover-empty is-compact">暂无合作历史。首次合作建议先走小预算测试。</div>}
      </section>

      <section className="vkpi-discover-card">
        <div className="vkpi-discover-card__title"><b>最近内容</b><span>{posts.length} 条{candidateOnly && posts.length ? ' · 平台样本' : ''}</span></div>
        {posts.length ? posts.slice(0, 5).map((post, index) => (
          <div className="vkpi-discover-post" key={String(post.id || post.post_url || post.url || index)}>
            <div>▶</div>
            <span><strong>{textValue(post.title || post.caption || post.post_url || post.url, '未命名内容')}</strong><em>{textValue(post.post_url || post.url || post.content_url, '-')}</em></span>
            <b>{compactLabel(post.views || post.view_count || post.play_count)}</b>
          </div>
        )) : <div className="vkpi-discover-empty is-compact">暂无最近内容。平台未返回样本时，先建档并运行抓取账号。</div>}
      </section>

      <section className="vkpi-discover-card">
        <b>操作</b>
        <div className="vkpi-discover-actions">
          <button className="vkpi-discover-btn is-primary" type="button" onClick={onOpenProject} disabled={!canCreateProject || busy}>加入项目</button>
          <button className="vkpi-discover-btn" type="button" onClick={() => void onDeepScan()} disabled={!canScan || scanBusy}>深度评估</button>
          <button className="vkpi-discover-btn" type="button" onClick={() => void onClaim()} disabled={!canClaim || busy}>认领</button>
        </div>
        <textarea value={internalNote} onChange={(event) => setInternalNote(event.target.value)} placeholder="内部备注（UI 本地态，后续接消息/备注接口）" />
      </section>
    </div>
  );
}
