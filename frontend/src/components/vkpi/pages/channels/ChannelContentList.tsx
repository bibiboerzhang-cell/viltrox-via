import { useEffect, useMemo, useState } from 'react';
import { collectChannelPostComments, enqueueVideoCacheTask, getChannelPostComments, getOfficialChannelPosts } from '../../../../services/vkpi.ui-api';
import type { ChannelCommentItem, ChannelCommentsResponse, ChannelContentPost, ChannelPostPagination, OfficialChannelAccount } from './channelTypes';
import { useTaskCenter } from '../../../tasks/TaskCenter';
import { invalidateCachedVideoUrl, likelyVideoUrl, platformExternalUrl, proxiedImageUrl, proxiedVideoUrl, useCachedVideoUrl } from '../../shared/mediaProxy';

const formatter = new Intl.NumberFormat('en-US');
const PAGE_SIZE = 10;
const SORT_OPTIONS = [
  { value: 'latest', label: '最新' },
  { value: 'views', label: '播放' },
  { value: 'likes', label: '点赞' },
  { value: 'comments', label: '评论' },
  { value: 'shares', label: '分享' },
];
const WINDOW_OPTIONS = [
  { value: 'year', label: '今年分析' },
  { value: '7d', label: '近 7 天' },
  { value: '30d', label: '近 30 天' },
  { value: '90d', label: '近 90 天' },
  { value: '180d', label: '近 180 天' },
  { value: '365d', label: '近 1 年' },
  { value: 'all', label: '全部记录' },
];
const COMMENT_PLATFORMS = new Set(['youtube', 'instagram', 'tiktok', 'facebook', 'reddit', 'x']);
const VIDEO_CACHE_PLATFORMS = new Set(['instagram', 'tiktok']);

function compact(value: number) {
  if (!value) return '0';
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 1 : 2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 10_000 ? 0 : 1)}K`;
  return formatter.format(value);
}

function conciseTitle(post: ChannelContentPost) {
  return post.title.replace(/\s+/g, ' ').trim() || '内容快照';
}

function contentCopy(post: ChannelContentPost) {
  const clean = conciseTitle(post);
  const splitAt = clean.length > 82 ? clean.lastIndexOf(' ', 82) : -1;
  const headlineEnd = splitAt > 42 ? splitAt : Math.min(clean.length, 82);
  return {
    headline: clean.slice(0, headlineEnd).trim(),
    excerpt: clean.slice(headlineEnd).trim(),
  };
}

function compactDate(value: string) {
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

function fallbackInitial(account: OfficialChannelAccount) {
  return (account.displayName || account.handle || account.platformLabel || 'V').slice(0, 1).toUpperCase();
}

function ChannelContentSkeletons() {
  return (
    <div className="vkpi-channel-content-list" aria-hidden="true">
      {[0, 1, 2, 3].map((item) => (
        <article className="vkpi-channel-content-card vkpi-channel-content-card--skeleton" key={item}>
          <div className="vkpi-channel-content-card__media">
            <span className="vkpi-skeleton vkpi-skeleton-pill" />
          </div>
          <div className="vkpi-channel-content-card__body">
            <span className="vkpi-skeleton vkpi-skeleton-line is-long" />
            <span className="vkpi-skeleton vkpi-skeleton-line is-medium" />
            <div className="vkpi-channel-content-card__metrics">
              <span className="vkpi-skeleton vkpi-skeleton-pill" />
              <span className="vkpi-skeleton vkpi-skeleton-pill" />
              <span className="vkpi-skeleton vkpi-skeleton-pill" />
            </div>
          </div>
          <footer className="vkpi-channel-content-card__footer">
            <span className="vkpi-skeleton vkpi-skeleton-line is-short" />
            <span className="vkpi-skeleton vkpi-skeleton-line is-short" />
          </footer>
        </article>
      ))}
    </div>
  );
}

type Row = Record<string, unknown>;

function text(value: unknown, fallback = '') {
  return String(value ?? fallback).trim();
}

function numberValue(value: unknown) {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function firstPresent(...values: unknown[]) {
  return values.find((value) => value !== undefined && value !== null && value !== '');
}

function textList(value: unknown): string[] {
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

function renderableUrl(url: string, platform: string) {
  if (!url) return false;
  if (url.startsWith('/')) return true;
  return /^https?:\/\//i.test(url) && !['instagram', 'tiktok'].includes(platform.toLowerCase());
}

function youtubeVideoId(post: ChannelContentPost) {
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

function youtubeEmbedUrl(post: ChannelContentPost, account: OfficialChannelAccount) {
  if (account.platform.toLowerCase() !== 'youtube') return '';
  const id = youtubeVideoId(post);
  return id ? `https://www.youtube.com/embed/${encodeURIComponent(id)}?autoplay=1&rel=0` : '';
}

function mediaState(post: ChannelContentPost, account: OfficialChannelAccount) {
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

function postVideoId(post: ChannelContentPost) {
  return text(post.sourceId || post.id || post.url);
}

function postVideoSourceUrl(post: ChannelContentPost, account: OfficialChannelAccount) {
  const platformUrl = platformExternalUrl(post.url);
  if (platformUrl) return platformUrl;
  const videoUrl = text(post.videoUrl);
  if (videoUrl) return videoUrl;
  const mediaUrl = text(post.mediaUrl);
  if (likelyVideoUrl(mediaUrl, account.platform)) return mediaUrl;
  return mediaUrl;
}

function videoCacheKey(post: ChannelContentPost, account: OfficialChannelAccount) {
  return `${account.platform.toLowerCase()}:${postVideoId(post)}`;
}

function canQueueVideoCache(post: ChannelContentPost, account: OfficialChannelAccount) {
  const platform = account.platform.toLowerCase();
  if (!VIDEO_CACHE_PLATFORMS.has(platform)) return false;
  if (!postVideoId(post) || !postVideoSourceUrl(post, account)) return false;
  const media = mediaState(post, account);
  if (media.videoUrl) return false;
  const explicitKind = text(post.mediaKind || post.mediaType).toLowerCase();
  return media.kind === 'video-poster' || media.kind === 'video' || ['video', 'reel', 'reels', 'clip', 'clips'].includes(explicitKind);
}

function taskResultText(task: { result_json?: Record<string, unknown>; result?: Record<string, unknown>; progress_text?: string; error?: string }) {
  const result = task.result_json || task.result || {};
  for (const key of ['summary', 'message', 'skip_reason', 'reason', 'cached_url']) {
    const value = result[key];
    if (value != null && value !== '') return String(value);
  }
  return task.error || task.progress_text || '';
}

function MediaSlot({ post, account, apiToken, compact = false, refreshKey = 0 }: { post: ChannelContentPost; account: OfficialChannelAccount; apiToken?: string; compact?: boolean; refreshKey?: number }) {
  const [active, setActive] = useState(0);
  const [failedImages, setFailedImages] = useState<Set<string>>(() => new Set());
  useEffect(() => {
    setActive(0);
    setFailedImages(new Set());
  }, [post.id, post.mediaUrl, post.videoUrl, (post.imageUrls || []).join('|'), (post.mediaUrls || []).join('|')]);
  const media = mediaState(post, account);
  const resolvedVideoUrl = useCachedVideoUrl(apiToken, account.platform, postVideoId(post), media.videoUrl, refreshKey);
  const hasPlayableVideo = Boolean(media.embedUrl || resolvedVideoUrl);
  if (!media.renderable && !hasPlayableVideo) {
    return <span className="vkpi-channel-content-card__pending">待缓存</span>;
  }
  const imageUrls = media.imageUrls.filter((url) => !failedImages.has(url));
  const current = imageUrls[Math.min(active, Math.max(0, imageUrls.length - 1))];
  const markFailed = (url: string) => setFailedImages((prev) => {
    const next = new Set(prev);
    next.add(url);
    return next;
  });
  if (media.kind === 'video' && media.embedUrl && !compact) {
    return <iframe src={media.embedUrl} title={conciseTitle(post)} allow="autoplay; encrypted-media; picture-in-picture" allowFullScreen />;
  }
  if ((media.kind === 'video' || media.kind === 'video-poster' || (!media.renderable && hasPlayableVideo)) && hasPlayableVideo && (!compact || !current)) {
    return (
      <>
        <video
          src={resolvedVideoUrl}
          poster={current || media.imageUrls[0] || undefined}
          controls={!compact}
          muted={compact}
          playsInline
          preload="metadata"
        />
        {compact ? <span className="vkpi-channel-content-card__play">▶</span> : null}
      </>
    );
  }
  if (!current) {
    return <span className="vkpi-channel-content-card__pending">待缓存</span>;
  }
  return (
    <div className="vkpi-channel-content-card__carousel">
      <img src={current} alt="" loading="lazy" onError={() => markFailed(current)} />
      {hasPlayableVideo ? <span className="vkpi-channel-content-card__play">▶</span> : null}
      {media.kind === 'video-poster' ? <span className="vkpi-channel-content-card__video-pending">视频待缓存</span> : null}
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

function viewsLabel(post: ChannelContentPost) {
  return post.viewsUnavailable ? '--' : compact(post.views);
}

function viewsMetricLabel(post: ChannelContentPost, account: OfficialChannelAccount) {
  if (post.viewsMetricLabel) return post.viewsMetricLabel;
  if (post.viewsUnavailable || account.platform.toLowerCase() === 'reddit') return '公开播放';
  return '播放';
}

function viewsUnavailableText(post: ChannelContentPost, account: OfficialChannelAccount) {
  if (post.viewsUnavailableReason) return post.viewsUnavailableReason;
  if (account.viewsUnavailableReason) return account.viewsUnavailableReason;
  if (account.platform.toLowerCase() === 'reddit') return 'Reddit 不公开帖子播放量；今年分析使用点赞、评论和站内评分。';
  return '图文无公开播放，需后台 Insights 才能补齐。';
}

function accountViewsValue(account: OfficialChannelAccount) {
  return account.viewsUnavailable ? '-' : compact(account.totalViews);
}

function mediaBadge(post: ChannelContentPost, account: OfficialChannelAccount) {
  if ((post.imageUrls || []).length > 1) return `1/${post.imageUrls?.length}`;
  const media = mediaState(post, account);
  if (media.kind === 'video') return 'video';
  if (media.kind === 'video-poster') return 'video 待缓存';
  if (media.kind === 'image' || media.kind === 'carousel') return 'image';
  return 'pending';
}

function commentRow(row: Row): ChannelCommentItem {
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

function commentsResponse(row: Row): ChannelCommentsResponse {
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

function canCollectComments(account: OfficialChannelAccount, payload: ChannelCommentsResponse | null) {
  if (typeof payload?.collectSupported === 'boolean') return payload.collectSupported;
  return COMMENT_PLATFORMS.has(account.platform.toLowerCase());
}

function commentEmptyText(post: ChannelContentPost, account: OfficialChannelAccount, payload: ChannelCommentsResponse | null) {
  if (payload?.message) return payload.message;
  if (canCollectComments(account, payload)) {
    if (post.comments > 0) return `评论数已同步：${formatter.format(post.comments)} 条；评论正文还未缓存。`;
    return '当前帖子暂无评论正文缓存。';
  }
  return '当前平台评论抓取未配置。';
}

function commentActionLabel(post: ChannelContentPost, payload: ChannelCommentsResponse | null, canCollect: boolean) {
  const cached = payload?.commentContract?.cached ?? payload?.cachedCount ?? payload?.commentCount ?? payload?.comments.length ?? 0;
  if (!canCollect) return '未接入';
  if (cached && post.comments > cached) return '继续补齐';
  if (cached) return '刷新明细';
  if (post.comments > 0) return '补齐明细';
  return '尝试获取';
}

function commentCoverage(post: ChannelContentPost, account: OfficialChannelAccount, payload: ChannelCommentsResponse | null) {
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

function MediaLightbox({ post, account, apiToken, refreshKey = 0, onClose }: { post: ChannelContentPost; account: OfficialChannelAccount; apiToken?: string; refreshKey?: number; onClose: () => void }) {
  const [active, setActive] = useState(0);
  const media = mediaState(post, account);
  const resolvedVideoUrl = useCachedVideoUrl(apiToken, account.platform, postVideoId(post), media.videoUrl, refreshKey);
  const title = conciseTitle(post);
  const isVideo = Boolean(media.embedUrl || resolvedVideoUrl) && ['video', 'video-poster', 'pending'].includes(media.kind);

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

  const currentImage = media.imageUrls[Math.min(active, media.imageUrls.length - 1)];

  return (
    <div className="vkpi-media-lightbox" role="dialog" aria-modal="true" aria-label="内容媒体预览" onClick={onClose}>
      <div className="vkpi-media-lightbox__panel" onClick={(event) => event.stopPropagation()}>
        <header className="vkpi-media-lightbox__header">
          <div>
            <span>{account.platformLabel}</span>
            <h3 title={title}>{title}</h3>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭">×</button>
        </header>
        <div className={`vkpi-media-lightbox__stage ${isVideo ? 'is-video' : 'is-image'}`}>
          {isVideo && media.embedUrl ? (
            <iframe src={media.embedUrl} title={title} allow="autoplay; encrypted-media; picture-in-picture" allowFullScreen />
          ) : isVideo ? (
            <video src={resolvedVideoUrl} poster={media.imageUrls[0] || undefined} controls playsInline autoPlay />
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
            <div className="vkpi-media-lightbox__pending">待缓存</div>
          )}
        </div>
        <footer className="vkpi-media-lightbox__footer">
          <span>{viewsMetricLabel(post, account)} {viewsLabel(post)}</span>
          <span>点赞 {formatter.format(post.likes)}</span>
          <span>评论 {formatter.format(post.comments)}</span>
          <span>分享 {formatter.format(post.shares)}</span>
          <small>{post.accountLevel ? '账号级快照' : account.syncStatus || 'synced'} · {post.postedAt || account.lastSyncAt || '-'}</small>
        </footer>
      </div>
    </div>
  );
}

function CommentModal({
  post,
  account,
  payload,
  loading,
  error,
  onClose,
  onCollect,
}: {
  post: ChannelContentPost;
  account: OfficialChannelAccount;
  payload: ChannelCommentsResponse | null;
  loading: boolean;
  error: string;
  onClose: () => void;
  onCollect: () => void;
}) {
  const title = conciseTitle(post);
  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onClose]);
  const comments = payload?.comments || [];
  const canCollect = canCollectComments(account, payload);
  const coverage = commentCoverage(post, account, payload);
  return (
    <div className="vkpi-comment-modal" role="dialog" aria-modal="true" aria-label="内容评论" onClick={onClose}>
      <section className="vkpi-comment-modal__panel" onClick={(event) => event.stopPropagation()}>
        <header className="vkpi-comment-modal__header">
          <div>
            <span>{account.platformLabel} 评论</span>
            <h3 title={title}>{title}</h3>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭">×</button>
        </header>
        <div className="vkpi-comment-modal__meta">
          <span className={`vkpi-comment-modal__coverage is-${coverage.tone}`}>{coverage.label}</span>
          <span>点赞 {formatter.format(post.likes)}</span>
          <span>{post.postedAt || account.lastSyncAt || '-'}</span>
          {payload?.newCount ? <span>本次新增 {formatter.format(payload.newCount)}</span> : null}
          {payload?.fetchedCount ? <span>本次读取 {formatter.format(payload.fetchedCount)}</span> : null}
        </div>
        <div className="vkpi-comment-modal__body">
          {loading ? <div className="vkpi-comment-modal__empty">评论加载中。</div> : null}
          {!loading && error ? <div className="vkpi-comment-modal__empty">{error}</div> : null}
          {!loading && !error && comments.length ? (
            <div className="vkpi-comment-list">
              {comments.map((comment) => (
                <article className="vkpi-comment-item" key={comment.externalCommentId || comment.id} style={{ marginLeft: `${Math.min(comment.depth, 4) * 14}px` }}>
                  <header>
                    <strong>{comment.author}</strong>
                    <span>{comment.isOp ? 'OP · ' : ''}赞 {formatter.format(comment.likes)}</span>
                  </header>
                  <p>{comment.text || '无正文'}</p>
                  <small>{comment.createdAt || comment.fetchedAt || '-'}</small>
                </article>
              ))}
            </div>
          ) : null}
          {!loading && !error && !comments.length ? (
            <div className="vkpi-comment-modal__empty">
              {commentEmptyText(post, account, payload)}
            </div>
          ) : null}
        </div>
        <footer className="vkpi-comment-modal__footer">
          {platformExternalUrl(post.url) ? <a href={platformExternalUrl(post.url)} target="_blank" rel="noreferrer">打开原帖 ↗</a> : <span />}
          <button type="button" disabled={loading || !canCollect} onClick={onCollect}>{commentActionLabel(post, payload, canCollect)}</button>
        </footer>
      </section>
    </div>
  );
}

function mapPost(row: Row): ChannelContentPost {
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
    postedAt: text(row.posted_at || row.postedAt),
    views: numberValue(row.views),
    likes: numberValue(row.likes),
    comments: numberValue(row.comments),
    shares: numberValue(row.shares),
    accountLevel: Boolean(row.account_level || row.accountLevel),
    viewsUnavailable: Boolean(row.views_unavailable || row.viewsUnavailable),
    viewsMetricLabel: text(row.views_metric_label || row.viewsMetricLabel),
    viewsUnavailableReason: text(row.views_unavailable_reason || row.viewsUnavailableReason),
  };
}

function mapPagination(row?: Row): ChannelPostPagination {
  return {
    page: numberValue(row?.page) || 1,
    limit: numberValue(row?.limit) || PAGE_SIZE,
    total: numberValue(row?.total),
    pages: numberValue(row?.pages),
    hasNext: Boolean(row?.has_next || row?.hasNext),
    hasPrev: Boolean(row?.has_prev || row?.hasPrev),
  };
}

export function ChannelContentList({ account, apiToken }: { account?: OfficialChannelAccount; apiToken?: string }) {
  const { waitForTask } = useTaskCenter();
  const [sort, setSort] = useState('latest');
  const [direction, setDirection] = useState('desc');
  const [windowKey, setWindowKey] = useState('year');
  const [page, setPage] = useState(1);
  const [remotePosts, setRemotePosts] = useState<ChannelContentPost[]>([]);
  const [pagination, setPagination] = useState<ChannelPostPagination>({ page: 1, limit: PAGE_SIZE, total: 0, pages: 0, hasNext: false, hasPrev: false });
  const [source, setSource] = useState('');
  const [previewPost, setPreviewPost] = useState<ChannelContentPost | null>(null);
  const [commentPost, setCommentPost] = useState<ChannelContentPost | null>(null);
  const [commentPayload, setCommentPayload] = useState<ChannelCommentsResponse | null>(null);
  const [commentsLoading, setCommentsLoading] = useState(false);
  const [commentsError, setCommentsError] = useState('');
  const [notice, setNotice] = useState('');
  const [videoCachePending, setVideoCachePending] = useState<Set<string>>(() => new Set());
  const [mediaRefreshKey, setMediaRefreshKey] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    setPage(1);
    setNotice('');
    setVideoCachePending(new Set());
  }, [account?.id, sort, direction, windowKey]);

  useEffect(() => {
    let cancelled = false;
    if (!apiToken || !account?.id) {
      setRemotePosts([]);
      setPagination({ page: 1, limit: PAGE_SIZE, total: 0, pages: 0, hasNext: false, hasPrev: false });
      setSource('');
      setError('');
      return;
    }
    setLoading(true);
    setError('');
    getOfficialChannelPosts(apiToken, account.id, { page, limit: PAGE_SIZE, sort, direction, window: windowKey })
      .then((response) => {
        if (cancelled) return;
        const rows = Array.isArray(response.posts) ? response.posts.filter((item): item is Row => Boolean(item) && typeof item === 'object') : [];
        setRemotePosts(rows.map(mapPost));
        setPagination(mapPagination(response.pagination));
        setSource(text(response.source));
      })
      .catch((requestError) => {
        if (cancelled) return;
        setRemotePosts([]);
        setPagination({ page: 1, limit: PAGE_SIZE, total: 0, pages: 0, hasNext: false, hasPrev: false });
        setSource('');
        setError(requestError instanceof Error ? requestError.message : '内容列表加载失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken, account?.id, page, sort, direction, windowKey]);

  const loadComments = async (post: ChannelContentPost, collect = false) => {
    if (!apiToken || !account?.id) return;
    setCommentsLoading(true);
    setCommentsError('');
    try {
      const response = collect
        ? await collectChannelPostComments(apiToken, account.id, { postId: post.sourceId || post.id, url: post.url, limit: 300 })
        : await getChannelPostComments(apiToken, account.id, { postId: post.sourceId || post.id, url: post.url, limit: 300 });
      setCommentPayload(commentsResponse(response));
    } catch (requestError) {
      setCommentPayload(null);
      setCommentsError(requestError instanceof Error ? requestError.message : '评论加载失败');
    } finally {
      setCommentsLoading(false);
    }
  };

  const openComments = (post: ChannelContentPost) => {
    setCommentPost(post);
    setCommentPayload(null);
    void loadComments(post);
  };

  const runVideoCache = async (post: ChannelContentPost) => {
    if (!apiToken || !account) return;
    const videoId = postVideoId(post);
    const sourceUrl = postVideoSourceUrl(post, account);
    if (!videoId || !sourceUrl) {
      setNotice('缺少视频缓存所需的帖子 ID 或来源链接。');
      return;
    }
    const key = videoCacheKey(post, account);
    setNotice('视频缓存任务已提交，完成后会自动刷新卡片。');
    setVideoCachePending((prev) => new Set(prev).add(key));
    try {
      const response = await enqueueVideoCacheTask(apiToken, {
        platform: account.platform,
        videoId,
        sourceUrl,
        channelId: account.id,
      });
      if (!response.task_id) throw new Error('视频缓存任务创建失败');
      waitForTask(response.task_id, {
        onDone: (task) => {
          invalidateCachedVideoUrl(account.platform, videoId);
          setMediaRefreshKey((value) => value + 1);
          setVideoCachePending((prev) => {
            const next = new Set(prev);
            next.delete(key);
            return next;
          });
          const summary = taskResultText(task);
          setNotice(summary ? `视频缓存完成：${summary}` : '视频缓存完成，卡片已刷新。');
        },
        onFailed: (task) => {
          setVideoCachePending((prev) => {
            const next = new Set(prev);
            next.delete(key);
            return next;
          });
          setNotice(`视频缓存失败：${taskResultText(task) || '任务未完成'}`);
        },
        onCancelled: () => {
          setVideoCachePending((prev) => {
            const next = new Set(prev);
            next.delete(key);
            return next;
          });
          setNotice('视频缓存任务已取消。');
        },
      });
    } catch (requestError) {
      setVideoCachePending((prev) => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      });
      setNotice(requestError instanceof Error ? `视频缓存提交失败：${requestError.message}` : '视频缓存提交失败');
    }
  };

  const fallbackPosts = useMemo(() => (account?.posts || []).slice(0, PAGE_SIZE), [account?.posts]);
  if (!account) return null;
  const posts = remotePosts.length || apiToken ? remotePosts : fallbackPosts;
  const avatarUrl = proxiedImageUrl(account.avatarUrl);
  const start = pagination.total ? (pagination.page - 1) * pagination.limit + 1 : 0;
  const end = pagination.total ? Math.min(pagination.page * pagination.limit, pagination.total) : posts.length;
  return (
    <section className="vkpi-channel-content">
      <header className="vkpi-channel-content__header">
        <div className="vkpi-channel-content__identity">
          <div className="vkpi-channel-content__avatar">
            {avatarUrl ? <img src={avatarUrl} alt="" loading="lazy" /> : <span>{fallbackInitial(account)}</span>}
          </div>
          <div>
            <span>内容层</span>
            <h2>{account.displayName}</h2>
            <p>@{account.handle || '-'} · {formatter.format(account.followers)} 粉丝 · {formatter.format(account.postsCount)} 内容</p>
          </div>
        </div>
        <strong title={account.viewsUnavailable ? account.viewsUnavailableReason : undefined}>{accountViewsValue(account)}</strong>
      </header>
      <div className="vkpi-channel-content-toolbar">
        <div className="vkpi-channel-content-toolbar__sort" aria-label="内容排序">
          {SORT_OPTIONS.map((option) => (
            <button
              type="button"
              key={option.value}
              className={sort === option.value ? 'is-active' : ''}
              onClick={() => setSort(option.value)}
            >
              {option.label}
            </button>
          ))}
          <select value={windowKey} onChange={(event) => setWindowKey(event.target.value)} aria-label="时间范围">
            {WINDOW_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
          <select value={direction} onChange={(event) => setDirection(event.target.value)} aria-label="排序方向">
            <option value="desc">{sort === 'latest' ? '最新优先' : '最高优先'}</option>
            <option value="asc">{sort === 'latest' ? '最早优先' : '最低优先'}</option>
          </select>
        </div>
        <span>{pagination.total ? `${formatter.format(start)}-${formatter.format(end)} / ${formatter.format(pagination.total)}` : source === 'snapshot_sample' ? '快照样本' : '暂无内容'}</span>
      </div>
      {error ? <div className="vkpi-inline-message">{error}</div> : null}
      {notice ? <div className="vkpi-inline-message">{notice}</div> : null}
      {posts.length ? (
        <div className="vkpi-channel-content-list">
          {posts.map((post, index) => {
            const title = conciseTitle(post);
            const copy = contentCopy(post);
            const status = post.viewsUnavailable ? 'pending insights' : (post.accountLevel ? 'snapshot' : account.syncStatus || 'synced');
            const date = compactDate(post.postedAt || account.lastSyncAt || '');
            const primaryMetric = viewsMetricLabel(post, account);
            const canCacheVideo = canQueueVideoCache(post, account);
            const cacheKey = videoCacheKey(post, account);
            const cachePending = videoCachePending.has(cacheKey);
            return (
              <article className="vkpi-channel-content-card" key={`${account.id}-${post.id || index}`}>
                <div
                  className="vkpi-channel-content-card__media"
                  role="button"
                  tabIndex={0}
                  onClick={() => setPreviewPost(post)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      setPreviewPost(post);
                    }
                  }}
                  aria-label="放大查看媒体"
                >
                  <span className="vkpi-channel-content-card__badge is-sync">{status}</span>
                  <span className="vkpi-channel-content-card__badge is-kind">{mediaBadge(post, account)}</span>
                  <MediaSlot post={post} account={account} apiToken={apiToken} compact refreshKey={mediaRefreshKey} />
                </div>
                <div className="vkpi-channel-content-card__body">
                  <h3 title={title}>{copy.headline}</h3>
                  <p title={title}>{copy.excerpt || title}</p>
                  <div className="vkpi-channel-content-card__metrics">
                    <span title={post.viewsUnavailable ? viewsUnavailableText(post, account) : undefined}>{primaryMetric} <strong>{viewsLabel(post)}</strong></span>
                    <span>点赞 <strong>{formatter.format(post.likes)}</strong></span>
                    <button type="button" onClick={() => openComments(post)}>评论 <strong>{formatter.format(post.comments)}</strong></button>
                    <span>分享 <strong>{formatter.format(post.shares)}</strong></span>
                  </div>
                  {post.viewsUnavailable ? <p className="vkpi-channel-content-card__note">{viewsUnavailableText(post, account)}</p> : null}
                </div>
                <footer className="vkpi-channel-content-card__footer">
                  <small>{date}</small>
                  <div>
                    {platformExternalUrl(post.url) ? <a href={platformExternalUrl(post.url)} target="_blank" rel="noreferrer">打开原帖</a> : null}
                    {canCacheVideo ? <button type="button" disabled={cachePending} onClick={() => void runVideoCache(post)}>{cachePending ? '缓存中' : '缓存视频'}</button> : null}
                    <button type="button" onClick={() => setPreviewPost(post)}>详情</button>
                  </div>
                </footer>
              </article>
            );
          })}
        </div>
      ) : loading ? (
        <ChannelContentSkeletons />
      ) : (
        <div className="vkpi-empty-state">当前账号暂无内容级明细。</div>
      )}
      {previewPost ? <MediaLightbox post={previewPost} account={account} apiToken={apiToken} refreshKey={mediaRefreshKey} onClose={() => setPreviewPost(null)} /> : null}
      {commentPost ? (
        <CommentModal
          post={commentPost}
          account={account}
          payload={commentPayload}
          loading={commentsLoading}
          error={commentsError}
          onClose={() => setCommentPost(null)}
          onCollect={() => void loadComments(commentPost, true)}
        />
      ) : null}
      <footer className="vkpi-channel-content-pagination">
        <button type="button" disabled={loading || !pagination.hasPrev} onClick={() => setPage((value) => Math.max(1, value - 1))}>上一页</button>
        <span>第 {formatter.format(pagination.page)} / {formatter.format(pagination.pages || 1)} 页</span>
        <button type="button" disabled={loading || !pagination.hasNext} onClick={() => setPage((value) => value + 1)}>下一页</button>
      </footer>
    </section>
  );
}
