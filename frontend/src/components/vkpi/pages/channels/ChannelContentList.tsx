import { useEffect, useMemo, useState } from 'react';
import {
  collectChannelPostComments,
  enqueueVideoCacheTask,
  getChannelPostComments,
  getOfficialChannelPosts,
} from '../../../../domains/channels';
import type { ChannelCommentItem, ChannelCommentsResponse, ChannelContentPost, ChannelPostPagination, OfficialChannelAccount } from './channelTypes';
import { useTaskCenter } from '../../../tasks/TaskCenter';
import { invalidateCachedVideoUrl, platformExternalUrl, proxiedImageUrl, useCachedVideoUrl } from "../../shared/mediaProxy";
import {
  PAGE_SIZE,
  SORT_OPTIONS,
  WINDOW_OPTIONS,
  accountViewsValue,
  canCollectComments,
  canQueueVideoCache,
  commentActionLabel,
  commentCoverage,
  commentEmptyText,
  commentsResponse,
  compactDate,
  conciseTitle,
  contentCopy,
  fallbackInitial,
  formatter,
  mapPagination,
  mapPost,
  mediaBadge,
  mediaState,
  mediaStatusLabel,
  mediaStatusNote,
  postVideoId,
  postVideoSourceUrl,
  taskResultText,
  text,
  videoCacheKey,
  viewsLabel,
  viewsMetricLabel,
  viewsUnavailableText,
} from "./ChannelContentList.helpers";
import type { Row } from "./ChannelContentList.helpers";

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
    return <span className="vkpi-channel-content-card__pending">{mediaStatusLabel(post)}</span>;
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
    return <span className="vkpi-channel-content-card__pending">{mediaStatusLabel(post)}</span>;
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
            const mediaNote = mediaStatusNote(post);
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
                  {mediaNote ? <p className="vkpi-channel-content-card__note">{mediaNote}</p> : null}
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
