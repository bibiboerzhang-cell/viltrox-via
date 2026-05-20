import { useEffect, useMemo, useState } from 'react';
import { collectChannelPostComments, getChannelPostComments, getOfficialChannelPosts } from '../../../../services/vkpi.ui-api';
import type { ChannelCommentItem, ChannelCommentsResponse, ChannelContentPost, ChannelPostPagination, OfficialChannelAccount } from './channelTypes';
import { likelyVideoUrl, platformExternalUrl, proxiedImageUrl, proxiedVideoUrl, useCachedVideoUrl } from '../../shared/mediaProxy';

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
  const kind = videoRenderable ? 'video' : (imageUrls.length > 1 || explicitKind === 'carousel' || explicitKind === 'sidecar' ? 'carousel' : imageUrls.length ? 'image' : 'pending');
  return {
    kind,
    embedUrl,
    videoUrl: videoRenderable ? resolvedVideoUrl : '',
    imageUrls,
    renderable: videoRenderable || imageUrls.length > 0,
  };
}

function MediaSlot({ post, account, apiToken, compact = false }: { post: ChannelContentPost; account: OfficialChannelAccount; apiToken?: string; compact?: boolean }) {
  const [active, setActive] = useState(0);
  const [failedImages, setFailedImages] = useState<Set<string>>(() => new Set());
  useEffect(() => {
    setActive(0);
    setFailedImages(new Set());
  }, [post.id, post.mediaUrl, post.videoUrl, (post.imageUrls || []).join('|'), (post.mediaUrls || []).join('|')]);
  const media = mediaState(post, account);
  const resolvedVideoUrl = useCachedVideoUrl(apiToken, account.platform, text(post.sourceId || post.id), media.videoUrl);
  if (!media.renderable) {
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
  if (media.kind === 'video' && (!compact || !current)) {
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
      {media.kind === 'video' ? <span className="vkpi-channel-content-card__play">▶</span> : null}
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

function mediaBadge(post: ChannelContentPost, account: OfficialChannelAccount) {
  if ((post.imageUrls || []).length > 1) return `1/${post.imageUrls?.length}`;
  const media = mediaState(post, account);
  if (media.kind === 'video') return 'video';
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
  return {
    channelId: numberValue(row.channel_id || row.channelId),
    postId: text(row.post_id || row.postId),
    platform: text(row.platform),
    status: text(row.status),
    message: text(row.message),
    fetchedCount: numberValue(row.fetched_count || row.fetchedCount),
    newCount: numberValue(row.new_count || row.newCount),
    collectSupported: row.collect_supported === false || row.collectSupported === false ? false : Boolean(row.collect_supported || row.collectSupported),
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

function commentActionLabel(post: ChannelContentPost, comments: ChannelCommentItem[], canCollect: boolean) {
  if (!canCollect) return '未接入';
  if (comments.length) return '刷新明细';
  if (post.comments > 0) return '补齐明细';
  return '尝试获取';
}

function MediaLightbox({ post, account, apiToken, onClose }: { post: ChannelContentPost; account: OfficialChannelAccount; apiToken?: string; onClose: () => void }) {
  const [active, setActive] = useState(0);
  const media = mediaState(post, account);
  const resolvedVideoUrl = useCachedVideoUrl(apiToken, account.platform, text(post.sourceId || post.id), media.videoUrl);
  const title = conciseTitle(post);
  const isVideo = media.kind === 'video' && Boolean(media.videoUrl || media.embedUrl);

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
          <span>播放 {viewsLabel(post)}</span>
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
          <span>评论 {formatter.format(post.comments)}</span>
          <span>点赞 {formatter.format(post.likes)}</span>
          <span>{post.postedAt || account.lastSyncAt || '-'}</span>
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
          <button type="button" disabled={loading || !canCollect} onClick={onCollect}>{commentActionLabel(post, comments, canCollect)}</button>
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
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    setPage(1);
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
        ? await collectChannelPostComments(apiToken, account.id, { postId: post.sourceId || post.id, url: post.url, limit: 100 })
        : await getChannelPostComments(apiToken, account.id, { postId: post.sourceId || post.id, url: post.url, limit: 80 });
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
        <strong>{compact(account.totalViews)}</strong>
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
      {posts.length ? (
        <div className="vkpi-channel-content-list">
          {posts.map((post, index) => {
            const title = conciseTitle(post);
            const copy = contentCopy(post);
            const status = post.viewsUnavailable ? 'pending insights' : (post.accountLevel ? 'snapshot' : account.syncStatus || 'synced');
            const date = compactDate(post.postedAt || account.lastSyncAt || '');
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
                  <MediaSlot post={post} account={account} apiToken={apiToken} compact />
                </div>
                <div className="vkpi-channel-content-card__body">
                  <h3 title={title}>{copy.headline}</h3>
                  <p title={title}>{copy.excerpt || title}</p>
                  <div className="vkpi-channel-content-card__metrics">
                    <span title={post.viewsUnavailable ? 'IG 图文/轮播无公开播放量' : undefined}>播放 <strong>{viewsLabel(post)}</strong></span>
                    <span>点赞 <strong>{formatter.format(post.likes)}</strong></span>
                    <button type="button" onClick={() => openComments(post)}>评论 <strong>{formatter.format(post.comments)}</strong></button>
                    <span>分享 <strong>{formatter.format(post.shares)}</strong></span>
                  </div>
                  {post.viewsUnavailable ? <p className="vkpi-channel-content-card__note">图文无公开播放，需后台 Insights 才能补齐。</p> : null}
                </div>
                <footer className="vkpi-channel-content-card__footer">
                  <small>{date}</small>
                  <div>
                    {platformExternalUrl(post.url) ? <a href={platformExternalUrl(post.url)} target="_blank" rel="noreferrer">打开原帖</a> : null}
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
      {previewPost ? <MediaLightbox post={previewPost} account={account} apiToken={apiToken} onClose={() => setPreviewPost(null)} /> : null}
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
