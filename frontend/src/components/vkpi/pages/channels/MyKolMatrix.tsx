import { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { claimKol, getKolComments, getKolPosts, getKolProfile, releaseKolClaim, scanKolAccount, updateMarketingKol } from '../../../../services/vkpi.ui-api';
import type { VkpiContactLink, VkpiDashboardData, VkpiKolOption, VkpiKolProfile, VkpiPlatform, VkpiProjectRow, VkpiProjectStage } from '../../vkpiTypes';
import { stageLabels } from '../../shared/vkpiConstants';
import { compactCount, platformDisplay, platformFromRaw, safeNumber } from '../../shared/vkpiDataUtils';
import { numberFormatter } from '../../shared/vkpiFormatters';
import { likelyVideoUrl, platformExternalUrl, proxiedImageUrl, proxiedVideoUrl } from '../../shared/mediaProxy';
import { PlatformLogo } from './PlatformLogo';
import './channelKols.css';

type MyKolView = 'watchlist' | 'funnel';
type PlatformFilter = 'all' | 'Facebook' | 'Instagram' | 'TikTok' | 'YouTube' | 'X' | 'Reddit';
type FunnelStageKey = 'claimed' | 'contacted' | 'replied' | 'agreed' | 'shipped' | 'received' | 'published' | 'measured';
type KolContentSort = 'latest' | 'views' | 'likes' | 'comments' | 'shares';
type KolContentDirection = 'desc' | 'asc';
type KolContentWindow = 'all' | '7d' | '30d' | '90d' | '180d' | '365d' | 'year';
type KolContentFilter = 'all' | 'viltrox' | 'competitor' | 'other' | 'gear';

interface MyKolItem {
  id: string;
  kolId: string;
  name: string;
  handle: string;
  platform: VkpiPlatform;
  avatar?: string;
  profileUrl?: string;
  contactEmail?: string;
  contactPhone?: string;
  contactLinks: VkpiContactLink[];
  followers: string;
  contentCount: string;
  projectCount: number;
  views: number;
  clicks: number;
  subStatus: string;
  latestStage?: VkpiProjectStage;
  latestProjectAt: number;
  projectStages: VkpiProjectStage[];
  activeClaimId?: string;
  scanStatus?: string;
}

interface EffectiveMyKolItem extends MyKolItem {
  isFollowed: boolean;
  funnelStage: FunnelStageKey;
}

interface PlatformEntryMetric {
  platform: Exclude<PlatformFilter, 'all'>;
  kolCount: number;
  projectCount: number;
  views: number;
}

interface ContactDraft {
  contactEmail: string;
  contactPhone: string;
  profileUrl: string;
}

interface PostPreview {
  id: string;
  snapshotId: string;
  title: string;
  url: string;
  mediaUrl: string;
  videoUrl: string;
  imageUrl: string;
  imageUrls: string[];
  mediaUrls: string[];
  views: number;
  likes: number;
  comments: number;
  shares: number;
  publishedAt: string;
  contentType: string;
  brandMentions: string[];
  competitorMentions: string[];
  gearMentions: string[];
  rawText: string;
}

interface KolCommentItem {
  id: string;
  postUrl: string;
  author: string;
  text: string;
  likes: number;
  sentiment: string;
  intentTags: string[];
  createdAt: string;
}

interface KolPostState {
  items: PostPreview[];
  total: number;
  loading: boolean;
  requested: boolean;
  error: string;
}

interface KolCommentState {
  items: KolCommentItem[];
  total: number;
  loading: boolean;
  requested: boolean;
  error: string;
}

const VIEW_TABS: Array<{ key: MyKolView; label: string }> = [
  { key: 'watchlist', label: '关注列表' },
  { key: 'funnel', label: '合作漏斗' },
];

const FUNNEL_STAGES: Array<{ key: FunnelStageKey; label: string }> = [
  { key: 'claimed', label: '已认领' },
  { key: 'contacted', label: '已联系' },
  { key: 'replied', label: '已回复' },
  { key: 'agreed', label: '已合作' },
  { key: 'shipped', label: '已发货' },
  { key: 'received', label: '已到货' },
  { key: 'published', label: '已发布' },
  { key: 'measured', label: '已统计' },
];

const PLATFORM_OPTIONS: Array<{ key: PlatformFilter; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'Facebook', label: 'Facebook' },
  { key: 'Instagram', label: 'Instagram' },
  { key: 'TikTok', label: 'TikTok' },
  { key: 'YouTube', label: 'YouTube' },
  { key: 'X', label: 'X' },
  { key: 'Reddit', label: 'Reddit' },
];

const PLATFORM_ENTRIES: Exclude<PlatformFilter, 'all'>[] = ['Facebook', 'Instagram', 'TikTok', 'YouTube', 'X', 'Reddit'];

const CONTENT_SORT_OPTIONS: Array<{ key: KolContentSort; label: string }> = [
  { key: 'latest', label: '最新' },
  { key: 'views', label: '播放' },
  { key: 'likes', label: '点赞' },
  { key: 'comments', label: '评论' },
  { key: 'shares', label: '分享' },
];

const CONTENT_WINDOW_OPTIONS: Array<{ key: KolContentWindow; label: string }> = [
  { key: 'all', label: '全部时间' },
  { key: '7d', label: '近 7 天' },
  { key: '30d', label: '近 30 天' },
  { key: '90d', label: '近 90 天' },
  { key: '180d', label: '近 180 天' },
  { key: '365d', label: '近 1 年' },
  { key: 'year', label: '今年' },
];

const CONTENT_FILTER_OPTIONS: Array<{ key: KolContentFilter; label: string }> = [
  { key: 'all', label: '全部内容' },
  { key: 'viltrox', label: 'Viltrox相关' },
  { key: 'competitor', label: '竞品相关' },
  { key: 'gear', label: '设备已识别' },
  { key: 'other', label: '其它内容' },
];

const VILTROX_TERMS = ['viltrox', 'viltroxthailand', 'viltroxusa', 'viltrox cine', 'viltrox flash'];
const COMPETITOR_TERMS = ['sigma', 'tamron', 'sony', 'zeiss', 'sirui', 'laowa', 'meike', 'ttartisan', '7artisans'];
const GEAR_PATTERNS: Array<{ label: string; terms: string[] }> = [
  { label: 'Fuji', terms: ['fuji', 'fujifilm', 'x-t', 'xt5', 'x-h', 'xh2', 'gfx'] },
  { label: 'Sony', terms: ['sony', 'a7', 'fx3', 'fx30', 'zv-e'] },
  { label: 'Canon', terms: ['canon', 'eos', 'rf '] },
  { label: 'Nikon', terms: ['nikon', 'z6', 'z7', 'z8', 'z9'] },
  { label: 'Panasonic', terms: ['panasonic', 'lumix', 's5', 'gh6'] },
  { label: 'Blackmagic', terms: ['blackmagic', 'bmpcc'] },
  { label: '16mm', terms: ['16mm'] },
  { label: '27mm', terms: ['27mm'] },
  { label: '35mm', terms: ['35mm'] },
  { label: '56mm', terms: ['56mm'] },
  { label: '镜头', terms: ['lens', 'lenses', '镜头'] },
];

function normalizedKey(value: unknown) {
  return String(value || '').trim().toLowerCase();
}

function normalizedHandle(value: unknown) {
  const handle = String(value || '').trim();
  if (!handle || handle === '-') return '-';
  return handle.startsWith('@') ? handle : `@${handle}`;
}

function aliasFor(platform: unknown, value: unknown) {
  const text = normalizedKey(value).replace(/^@/, '');
  if (!text || text === '-') return '';
  return `${platformFromRaw(platform)}:${text}`;
}

function projectDate(project: VkpiProjectRow) {
  const parsed = Date.parse(project.updatedAt || project.latestMessageAt || project.closedAt || project.startedAt || project.createdAt || '');
  return Number.isFinite(parsed) ? parsed : 0;
}

function initialItemFromKol(kol: VkpiKolOption): MyKolItem {
  return {
    id: kol.id || aliasFor(kol.platform, kol.handle || kol.name) || `${kol.platform}:${kol.name}`,
    kolId: kol.id,
    name: kol.name || kol.handle || '未命名 KOL',
    handle: normalizedHandle(kol.handle),
    platform: platformFromRaw(kol.platform),
    avatar: kol.avatar,
    profileUrl: kol.profileUrl,
    contactEmail: kol.contactEmail,
    contactPhone: kol.contactPhone,
    contactLinks: kol.contactLinks || [],
    followers: kol.followerLabel || '-',
    contentCount: kol.contentCountLabel || '-',
    projectCount: 0,
    views: 0,
    clicks: 0,
    subStatus: kol.scanStatus || '未建项目',
    latestProjectAt: 0,
    projectStages: [],
    activeClaimId: kol.activeClaimId,
    scanStatus: kol.scanStatus,
  };
}

function initialItemFromProject(project: VkpiProjectRow): MyKolItem {
  return {
    id: project.kolId || aliasFor(project.platform, project.kolHandle || project.kolName) || `${project.platform}:${project.kolName}`,
    kolId: project.kolId || '',
    name: project.kolName || project.kolHandle || '未命名 KOL',
    handle: normalizedHandle(project.kolHandle),
    platform: platformFromRaw(project.platform),
    avatar: project.kolAvatar,
    contactLinks: [],
    followers: '-',
    contentCount: '-',
    projectCount: 0,
    views: 0,
    clicks: 0,
    subStatus: stageLabels[project.stage] || '跟进中',
    latestStage: project.stage,
    latestProjectAt: 0,
    projectStages: [],
  };
}

function funnelStageFor(item: MyKolItem): FunnelStageKey {
  if (item.projectCount === 0 || !item.latestStage) return 'claimed';
  if (item.latestStage === 'contacted') return 'contacted';
  if (item.latestStage === 'replied') return 'replied';
  if (item.latestStage === 'agreed') return 'agreed';
  if (item.latestStage === 'shipped') return 'shipped';
  if (item.latestStage === 'received') return 'received';
  if (['content_published', 'published', 'released'].includes(item.latestStage)) return 'published';
  if (['measured', 'closed'].includes(item.latestStage)) return 'measured';
  return 'claimed';
}

function buildMyKolItems(data: VkpiDashboardData): MyKolItem[] {
  const items = new Map<string, MyKolItem>();
  const aliases = new Map<string, string>();

  const registerAliases = (item: MyKolItem, kolId?: string) => {
    const idAlias = normalizedKey(kolId);
    if (idAlias) aliases.set(`id:${idAlias}`, item.id);
    [aliasFor(item.platform, item.handle), aliasFor(item.platform, item.name)].forEach((alias) => {
      if (alias) aliases.set(alias, item.id);
    });
  };

  data.kolOptions.forEach((kol) => {
    const item = initialItemFromKol(kol);
    items.set(item.id, item);
    registerAliases(item, kol.id);
  });

  data.projects.forEach((project) => {
    const projectAliases = [
      project.kolId ? `id:${normalizedKey(project.kolId)}` : '',
      aliasFor(project.platform, project.kolHandle),
      aliasFor(project.platform, project.kolName),
    ].filter(Boolean);
    const existingId = projectAliases.map((alias) => aliases.get(alias)).find(Boolean);
    const item = existingId ? items.get(existingId) : initialItemFromProject(project);
    if (!item) return;

    item.kolId = item.kolId || project.kolId || '';
    item.name = item.name === '-' ? project.kolName : item.name || project.kolName;
    item.handle = item.handle === '-' ? normalizedHandle(project.kolHandle) : item.handle || normalizedHandle(project.kolHandle);
    item.platform = item.platform || platformFromRaw(project.platform);
    item.avatar = item.avatar || project.kolAvatar;
    item.projectCount += 1;
    item.views += safeNumber(project.views);
    item.clicks += safeNumber(project.clicks);
    item.projectStages.push(project.stage);

    const updatedAt = projectDate(project);
    if (updatedAt >= item.latestProjectAt) {
      item.latestProjectAt = updatedAt;
      item.latestStage = project.stage;
    }

    items.set(item.id, item);
    registerAliases(item, project.kolId);
  });

  return Array.from(items.values()).map((item) => ({
    ...item,
    subStatus: item.projectCount === 0 ? item.scanStatus || '未建项目' : item.latestStage ? stageLabels[item.latestStage] : '跟进中',
  })).sort((left, right) => (
    right.projectCount - left.projectCount
    || right.views - left.views
    || left.name.localeCompare(right.name)
  ));
}

function displayCount(value: unknown) {
  const parsed = safeNumber(value);
  return parsed ? compactCount(parsed) : '0';
}

function initials(name: string) {
  return (name || 'K').trim().slice(0, 1).toUpperCase();
}

function cleanHandle(handle: string) {
  const value = String(handle || '').trim().replace(/^@/, '');
  return value && value !== '-' ? value : '';
}

function inferredProfileUrl(platform: VkpiPlatform, handle: string) {
  const safeHandle = cleanHandle(handle);
  if (!safeHandle) return '';
  if (platform === 'Instagram') return `https://www.instagram.com/${safeHandle}`;
  if (platform === 'TikTok') return `https://www.tiktok.com/@${safeHandle}`;
  if (platform === 'YouTube') return `https://www.youtube.com/@${safeHandle}`;
  if (platform === 'Facebook') return `https://www.facebook.com/${safeHandle}`;
  if (platform === 'X') return `https://x.com/${safeHandle}`;
  if (platform === 'Reddit') return `https://www.reddit.com/user/${safeHandle}`;
  return '';
}

function compactContactValue(value: string) {
  const text = String(value || '').replace(/^mailto:/, '').replace(/^tel:/, '').trim();
  if (!text) return '';
  if (text.length <= 30) return text;
  return `${text.slice(0, 16)}...${text.slice(-8)}`;
}

function textField(row: Record<string, unknown> | undefined, ...keys: string[]) {
  if (!row) return '';
  for (const key of keys) {
    const text = String(row[key] ?? '').trim();
    if (text) return text;
  }
  return '';
}

function parsedObject(value: unknown): Record<string, unknown> {
  if (value && typeof value === 'object' && !Array.isArray(value)) return value as Record<string, unknown>;
  if (typeof value !== 'string' || !value.trim()) return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

function parsedList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((item) => String(item || '').trim()).filter(Boolean);
  if (typeof value !== 'string') return [];
  const raw = value.trim();
  if (!raw) return [];
  if (raw.startsWith('[')) {
    try {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) return parsed.map((item) => String(item || '').trim()).filter(Boolean);
    } catch {
      return [raw];
    }
  }
  return [raw];
}

function textList(value: unknown): string[] {
  if (Array.isArray(value)) return value.flatMap((item) => textList(item));
  if (typeof value === 'string') {
    const raw = value.trim();
    if (!raw) return [];
    if (raw.startsWith('[') || raw.startsWith('{')) {
      try {
        return textList(JSON.parse(raw));
      } catch {
        return [raw];
      }
    }
    return [raw];
  }
  if (value && typeof value === 'object') {
    const row = value as Record<string, unknown>;
    return [
      textField(row, 'url', 'src', 'displayUrl', 'display_url', 'imageUrl', 'image_url', 'thumbnailUrl', 'thumbnail_url', 'coverUrl', 'cover_url', 'videoUrl', 'video_url', 'downloadUrl', 'downloadAddr', 'playUrl', 'play_url', 'mediaUrl', 'media_url'),
    ].filter(Boolean);
  }
  return [];
}

function mediaUrlsFrom(value: unknown, depth = 0): string[] {
  if (!value || depth > 4) return [];
  if (typeof value === 'string') {
    const text = value.trim();
    if (!text) return [];
    if (text.startsWith('[') || text.startsWith('{')) {
      try {
        return mediaUrlsFrom(JSON.parse(text), depth + 1);
      } catch {
        return /^https?:\/\//i.test(text) || text.startsWith('/') ? [text] : [];
      }
    }
    return /^https?:\/\//i.test(text) || text.startsWith('/') ? [text] : [];
  }
  if (Array.isArray(value)) return value.flatMap((item) => mediaUrlsFrom(item, depth + 1));
  if (typeof value === 'object') {
    const urls: string[] = [];
    Object.entries(value as Record<string, unknown>).forEach(([key, child]) => {
      const normalized = key.toLowerCase();
      if (/url|src|image|thumbnail|cover|display|media|video|download|play|photo|picture/.test(normalized)) {
        urls.push(...mediaUrlsFrom(child, depth + 1));
      }
    });
    return urls;
  }
  return [];
}

function uniqueStrings(values: string[]) {
  const seen = new Set<string>();
  return values.map((value) => value.trim()).filter((value) => {
    if (!value || seen.has(value)) return false;
    seen.add(value);
    return true;
  });
}

function renderableMediaUrl(url: string, platform: VkpiPlatform) {
  if (!url) return false;
  if (url.startsWith('/')) return true;
  return /^https?:\/\//i.test(url) && !['instagram', 'tiktok'].includes(platform.toLowerCase());
}

function matchTerms(text: string, terms: string[]) {
  const source = text.toLowerCase();
  return terms.filter((term) => source.includes(term.toLowerCase()));
}

function gearMentions(text: string) {
  const source = text.toLowerCase();
  return GEAR_PATTERNS.filter((pattern) => (
    pattern.terms.some((term) => source.includes(term.toLowerCase()))
  )).map((pattern) => pattern.label);
}

function compactDate(value: unknown) {
  const raw = String(value || '').trim();
  if (!raw) return '-';
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw.replace('T', ' ').replace('.000Z', '').replace('Z', '').slice(0, 16);
  const yyyy = parsed.getFullYear();
  const mm = String(parsed.getMonth() + 1).padStart(2, '0');
  const dd = String(parsed.getDate()).padStart(2, '0');
  const hh = String(parsed.getHours()).padStart(2, '0');
  const mi = String(parsed.getMinutes()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd} ${hh}:${mi}`;
}

function conciseText(value: string, max = 82) {
  const clean = String(value || '').replace(/\s+/g, ' ').trim();
  if (clean.length <= max) return clean;
  const splitAt = clean.lastIndexOf(' ', max);
  return `${clean.slice(0, splitAt > 32 ? splitAt : max).trim()}...`;
}

function contactLinksFromUnknown(value: unknown): VkpiContactLink[] {
  const source = Array.isArray(value) ? value : [];
  const links: VkpiContactLink[] = [];
  source.forEach((row) => {
    if (typeof row === 'string') {
      links.push({ label: row.includes('@') && !row.startsWith('http') ? '邮箱' : '链接', value: row, url: row.startsWith('http') || row.startsWith('mailto:') ? row : undefined });
      return;
    }
    const item = parsedObject(row);
    const url = textField(item, 'url', 'href');
    const valueText = textField(item, 'value', 'label') || url;
    if (!valueText && !url) return;
    links.push({ label: textField(item, 'label') || (url.includes('mailto:') ? '邮箱' : '链接'), value: valueText, url: url || undefined });
  });
  return links;
}

function contactDraftFor(item: MyKolItem, profile?: VkpiKolProfile, override?: Partial<ContactDraft>): ContactDraft {
  const contacts = profile?.contacts || {};
  return {
    contactEmail: override?.contactEmail ?? item.contactEmail ?? textField(contacts, 'email'),
    contactPhone: override?.contactPhone ?? item.contactPhone ?? textField(contacts, 'phone'),
    profileUrl: override?.profileUrl ?? item.profileUrl ?? textField(contacts, 'profile_url') ?? inferredProfileUrl(item.platform, item.handle),
  };
}

function contactItems(item: MyKolItem, profile?: VkpiKolProfile, override?: Partial<ContactDraft>): VkpiContactLink[] {
  const draft = contactDraftFor(item, profile, override);
  const contacts = profile?.contacts || {};
  const links: VkpiContactLink[] = [];
  if (draft.contactEmail) links.push({ label: '邮箱', value: draft.contactEmail, url: `mailto:${draft.contactEmail}` });
  if (draft.contactPhone) links.push({ label: '电话', value: draft.contactPhone, url: `tel:${draft.contactPhone}` });
  if (draft.profileUrl) links.push({ label: '主页', value: platformDisplay(item.platform), url: draft.profileUrl });
  item.contactLinks.forEach((link) => links.push(link));
  contactLinksFromUnknown(contacts.links).forEach((link) => links.push(link));
  const seen = new Set<string>();
  return links.filter((link) => {
    const key = String(link.url || link.value || '').toLowerCase();
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 4);
}

function postPreviews(profile?: VkpiKolProfile): PostPreview[] {
  const rows = ((profile?.posts || []).length ? profile?.posts : profile?.content_posts) || [];
  return mapPostRows(rows);
}

function mapPostRows(rows: Array<Record<string, unknown>>): PostPreview[] {
  return rows.map((row, index) => {
    const raw = parsedObject(row.raw_json);
    const fallbackMediaUrl = textField(row, 'media_url', 'mediaUrl', 'asset_url')
      || textField(raw, 'media_url', 'mediaUrl', 'displayUrl', 'display_url', 'imageUrl', 'image_url');
    const explicitVideoUrl = textField(
      row,
      'video_url',
      'videoUrl',
      'video_url_no_watermark',
      'videoUrlNoWaterMark',
      'video_download_url',
      'videoDownloadUrl',
      'downloadUrl',
      'downloadAddr',
      'playUrl',
      'play_url',
    ) || textField(
      raw,
      'video_url',
      'videoUrl',
      'video_url_no_watermark',
      'videoUrlNoWaterMark',
      'video_download_url',
      'videoDownloadUrl',
      'downloadUrl',
      'downloadAddr',
      'playUrl',
      'play_url',
      'url_to_video',
    );
    const explicitImageUrl = textField(row, 'thumbnail_url', 'image_url', 'imageUrl', 'cover_url', 'coverUrl', 'asset_url')
      || textField(raw, 'thumbnail', 'thumbnail_url', 'thumbnailUrl', 'displayUrl', 'display_url', 'imageUrl', 'image_url', 'coverUrl', 'cover_url', 'videoThumbnail', 'video_thumbnail');
    const rawMediaUrls = uniqueStrings([
      ...textList(row.media_urls || row.mediaUrls),
      ...textList(row.image_urls || row.imageUrls),
      ...mediaUrlsFrom(raw.media),
      ...mediaUrlsFrom(raw.images),
      ...mediaUrlsFrom(raw.image),
      ...mediaUrlsFrom(raw.photos),
      ...mediaUrlsFrom(raw.picture),
      ...mediaUrlsFrom(raw.displayResources),
      ...mediaUrlsFrom(raw.sidecarChildren),
      ...mediaUrlsFrom(raw.edge_sidecar_to_children),
      ...mediaUrlsFrom(raw.childPosts),
      ...mediaUrlsFrom(raw.video),
    ]);
    const videoCandidates = uniqueStrings([explicitVideoUrl, ...rawMediaUrls.filter((url) => likelyVideoUrl(url, textField(row, 'platform')))]);
    const imageCandidates = uniqueStrings([
      explicitImageUrl,
      fallbackMediaUrl && !likelyVideoUrl(fallbackMediaUrl, textField(row, 'platform')) ? fallbackMediaUrl : '',
      ...rawMediaUrls.filter((url) => !likelyVideoUrl(url, textField(row, 'platform'))),
    ]);
    const imageUrls = uniqueStrings(imageCandidates.map((url) => proxiedImageUrl(url)).filter((url) => renderableMediaUrl(url, platformFromRaw(row.platform))));
    const imageUrl = imageUrls[0] || '';
    const mediaUrl = fallbackMediaUrl || imageCandidates[0] || explicitVideoUrl;
    const videoUrl = videoCandidates[0] || (fallbackMediaUrl && likelyVideoUrl(fallbackMediaUrl, textField(row, 'platform')) ? fallbackMediaUrl : '');
    const url = textField(row, 'post_url', 'url', 'permalink') || textField(raw, 'url', 'postUrl', 'permalink');
    const title = textField(row, 'title', 'caption', 'description') || textField(raw, 'title', 'caption', 'text') || '主页内容';
    const rawText = `${title} ${JSON.stringify(raw).slice(0, 3000)}`;
    const brandMentions = Array.from(new Set([...parsedList(row.brand_mentions_json), ...matchTerms(rawText, VILTROX_TERMS)]));
    const competitorMentions = Array.from(new Set([...parsedList(row.competitor_mentions_json), ...matchTerms(rawText, COMPETITOR_TERMS)]));
    const gears = Array.from(new Set(gearMentions(rawText)));
    return {
      id: textField(row, 'id', 'post_url') || `${url}-${index}`,
      snapshotId: textField(row, 'snapshot_id', 'snapshotId'),
      title,
      url,
      mediaUrl,
      videoUrl,
      imageUrl,
      imageUrls,
      mediaUrls: rawMediaUrls,
      views: safeNumber(row.views || row.view_count || raw.views || raw.videoViewCount || raw.videoPlayCount),
      likes: safeNumber(row.likes || row.like_count || raw.likesCount || raw.likes),
      comments: safeNumber(row.comments || row.comment_count || raw.commentsCount || raw.comments),
      shares: safeNumber(row.shares || row.share_count || raw.sharesCount || raw.shares),
      publishedAt: textField(row, 'published_at', 'published') || textField(raw, 'publishedAt', 'timestamp', 'takenAtTimestamp'),
      contentType: textField(row, 'content_type', 'type') || textField(raw, 'type', 'productType') || 'post',
      brandMentions,
      competitorMentions,
      gearMentions: gears,
      rawText,
    };
  });
}

function mapCommentRows(rows: Array<Record<string, unknown>>): KolCommentItem[] {
  return rows.map((row, index) => ({
    id: textField(row, 'id') || `${textField(row, 'post_url')}-${index}`,
    postUrl: textField(row, 'post_url', 'url'),
    author: textField(row, 'author_handle', 'author') || '匿名',
    text: textField(row, 'comment_text', 'text') || '无正文',
    likes: safeNumber(row.like_count || row.likes),
    sentiment: textField(row, 'sentiment') || 'unknown',
    intentTags: parsedList(row.intent_tags_json || row.intentTags),
    createdAt: textField(row, 'created_at', 'createdAt'),
  }));
}

function categoryForPost(post: PostPreview): KolContentFilter {
  if (post.brandMentions.length) return 'viltrox';
  if (post.competitorMentions.length) return 'competitor';
  return 'other';
}

function kolMediaState(post: PostPreview, platform: VkpiPlatform) {
  const fallbackUrl = String(post.mediaUrl || '').trim();
  const fallbackLooksVideo = likelyVideoUrl(fallbackUrl, platform);
  const videoUrl = post.videoUrl || (fallbackLooksVideo ? fallbackUrl : '');
  const resolvedVideoUrl = videoUrl ? proxiedVideoUrl(videoUrl) : '';
  const imageCandidates = [
    ...(post.imageUrls || []),
    ...(post.mediaUrls || []).filter((url) => !likelyVideoUrl(url, platform)),
    ...(post.imageUrl ? [post.imageUrl] : []),
    ...(fallbackUrl && !fallbackLooksVideo ? [fallbackUrl] : []),
  ];
  const imageUrls = uniqueStrings(imageCandidates.map((url) => proxiedImageUrl(url)).filter((url) => renderableMediaUrl(url, platform)));
  const videoRenderable = renderableMediaUrl(resolvedVideoUrl, platform);
  const explicitKind = `${post.contentType}`.toLowerCase();
  const kind = videoRenderable
    ? 'video'
    : (imageUrls.length > 1 || explicitKind.includes('carousel') || explicitKind.includes('sidecar') ? 'carousel' : imageUrls.length ? 'image' : 'pending');
  return {
    kind,
    videoUrl: videoRenderable ? resolvedVideoUrl : '',
    imageUrls,
    renderable: videoRenderable || imageUrls.length > 0,
  };
}

function mediaBadge(post: PostPreview, platform: VkpiPlatform) {
  const media = kolMediaState(post, platform);
  if (media.kind === 'video') return 'video';
  if (media.kind === 'carousel') return media.imageUrls.length > 1 ? `1/${media.imageUrls.length}` : 'carousel';
  const type = `${post.contentType} ${post.rawText}`.toLowerCase();
  if (type.includes('video') || type.includes('reel')) return 'video';
  if (type.includes('carousel') || type.includes('sidecar')) return 'carousel';
  if (media.kind === 'image') return 'image';
  return '待缓存';
}

function KolMediaSlot({ post, platform, compact = false }: { post: PostPreview; platform: VkpiPlatform; compact?: boolean }) {
  const [active, setActive] = useState(0);
  const [failedImages, setFailedImages] = useState<Set<string>>(() => new Set());
  useEffect(() => {
    setActive(0);
    setFailedImages(new Set());
  }, [post.id, post.mediaUrl, post.videoUrl, post.imageUrls.join('|'), post.mediaUrls.join('|')]);

  const media = kolMediaState(post, platform);
  if (!media.renderable) return <span className="vkpi-my-kol-content-card__pending">待缓存</span>;

  const imageUrls = media.imageUrls.filter((url) => !failedImages.has(url));
  const currentImage = imageUrls[Math.min(active, Math.max(0, imageUrls.length - 1))];
  const markFailed = (url: string) => setFailedImages((prev) => {
    const next = new Set(prev);
    next.add(url);
    return next;
  });

  if (media.kind === 'video' && (!compact || !currentImage)) {
    return (
      <>
        <video
          src={media.videoUrl}
          poster={currentImage || imageUrls[0] || undefined}
          controls={!compact}
          muted={compact}
          playsInline
          preload="metadata"
        />
        {compact ? <span className="vkpi-my-kol-content-card__play">▶</span> : null}
      </>
    );
  }

  if (!currentImage) return <span className="vkpi-my-kol-content-card__pending">待缓存</span>;

  return (
    <div className="vkpi-my-kol-content-card__carousel">
      <img src={currentImage} alt="" loading="lazy" onError={() => markFailed(currentImage)} />
      {media.kind === 'video' ? <span className="vkpi-my-kol-content-card__play">▶</span> : null}
      {imageUrls.length > 1 ? (
        <>
          <button type="button" className="is-prev" onClick={(event) => { event.stopPropagation(); setActive((value) => (value + imageUrls.length - 1) % imageUrls.length); }} aria-label="上一张">‹</button>
          <button type="button" className="is-next" onClick={(event) => { event.stopPropagation(); setActive((value) => (value + 1) % imageUrls.length); }} aria-label="下一张">›</button>
          <span>{Math.min(active + 1, imageUrls.length)}/{imageUrls.length}</span>
        </>
      ) : null}
    </div>
  );
}

function KolMediaLightbox({ post, platform, onClose }: { post: PostPreview; platform: VkpiPlatform; onClose: () => void }) {
  const [active, setActive] = useState(0);
  const media = kolMediaState(post, platform);
  const isVideo = media.kind === 'video' && Boolean(media.videoUrl);

  useEffect(() => {
    setActive(0);
  }, [post.id]);

  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
      if (event.key === 'ArrowLeft' && media.imageUrls.length > 1) setActive((value) => (value + media.imageUrls.length - 1) % media.imageUrls.length);
      if (event.key === 'ArrowRight' && media.imageUrls.length > 1) setActive((value) => (value + 1) % media.imageUrls.length);
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [media.imageUrls.length, onClose]);

  const currentImage = media.imageUrls[Math.min(active, Math.max(0, media.imageUrls.length - 1))];
  const externalUrl = platformExternalUrl(post.url);

  return createPortal(
    <div className="vkpi-media-lightbox vkpi-kol-media-lightbox" role="dialog" aria-modal="true" aria-label="KOL内容媒体预览" onClick={onClose}>
      <div className="vkpi-media-lightbox__panel" onClick={(event) => event.stopPropagation()}>
        <header className="vkpi-media-lightbox__header">
          <div>
            <span>{platformDisplay(platform)} 本地预览</span>
            <h3 title={post.title}>{conciseText(post.title, 96)}</h3>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭">×</button>
        </header>
        <div className={`vkpi-media-lightbox__stage ${isVideo ? 'is-video' : 'is-image'}`}>
          {isVideo ? (
            <video src={media.videoUrl} poster={media.imageUrls[0] || undefined} controls playsInline autoPlay />
          ) : currentImage ? (
            <>
              <img src={currentImage} alt="" />
              {media.imageUrls.length > 1 ? (
                <>
                  <button type="button" className="is-prev" onClick={() => setActive((value) => (value + media.imageUrls.length - 1) % media.imageUrls.length)} aria-label="上一张">‹</button>
                  <button type="button" className="is-next" onClick={() => setActive((value) => (value + 1) % media.imageUrls.length)} aria-label="下一张">›</button>
                  <span>{active + 1}/{media.imageUrls.length}</span>
                </>
              ) : null}
            </>
          ) : (
            <div className="vkpi-media-lightbox__pending">当前帖子还没有缓存可播放媒体</div>
          )}
        </div>
        <footer className="vkpi-media-lightbox__footer">
          <span>播放 {displayCount(post.views)}</span>
          <span>点赞 {displayCount(post.likes)}</span>
          <span>评论 {displayCount(post.comments)}</span>
          <span>分享 {displayCount(post.shares)}</span>
          {externalUrl ? <a href={externalUrl} target="_blank" rel="noreferrer">打开原帖</a> : null}
          <small>{media.renderable ? '本地媒体代理' : '只保留原帖链接'} · {compactDate(post.publishedAt)}</small>
        </footer>
      </div>
    </div>,
    document.body,
  );
}

function postMatchesWindow(post: PostPreview, windowKey: KolContentWindow) {
  if (windowKey === 'all') return true;
  if (!post.publishedAt) return true;
  const parsed = new Date(post.publishedAt);
  if (Number.isNaN(parsed.getTime())) return true;
  const now = Date.now();
  if (windowKey === 'year') return parsed.getFullYear() === new Date().getFullYear();
  const days = Number(windowKey.replace('d', ''));
  return now - parsed.getTime() <= days * 24 * 60 * 60 * 1000;
}

function filterAndSortPosts(posts: PostPreview[], filter: KolContentFilter, sort: KolContentSort, direction: KolContentDirection, windowKey: KolContentWindow) {
  const directionFactor = direction === 'desc' ? -1 : 1;
  return posts.filter((post) => {
    if (!postMatchesWindow(post, windowKey)) return false;
    if (filter === 'all') return true;
    if (filter === 'gear') return post.gearMentions.length > 0;
    return categoryForPost(post) === filter;
  }).sort((left, right) => {
    if (sort === 'latest') {
      const leftTime = Date.parse(left.publishedAt || '') || 0;
      const rightTime = Date.parse(right.publishedAt || '') || 0;
      return (leftTime - rightTime) * directionFactor;
    }
    return (safeNumber(left[sort]) - safeNumber(right[sort])) * directionFactor;
  });
}

function summarizePostInsights(posts: PostPreview[], profile?: VkpiKolProfile) {
  const snapshot = profile?.snapshot || {};
  const viltroxCount = posts.filter((post) => categoryForPost(post) === 'viltrox').length;
  const competitorCount = posts.filter((post) => categoryForPost(post) === 'competitor').length;
  const totalViews = posts.reduce((sum, post) => sum + post.views, 0);
  const totalLikes = posts.reduce((sum, post) => sum + post.likes, 0);
  const totalComments = posts.reduce((sum, post) => sum + post.comments, 0);
  const totalShares = posts.reduce((sum, post) => sum + post.shares, 0);
  const gear = Array.from(new Set(posts.flatMap((post) => post.gearMentions))).slice(0, 4);
  const engagementBase = totalViews || 0;
  const engagement = engagementBase ? ((totalLikes + totalComments + totalShares) / engagementBase) * 100 : 0;
  return {
    viltroxCount,
    competitorCount,
    otherCount: Math.max(0, posts.length - viltroxCount - competitorCount),
    totalViews,
    totalLikes,
    totalComments,
    avgViews: posts.length ? Math.round(totalViews / posts.length) : 0,
    engagement,
    gearLabel: gear.length ? gear.join(' / ') : '设备待识别',
    scanLabel: compactDate(snapshot.scanned_at || snapshot.latest_scanned_at),
  };
}

function commentsForPost(post: PostPreview | null, comments: KolCommentItem[]) {
  if (!post) return [];
  const postUrl = post.url.trim().toLowerCase();
  return comments.filter((comment) => comment.postUrl.trim().toLowerCase() === postUrl);
}

function latestSnapshotPosts(posts: PostPreview[], profile?: VkpiKolProfile) {
  const latestSnapshotId = textField(profile?.snapshot, 'id');
  if (!latestSnapshotId) return posts;
  const current = posts.filter((post) => !post.snapshotId || post.snapshotId === latestSnapshotId);
  return current.length ? current : posts;
}

async function fetchAllKolPostRows(apiToken: string, kolId: string) {
  const rows: Array<Record<string, unknown>> = [];
  let total = 0;
  let offset = 0;
  const seenOffsets = new Set<number>();

  for (let page = 0; page < 30; page += 1) {
    if (seenOffsets.has(offset)) break;
    seenOffsets.add(offset);
    const response = await getKolPosts(apiToken, kolId, { limit: 100, offset });
    const pageRows = Array.isArray(response.items) ? response.items : [];
    rows.push(...pageRows);
    total = safeNumber(response.page?.total) || rows.length;
    const nextOffset = safeNumber(response.page?.next_offset);
    if (!nextOffset || nextOffset <= offset || !pageRows.length) break;
    offset = nextOffset;
  }

  return { rows, total: total || rows.length };
}

function searchMatches(item: MyKolItem, query: string) {
  if (!query) return true;
  const target = `${item.name} ${item.handle}`.toLowerCase();
  return target.includes(query.toLowerCase());
}

function summarize(items: EffectiveMyKolItem[]) {
  return items.reduce(
    (sum, item) => ({
      kols: sum.kols + 1,
      projects: sum.projects + item.projectCount,
      views: sum.views + item.views,
      clicks: sum.clicks + item.clicks,
    }),
    { kols: 0, projects: 0, views: 0, clicks: 0 },
  );
}

function buildPlatformMetrics(items: EffectiveMyKolItem[]): PlatformEntryMetric[] {
  return PLATFORM_ENTRIES.map((platform) => {
    const platformItems = items.filter((item) => item.platform === platform);
    return {
      platform,
      kolCount: platformItems.length,
      projectCount: platformItems.reduce((sum, item) => sum + item.projectCount, 0),
      views: platformItems.reduce((sum, item) => sum + item.views, 0),
    };
  });
}

interface MyKolMatrixProps {
  apiToken?: string;
  data: VkpiDashboardData;
  onRefreshData?: () => void;
}

export function MyKolMatrix({ apiToken, data, onRefreshData }: MyKolMatrixProps) {
  const [activeView, setActiveView] = useState<MyKolView>('watchlist');
  const [activeFunnelStage, setActiveFunnelStage] = useState<FunnelStageKey>('claimed');
  const [selectedKolId, setSelectedKolId] = useState('');
  const [query, setQuery] = useState('');
  const [platformFilter, setPlatformFilter] = useState<PlatformFilter>('all');
  const [subStatusFilter, setSubStatusFilter] = useState('all');
  const [viltroxRelated, setViltroxRelated] = useState(true);
  const [releasedClaimIds, setReleasedClaimIds] = useState<Set<string>>(() => new Set());
  const [claimedKolIds, setClaimedKolIds] = useState<Set<string>>(() => new Set());
  const [busyKolId, setBusyKolId] = useState('');
  const [scanningKolId, setScanningKolId] = useState('');
  const [savingContactId, setSavingContactId] = useState('');
  const [editingContactId, setEditingContactId] = useState('');
  const [contactDrafts, setContactDrafts] = useState<Record<string, ContactDraft>>({});
  const [contactOverrides, setContactOverrides] = useState<Record<string, Partial<ContactDraft>>>({});
  const [kolProfiles, setKolProfiles] = useState<Record<string, VkpiKolProfile>>({});
  const [kolPosts, setKolPosts] = useState<Record<string, KolPostState>>({});
  const [kolComments, setKolComments] = useState<Record<string, KolCommentState>>({});
  const [contentSort, setContentSort] = useState<KolContentSort>('latest');
  const [contentDirection, setContentDirection] = useState<KolContentDirection>('desc');
  const [contentWindow, setContentWindow] = useState<KolContentWindow>('all');
  const [contentFilter, setContentFilter] = useState<KolContentFilter>('all');
  const [previewPost, setPreviewPost] = useState<PostPreview | null>(null);
  const [commentPost, setCommentPost] = useState<PostPreview | null>(null);
  const [devicePopoverId, setDevicePopoverId] = useState('');
  const [accountLayerCollapsed, setAccountLayerCollapsed] = useState(false);
  const [contentLayerCollapsed, setContentLayerCollapsed] = useState(false);
  const [loadingProfileIds, setLoadingProfileIds] = useState<Set<string>>(() => new Set());
  const [requestedProfileIds, setRequestedProfileIds] = useState<Set<string>>(() => new Set());
  const [message, setMessage] = useState('');
  const rawItems = useMemo(() => buildMyKolItems(data), [data]);

  const items = useMemo<EffectiveMyKolItem[]>(() => rawItems.map((item) => {
    const isReleased = item.activeClaimId ? releasedClaimIds.has(item.activeClaimId) : false;
    const isClaimedNow = item.kolId ? claimedKolIds.has(item.kolId) : false;
    return {
      ...item,
      isFollowed: Boolean((item.activeClaimId && !isReleased) || isClaimedNow),
      funnelStage: funnelStageFor(item),
    };
  }).filter((item) => item.isFollowed || item.projectCount > 0), [claimedKolIds, rawItems, releasedClaimIds]);

  const watchlistCount = items.filter((item) => item.isFollowed).length;
  const funnelCount = items.filter((item) => item.projectCount > 0 || item.isFollowed).length;
  const funnelCounts = useMemo(() => FUNNEL_STAGES.reduce<Record<FunnelStageKey, number>>((counts, stage) => {
    counts[stage.key] = items.filter((item) => item.funnelStage === stage.key).length;
    return counts;
  }, { claimed: 0, contacted: 0, replied: 0, agreed: 0, shipped: 0, received: 0, published: 0, measured: 0 }), [items]);

  const subStatusOptions = useMemo(() => {
    const labels = new Set<string>();
    items.forEach((item) => {
      if (activeView === 'watchlist' && !item.isFollowed) return;
      if (activeView === 'funnel' && item.funnelStage !== activeFunnelStage) return;
      if (item.subStatus) labels.add(item.subStatus);
    });
    return Array.from(labels).sort((left, right) => left.localeCompare(right));
  }, [activeFunnelStage, activeView, items]);

  const platformBaseItems = useMemo(() => items.filter((item) => (
    (activeView === 'watchlist' ? item.isFollowed : item.funnelStage === activeFunnelStage)
    && searchMatches(item, query)
    && (subStatusFilter === 'all' || item.subStatus === subStatusFilter)
  )), [activeFunnelStage, activeView, items, query, subStatusFilter]);

  const filteredItems = useMemo(() => platformBaseItems.filter((item) => (
    platformFilter === 'all' || item.platform === platformFilter
  )), [platformBaseItems, platformFilter]);
  const selectedItem = useMemo(() => (
    filteredItems.find((item) => item.id === selectedKolId) || filteredItems[0]
  ), [filteredItems, selectedKolId]);

  const totals = useMemo(() => summarize(filteredItems), [filteredItems]);
  const platformMetrics = useMemo(() => buildPlatformMetrics(platformBaseItems), [platformBaseItems]);
  const maxFunnelCount = useMemo(() => Math.max(1, ...FUNNEL_STAGES.map((stage) => funnelCounts[stage.key])), [funnelCounts]);
  const viewLabel = viltroxRelated ? 'Viltrox播放' : '账号播放';
  const clickLabel = viltroxRelated ? 'Viltrox点击' : '总点击';
  const selectedProfile = selectedItem?.kolId ? kolProfiles[selectedItem.kolId] : undefined;
  const selectedPostState = selectedItem?.kolId ? kolPosts[selectedItem.kolId] : undefined;
  const selectedCommentState = selectedItem?.kolId ? kolComments[selectedItem.kolId] : undefined;
  const selectedSummary = selectedProfile?.summary || {};
  const profilePosts = useMemo(() => postPreviews(selectedProfile), [selectedProfile]);
  const selectedSourcePosts = selectedPostState?.items?.length ? selectedPostState.items : profilePosts;
  const selectedRawPosts = useMemo(() => latestSnapshotPosts(selectedSourcePosts, selectedProfile), [selectedProfile, selectedSourcePosts]);
  const selectedPosts = useMemo(() => (
    filterAndSortPosts(selectedRawPosts, contentFilter, contentSort, contentDirection, contentWindow)
  ), [contentDirection, contentFilter, contentSort, contentWindow, selectedRawPosts]);
  const selectedPostInsights = useMemo(() => summarizePostInsights(selectedRawPosts, selectedProfile), [selectedProfile, selectedRawPosts]);
  const selectedComments = selectedCommentState?.items || [];
  const selectedTotalPosts = selectedRawPosts.length || selectedPostState?.total || safeNumber(selectedSummary.content_count);
  const selectedProfileLoading = selectedItem?.kolId ? !selectedProfile && loadingProfileIds.has(selectedItem.kolId) : false;
  const selectedAvatar = selectedItem ? proxiedImageUrl(textField(selectedProfile?.kol, 'avatar_url') || selectedItem.avatar) : '';
  const selectedFollowerLabel = selectedItem && displayCount(selectedSummary.follower_count || 0) !== '0' ? displayCount(selectedSummary.follower_count) : selectedItem?.followers || '0';
  const selectedContentLabel = selectedItem && displayCount(selectedSummary.content_count || 0) !== '0' ? displayCount(selectedSummary.content_count) : selectedItem?.contentCount || '0';
  const selectedContacts = selectedItem ? contactItems(selectedItem, selectedProfile, contactOverrides[selectedItem.id]) : [];
  const selectedDraft = selectedItem ? contactDrafts[selectedItem.id] || contactDraftFor(selectedItem, selectedProfile, contactOverrides[selectedItem.id]) : undefined;

  useEffect(() => {
    if (!filteredItems.length) {
      if (selectedKolId) setSelectedKolId('');
      return;
    }
    if (!filteredItems.some((item) => item.id === selectedKolId)) {
      setSelectedKolId(filteredItems[0].id);
    }
  }, [filteredItems, selectedKolId]);

  useEffect(() => {
    if (!apiToken) return;
    const ids = Array.from(new Set(filteredItems.map((item) => item.kolId).filter(Boolean))).slice(0, 8)
      .filter((id) => !kolProfiles[id] && !loadingProfileIds.has(id) && !requestedProfileIds.has(id));
    if (!ids.length) return;
    setRequestedProfileIds((current) => {
      const next = new Set(current);
      ids.forEach((id) => next.add(id));
      return next;
    });
    setLoadingProfileIds((current) => {
      const next = new Set(current);
      ids.forEach((id) => next.add(id));
      return next;
    });
    ids.forEach((kolId) => {
      getKolProfile(apiToken, kolId)
        .then((profile) => {
          setKolProfiles((current) => ({ ...current, [kolId]: profile }));
        })
        .catch(() => undefined)
        .finally(() => {
          setLoadingProfileIds((current) => {
            const next = new Set(current);
            next.delete(kolId);
            return next;
          });
        });
    });
  }, [apiToken, filteredItems, kolProfiles, loadingProfileIds, requestedProfileIds]);

  useEffect(() => {
    setCommentPost(null);
    setPreviewPost(null);
    setDevicePopoverId('');
    setContentFilter('all');
  }, [selectedItem?.id]);

  useEffect(() => {
    if (!apiToken || !selectedItem?.kolId) return;
    const kolId = selectedItem.kolId;
    const postState = kolPosts[kolId];
    if (!postState?.requested && !postState?.loading) {
      setKolPosts((current) => ({
        ...current,
        [kolId]: { items: current[kolId]?.items || [], total: current[kolId]?.total || 0, loading: true, requested: true, error: '' },
      }));
      fetchAllKolPostRows(apiToken, kolId)
        .then((response) => {
          setKolPosts((current) => ({
            ...current,
            [kolId]: {
              items: mapPostRows(response.rows),
              total: response.total,
              loading: false,
              requested: true,
              error: '',
            },
          }));
        })
        .catch((error) => {
          setKolPosts((current) => ({
            ...current,
            [kolId]: {
              items: current[kolId]?.items || [],
              total: current[kolId]?.total || 0,
              loading: false,
              requested: true,
              error: error instanceof Error ? error.message : '内容加载失败',
            },
          }));
        });
    }

    const commentState = kolComments[kolId];
    if (!commentState?.requested && !commentState?.loading) {
      setKolComments((current) => ({
        ...current,
        [kolId]: { items: current[kolId]?.items || [], total: current[kolId]?.total || 0, loading: true, requested: true, error: '' },
      }));
      getKolComments(apiToken, kolId, { limit: 100 })
        .then((response) => {
          const rows = Array.isArray(response.items) ? response.items : [];
          setKolComments((current) => ({
            ...current,
            [kolId]: {
              items: mapCommentRows(rows),
              total: safeNumber(response.page?.total) || rows.length,
              loading: false,
              requested: true,
              error: '',
            },
          }));
        })
        .catch((error) => {
          setKolComments((current) => ({
            ...current,
            [kolId]: {
              items: current[kolId]?.items || [],
              total: current[kolId]?.total || 0,
              loading: false,
              requested: true,
              error: error instanceof Error ? error.message : '评论加载失败',
            },
          }));
        });
    }
  }, [apiToken, kolComments, kolPosts, selectedItem?.kolId]);

  const selectView = (view: MyKolView) => {
    setActiveView(view);
    setSubStatusFilter('all');
  };

  const toggleFollow = async (item: EffectiveMyKolItem) => {
    if (!apiToken || !item.kolId) {
      setMessage('缺少登录 token 或 KOL ID，不能更新关注状态。');
      return;
    }
    setBusyKolId(item.id);
    setMessage('');
    try {
      if (item.isFollowed && item.activeClaimId && !releasedClaimIds.has(item.activeClaimId)) {
        await releaseKolClaim(apiToken, item.activeClaimId, 'employee_unfollow');
        setReleasedClaimIds((current) => new Set(current).add(item.activeClaimId || ''));
        setClaimedKolIds((current) => {
          const next = new Set(current);
          next.delete(item.kolId);
          return next;
        });
        setMessage(`已取消关注：${item.name}`);
      } else if (!item.isFollowed) {
        await claimKol(apiToken, item.kolId);
        setClaimedKolIds((current) => new Set(current).add(item.kolId));
        setMessage(`已关注：${item.name}`);
      }
      onRefreshData?.();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '关注状态更新失败');
    } finally {
      setBusyKolId('');
    }
  };

  const startContactEdit = (item: EffectiveMyKolItem) => {
    setEditingContactId(item.id);
    setContactDrafts((current) => ({
      ...current,
      [item.id]: contactDraftFor(item, kolProfiles[item.kolId], contactOverrides[item.id]),
    }));
  };

  const saveContact = async (item: EffectiveMyKolItem) => {
    const draft = contactDrafts[item.id];
    if (!apiToken || !item.kolId || !draft) {
      setMessage('缺少登录 token 或 KOL ID，不能保存联系方式。');
      return;
    }
    setSavingContactId(item.id);
    setMessage('');
    try {
      await updateMarketingKol(apiToken, item.kolId, {
        contactEmail: draft.contactEmail.trim(),
        contactPhone: draft.contactPhone.trim(),
        profileUrl: draft.profileUrl.trim(),
      });
      setContactOverrides((current) => ({ ...current, [item.id]: draft }));
      setEditingContactId('');
      setMessage(`已保存联系方式：${item.name}`);
      onRefreshData?.();
      const profile = await getKolProfile(apiToken, item.kolId).catch(() => null);
      if (profile) setKolProfiles((current) => ({ ...current, [item.kolId]: profile }));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '联系方式保存失败');
    } finally {
      setSavingContactId('');
    }
  };

  const scanAccount = async (item: EffectiveMyKolItem) => {
    if (!apiToken || !item.kolId) {
      setMessage('缺少登录 token 或 KOL ID，不能抓取账号。');
      return;
    }
    setScanningKolId(item.id);
    setMessage(`正在抓取 ${platformDisplay(item.platform)} 账号数据：${item.handle}`);
    try {
      await scanKolAccount(apiToken, item.kolId, 24);
      const [profile, postsResponse, commentsResponse] = await Promise.all([
        getKolProfile(apiToken, item.kolId),
        fetchAllKolPostRows(apiToken, item.kolId).catch(() => null),
        getKolComments(apiToken, item.kolId, { limit: 100 }).catch(() => null),
      ]);
      setKolProfiles((current) => ({ ...current, [item.kolId]: profile }));
      if (postsResponse) {
        setKolPosts((current) => ({
          ...current,
          [item.kolId]: { items: mapPostRows(postsResponse.rows), total: postsResponse.total, loading: false, requested: true, error: '' },
        }));
      }
      if (commentsResponse) {
        const rows = Array.isArray(commentsResponse.items) ? commentsResponse.items : [];
        setKolComments((current) => ({
          ...current,
          [item.kolId]: { items: mapCommentRows(rows), total: safeNumber(commentsResponse.page?.total) || rows.length, loading: false, requested: true, error: '' },
        }));
      }
      setMessage(`已抓取账号数据：${item.name}`);
      onRefreshData?.();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '账号抓取失败');
    } finally {
      setScanningKolId('');
    }
  };

  return (
    <section className="vkpi-my-kol-matrix">
      <header className="vkpi-my-kol-matrix__header">
        <div>
          <span>员工KOL库</span>
          <h2>我的KOL</h2>
          <em>{numberFormatter.format(items.length)} 个KOL</em>
        </div>
        <div className="vkpi-my-kol-matrix__totals">
          <span><strong>{numberFormatter.format(totals.kols)}</strong><small>KOL</small></span>
          <span><strong>{numberFormatter.format(totals.projects)}</strong><small>项目</small></span>
          <span className="is-primary"><strong>{displayCount(totals.views)}</strong><small>{viewLabel}</small></span>
          <span><strong>{displayCount(totals.clicks)}</strong><small>{clickLabel}</small></span>
        </div>
      </header>

      <div className="vkpi-my-kol-tabs" role="tablist" aria-label="我的KOL视图">
        {VIEW_TABS.map((view) => (
          <button
            aria-selected={activeView === view.key}
            className={`vkpi-my-kol-tab${activeView === view.key ? ' is-active' : ''}`}
            key={view.key}
            onClick={() => selectView(view.key)}
            role="tab"
            type="button"
          >
            <span>{view.label}</span>
            <strong>{numberFormatter.format(view.key === 'watchlist' ? watchlistCount : funnelCount)}</strong>
          </button>
        ))}
      </div>

      {activeView === 'funnel' ? (
        <div className="vkpi-my-kol-funnel" aria-label="KOL 合作漏斗">
          {FUNNEL_STAGES.map((stage, index) => {
            const count = funnelCounts[stage.key];
            const active = activeFunnelStage === stage.key;
            const meterWidth = count ? Math.max(12, Math.round((count / maxFunnelCount) * 100)) : 0;
            return (
              <button
                className={`vkpi-my-kol-funnel__row${active ? ' is-active' : ''}`}
                key={stage.key}
                onClick={() => {
                  setActiveFunnelStage(stage.key);
                  setSubStatusFilter('all');
                }}
                type="button"
              >
                <span className="vkpi-my-kol-funnel__step">{String(index + 1).padStart(2, '0')}</span>
                <span className="vkpi-my-kol-funnel__meta"><b>{stage.label}</b><strong>{numberFormatter.format(count)}</strong></span>
                <span className="vkpi-my-kol-funnel__meter" aria-hidden="true"><i style={{ width: `${meterWidth}%` }} /></span>
              </button>
            );
          })}
        </div>
      ) : null}

      <div className="vkpi-my-kol-toolbar" aria-label="我的KOL筛选">
        <label className="vkpi-my-kol-search">
          <span>搜索</span>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索 KOL / handle" />
        </label>
        <label>
          <span>平台</span>
          <select value={platformFilter} onChange={(event) => setPlatformFilter(event.target.value as PlatformFilter)}>
            {PLATFORM_OPTIONS.map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}
          </select>
        </label>
        <label>
          <span>子状态</span>
          <select value={subStatusFilter} onChange={(event) => setSubStatusFilter(event.target.value)}>
            <option value="all">全部</option>
            {subStatusOptions.map((status) => <option key={status} value={status}>{status}</option>)}
          </select>
        </label>
        <label className="vkpi-my-kol-switch">
          <input checked={viltroxRelated} onChange={(event) => setViltroxRelated(event.target.checked)} type="checkbox" />
          <span aria-hidden="true" />
          <em>Viltrox相关</em>
        </label>
      </div>

      <div className="vkpi-my-kol-platforms" aria-label="平台入口">
        {platformMetrics.map((entry) => (
          <button
            className={`vkpi-my-kol-platform-card${platformFilter === entry.platform ? ' is-active' : ''}`}
            key={entry.platform}
            onClick={() => setPlatformFilter(entry.platform)}
            type="button"
          >
            <PlatformLogo platform={entry.platform} label={platformDisplay(entry.platform)} size="small" />
            <strong>{platformDisplay(entry.platform)}</strong>
            <small>{numberFormatter.format(entry.kolCount)} KOL</small>
            <div>
              <span><b>{numberFormatter.format(entry.projectCount)}</b>项目</span>
              <span><b>{displayCount(entry.views)}</b>{viewLabel}</span>
            </div>
          </button>
        ))}
      </div>

      {message ? <div className="vkpi-my-kol-message">{message}</div> : null}

      {filteredItems.length ? (
        <>
          <section className="vkpi-my-kol-accounts">
            <div className="vkpi-my-kol-accounts__header">
              <div>
                <span>账号层</span>
                <h3>{platformFilter === 'all' ? '我的 KOL 账号' : `${platformFilter} KOL 账号`}</h3>
              </div>
              <div className="vkpi-my-kol-section-actions">
                <strong>{numberFormatter.format(filteredItems.length)} 个账号</strong>
                <button
                  aria-expanded={!accountLayerCollapsed}
                  className="vkpi-my-kol-section-toggle"
                  onClick={() => setAccountLayerCollapsed((value) => !value)}
                  type="button"
                >
                  {accountLayerCollapsed ? '展开' : '折叠'}
                </button>
              </div>
            </div>
            {accountLayerCollapsed ? (
              <div className="vkpi-my-kol-section-collapsed">账号层已折叠 · {numberFormatter.format(filteredItems.length)} 个账号</div>
            ) : (
            <div className="vkpi-my-kol-account-grid">
              {filteredItems.map((item) => {
                const profile = kolProfiles[item.kolId];
                const contacts = contactItems(item, profile, contactOverrides[item.id]);
                const avatar = proxiedImageUrl(textField(profile?.kol, 'avatar_url') || item.avatar);
                const summary = profile?.summary || {};
                const followerLabel = displayCount(summary.follower_count || 0) !== '0' ? displayCount(summary.follower_count) : item.followers;
                const contentLabel = displayCount(summary.content_count || 0) !== '0' ? displayCount(summary.content_count) : item.contentCount;
                const accountPosts = latestSnapshotPosts(kolPosts[item.kolId]?.items?.length ? kolPosts[item.kolId].items : postPreviews(profile), profile);
                const accountInsights = summarizePostInsights(accountPosts, profile);
                const active = selectedItem?.id === item.id;
                return (
                  <article
                    className={`vkpi-my-kol-account-card${active ? ' is-active' : ''}`}
                    key={item.id}
                    onClick={() => setSelectedKolId(item.id)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') setSelectedKolId(item.id);
                    }}
                    role="button"
                    tabIndex={0}
                  >
                    <div className="vkpi-my-kol-account-card__avatar">
                      {avatar ? <img src={avatar} alt="" loading="lazy" /> : <span>{initials(item.name)}</span>}
                    </div>
                    <div className="vkpi-my-kol-account-card__main">
                      <div className="vkpi-my-kol-account-card__title">
                        <h3>{item.name}</h3>
                        <strong>{followerLabel}</strong>
                      </div>
                      <p>{item.handle} · {platformDisplay(item.platform)} · {contentLabel} 内容</p>
                      <div className="vkpi-my-kol-account-card__metrics">
                        <span>播放 {displayCount(accountInsights.totalViews)}</span>
                        <span>评论 {displayCount(accountInsights.totalComments)}</span>
                        <span>互动率 {accountInsights.engagement ? `${accountInsights.engagement.toFixed(2)}%` : '-'}</span>
                      </div>
                      <div className="vkpi-my-kol-account-card__chips">
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            setDevicePopoverId((current) => (current === item.id ? '' : item.id));
                          }}
                        >
                          设备分析
                        </button>
                        <span>V内容 {numberFormatter.format(accountInsights.viltroxCount)}</span>
                        <span>竞品 {numberFormatter.format(accountInsights.competitorCount)}</span>
                        <span>均播 {displayCount(accountInsights.avgViews)}</span>
                      </div>
                      {devicePopoverId === item.id ? (
                        <div className="vkpi-my-kol-account-popover" onClick={(event) => event.stopPropagation()} role="dialog" aria-label={`${item.name} 设备与内容分析`}>
                          <header>
                            <strong>设备与内容分析</strong>
                            <button type="button" onClick={() => setDevicePopoverId('')} aria-label="关闭">×</button>
                          </header>
                          <dl>
                            <div><dt>设备使用</dt><dd>{accountInsights.gearLabel}</dd></div>
                            <div><dt>Viltrox内容</dt><dd>{numberFormatter.format(accountInsights.viltroxCount)}</dd></div>
                            <div><dt>竞品内容</dt><dd>{numberFormatter.format(accountInsights.competitorCount)}</dd></div>
                            <div><dt>其它内容</dt><dd>{numberFormatter.format(accountInsights.otherCount)}</dd></div>
                            <div><dt>最后抓取</dt><dd>{accountInsights.scanLabel}</dd></div>
                            <div><dt>平均播放</dt><dd>{displayCount(accountInsights.avgViews)}</dd></div>
                            <div><dt>互动率</dt><dd>{accountInsights.engagement ? `${accountInsights.engagement.toFixed(2)}%` : '-'}</dd></div>
                          </dl>
                        </div>
                      ) : null}
                      <small>{profile ? '已抓取账号数据' : '待抓取账号数据'} · {item.subStatus} · {item.isFollowed ? '已关注' : '未关注'} · {contacts.length ? `联系 ${contacts.length}` : '待补联系方式'}</small>
                    </div>
                  </article>
                );
              })}
            </div>
            )}
          </section>

          {selectedItem ? (
            <section className="vkpi-my-kol-content-layer">
              <header className="vkpi-my-kol-content-layer__header">
                <div className="vkpi-my-kol-content-layer__identity">
                  <div className="vkpi-my-kol-content-layer__avatar">
                    {selectedAvatar ? <img src={selectedAvatar} alt="" loading="lazy" /> : <span>{initials(selectedItem.name)}</span>}
                  </div>
                  <div>
                    <span>内容层</span>
                    <h3>{selectedItem.name}</h3>
                    <p>{selectedItem.handle} · {selectedFollowerLabel} 粉丝 · {selectedContentLabel} 内容</p>
                  </div>
                </div>
                <div className="vkpi-my-kol-section-actions">
                  <strong>{selectedFollowerLabel}</strong>
                  <button
                    aria-expanded={!contentLayerCollapsed}
                    className="vkpi-my-kol-section-toggle"
                    onClick={() => setContentLayerCollapsed((value) => !value)}
                    type="button"
                  >
                    {contentLayerCollapsed ? '展开' : '折叠'}
                  </button>
                </div>
              </header>

              {contentLayerCollapsed ? (
                <div className="vkpi-my-kol-section-collapsed">内容层已折叠 · {numberFormatter.format(selectedTotalPosts)} 条历史内容</div>
              ) : (
              <>
              <div className="vkpi-my-kol-content-toolbar">
                <div className="vkpi-my-kol-content-toolbar__sort" aria-label="KOL内容排序">
                  {CONTENT_SORT_OPTIONS.map((option) => (
                    <button
                      className={contentSort === option.key ? 'is-active' : ''}
                      key={option.key}
                      onClick={() => setContentSort(option.key)}
                      type="button"
                    >
                      {option.label}
                    </button>
                  ))}
                  <select value={contentWindow} onChange={(event) => setContentWindow(event.target.value as KolContentWindow)} aria-label="时间范围">
                    {CONTENT_WINDOW_OPTIONS.map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}
                  </select>
                  <select value={contentDirection} onChange={(event) => setContentDirection(event.target.value as KolContentDirection)} aria-label="排序方向">
                    <option value="desc">{contentSort === 'latest' ? '最新优先' : '最高优先'}</option>
                    <option value="asc">{contentSort === 'latest' ? '最早优先' : '最低优先'}</option>
                  </select>
                </div>
                <div className="vkpi-my-kol-content-toolbar__actions">
                  <button type="button" onClick={() => startContactEdit(selectedItem)}>{selectedContacts.length ? '编辑联系方式' : '补联系方式'}</button>
                  <button type="button" disabled={scanningKolId === selectedItem.id || !apiToken || !selectedItem.kolId} onClick={() => void scanAccount(selectedItem)}>
                    {scanningKolId === selectedItem.id ? '抓取中' : `抓取${platformDisplay(selectedItem.platform)}`}
                  </button>
                  <button
                    className={selectedItem.isFollowed ? 'is-danger' : ''}
                    disabled={busyKolId === selectedItem.id || !apiToken || !selectedItem.kolId}
                    type="button"
                    onClick={() => void toggleFollow(selectedItem)}
                  >
                    {!selectedItem.kolId ? '缺KOL ID' : selectedItem.isFollowed ? '不关注' : '关注'}
                  </button>
                </div>
                <span>{selectedProfileLoading || selectedPostState?.loading ? '加载中' : selectedPosts.length ? `1-${numberFormatter.format(selectedPosts.length)} / ${numberFormatter.format(selectedTotalPosts)}` : '0 / 0'}</span>
              </div>

              <div className="vkpi-my-kol-content-insights" aria-label="KOL内容分析">
                <span><b>设备使用</b>{selectedPostInsights.gearLabel}</span>
                <span><b>Viltrox内容</b>{numberFormatter.format(selectedPostInsights.viltroxCount)}</span>
                <span><b>竞品内容</b>{numberFormatter.format(selectedPostInsights.competitorCount)}</span>
                <span><b>其它内容</b>{numberFormatter.format(selectedPostInsights.otherCount)}</span>
                <span><b>最后抓取</b>{selectedPostInsights.scanLabel}</span>
                <span><b>平均播放</b>{displayCount(selectedPostInsights.avgViews)}</span>
                <span><b>互动率</b>{selectedPostInsights.engagement ? `${selectedPostInsights.engagement.toFixed(2)}%` : '-'}</span>
              </div>

              <div className="vkpi-my-kol-content-filters" aria-label="内容分类筛选">
                {CONTENT_FILTER_OPTIONS.map((option) => (
                  <button
                    className={contentFilter === option.key ? 'is-active' : ''}
                    key={option.key}
                    onClick={() => setContentFilter(option.key)}
                    type="button"
                  >
                    {option.label}
                  </button>
                ))}
              </div>

              <div className="vkpi-my-kol-content-contacts">
                <span>联系</span>
                {selectedContacts.length ? selectedContacts.map((contact) => (
                  contact.url ? (
                    <a href={contact.url} key={`${contact.label}-${contact.value}`} target="_blank" rel="noreferrer">
                      <b>{contact.label}</b>{compactContactValue(contact.value)}
                    </a>
                  ) : (
                    <em key={`${contact.label}-${contact.value}`}>
                      <b>{contact.label}</b>{compactContactValue(contact.value)}
                    </em>
                  )
                )) : <em><b>暂无</b>未补联系方式</em>}
              </div>

              {editingContactId === selectedItem.id && selectedDraft ? (
                <div className="vkpi-my-kol-contact-editor">
                  <label><span>邮箱</span><input value={selectedDraft.contactEmail} onChange={(event) => setContactDrafts((current) => ({ ...current, [selectedItem.id]: { ...selectedDraft, contactEmail: event.target.value } }))} placeholder="email@example.com" /></label>
                  <label><span>手机号 / WhatsApp</span><input value={selectedDraft.contactPhone} onChange={(event) => setContactDrafts((current) => ({ ...current, [selectedItem.id]: { ...selectedDraft, contactPhone: event.target.value } }))} placeholder="+1 ..." /></label>
                  <label><span>主页</span><input value={selectedDraft.profileUrl} onChange={(event) => setContactDrafts((current) => ({ ...current, [selectedItem.id]: { ...selectedDraft, profileUrl: event.target.value } }))} placeholder="https://..." /></label>
                  <div>
                    <button className="vkpi-my-kol-action" disabled={savingContactId === selectedItem.id || !apiToken || !selectedItem.kolId} type="button" onClick={() => void saveContact(selectedItem)}>保存</button>
                    <button className="vkpi-my-kol-action is-muted" type="button" onClick={() => setEditingContactId('')}>取消</button>
                  </div>
                </div>
              ) : null}

              {selectedPosts.length ? (
                <div className="vkpi-my-kol-content-list">
                  {selectedPosts.map((post) => (
                    <article className="vkpi-my-kol-content-card" key={post.id}>
                      <div
                        className="vkpi-my-kol-content-card__media"
                        onClick={() => setPreviewPost(post)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' || event.key === ' ') setPreviewPost(post);
                        }}
                        role="button"
                        tabIndex={0}
                      >
                        <span className={`vkpi-my-kol-content-card__badge is-${categoryForPost(post)}`}>
                          {categoryForPost(post) === 'viltrox' ? 'Viltrox相关' : categoryForPost(post) === 'competitor' ? '竞品相关' : '其它内容'}
                        </span>
                        <span className="vkpi-my-kol-content-card__kind">{mediaBadge(post, selectedItem.platform)}</span>
                        <KolMediaSlot post={post} platform={selectedItem.platform} compact />
                      </div>
                      <div className="vkpi-my-kol-content-card__body">
                        <h3 title={post.title}>{conciseText(post.title, 96)}</h3>
                        {post.gearMentions.length ? <p>设备：{post.gearMentions.join(' / ')}</p> : <p>设备待识别</p>}
                        <div className="vkpi-my-kol-content-card__metrics">
                          <span><strong>播放</strong>{displayCount(post.views)}</span>
                          <span><strong>点赞</strong>{displayCount(post.likes)}</span>
                          <button type="button" onClick={() => setCommentPost(post)}><strong>评论</strong>{displayCount(post.comments)}</button>
                          <span><strong>分享</strong>{displayCount(post.shares)}</span>
                        </div>
                      </div>
                      <footer className="vkpi-my-kol-content-card__footer">
                        <small>{compactDate(post.publishedAt)}</small>
                        <div>
                          <button type="button" onClick={() => setCommentPost(post)}>评论明细</button>
                          {post.url ? <a href={post.url} target="_blank" rel="noreferrer">打开原帖</a> : null}
                        </div>
                      </footer>
                    </article>
                  ))}
                </div>
              ) : (
                <div className="vkpi-my-kol-content-empty">
                  <span>{selectedPostState?.loading ? '主页内容加载中。' : '暂无符合筛选条件的主页视频 / 帖子样本。'}</span>
                  <button type="button" onClick={() => void scanAccount(selectedItem)} disabled={scanningKolId === selectedItem.id || !apiToken || !selectedItem.kolId}>
                    {scanningKolId === selectedItem.id ? '抓取中' : '抓取主页内容'}
                  </button>
                </div>
              )}
              {selectedPostState?.error ? <div className="vkpi-my-kol-message">{selectedPostState.error}</div> : null}
              {selectedCommentState?.error ? <div className="vkpi-my-kol-message">{selectedCommentState.error}</div> : null}
              </>
              )}
            </section>
          ) : null}

          {commentPost ? (
            <div className="vkpi-my-kol-comment-modal" role="dialog" aria-modal="true" aria-label="KOL评论明细">
              <section>
                <header>
                  <div>
                    <span>评论层</span>
                    <h3>{conciseText(commentPost.title, 72)}</h3>
                    <p>{displayCount(commentPost.comments)} 条公开评论 · {commentsForPost(commentPost, selectedComments).length} 条正文缓存</p>
                  </div>
                  <button type="button" onClick={() => setCommentPost(null)}>关闭</button>
                </header>
                <div className="vkpi-my-kol-comment-list">
                  {commentsForPost(commentPost, selectedComments).length ? commentsForPost(commentPost, selectedComments).map((comment) => (
                    <article key={comment.id}>
                      <header><strong>{comment.author}</strong><span>赞 {numberFormatter.format(comment.likes)}</span></header>
                      <p>{comment.text}</p>
                      <small>{comment.intentTags.length ? comment.intentTags.join(' / ') : comment.sentiment} · {compactDate(comment.createdAt)}</small>
                    </article>
                  )) : (
                    <div className="vkpi-my-kol-content-empty">
                      <span>{commentPost.comments > 0 ? `评论数已同步：${numberFormatter.format(commentPost.comments)} 条；评论正文还未缓存。` : '当前帖子暂无评论正文缓存。'}</span>
                      <button type="button" onClick={() => selectedItem ? void scanAccount(selectedItem) : undefined} disabled={!selectedItem || scanningKolId === selectedItem.id || !apiToken || !selectedItem.kolId}>
                        {selectedItem && scanningKolId === selectedItem.id ? '抓取中' : '重新抓取评论'}
                      </button>
                    </div>
                  )}
                </div>
                <footer>
                  {commentPost.url ? <a href={commentPost.url} target="_blank" rel="noreferrer">打开原帖</a> : <span />}
                  <button type="button" onClick={() => setCommentPost(null)}>完成</button>
                </footer>
              </section>
            </div>
          ) : null}
          {previewPost && selectedItem ? (
            <KolMediaLightbox post={previewPost} platform={selectedItem.platform} onClose={() => setPreviewPost(null)} />
          ) : null}
        </>
      ) : (
        <div className="vkpi-my-kol-empty">
          {activeView === 'watchlist' ? '当前没有关注 KOL。去搜索红人后点“关注”，这里才会出现。' : '当前漏斗阶段没有 KOL。'}
        </div>
      )}
    </section>
  );
}
