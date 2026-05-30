import { useEffect, useMemo, useState } from 'react';
import { ExternalLink, ImageIcon, MessageCircle, Play, RefreshCw, X } from 'lucide-react';
import { getChannelPostComments, getOfficialChannelPosts } from '../../../../domains/channels';
import type { ChannelContentPost, ChannelCommentsResponse, ChannelPostPagination, OfficialChannelAccount } from '../channels/channelTypes';
import {
  compact,
  compactDate,
  conciseTitle,
  contentCopy,
  commentsResponse,
  mapPagination,
  mapPost,
  mediaStatusLabel,
  mediaStatusNote,
  mediaState,
  PAGE_SIZE,
  postVideoId,
  SORT_OPTIONS,
  type Row,
  viewsMetricLabel,
  WINDOW_OPTIONS,
} from '../channels/ChannelContentList.helpers';
import { platformDisplay } from '../../shared/vkpiDataUtils';
import { platformExternalUrl, useCachedVideoUrl } from '../../shared/mediaProxy';
import './myKolContentLayer.css';

type ContentSnapshot = {
  posts: ChannelContentPost[];
  pagination: ChannelPostPagination;
  fetchedAt: number;
};

const CONTENT_STALE_MS = 30_000;
const CONTENT_STORAGE_TTL_MS = 6 * 60 * 60 * 1000;
const CONTENT_STORAGE_PREFIX = 'vkpi:mykol:official-posts:';
const CONTENT_CACHE = new Map<string, ContentSnapshot>();

const METRIC_TABS = [
  { key: 'latest', label: '最新' },
  { key: 'views', label: '播放' },
  { key: 'likes', label: '点赞' },
  { key: 'comments', label: '评论' },
  { key: 'shares', label: '分享' },
];

function contentCacheKey(apiToken: string | undefined, accountId: number, page: number, sort: string, windowKey: string) {
  return `${accountId}:${page}:${sort}:${windowKey}:${apiToken ? apiToken.slice(-10) : 'no-token'}`;
}

function readStoredContent(key: string): ContentSnapshot | undefined {
  if (typeof window === 'undefined') return undefined;
  try {
    const raw = window.localStorage.getItem(`${CONTENT_STORAGE_PREFIX}${key}`);
    if (!raw) return undefined;
    const parsed = JSON.parse(raw) as ContentSnapshot;
    if (!parsed || !Array.isArray(parsed.posts) || !parsed.pagination || !parsed.fetchedAt) return undefined;
    if (Date.now() - parsed.fetchedAt > CONTENT_STORAGE_TTL_MS) return undefined;
    CONTENT_CACHE.set(key, parsed);
    return parsed;
  } catch {
    return undefined;
  }
}

function writeStoredContent(key: string, snapshot: ContentSnapshot) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(`${CONTENT_STORAGE_PREFIX}${key}`, JSON.stringify(snapshot));
  } catch {
    // Best effort cache only. The in-memory cache remains active for this session.
  }
}

function PostMedia({
  post,
  account,
  apiToken,
  compactMode = false,
}: {
  post: ChannelContentPost;
  account: OfficialChannelAccount;
  apiToken?: string;
  compactMode?: boolean;
}) {
  const media = mediaState(post, account);
  const cachedVideoUrl = useCachedVideoUrl(apiToken, account.platform, postVideoId(post), media.videoUrl || post.videoUrl || '');
  const videoUrl = cachedVideoUrl || media.videoUrl;
  const image = media.imageUrls[0];
  if (media.embedUrl && !compactMode) {
    return <iframe src={media.embedUrl} title={conciseTitle(post)} allow="autoplay; encrypted-media; picture-in-picture" allowFullScreen />;
  }
  if (videoUrl && compactMode && !image) {
    return <video src={videoUrl} muted playsInline preload="metadata" />;
  }
  if (videoUrl && !compactMode) {
    return <video src={videoUrl} poster={image || undefined} controls playsInline />;
  }
  if (image) return <img src={image} alt="" loading="lazy" />;
  return <ImageIcon size={30} />;
}

function PostPreviewModal({
  post,
  account,
  apiToken,
  onClose,
}: {
  post: ChannelContentPost;
  account: OfficialChannelAccount;
  apiToken?: string;
  onClose: () => void;
}) {
  const media = mediaState(post, account);
  const external = platformExternalUrl(post.url);
  const copy = contentCopy(post);
  const modalTitle = copy.headline || conciseTitle(post);
  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onClose]);

  return (
    <div className="mykol-content-modal" role="dialog" aria-modal="true" onClick={onClose}>
      <section className="mykol-content-modal__panel" onClick={(event) => event.stopPropagation()}>
        <header>
          <div>
            <span>{account.displayName} · {platformDisplay(account.platform)}</span>
            <h3 title={conciseTitle(post)}>{modalTitle}</h3>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭"><X size={18} /></button>
        </header>
        <div className={`mykol-content-modal__stage ${media.videoUrl || media.embedUrl ? 'is-video' : 'is-image'}`}>
          <PostMedia post={post} account={account} apiToken={apiToken} />
        </div>
        <footer>
          <span>{post.viewsUnavailable ? '播放不可用' : `${compact(post.views)} 播放`}</span>
          <span>{compact(post.likes)} 赞</span>
          <span>{compact(post.comments)} 评论</span>
          <span>{compact(post.shares)} 分享</span>
          {external ? <a href={external} target="_blank" rel="noreferrer">打开原帖</a> : null}
        </footer>
      </section>
    </div>
  );
}

function CommentsModal({
  post,
  account,
  apiToken,
  onClose,
}: {
  post: ChannelContentPost;
  account: OfficialChannelAccount;
  apiToken?: string;
  onClose: () => void;
}) {
  const [payload, setPayload] = useState<ChannelCommentsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!apiToken) {
      setError('未登录，无法读取评论。');
      return;
    }
    let cancelled = false;
    setLoading(true);
      setError('');
    getChannelPostComments(apiToken, account.id, { postId: post.sourceId || post.id, url: post.url, limit: 80 })
      .then((response) => {
        if (!cancelled) setPayload(commentsResponse(response as Row) as ChannelCommentsResponse);
      })
      .catch((requestError) => {
        if (!cancelled) setError(requestError instanceof Error ? requestError.message : '评论加载失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [account.id, apiToken, post.id, post.sourceId, post.url]);

  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onClose]);

  const comments = payload?.comments || [];

  return (
    <div className="mykol-content-modal" role="dialog" aria-modal="true" onClick={onClose}>
      <section className="mykol-content-modal__panel mykol-comments-panel" onClick={(event) => event.stopPropagation()}>
        <header>
          <div>
            <span>{account.displayName} · 评论</span>
            <h3>{conciseTitle(post)}</h3>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭"><X size={18} /></button>
        </header>
        <div className="mykol-comments-list">
          {loading ? <div className="mykol-empty">评论加载中...</div> : null}
          {error ? <div className="mykol-warning">{error}</div> : null}
          {!loading && !error && comments.length ? comments.map((comment) => (
            <article key={comment.id || comment.externalCommentId}>
              <b>{comment.author || '用户'}</b>
              <p>{comment.text || '无正文'}</p>
              <span>{compact(comment.likes)} 赞 · {comment.replyCount || 0} 回复 · {compactDate(comment.createdAt)}</span>
            </article>
          )) : null}
          {!loading && !error && !comments.length ? <div className="mykol-empty">暂无评论缓存。</div> : null}
        </div>
      </section>
    </div>
  );
}

function ContentSkeleton() {
  return (
    <div className="mykol-post-grid" aria-label="内容加载中">
      {[0, 1, 2, 3].map((item) => (
        <article className="mykol-post-card mykol-post-card--skeleton" key={item}>
          <span className="mykol-post-media" />
          <b />
          <em />
        </article>
      ))}
    </div>
  );
}

export function OfficialContentLayer({ account, apiToken }: { account?: OfficialChannelAccount; apiToken?: string }) {
  const [posts, setPosts] = useState<ChannelContentPost[]>([]);
  const [pagination, setPagination] = useState<ChannelPostPagination>(() => mapPagination());
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState('latest');
  const [windowKey, setWindowKey] = useState('year');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [preview, setPreview] = useState<ChannelContentPost | null>(null);
  const [commentsPost, setCommentsPost] = useState<ChannelContentPost | null>(null);
  const key = account ? contentCacheKey(apiToken, account.id, page, sort, windowKey) : '';

  const fallbackPosts = useMemo(() => account?.posts || [], [account]);
  const quickStats = useMemo(() => {
    if (!account) {
      return {
        total: 0,
        views: 0,
        likes: 0,
        comments: 0,
        shares: 0,
        renderable: 0,
        latest: '-',
      };
    }
    const renderable = posts.filter((post) => mediaState(post, account).renderable).length;
    const latest = posts
      .map((post) => post.postedAt)
      .filter(Boolean)
      .sort((left, right) => Date.parse(right) - Date.parse(left))[0];
    const pageViews = posts.reduce((sum, post) => sum + (post.viewsUnavailable ? 0 : post.views), 0);
    const accountViews = account.viewsUnavailable ? 0 : account.totalViews;
    const totalContent = account.postsCount || pagination.total || posts.length;
    return {
      total: totalContent,
      views: accountViews || pageViews,
      likes: account.totalLikes || posts.reduce((sum, post) => sum + post.likes, 0),
      comments: account.totalComments || posts.reduce((sum, post) => sum + post.comments, 0),
      shares: posts.reduce((sum, post) => sum + post.shares, 0),
      renderable,
      latest: latest ? compactDate(latest) : account.lastSyncAt ? compactDate(account.lastSyncAt) : '-',
    };
  }, [account, pagination.total, posts]);

  useEffect(() => {
    setPage(1);
  }, [account?.id, sort, windowKey]);

  useEffect(() => {
    if (!account) {
      setPosts([]);
      setPagination(mapPagination());
      setError('');
      setLoading(false);
      return;
    }
    if (!apiToken) {
      setPosts(fallbackPosts);
      setPagination(mapPagination({ page: 1, limit: PAGE_SIZE, total: fallbackPosts.length, pages: fallbackPosts.length ? 1 : 0 } as Row));
      setError(fallbackPosts.length ? '' : '未登录，无法读取真实内容分页。');
      setLoading(false);
      return;
    }
    const cached = CONTENT_CACHE.get(key) || readStoredContent(key);
    if (cached) setPosts(cached.posts);
    if (cached) setPagination(cached.pagination);
    setLoading(!cached);
    setError('');
    if (cached && Date.now() - cached.fetchedAt < CONTENT_STALE_MS) return;
    let cancelled = false;
    const startedAt = typeof performance !== 'undefined' ? performance.now() : Date.now();
    getOfficialChannelPosts(apiToken, account.id, { page, limit: PAGE_SIZE, sort, direction: 'desc', window: windowKey })
      .then((response) => {
        if (cancelled) return;
        const next = (Array.isArray(response.posts) ? response.posts : []).map((row) => mapPost(row as Row));
        const nextPagination = mapPagination(response.pagination as Row | undefined);
        const snapshot = { posts: next, pagination: nextPagination, fetchedAt: Date.now() };
        CONTENT_CACHE.set(key, snapshot);
        writeStoredContent(key, snapshot);
        setPosts(next);
        setPagination(nextPagination);
        if ((import.meta as unknown as { env?: { DEV?: boolean } }).env?.DEV) {
          const elapsed = Math.round((typeof performance !== 'undefined' ? performance.now() : Date.now()) - startedAt);
          console.info(`[V-KPI] official posts loaded in ${elapsed}ms`, { accountId: account.id, posts: next.length });
        }
      })
      .catch((requestError) => {
        if (cancelled) return;
        if (!cached) setPosts(fallbackPosts);
        if (!cached) setPagination(mapPagination({ page, limit: PAGE_SIZE, total: fallbackPosts.length, pages: fallbackPosts.length ? 1 : 0 } as Row));
        setError(requestError instanceof Error ? requestError.message : '官方账号内容加载失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [account, apiToken, fallbackPosts, key, page, sort, windowKey]);

  if (!account) return <div className="mykol-empty">选择平台和账号后查看真实内容样本。</div>;

  return (
    <div className="mykol-content-layer">
      <header>
        <div>
          <h3>{account.displayName}</h3>
          <p>{account.platformLabel || platformDisplay(account.platform)} · {account.handle}</p>
        </div>
        <div className="mykol-content-actions">
          {loading ? <span><RefreshCw size={13} /> 刷新中</span> : null}
          {account.accountUrl ? <a href={account.accountUrl} target="_blank" rel="noreferrer"><ExternalLink size={16} /></a> : null}
        </div>
      </header>
      <div className="mykol-content-toolbar">
        <div className="mykol-content-tabs">
          {METRIC_TABS.map((tab) => (
            <button className={sort === tab.key ? 'is-active' : ''} key={tab.key} type="button" onClick={() => setSort(tab.key)}>
              {tab.label}
            </button>
          ))}
        </div>
        <select value={windowKey} onChange={(event) => setWindowKey(event.target.value)}>
          {WINDOW_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
        <select value={sort} onChange={(event) => setSort(event.target.value)}>
          {SORT_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}优先</option>)}
        </select>
      </div>
      <div className="mykol-content-insights" aria-label="官方内容快速梳理">
        <span><b>{quickStats.total ? compact(quickStats.total) : '0'}</b> 内容总数</span>
        <span><b>{compact(quickStats.views)}</b> 累计{account.viewsMetricLabel || '播放'}</span>
        <span><b>{compact(quickStats.likes)}</b> 累计点赞</span>
        <span><b>{compact(quickStats.comments)}</b> 累计评论</span>
        <span><b>{compact(quickStats.shares)}</b> 本页分享</span>
        <span><b>{quickStats.renderable}/{posts.length || 0}</b> 本页媒体</span>
        <span><b>{quickStats.latest}</b> 最新</span>
      </div>
      {error ? <div className="mykol-warning">{error}</div> : null}
      {loading && !posts.length ? <ContentSkeleton /> : null}
      {posts.length ? (
        <div className="mykol-post-grid">
          {posts.map((post) => {
            const media = mediaState(post, account);
            const copy = contentCopy(post);
            const note = mediaStatusNote(post);
            const external = platformExternalUrl(post.url);
            return (
              <article className="mykol-post-card" key={post.id || post.url}>
                <button className="mykol-post-card__preview" type="button" onClick={() => setPreview(post)} aria-label="预览内容">
                  <span className="mykol-post-media">
                    <PostMedia post={post} account={account} apiToken={apiToken} compactMode />
                    {media.videoUrl || media.embedUrl || post.videoUrl || post.mediaType === 'video' ? <i><Play size={18} /></i> : null}
                    <strong>{mediaStatusLabel(post)}</strong>
                  </span>
                </button>
                <div className="mykol-post-card__body">
                  <b title={post.title || '内容快照'}>{copy.headline || '内容快照'}</b>
                  <p title={copy.excerpt || note || ''}>{copy.excerpt || note || viewsMetricLabel(post, account)}</p>
                </div>
                <div className="mykol-post-metrics">
                  <span>{post.viewsUnavailable ? `${viewsMetricLabel(post, account)} --` : `${viewsMetricLabel(post, account)} ${compact(post.views)}`}</span>
                  <span>点赞 {compact(post.likes)}</span>
                  <button type="button" onClick={(event) => { event.stopPropagation(); setCommentsPost(post); }}>
                    评论 {compact(post.comments)}
                  </button>
                  <span>分享 {compact(post.shares)}</span>
                </div>
                <footer className="mykol-post-card__footer">
                  <small>{post.postedAt ? compactDate(post.postedAt) : '发布时间待接入'}</small>
                  <div>
                    <button type="button" onClick={() => setCommentsPost(post)}>评论明细</button>
                    {external ? <a href={external} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>原帖</a> : null}
                  </div>
                </footer>
              </article>
            );
          })}
        </div>
      ) : !loading ? (
        <div className="mykol-empty">该账号暂无真实内容样本。</div>
      ) : null}
      <footer className="mykol-content-pagination">
        <span>显示 {posts.length} / {pagination.total || posts.length} 条</span>
        <div>
          <button type="button" disabled={!pagination.hasPrev || loading} onClick={() => setPage((current) => Math.max(1, current - 1))}>上一页</button>
          <b>{pagination.page || page} / {pagination.pages || 1}</b>
          <button type="button" disabled={!pagination.hasNext || loading} onClick={() => setPage((current) => current + 1)}>下一页</button>
        </div>
      </footer>
      {preview ? <PostPreviewModal post={preview} account={account} apiToken={apiToken} onClose={() => setPreview(null)} /> : null}
      {commentsPost ? <CommentsModal post={commentsPost} account={account} apiToken={apiToken} onClose={() => setCommentsPost(null)} /> : null}
    </div>
  );
}
