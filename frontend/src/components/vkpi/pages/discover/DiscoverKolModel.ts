import { creatorPlatformOptions, platformLabels } from '../../shared/vkpiConstants';
import { arrayValue, compactCount, contactLinksFrom, objectValue, platformFromRaw, safeNumber, textValue } from '../../shared/vkpiDataUtils';
import type { VkpiDashboardData, VkpiKolLookupResult, VkpiKolOption, VkpiKolProfile, VkpiProjectRow } from '../../vkpiTypes';
import type { IntelligenceCardModel } from '../../intelligence/IntelligenceCard';
import type { VkpiKolAssessmentResponse } from '../../../../domains/kol';
import type { SmartRecommendation } from './DiscoverRecommendationPanel';
import type { CompetitorRelation, ContactItem, Dimensions11Payload, ProductFitItem, SearchHistoryItem, UiKol } from './DiscoverTypes';

const SEARCH_HISTORY_STORAGE_KEY = 'vkpi.discover.searchHistory.v1';
export const MAX_SEARCH_HISTORY = 12;

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

export function platformInputValue(platformLabel: string): string {
  const normalized = String(platformLabel || '').toLowerCase();
  return creatorPlatformOptions.find((option) => option.value === normalized || option.label.toLowerCase() === normalized)?.value || normalized || 'other';
}

export function loadSearchHistory(): SearchHistoryItem[] {
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

export function saveSearchHistory(items: SearchHistoryItem[]) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(SEARCH_HISTORY_STORAGE_KEY, JSON.stringify(items.slice(0, MAX_SEARCH_HISTORY)));
  } catch {
    // Ignore private-mode storage failures; search itself should still work.
  }
}

export function kolPoolIdForCompetitors(kol?: UiKol): string {
  if (!kol) return '';
  const raw = objectValue(kol.raw);
  const historyMatch = objectValue(raw.historical_match || raw.history_match);
  const id = raw.kol_pool_id || raw.history_kol_pool_id || historyMatch.kol_pool_id || historyMatch.id;
  if (id) return String(id);
  return kol.sourceKind === 'kol_pool' && /^\d+$/.test(kol.id) ? kol.id : '';
}

export function competitorTierLabel(value: unknown): string {
  const tier = textValue(value, 'opportunity');
  if (tier === 'avoid') return '高风险';
  if (tier === 'caution') return '谨慎';
  if (tier === 'safe') return '可合作';
  return '机会';
}

export function visibleCompetitorRelations(relations: CompetitorRelation[]): CompetitorRelation[] {
  return relations
    .filter((relation) => safeNumber(relation.risk_score) > 0 || textValue(relation.collaboration_depth, 'none') !== 'none')
    .sort((a, b) => safeNumber(b.risk_score) - safeNumber(a.risk_score))
    .slice(0, 4);
}

export function normalizeHandle(value: unknown): string {
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

export function kolOptionToUiKol(kol: VkpiKolOption): UiKol {
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

export function rawToUiKol(raw: Record<string, unknown>): UiKol {
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

export function lookupToUiKol(result: VkpiKolLookupResult): UiKol | null {
  const raw = objectValue(result.kol);
  if (!raw.id && !raw.channel_name && !raw.media_name) return null;
  const merged = {
    ...raw,
    ...objectValue(result.dossier?.snapshot),
    ...objectValue(result.dossier?.report),
  };
  return rawToUiKol(merged);
}

export function candidatePostsFromRaw(raw: Record<string, unknown>): Array<Record<string, unknown>> {
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

export function platformSearchItemToUiKol(raw: Record<string, unknown>, index: number, candidateId?: number): UiKol {
  const historyMatch = objectValue(raw.historical_match || raw.history_match);
  const historyCooperationCount = safeNumber(historyMatch.cooperation_count || raw.history_cooperation_count);
  const hasHistoryMatch = Boolean(historyMatch.matched || historyMatch.kol_pool_id || raw.history_kol_pool_id);
  const searchQuery = usableCandidateText(raw.search_query);
  const handleSource = usableCandidateText(raw.handle, raw.username, raw.ownerUsername, raw.channel_name, raw.display_name, raw.ownerFullName, searchQuery) || `candidate-${index + 1}`;
  const displayName = usableCandidateText(raw.channel_name, raw.display_name, raw.media_name, raw.creator_name, raw.ownerFullName, raw.owner_name, handleSource, searchQuery) || handleSource;
  const platform = platformFromRaw(raw.platform);
  const views = safeNumber(raw.views || raw.avg_views || raw.view_count || raw.play_count);
  const likes = safeNumber(raw.likes || raw.like_count);
  const followerCount = safeNumber(raw.follower_count || raw.followers || raw.subscriber_count || raw.fans);
  const contentCount = safeNumber(raw.content_count || raw.video_count || raw.posts_count || raw.items_count) || candidatePostsFromRaw(raw).length;
  const score = Math.max(
    clampScore(raw.score || raw.priority_score || raw.account_score),
    historyCooperationCount ? 72 : 0,
    views ? Math.min(85, Math.round(Math.log10(Math.max(views, 10)) * 16)) : 0,
    likes ? Math.min(78, Math.round(Math.log10(Math.max(likes, 10)) * 14)) : 0,
  );
  return {
    id: `candidate:${candidateId || candidateKey(`${platform}-${handleSource}-${index}`)}`,
    name: displayName,
    handle: normalizeHandle(handleSource),
    platform,
    avatar: textValue(raw.avatar_url || raw.profile_pic_url || raw.thumbnail, ''),
    profileUrl: textValue(raw.profile_url || raw.channel_url || raw.url || raw.source_url, ''),
    contactEmail: '',
    contactPhone: '',
    followerCount,
    followerLabel: followerCount ? compactCount(followerCount) : '待抓取',
    contentCount,
    contentCountLabel: contentCount ? compactCount(contentCount) : (views ? '样本 1' : '待抓取'),
    score,
    grade: score ? scoreToGrade(score) : '-',
    country: textValue(raw.country || raw.country_code || raw.region, '-'),
    topic: textValue(raw.niche || raw.category || raw.title || raw.caption || searchQuery, searchQuery || '平台候选'),
    claimOwner: '',
    status: hasHistoryMatch ? `历史合作 ${historyCooperationCount || 1} 条 / 可复用` : '平台候选 / 待建档',
    freshness: textValue(raw.searched_at || raw.fetched_at || raw.updated_at, '刚刚搜索'),
    contactCount: 0,
    projectCount: historyCooperationCount,
    hasCollaboration: hasHistoryMatch,
    riskLabel: safeNumber(historyMatch.risk_rows) ? '历史风险待核' : '',
    sourceKind: 'platform_search',
    raw: { ...raw, candidate_id: candidateId, source_kind: 'platform_search' },
  };
}

export function instantCandidateToUiKol(term: string, selectedPlatform: string): UiKol {
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

export function isCandidateKol(kol?: UiKol) {
  return Boolean(kol?.sourceKind === 'platform_search' || kol?.sourceKind === 'kol_pool' || String(kol?.id || '').startsWith('candidate:') || String(kol?.id || '').startsWith('pool:'));
}

export function isPlatformSearchCandidate(kol?: UiKol) {
  return Boolean(kol?.sourceKind === 'platform_search' || String(kol?.id || '').startsWith('candidate:'));
}

function searchKolMergeKey(kol: UiKol): string {
  const handle = normalizeHandle(kol.handle || kol.profileUrl || kol.name);
  if (handle) return `${platformInputValue(kol.platform)}:${handle}`;
  return `${platformInputValue(kol.platform)}:${candidateKey(kol.id || kol.name)}`;
}

export function mergeSearchKols(...groups: UiKol[][]): UiKol[] {
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

export function cleanProductLabel(value: unknown) {
  let label = textValue(value, '未命名产品');
  label = label.replace(/^viltrox\s+/i, '').replace(/\s+/g, ' ').trim();
  label = label.replace(/\s+(FE|E|Z|X|L|RF)$/i, ' ($1)');
  return label;
}

export function searchIntentTags(query: unknown): Array<{ label: string; detail: string }> {
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

export function filterKols(kols: UiKol[], filters: { platform: string; level: string; grade: string; collab: string; risk: string; freshness: string }, query: string): UiKol[] {
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

export function productFitsFromDimensions11(payload: Dimensions11Payload | null): ProductFitItem[] {
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

export function dimensionsFromProfile(profile: VkpiKolProfile | null, selected: UiKol | undefined, assessment: VkpiKolAssessmentResponse | null, dimensions11: Dimensions11Payload | null) {
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

export function apiContactsToItems(rows: Array<Record<string, unknown>> | undefined): ContactItem[] {
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

export function contactsFromProfile(profile: VkpiKolProfile | null, selected: UiKol | undefined): ContactItem[] {
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

export function profileProjects(profile: VkpiKolProfile | null, data: VkpiDashboardData, selected: UiKol | undefined): VkpiProjectRow[] {
  if (!selected) return [];
  const direct = data.projects.filter((project) => project.kolId && String(project.kolId) === selected.id);
  if (direct.length) return direct;
  const handle = selected.handle.replace(/^@/, '').toLowerCase();
  return data.projects.filter((project) => [project.kolHandle, project.kolName].join(' ').toLowerCase().includes(handle)).slice(0, 5);
}

export function recentPosts(profile: VkpiKolProfile | null, fallbackPosts: Array<Record<string, unknown>>, selected?: UiKol) {
  const rows = [
    ...arrayValue(profile?.posts).map(objectValue),
    ...arrayValue(profile?.content_posts).map(objectValue),
    ...fallbackPosts,
    ...(selected && isCandidateKol(selected) ? candidatePostsFromRaw(selected.raw) : []),
  ];
  return rows.filter((row, index) => row.id || index < 6).slice(0, 6);
}

export function jsonObjectValue(value: unknown): Record<string, unknown> {
  if (value && typeof value === 'object' && !Array.isArray(value)) return value as Record<string, unknown>;
  try {
    const parsed = JSON.parse(String(value || '{}'));
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

export function recommendationToSmart(row: Record<string, unknown>, index: number): SmartRecommendation {
  const explanation = jsonObjectValue(row.explanation_json);
  const scoringBreakdown = jsonObjectValue(row.scoring_breakdown_json);
  const kol = objectValue(row.kol);
  const launch = objectValue(row.launch);
  const suggestion = objectValue(row.suggestion);
  const competitor = objectValue(row.competitor);
  const handle = textValue(kol.handle || kol.display_name, `candidate-${index + 1}`);
  const score = safeNumber(row.score || scoringBreakdown.final_score || suggestion.score);
  return {
    id: textValue(row.recommendation_uid || row.id, `rec-${index}`),
    kolId: textValue(kol.id || row.kol_id || row.linked_main_kol_id, ''),
    handle: handle.startsWith('@') ? handle : `@${handle}`,
    displayName: textValue(kol.display_name || kol.media_name || handle, handle),
    platform: textValue(kol.platform, 'unknown'),
    score,
    rank: safeNumber(row.rank) || index + 1,
    status: textValue(row.status, 'pending'),
    reason: arrayValue(explanation.reasons).map((value) => textValue(value, '')).filter(Boolean)[0]
      || arrayValue(suggestion.reasons).map((value) => textValue(value, '')).filter(Boolean)[0]
      || textValue(suggestion.suggested_action, '推荐待复核'),
    source: textValue(row.source || suggestion.source || launch.product_sku, 'product recommendation'),
    competitorRiskTier: textValue(competitor.risk_tier || row.competitor_risk_tier, 'opportunity'),
    competitorRiskScore: safeNumber(competitor.risk_score || row.competitor_risk_score),
    competitorBrand: textValue(competitor.brand, ''),
    raw: row,
  };
}

export function recommendationToUiKol(row: SmartRecommendation): UiKol {
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

export function buildDiscoverReviewCard(input: {
  kol: UiKol;
  assessment: VkpiKolAssessmentResponse | null;
  productFits: ProductFitItem[];
  competitorRelations: CompetitorRelation[];
  contacts: ContactItem[];
  posts: Array<Record<string, unknown>>;
  sourceQuery: string;
}): IntelligenceCardModel {
  const score = safeNumber(input.assessment?.score) || input.kol.score;
  const product = input.productFits[0] as Record<string, unknown> | undefined;
  const productName = product ? cleanProductLabel(product.product_name || product.productName || product.product_sku || product.productSku) : '';
  const topCompetitor = visibleCompetitorRelations(input.competitorRelations)[0];
  const confidence = score ? Math.max(0.2, Math.min(0.95, score / 100)) : undefined;
  const candidatePosts = candidatePostsFromRaw(input.kol.raw);
  const postCount = input.posts.length || candidatePosts.length || input.kol.contentCount;
  return {
    id: `discover-review-${input.kol.id}-${Date.now()}`,
    type: 'recommendation',
    priority: score >= 75 || topCompetitor ? 'high' : score >= 45 || input.contacts.length ? 'medium' : 'low',
    status: 'open',
    title: `${input.kol.handle} · 候选复核`,
    summary: `${platformLabels[input.kol.platform] || input.kol.platform} · ${input.kol.followerLabel} 粉 · ${productName || input.kol.topic}`,
    entityType: 'discover_candidate',
    entityId: input.kol.id,
    confidence,
    freshnessLabel: input.kol.freshness || 'discover candidate',
    sourceLabel: 'discover candidate review',
    evidence: [
      { label: 'KOL', source: platformLabels[input.kol.platform] || input.kol.platform, value: `${input.kol.handle} · ${input.kol.country}` },
      { label: 'Score', source: 'assessment/discover', value: score ? `${score}/100` : '待评估' },
      { label: 'Product fit', source: 'product_fit', value: productName || '待补产品适配' },
      { label: 'Contacts', source: 'contacts', value: `${input.contacts.length} contacts` },
      { label: 'Recent posts', source: 'posts', value: `${postCount || 0} samples` },
      { label: 'Competitor', source: 'history pool', value: topCompetitor ? `${textValue(topCompetitor.competitor_brand, 'competitor')} · ${competitorTierLabel(topCompetitor.risk_tier)}` : '暂无命中' },
    ],
    actions: [
      { label: '查看证据', kind: 'primary' },
      { label: '打开红人搜索', kind: 'secondary' },
    ],
    metadata: {
      discover_query: input.sourceQuery || input.kol.handle,
      discover_platform: platformInputValue(input.kol.platform),
      kol: {
        id: input.kol.id,
        handle: input.kol.handle,
        display_name: input.kol.name,
        platform: input.kol.platform,
        kol_pool_id: kolPoolIdForCompetitors(input.kol),
      },
      product_fit: product || {},
      competitor: topCompetitor || {},
      source_kind: input.kol.sourceKind || 'kol',
    },
  };
}

export function promotedResultToUiKol(result: Record<string, unknown>, fallback: UiKol): UiKol | null {
  const main = objectValue(result.main_kol);
  const mainId = textValue(result.main_kol_id || main.id, '');
  if (!mainId) return null;
  return rawToUiKol({
    ...fallback.raw,
    ...main,
    id: mainId,
    kol_id: mainId,
    channel_name: main.channel_name || main.display_name || main.media_name || fallback.name,
    handle: main.handle || fallback.handle,
    platform: main.platform || fallback.platform,
    profile_url: main.profile_url || main.channel_url || fallback.profileUrl,
    channel_url: main.channel_url || main.profile_url || fallback.profileUrl,
    avatar_url: main.avatar_url || fallback.avatar,
    source_kind: '',
  });
}
