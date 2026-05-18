import React, { useEffect, useMemo, useState } from 'react';
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

function platformInputValue(platformLabel: string): string {
  const normalized = String(platformLabel || '').toLowerCase();
  return creatorPlatformOptions.find((option) => option.value === normalized || option.label.toLowerCase() === normalized)?.value || normalized || 'other';
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
  const score = scoreFromRaw(raw);
  const snapshotFollowers = raw.snapshot_follower_count || raw.follower_count || raw.followers || raw.subscriber_count;
  const snapshotContent = raw.snapshot_content_count || raw.content_count || raw.video_count || raw.posts_count;
  const platform = platformFromRaw(raw.platform);
  const handle = normalizeHandle(raw.channel_name || raw.handle || raw.username || raw.owner_name || raw.media_name);
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
    status: textValue(raw.snapshot_scan_status || raw.scan_status || raw.sync_status || raw.contact_status, 'known_profile'),
    freshness: textValue(raw.snapshot_scanned_at || raw.scanned_at || raw.updated_at, '待刷新'),
    contactCount: contactCountFromRaw(raw),
    projectCount: safeNumber(raw.project_count || raw.campaign_count),
    hasCollaboration: safeNumber(raw.project_count || raw.campaign_count) > 0 || safeNumber(raw.revenue_cents) > 0,
    riskLabel: textValue(raw.risk_level || raw.risk_label, ''),
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

function dimensionsFromProfile(profile: VkpiKolProfile | null, selected: UiKol | undefined, assessment: VkpiKolAssessmentResponse | null) {
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

function recentPosts(profile: VkpiKolProfile | null, fallbackPosts: Array<Record<string, unknown>>) {
  const rows = [
    ...arrayValue(profile?.posts).map(objectValue),
    ...arrayValue(profile?.content_posts).map(objectValue),
    ...fallbackPosts,
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
    .map((product) => textValue(product.productName || product.productSku || product.launchName, ''))
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
  const [selectedKolId, setSelectedKolId] = useState('');
  const [lookupResult, setLookupResult] = useState<VkpiKolLookupResult | null>(null);
  const [selectedProfile, setSelectedProfile] = useState<VkpiKolProfile | null>(null);
  const [selectedAssessment, setSelectedAssessment] = useState<VkpiKolAssessmentResponse | null>(null);
  const [selectedProductFits, setSelectedProductFits] = useState<ProductFitItem[]>([]);
  const [selectedContacts, setSelectedContacts] = useState<ContactItem[]>([]);
  const [smartRecommendations, setSmartRecommendations] = useState<SmartRecommendation[]>([]);
  const [recommendationLoading, setRecommendationLoading] = useState(false);
  const [recommendationMessage, setRecommendationMessage] = useState('');
  const [profilePosts, setProfilePosts] = useState<Array<Record<string, unknown>>>([]);
  const [profileLoading, setProfileLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [scanBusy, setScanBusy] = useState(false);
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

  const combinedKols = useMemo(() => {
    const map = new Map<string, UiKol>();
    [...searchKols, ...baseKols].forEach((kol) => {
      if (kol.id) map.set(kol.id, kol);
    });
    return Array.from(map.values());
  }, [baseKols, searchKols]);

  const visibleKols = useMemo(() => filterKols(combinedKols, filters, localQuery), [combinedKols, filters, localQuery]);
  const selectedKol = useMemo(() => combinedKols.find((kol) => kol.id === selectedKolId) || visibleKols[0] || combinedKols[0], [combinedKols, selectedKolId, visibleKols]);
  const selectedProjects = useMemo(() => profileProjects(selectedProfile, data, selectedKol), [data, selectedKol, selectedProfile]);
  const contacts = useMemo(() => selectedContacts.length ? selectedContacts : contactsFromProfile(selectedProfile, selectedKol), [selectedContacts, selectedProfile, selectedKol]);
  const dimensions = useMemo(() => dimensionsFromProfile(selectedProfile, selectedKol, selectedAssessment), [selectedAssessment, selectedProfile, selectedKol]);
  const posts = useMemo(() => recentPosts(selectedProfile, profilePosts), [profilePosts, selectedProfile]);
  const localCandidateRecommendations = useMemo(() => visibleKols.filter((kol) => kol.score >= 65 || kol.contactCount || kol.hasCollaboration).slice(0, 8), [visibleKols]);

  useEffect(() => {
    if (!selectedKolId && combinedKols[0]?.id) setSelectedKolId(combinedKols[0].id);
  }, [combinedKols, selectedKolId]);

  useEffect(() => {
    if (!selectedKol?.id || !apiToken) {
      setSelectedProfile(null);
      setSelectedAssessment(null);
      setSelectedProductFits([]);
      setSelectedContacts([]);
      setProfilePosts([]);
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

  const setNotice = (text: string, tone: MessageTone = 'info') => {
    setMessage(text);
    setMessageTone(tone);
  };

  const loadKols = async (searchText: string, platformFilter = filters.platform) => {
    if (!apiToken) {
      setNotice('当前未登录，列表使用已加载的本地真实数据。', 'warn');
      return;
    }
    const result = await listMarketingKols(apiToken, {
      search: searchText || undefined,
      platform: platformFilter !== 'all' ? platformFilter : undefined,
      limit: 100,
    });
    setSearchKols((result.kols || []).map(rawToUiKol));
  };

  const runNaturalSearch = async (searchText: string, platformFilter = filters.platform) => {
    const clean = searchText.trim();
    if (!apiToken) {
      await loadKols(clean, platformFilter);
      setLocalQuery(clean);
      return;
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

  const handleSearch = async (event?: React.FormEvent) => {
    event?.preventDefault();
    const term = query.trim();
    if (!term) {
      await loadKols('', filters.platform);
      setNotice('已刷新红人列表。');
      return;
    }
    setBusy(true);
    setNotice('');
    try {
      const looksLikeHandle = term.startsWith('@') || term.includes('instagram.com') || term.includes('youtube.com') || term.includes('tiktok.com') || term.includes('/');
      if (looksLikeHandle && onLookupKol) {
        const result = await onLookupKol({
          platform,
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
          setNotice('查重完成，正在用真实接口抓取账号数据。');
          await onScanKolAccount(kolId, 24);
          if (onLookupKol) {
            const refreshed = await onLookupKol({ platform, handleOrUrl: term, createIfMissing: false, scanAccount: false, maxPosts: 24 });
            setLookupResult(refreshed || result);
            const refreshedKol = refreshed ? lookupToUiKol(refreshed) : null;
            if (refreshedKol) {
              setSearchKols((previous) => [refreshedKol, ...previous.filter((kol) => kol.id !== refreshedKol.id)]);
              setSelectedKolId(refreshedKol.id);
            }
          }
          setNotice('账号抓取完成；右侧画像会读取最新 profile / posts。');
        } else {
          setNotice(found ? '查重完成，已打开红人画像。' : '查重完成，但没有返回可展示的红人档案。', found ? 'info' : 'warn');
        }
      } else {
        await runNaturalSearch(term, filters.platform);
      }
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '红人搜索失败', 'error');
    } finally {
      setBusy(false);
      setScanBusy(false);
    }
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
                void runNaturalSearch(chip.query, filters.platform);
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
              {creatorPlatformOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
            <button className="vkpi-discover-btn is-primary" type="submit" disabled={busy || scanBusy || (!apiToken && !onLookupKol)}>
              {busy || scanBusy ? '处理中' : '搜索'}
            </button>
          </form>
          <div className="vkpi-discover-command__actions">
            <label><input type="checkbox" checked={createIfMissing} onChange={(event) => setCreateIfMissing(event.target.checked)} /> 自动建档</label>
            <label><input type="checkbox" checked={scanAccount} onChange={(event) => setScanAccount(event.target.checked)} /> 抓取账号</label>
            <button className="vkpi-discover-btn" type="button" onClick={() => void loadKols('', filters.platform)} disabled={!apiToken || busy}>刷新</button>
          </div>
        </section>

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
                  onSelect={setSelectedKolId}
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
                lookupResult={lookupResult}
                contacts={contacts}
                dimensions={dimensions}
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
                canClaim={Boolean(onClaimKol && selectedKol && !selectedKol.claimOwner)}
                canScan={Boolean(onScanKolAccount && selectedKol)}
                canUpdate={Boolean((apiToken || onUpdateKol) && selectedKol)}
                canCreateProject={Boolean(onCreateProject && selectedKol)}
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

function SearchPanel({
  localQuery,
  setLocalQuery,
  filters,
  setFilters,
  visibleKols,
  selectedKolId,
  onSelect,
}: {
  localQuery: string;
  setLocalQuery: (value: string) => void;
  filters: { platform: string; level: string; grade: string; collab: string; risk: string; freshness: string };
  setFilters: React.Dispatch<React.SetStateAction<{ platform: string; level: string; grade: string; collab: string; risk: string; freshness: string }>>;
  visibleKols: UiKol[];
  selectedKolId: string;
  onSelect: (kolId: string) => void;
}) {
  const setFilter = (key: keyof typeof filters, value: string) => setFilters((previous) => ({ ...previous, [key]: value }));
  return (
    <section className="vkpi-discover-panel">
      <div className="vkpi-discover-panel__header">
        <div>
          <h3>主动搜索结果</h3>
          <span>{visibleKols.length} 个真实档案 / 搜索后由接口刷新</span>
        </div>
      </div>
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
        {visibleKols.length ? visibleKols.map((kol) => (
          <KolCard key={kol.id} kol={kol} active={selectedKolId === kol.id} onClick={() => onSelect(kol.id)} />
        )) : <div className="vkpi-discover-empty">当前筛选下没有红人。请用顶部搜索从真实接口加载，或切到候选池。</div>}
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
          return (
          <div
            className={`vkpi-discover-rec ${selectedKolId === activeId ? 'is-active' : ''}`}
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
              <em>{recommendation.reason}</em>
              <div className="vkpi-discover-rec__actions" onClick={(event) => event.stopPropagation()}>
                <button type="button" onClick={() => onAction(recommendation, 'shortlist')} disabled={loading}>入选</button>
                <button type="button" onClick={() => onAction(recommendation, 'claim')} disabled={loading}>认领</button>
                <button type="button" onClick={() => onAction(recommendation, 'reject')} disabled={loading}>拒绝</button>
              </div>
            </div>
            <b>{recommendation.score || '-'}</b>
          </div>
        );}) : <div className="vkpi-discover-empty">推荐表暂无真实记录。本地候选可推荐 {localFallbackCount} 个，但这里不再用前端排序冒充智能推荐。</div>}
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
  lookupResult,
  contacts,
  dimensions,
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
  lookupResult: VkpiKolLookupResult | null;
  contacts: ContactItem[];
  dimensions: Array<{ key: string; label: string; source: string; value: number; pending: boolean }>;
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
  const decision = textValue(selectedAssessment?.recommended_action || summary.recommended_action, assessmentScore >= 80 ? '优先合作；建议补齐产品适配和联系方式证据。' : assessmentScore ? '可观察；先补齐近期内容与联系方式。' : '待评估；请先运行真实抓取。');
  const productFitScore = safeNumber(summary.product_fit || selectedKol.raw.product_fit);
  const profileStatus = profileLoading ? '画像加载中' : selectedProfile ? '真实 profile 已加载' : lookupResult ? '查重结果已加载' : '列表档案';
  const productRows: Array<Record<string, unknown>> = productFits.length
    ? productFits as unknown as Array<Record<string, unknown>>
    : (productLaunches.length
      ? productLaunches.slice(0, 5)
      : [{ id: 'p1', productSku: 'AF 35mm F1.2 LAB FE', productName: 'AF 35mm F1.2 LAB FE', launchName: '默认产品' }]) as unknown as Array<Record<string, unknown>>;
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

      {scanBusy ? (
        <section className="vkpi-discover-card">
          <b>真实抓取运行中</b>
          <span>后端接口同步返回前不伪造 6 步进度。完成后会刷新 profile / posts。</span>
          <div className="vkpi-discover-progress"><i /></div>
        </section>
      ) : null}

      <section className="vkpi-discover-card">
        <div className="vkpi-discover-card__title"><b>8 维评估</b><span>{selectedAssessment?.method || '旧 profile fallback'}</span></div>
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
        <div className="vkpi-discover-card__title"><b>Top 5 产品适配</b><span>{productFits.length ? '本地规则接口' : '旧产品库 fallback'}</span></div>
        {productRows.map((product, index) => (
          <div className="vkpi-discover-fit" key={String(product.launch_id || product.id || product.product_sku || product.productSku || index)}>
            <div>
              <strong>{textValue(product.product_name || product.productName || product.product_sku || product.productSku, '未命名产品')}</strong>
              <span>{Array.isArray(product.reasons) && product.reasons.length ? textValue(product.reasons[0], '') : textValue(product.launch_name || product.launchName || product.category, 'Viltrox 产品')}</span>
            </div>
            <b>{safeNumber(product.score) || productFitScore || '待接'}</b>
          </div>
        ))}
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
        <div className="vkpi-discover-card__title"><b>Viltrox 合作历史</b><span>{projects.length} 个项目</span></div>
        {projects.length ? projects.slice(0, 4).map((project) => (
          <div className="vkpi-discover-history" key={project.id}>
            <strong>{project.campaign}</strong>
            <span>{stageLabels[project.stage] || project.stage} · {project.productName || project.productSku || '未绑定产品'} · ROI {project.roi ?? '-'}</span>
          </div>
        )) : <div className="vkpi-discover-empty is-compact">暂无合作历史。首次合作建议先走小预算测试。</div>}
      </section>

      <section className="vkpi-discover-card">
        <div className="vkpi-discover-card__title"><b>最近内容</b><span>{posts.length} 条</span></div>
        {posts.length ? posts.slice(0, 5).map((post, index) => (
          <div className="vkpi-discover-post" key={String(post.id || post.post_url || post.url || index)}>
            <div>▶</div>
            <span><strong>{textValue(post.title || post.caption || post.post_url || post.url, '未命名内容')}</strong><em>{textValue(post.post_url || post.url || post.content_url, '-')}</em></span>
            <b>{compactLabel(post.views || post.view_count || post.play_count)}</b>
          </div>
        )) : <div className="vkpi-discover-empty is-compact">暂无最近内容。需要先抓取账号或等待平台数据返回。</div>}
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
