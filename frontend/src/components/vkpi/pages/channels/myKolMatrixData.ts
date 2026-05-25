import { getKolPosts } from '../../../../domains/channels';
import type { VkpiContactLink, VkpiDashboardData, VkpiKolOption, VkpiKolProfile, VkpiPlatform, VkpiProjectRow } from '../../vkpiTypes';
import { stageLabels } from '../../shared/vkpiConstants';
import { compactCount, platformDisplay, platformFromRaw, safeNumber } from '../../shared/vkpiDataUtils';
import { likelyVideoUrl, proxiedImageUrl } from '../../shared/mediaProxy';
import {
  COMPETITOR_TERMS,
  CONTENT_FILTER_OPTIONS,
  FUNNEL_STAGES,
  GEAR_PATTERNS,
  PLATFORM_ENTRIES,
  PLATFORM_OPTIONS,
  VILTROX_TERMS,
  type ContactDraft,
  type EffectiveMyKolItem,
  type FunnelStageKey,
  type KolCommentItem,
  type KolContentDirection,
  type KolContentFilter,
  type KolContentSort,
  type KolContentWindow,
  type MyKolItem,
  type PlatformEntryMetric,
  type PlatformFilter,
  type PostPreview,
} from './myKolMatrixTypes';

export function normalizedKey(value: unknown) {
  return String(value || '').trim().toLowerCase();
}

export function normalizedHandle(value: unknown) {
  const handle = String(value || '').trim();
  if (!handle || handle === '-') return '-';
  return handle.startsWith('@') ? handle : `@${handle}`;
}

export function aliasFor(platform: unknown, value: unknown) {
  const text = normalizedKey(value).replace(/^@/, '');
  if (!text || text === '-') return '';
  return `${platformFromRaw(platform)}:${text}`;
}

export function projectDate(project: VkpiProjectRow) {
  const parsed = Date.parse(project.updatedAt || project.latestMessageAt || project.closedAt || project.startedAt || project.createdAt || '');
  return Number.isFinite(parsed) ? parsed : 0;
}

export function initialItemFromKol(kol: VkpiKolOption): MyKolItem {
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

export function initialItemFromProject(project: VkpiProjectRow): MyKolItem {
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

export function funnelStageFor(item: MyKolItem): FunnelStageKey {
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

export function buildMyKolItems(data: VkpiDashboardData): MyKolItem[] {
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

export function displayCount(value: unknown) {
  const parsed = safeNumber(value);
  return parsed ? compactCount(parsed) : '0';
}

export function initials(name: string) {
  return (name || 'K').trim().slice(0, 1).toUpperCase();
}

export function cleanHandle(handle: string) {
  const value = String(handle || '').trim().replace(/^@/, '');
  return value && value !== '-' ? value : '';
}

export function inferredProfileUrl(platform: VkpiPlatform, handle: string) {
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

export function compactContactValue(value: string) {
  const text = String(value || '').replace(/^mailto:/, '').replace(/^tel:/, '').trim();
  if (!text) return '';
  if (text.length <= 30) return text;
  return `${text.slice(0, 16)}...${text.slice(-8)}`;
}

export function textField(row: Record<string, unknown> | undefined, ...keys: string[]) {
  if (!row) return '';
  for (const key of keys) {
    const text = String(row[key] ?? '').trim();
    if (text) return text;
  }
  return '';
}

export function parsedObject(value: unknown): Record<string, unknown> {
  if (value && typeof value === 'object' && !Array.isArray(value)) return value as Record<string, unknown>;
  if (typeof value !== 'string' || !value.trim()) return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

export function parsedList(value: unknown): string[] {
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

export function textList(value: unknown): string[] {
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

export function mediaUrlsFrom(value: unknown, depth = 0): string[] {
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

export function uniqueStrings(values: string[]) {
  const seen = new Set<string>();
  return values.map((value) => value.trim()).filter((value) => {
    if (!value || seen.has(value)) return false;
    seen.add(value);
    return true;
  });
}

export function renderableMediaUrl(url: string, platform: VkpiPlatform) {
  if (!url) return false;
  if (url.startsWith('/')) return true;
  return /^https?:\/\//i.test(url) && !['instagram', 'tiktok'].includes(platform.toLowerCase());
}

export function matchTerms(text: string, terms: string[]) {
  const source = text.toLowerCase();
  return terms.filter((term) => source.includes(term.toLowerCase()));
}

export function gearMentions(text: string) {
  const source = text.toLowerCase();
  return GEAR_PATTERNS.filter((pattern) => (
    pattern.terms.some((term) => source.includes(term.toLowerCase()))
  )).map((pattern) => pattern.label);
}

export function compactDate(value: unknown) {
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

export function conciseText(value: string, max = 82) {
  const clean = String(value || '').replace(/\s+/g, ' ').trim();
  if (clean.length <= max) return clean;
  const splitAt = clean.lastIndexOf(' ', max);
  return `${clean.slice(0, splitAt > 32 ? splitAt : max).trim()}...`;
}

export function contactLinksFromUnknown(value: unknown): VkpiContactLink[] {
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

export function contactDraftFor(item: MyKolItem, profile?: VkpiKolProfile, override?: Partial<ContactDraft>): ContactDraft {
  const contacts = profile?.contacts || {};
  return {
    contactEmail: override?.contactEmail ?? item.contactEmail ?? textField(contacts, 'email'),
    contactPhone: override?.contactPhone ?? item.contactPhone ?? textField(contacts, 'phone'),
    profileUrl: override?.profileUrl ?? item.profileUrl ?? textField(contacts, 'profile_url') ?? inferredProfileUrl(item.platform, item.handle),
  };
}

export function contactItems(item: MyKolItem, profile?: VkpiKolProfile, override?: Partial<ContactDraft>): VkpiContactLink[] {
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

export function postPreviews(profile?: VkpiKolProfile): PostPreview[] {
  const rows = ((profile?.posts || []).length ? profile?.posts : profile?.content_posts) || [];
  return mapPostRows(rows);
}

export function mapPostRows(rows: Array<Record<string, unknown>>): PostPreview[] {
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

export function mapCommentRows(rows: Array<Record<string, unknown>>): KolCommentItem[] {
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

export function categoryForPost(post: PostPreview): KolContentFilter {
  if (post.brandMentions.length) return 'viltrox';
  if (post.competitorMentions.length) return 'competitor';
  return 'other';
}

export function postMatchesWindow(post: PostPreview, windowKey: KolContentWindow) {
  if (windowKey === 'all') return true;
  if (!post.publishedAt) return true;
  const parsed = new Date(post.publishedAt);
  if (Number.isNaN(parsed.getTime())) return true;
  const now = Date.now();
  if (windowKey === 'year') return parsed.getFullYear() === new Date().getFullYear();
  const days = Number(windowKey.replace('d', ''));
  return now - parsed.getTime() <= days * 24 * 60 * 60 * 1000;
}

export function filterAndSortPosts(posts: PostPreview[], filter: KolContentFilter, sort: KolContentSort, direction: KolContentDirection, windowKey: KolContentWindow) {
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

export function summarizePostInsights(posts: PostPreview[], profile?: VkpiKolProfile) {
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

export function commentsForPost(post: PostPreview | null, comments: KolCommentItem[]) {
  if (!post) return [];
  const postUrl = post.url.trim().toLowerCase();
  return comments.filter((comment) => comment.postUrl.trim().toLowerCase() === postUrl);
}

export function latestSnapshotPosts(posts: PostPreview[], profile?: VkpiKolProfile) {
  const latestSnapshotId = textField(profile?.snapshot, 'id');
  if (!latestSnapshotId) return posts;
  const current = posts.filter((post) => !post.snapshotId || post.snapshotId === latestSnapshotId);
  return current.length ? current : posts;
}

export async function fetchAllKolPostRows(apiToken: string, kolId: string) {
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

export function searchMatches(item: MyKolItem, query: string) {
  if (!query) return true;
  const target = `${item.name} ${item.handle}`.toLowerCase();
  return target.includes(query.toLowerCase());
}

export function summarize(items: EffectiveMyKolItem[]) {
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

export function buildPlatformMetrics(items: EffectiveMyKolItem[]): PlatformEntryMetric[] {
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

export function platformFilterFromRaw(value: unknown): PlatformFilter {
  const platform = platformFromRaw(value);
  return PLATFORM_OPTIONS.some((option) => option.key === platform) ? platform as PlatformFilter : 'all';
}
