import type { ChannelCommentItem, ChannelCommentsResponse, ChannelContentPost, ChannelPostPagination, OfficialChannelAccount } from './channelTypes';
import { likelyVideoUrl, platformExternalUrl, proxiedImageUrl, proxiedVideoUrl } from '../../shared/mediaProxy';

export const formatter = new Intl.NumberFormat('en-US');
export const PAGE_SIZE = 10;
export const SORT_OPTIONS = [
  { value: 'latest', label: '最新' },
  { value: 'views', label: '播放' },
  { value: 'likes', label: '点赞' },
  { value: 'comments', label: '评论' },
  { value: 'shares', label: '分享' },
];
export const WINDOW_OPTIONS = [
  { value: 'year', label: '今年分析' },
  { value: '7d', label: '近 7 天' },
  { value: '30d', label: '近 30 天' },
  { value: '90d', label: '近 90 天' },
  { value: '180d', label: '近 180 天' },
  { value: '365d', label: '近 1 年' },
  { value: 'all', label: '全部记录' },
];
export const COMMENT_PLATFORMS = new Set(['youtube', 'instagram', 'tiktok', 'facebook', 'reddit', 'x']);
export const VIDEO_CACHE_PLATFORMS = new Set(['instagram', 'tiktok']);

export function compact(value: number) {
  if (!value) return '0';
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 1 : 2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 10_000 ? 0 : 1)}K`;
  return formatter.format(value);
}

export function conciseTitle(post: ChannelContentPost) {
  return post.title.replace(/\s+/g, ' ').trim() || '内容快照';
}

export function contentCopy(post: ChannelContentPost) {
  const clean = conciseTitle(post);
  const splitAt = clean.length > 82 ? clean.lastIndexOf(' ', 82) : -1;
  const headlineEnd = splitAt > 42 ? splitAt : Math.min(clean.length, 82);
  return {
    headline: clean.slice(0, headlineEnd).trim(),
    excerpt: clean.slice(headlineEnd).trim(),
  };
}

export function compactDate(value: string) {
  const raw = text(value);
  if (!raw) return '-';
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw.replace('T', ' ').replace('.000Z', '').replace('Z', '');
  const yyyy = parsed.getFullYear();
  const mm = String(parsed.getMonth() + 1).padStart(2, '0');
  const dd = String(parsed.getDate()).padStart(2, '0');
  const hh = String(parsed.getHours()).padStart(2, '0');
  const mi = String(parsed.getMinutes()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd} ${hh}:${mi}`;
}

export function fallbackInitial(account: OfficialChannelAccount) {
  return (account.displayName || account.handle || account.platformLabel || 'V').slice(0, 1).toUpperCase();
}
export type Row = Record<string, unknown>;

export function text(value: unknown, fallback = '') {
  return String(value ?? fallback).trim();
}

export function numberValue(value: unknown) {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function firstPresent(...values: unknown[]) {
  return values.find((value) => value !== undefined && value !== null && value !== '');
}

export function textList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((item) => text(item)).filter(Boolean);
  if (typeof value === 'string') {
    const raw = value.trim();
    if (!raw) return [];
    if (raw.startsWith('[')) {
      try {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) return parsed.map((item) => text(item)).filter(Boolean);
      } catch {
        return [raw];
      }
    }
    return [raw];
  }
  return [];
}

export function renderableUrl(url: string, platform: string) {
  if (!url) return false;
  if (url.startsWith('/')) return true;
  return /^https?:\/\//i.test(url) && !['instagram', 'tiktok'].includes(platform.toLowerCase());
}

export function youtubeVideoId(post: ChannelContentPost) {
  for (const candidate of [post.sourceId, post.id]) {
    const value = text(candidate);
    if (/^[a-zA-Z0-9_-]{11}$/.test(value)) return value;
  }
  const url = text(post.url);
  if (!url) return '';
  try {
    const parsed = new URL(url);
    const queryVideo = parsed.searchParams.get('v');
    if (queryVideo) return queryVideo;
    const parts = parsed.pathname.split('/').filter(Boolean);
    if (parsed.hostname.endsWith('youtu.be') && parts[0]) return parts[0];
    const shorts = parts.indexOf('shorts');
    if (shorts >= 0 && parts[shorts + 1]) return parts[shorts + 1];
  } catch {
    return '';
  }
  return '';
}

export function youtubeEmbedUrl(post: ChannelContentPost, account: OfficialChannelAccount) {
  if (account.platform.toLowerCase() !== 'youtube') return '';
  const id = youtubeVideoId(post);
  return id ? `https://www.youtube.com/embed/${encodeURIComponent(id)}?autoplay=1&rel=0` : '';
}

export function mediaState(post: ChannelContentPost, account: OfficialChannelAccount) {
  const fallbackUrl = text(post.mediaUrl);
  const rawVideoUrl = text(post.videoUrl);
  const embedUrl = youtubeEmbedUrl(post, account);
  const explicitKind = text(post.mediaKind || post.mediaType).toLowerCase();
  const explicitlyVideo = ['video', 'reel', 'reels', 'clip', 'clips'].includes(explicitKind);
  const fallbackLooksVideo = likelyVideoUrl(fallbackUrl, account.platform);
  const shouldUseFallbackAsVideo = !rawVideoUrl && fallbackLooksVideo;
  const videoUrl = rawVideoUrl || (shouldUseFallbackAsVideo ? fallbackUrl : '');
  const imageCandidates = [
    ...(post.imageUrls || []),
    ...(post.mediaUrls || []).filter((url) => !likelyVideoUrl(url, account.platform)),
    ...(fallbackUrl && !fallbackLooksVideo ? [fallbackUrl] : []),
  ];
  const imageUrls = Array.from(new Set(imageCandidates.map((url) => proxiedImageUrl(url)).filter((url) => renderableUrl(url, account.platform))));
  const resolvedVideoUrl = videoUrl ? proxiedVideoUrl(videoUrl) : '';
  const videoRenderable = Boolean(embedUrl) || renderableUrl(resolvedVideoUrl, account.platform);
  const kind = videoRenderable
    ? 'video'
    : explicitlyVideo && imageUrls.length
      ? 'video-poster'
      : (imageUrls.length > 1 || explicitKind === 'carousel' || explicitKind === 'sidecar' ? 'carousel' : imageUrls.length ? 'image' : 'pending');
  return {
    kind,
    embedUrl,
    videoUrl: videoRenderable ? resolvedVideoUrl : '',
    imageUrls,
    renderable: videoRenderable || imageUrls.length > 0,
  };
}

export function postVideoId(post: ChannelContentPost) {
  return text(post.sourceId || post.id || post.url);
}

export function postVideoSourceUrl(post: ChannelContentPost, account: OfficialChannelAccount) {
  const platformUrl = platformExternalUrl(post.url);
  if (platformUrl) return platformUrl;
  const videoUrl = text(post.videoUrl);
  if (videoUrl) return videoUrl;
  const mediaUrl = text(post.mediaUrl);
  if (likelyVideoUrl(mediaUrl, account.platform)) return mediaUrl;
  return mediaUrl;
}

export function videoCacheKey(post: ChannelContentPost, account: OfficialChannelAccount) {
  return `${account.platform.toLowerCase()}:${postVideoId(post)}`;
}

export function canQueueVideoCache(post: ChannelContentPost, account: OfficialChannelAccount) {
  const platform = account.platform.toLowerCase();
  if (!VIDEO_CACHE_PLATFORMS.has(platform)) return false;
  if (!postVideoId(post) || !postVideoSourceUrl(post, account)) return false;
  const media = mediaState(post, account);
  if (media.videoUrl) return false;
  const explicitKind = text(post.mediaKind || post.mediaType).toLowerCase();
  return media.kind === 'video-poster' || media.kind === 'video' || ['video', 'reel', 'reels', 'clip', 'clips'].includes(explicitKind);
}

export function taskResultText(task: { result_json?: Record<string, unknown>; result?: Record<string, unknown>; progress_text?: string; error?: string }) {
  const result = task.result_json || task.result || {};
  for (const key of ['summary', 'message', 'skip_reason', 'reason', 'cached_url']) {
    const value = result[key];
    if (value != null && value !== '') return String(value);
  }
  return task.error || task.progress_text || '';
}

export function mediaStatusLabel(post: ChannelContentPost) {
  const status = text(post.mediaStatus);
  if (post.mediaStatusLabel) return post.mediaStatusLabel;
  const labels: Record<string, string> = {
    cached: '已缓存',
    embed: '平台嵌入',
    source_only: '来源图片',
    source_limited: '打开原帖',
    inventory_only: '视频待缓存',
    failed: '缓存失败',
    pending: '待补媒体',
  };
  return labels[status] || '待缓存';
}

export function mediaStatusNote(post: ChannelContentPost) {
  const status = text(post.mediaStatus);
  if (!status || status === 'cached' || status === 'embed') return '';
  const reason = text(post.mediaStatusReason);
  return `媒体：${mediaStatusLabel(post)}${reason ? ` · ${reason}` : ''}`;
}

export function viewsLabel(post: ChannelContentPost) {
  // 诚实口径:无播放量口径(图文帖/Reddit)或来源未采集 → 显 —;0 只留给"实测为零"。
  return post.viewsUnavailable || post.viewsMissing ? '—' : compact(post.views);
}

export function viewsMetricLabel(post: ChannelContentPost, account: OfficialChannelAccount) {
  if (post.viewsMetricLabel) return post.viewsMetricLabel;
  if (post.viewsUnavailable || account.platform.toLowerCase() === 'reddit') return '公开播放';
  return '播放';
}

export function viewsUnavailableText(post: ChannelContentPost, account: OfficialChannelAccount) {
  if (post.viewsUnavailableReason) return post.viewsUnavailableReason;
  if (!post.viewsUnavailable && post.viewsMissing) return '播放量未采集(来源数据缺失),显示 — 以区分实测 0。';
  if (account.viewsUnavailableReason) return account.viewsUnavailableReason;
  if (account.platform.toLowerCase() === 'reddit') return 'Reddit 不公开帖子播放量；今年分析使用点赞、评论和站内评分。';
  return '图文帖无播放量口径(IG 图文/轮播不公开播放数),需后台 Insights 才能补齐。';
}

export function accountViewsValue(account: OfficialChannelAccount) {
  return account.viewsUnavailable ? '-' : compact(account.totalViews);
}

export function mediaBadge(post: ChannelContentPost, account: OfficialChannelAccount) {
  if ((post.imageUrls || []).length > 1) return `1/${post.imageUrls?.length}`;
  const media = mediaState(post, account);
  if (media.kind === 'video') return 'video';
  if (media.kind === 'video-poster') return 'video 待缓存';
  if (media.kind === 'image' || media.kind === 'carousel') return 'image';
  return 'pending';
}

export function commentRow(row: Row): ChannelCommentItem {
  return {
    id: numberValue(row.id),
    externalCommentId: text(row.external_comment_id || row.externalCommentId),
    externalPostId: text(row.external_post_id || row.externalPostId),
    text: text(row.text || row.comment_text || row.commentText),
    author: text(row.author || row.author_handle || row.authorHandle, '匿名'),
    likes: numberValue(row.likes || row.likes_count || row.likesCount),
    replyCount: numberValue(row.reply_count || row.replyCount),
    depth: numberValue(row.depth),
    parentCommentId: text(row.parent_comment_id || row.parentCommentId),
    isOp: Boolean(row.is_op || row.isOp),
    createdAt: text(row.created_at || row.createdAt),
    fetchedAt: text(row.fetched_at || row.fetchedAt),
    sentiment: text(row.sentiment),
  };
}

export function commentsResponse(row: Row): ChannelCommentsResponse {
  const comments = Array.isArray(row.comments) ? row.comments.filter((item): item is Row => Boolean(item) && typeof item === 'object').map(commentRow) : [];
  const contractRow = (row.comment_contract || row.commentContract) as Row | undefined;
  const contract = contractRow && typeof contractRow === 'object' ? {
    declared: numberValue(firstPresent(contractRow.declared, row.declared_count, row.declaredCount)),
    cached: numberValue(firstPresent(contractRow.cached, row.cached_count, row.cachedCount, row.comment_count, row.commentCount, comments.length)),
    cap: numberValue(firstPresent(contractRow.cap, row.comment_cap, row.commentCap)),
    status: text(firstPresent(contractRow.status, row.coverage_status, row.coverageStatus)),
  } : undefined;
  const collectSupportedValue = firstPresent(row.collect_supported, row.collectSupported);
  return {
    channelId: numberValue(row.channel_id || row.channelId),
    postId: text(row.post_id || row.postId),
    platform: text(row.platform),
    status: text(row.status),
    message: text(row.message),
    commentCount: numberValue(firstPresent(row.comment_count, row.commentCount, comments.length)),
    declaredCount: numberValue(firstPresent(row.declared_count, row.declaredCount, contract?.declared)),
    cachedCount: numberValue(firstPresent(row.cached_count, row.cachedCount, contract?.cached, row.comment_count, row.commentCount, comments.length)),
    commentCap: numberValue(firstPresent(row.comment_cap, row.commentCap, contract?.cap)),
    coverageStatus: text(firstPresent(row.coverage_status, row.coverageStatus, contract?.status)),
    fetchedCount: numberValue(firstPresent(row.fetched_count, row.fetchedCount)),
    newCount: numberValue(firstPresent(row.new_count, row.newCount)),
    collectSupported: typeof collectSupportedValue === 'boolean' ? collectSupportedValue : undefined,
    commentContract: contract,
    comments,
  };
}

export function canCollectComments(account: OfficialChannelAccount, payload: ChannelCommentsResponse | null) {
  if (typeof payload?.collectSupported === 'boolean') return payload.collectSupported;
  return COMMENT_PLATFORMS.has(account.platform.toLowerCase());
}

export function commentEmptyText(post: ChannelContentPost, account: OfficialChannelAccount, payload: ChannelCommentsResponse | null) {
  if (payload?.message) return payload.message;
  if (canCollectComments(account, payload)) {
    if (post.comments > 0) return `评论数已同步：${formatter.format(post.comments)} 条；评论正文还未缓存。`;
    return '当前帖子暂无评论正文缓存。';
  }
  return '当前平台评论抓取未配置。';
}

export function commentActionLabel(post: ChannelContentPost, payload: ChannelCommentsResponse | null, canCollect: boolean) {
  const cached = payload?.commentContract?.cached ?? payload?.cachedCount ?? payload?.commentCount ?? payload?.comments.length ?? 0;
  if (!canCollect) return '未接入';
  if (cached && post.comments > cached) return '继续补齐';
  if (cached) return '刷新明细';
  if (post.comments > 0) return '补齐明细';
  return '尝试获取';
}

export function commentCoverage(post: ChannelContentPost, account: OfficialChannelAccount, payload: ChannelCommentsResponse | null) {
  const contract = payload?.commentContract;
  const declared = Math.max(0, contract?.declared ?? payload?.declaredCount ?? post.comments);
  const cached = payload ? Math.max(0, contract?.cached ?? payload.cachedCount ?? payload.commentCount ?? payload.comments.length) : 0;
  const cap = Math.max(0, contract?.cap ?? payload?.commentCap ?? 0);
  const capLabel = cap ? ` / 上限 ${formatter.format(cap)}` : '';
  const supported = canCollectComments(account, payload);
  if (!payload) {
    return { label: declared ? `正文待读取 / 平台显示 ${formatter.format(declared)}` : '平台暂无评论', tone: 'neutral' };
  }
  if (!supported) {
    return { label: declared ? `正文未接入 / 平台显示 ${formatter.format(declared)}` : '平台暂无评论', tone: 'blocked' };
  }
  if (declared <= 0) {
    return { label: cached ? `正文缓存 ${formatter.format(cached)} / 平台未声明${capLabel}` : '平台暂无评论', tone: cached ? 'partial' : 'neutral' };
  }
  if (cached >= declared) {
    return { label: `正文缓存 ${formatter.format(cached)} / 平台 ${formatter.format(declared)}${capLabel}`, tone: 'ok' };
  }
  if (cached > 0) {
    return { label: `正文缓存 ${formatter.format(cached)} / 平台 ${formatter.format(declared)}${capLabel}`, tone: 'partial' };
  }
  return { label: `正文未缓存 / 平台 ${formatter.format(declared)}${capLabel}`, tone: 'blocked' };
}

export function mapPost(row: Row): ChannelContentPost {
  return {
    id: text(row.id || row.source_id || row.url),
    sourceId: text(row.source_id || row.sourceId),
    title: text(row.title, '内容快照'),
    url: text(row.url),
    mediaUrl: text(row.media_url || row.mediaUrl),
    videoUrl: text(row.video_url || row.videoUrl),
    imageUrls: textList(row.image_urls || row.imageUrls),
    mediaUrls: textList(row.media_urls || row.mediaUrls),
    mediaType: text(row.media_type || row.mediaType),
    mediaKind: text(row.media_kind || row.mediaKind),
    mediaStatus: text(row.media_status || row.mediaStatus),
    mediaStatusLabel: text(row.media_status_label || row.mediaStatusLabel),
    mediaStatusReason: text(row.media_status_reason || row.mediaStatusReason),
    mediaQuality: text(row.media_quality || row.mediaQuality),
    postedAt: text(row.posted_at || row.postedAt),
    views: numberValue(row.views),
    likes: numberValue(row.likes),
    comments: numberValue(row.comments),
    shares: numberValue(row.shares),
    accountLevel: Boolean(row.account_level || row.accountLevel),
    viewsUnavailable: Boolean(row.views_unavailable || row.viewsUnavailable),
    viewsMissing: Boolean(row.views_missing || row.viewsMissing),
    viewsMetricLabel: text(row.views_metric_label || row.viewsMetricLabel),
    viewsUnavailableReason: text(row.views_unavailable_reason || row.viewsUnavailableReason),
  };
}

export function mapPagination(row?: Row): ChannelPostPagination {
  return {
    page: numberValue(row?.page) || 1,
    limit: numberValue(row?.limit) || PAGE_SIZE,
    total: numberValue(row?.total),
    pages: numberValue(row?.pages),
    hasNext: Boolean(row?.has_next || row?.hasNext),
    hasPrev: Boolean(row?.has_prev || row?.hasPrev),
  };
}
