import { useCallback, useEffect, useMemo, useState } from 'react';
import { ExternalLink } from 'lucide-react';
import { getKolComments } from '../../../../domains/channels';
import type { VkpiKolOption, VkpiProjectRow } from '../../vkpiTypes';
import { numberFormatter } from '../../shared/vkpiFormatters';
import { KolMediaLightbox, KolMediaSlot, mediaBadge } from '../channels/MyKolMedia';
import {
  categoryForPost,
  commentsForPost,
  compactDate,
  conciseText,
  displayCount,
  fetchAllKolPostRows,
  filterAndSortPosts,
  mapCommentRows,
  mapPostRows,
  summarizePostInsights,
} from '../channels/myKolMatrixData';
import {
  CONTENT_SORT_OPTIONS,
  CONTENT_WINDOW_OPTIONS,
  type KolCommentItem,
  type KolContentDirection,
  type KolContentSort,
  type KolContentWindow,
  type PostPreview,
} from '../channels/myKolMatrixTypes';
import '../channels/styles/channel-kols-content.css';
import '../channels/styles/channel-kols-modal-responsive.css';
import './myKolEmployeeLayer.css';

const PAGE_SIZE = 8;
const KOL_POST_STALE_MS = 10 * 60 * 1000;
const KOL_POST_STORAGE_TTL_MS = 24 * 60 * 60 * 1000;
const KOL_POST_STORAGE_PREFIX = 'vkpi:mykol:kol-posts:';

type KolPostCacheEntry = {
  items: PostPreview[];
  total: number;
  fetchedAt: number;
};

const KOL_POST_CACHE = new Map<string, KolPostCacheEntry>();

function kolPostCacheKey(apiToken: string | undefined, kolId: string) {
  return `${kolId}:${apiToken ? apiToken.slice(-10) : 'no-token'}`;
}

function readStoredKolPosts(cacheKey: string): KolPostCacheEntry | undefined {
  if (typeof window === 'undefined') return undefined;
  try {
    const raw = window.localStorage.getItem(`${KOL_POST_STORAGE_PREFIX}${cacheKey}`);
    if (!raw) return undefined;
    const parsed = JSON.parse(raw) as Partial<KolPostCacheEntry>;
    if (!Array.isArray(parsed.items)) return undefined;
    const fetchedAt = Number(parsed.fetchedAt || 0);
    if (!fetchedAt || Date.now() - fetchedAt > KOL_POST_STORAGE_TTL_MS) {
      window.localStorage.removeItem(`${KOL_POST_STORAGE_PREFIX}${cacheKey}`);
      return undefined;
    }
    return {
      items: parsed.items,
      total: Number(parsed.total || parsed.items.length) || parsed.items.length,
      fetchedAt,
    };
  } catch {
    return undefined;
  }
}

function writeStoredKolPosts(cacheKey: string, entry: KolPostCacheEntry) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(`${KOL_POST_STORAGE_PREFIX}${cacheKey}`, JSON.stringify(entry));
  } catch {
    // localStorage quota is a performance hint, not a data dependency.
  }
}

function quietMissingError(error: unknown) {
  const message = error instanceof Error ? error.message : String(error || '');
  return /not found|404/i.test(message) ? '' : message || '内容加载失败';
}

function emptyPostState() {
  return { items: [] as PostPreview[], total: 0, loading: false, error: '' };
}

function emptyCommentState() {
  return { items: [] as KolCommentItem[], total: 0, loading: false, error: '' };
}

async function fetchKolCommentRows(apiToken: string, kolId: string, postUrl: string) {
  const rows: Array<Record<string, unknown>> = [];
  let total = 0;
  let offset = 0;
  const seenOffsets = new Set<number>();

  for (let page = 0; page < 10; page += 1) {
    if (seenOffsets.has(offset)) break;
    seenOffsets.add(offset);
    const response = await getKolComments(apiToken, kolId, { limit: 100, offset, postUrl });
    const pageRows = Array.isArray(response.items) ? response.items : [];
    rows.push(...pageRows);
    total = Number(response.page?.total || rows.length) || rows.length;
    const nextOffset = Number(response.page?.next_offset || 0);
    if (!nextOffset || nextOffset <= offset || !pageRows.length) break;
    offset = nextOffset;
  }

  return { rows, total: total || rows.length };
}

function postOneLine(post: PostPreview) {
  const signals = [...post.gearMentions, ...post.brandMentions, ...post.competitorMentions].slice(0, 4);
  if (signals.length) return `识别：${signals.join(' / ')}`;
  return post.title || '内容描述待接入';
}

function CommentsModal({
  comments,
  loading,
  post,
  onClose,
  onReload,
}: {
  comments: KolCommentItem[];
  loading: boolean;
  post: PostPreview;
  onClose: () => void;
  onReload: () => void;
}) {
  const postComments = commentsForPost(post, comments);
  return (
    <div className="vkpi-my-kol-comment-modal" role="dialog" aria-modal="true" aria-label="KOL评论明细">
      <section>
        <header>
          <div>
            <span>评论层</span>
            <h3>{conciseText(post.title, 72)}</h3>
            <p>{displayCount(post.comments)} 条公开评论 · {postComments.length} 条正文缓存</p>
          </div>
          <button type="button" onClick={onClose}>关闭</button>
        </header>
        <div className="vkpi-my-kol-comment-list">
          {postComments.length ? postComments.map((comment) => (
            <article key={comment.id}>
              <header><strong>{comment.author}</strong><span>赞 {numberFormatter.format(comment.likes)}</span></header>
              <p>{comment.text}</p>
              <small>{comment.intentTags.length ? comment.intentTags.join(' / ') : comment.sentiment} · {compactDate(comment.createdAt)}</small>
            </article>
          )) : (
            <div className="vkpi-my-kol-content-empty">
              <span>{loading ? '评论正文加载中。' : post.comments > 0 ? `评论数已同步：${numberFormatter.format(post.comments)} 条；正文未采集 —— 点本面板上方「采集评论」抓取后即可在此查看。` : '暂无评论正文 —— 点本面板上方「采集评论」抓取后即可在此查看。'}</span>
              <button type="button" onClick={onReload} disabled={loading}>{loading ? '加载中' : '重新加载评论'}</button>
            </div>
          )}
        </div>
        <footer>
          {post.url ? <a href={post.url} target="_blank" rel="noreferrer">打开原帖</a> : <span />}
          <button type="button" onClick={onClose}>完成</button>
        </footer>
      </section>
    </div>
  );
}

export function EmployeeKolContentLayer({
  apiToken,
  kol,
  projects,
  viltroxOnly = true,
  postsOverride,
  analyzedCount,
  subtitle,
  commentsFetcher,
}: {
  apiToken?: string;
  kol?: VkpiKolOption;
  projects: VkpiProjectRow[];
  viltroxOnly?: boolean;
  /** Pool 收藏行旁路(C4-full):evidence 已映射好的 PostPreview 直接喂入,跳过 kol posts 接口。 */
  postsOverride?: PostPreview[];
  /** 已深析条数(诚实空态用:0 命中时说明"共 N 条已分析"而非回退显示全部)。 */
  analyzedCount?: number;
  subtitle?: string;
  /** Pool 收藏行评论旁路:按 post 取评论(evidence 评论表),替代 marketing kol comments 接口。 */
  commentsFetcher?: (post: PostPreview) => Promise<{ rows: Array<Record<string, unknown>>; total: number }>;
}) {
  const [postState, setPostState] = useState(emptyPostState);
  const [commentState, setCommentState] = useState(emptyCommentState);
  const [contentSort, setContentSort] = useState<KolContentSort>('latest');
  const [contentDirection, setContentDirection] = useState<KolContentDirection>('desc');
  const [contentWindow, setContentWindow] = useState<KolContentWindow>('all');
  const [page, setPage] = useState(1);
  const [previewPost, setPreviewPost] = useState<PostPreview | null>(null);
  const [commentPost, setCommentPost] = useState<PostPreview | null>(null);

  const loadComments = useCallback((targetPost?: PostPreview | null) => {
    if (!targetPost?.url || (!commentsFetcher && (!apiToken || !kol?.id))) return;
    setCommentState((current) => ({ ...current, loading: true, error: '' }));
    (commentsFetcher ? commentsFetcher(targetPost) : fetchKolCommentRows(apiToken as string, kol!.id, targetPost.url))
      .then(({ rows, total }) => {
        setCommentState({
          items: mapCommentRows(rows),
          total,
          loading: false,
          error: '',
        });
      })
      .catch((error) => {
        setCommentState((current) => ({
          ...current,
          loading: false,
          error: quietMissingError(error),
        }));
      });
  }, [apiToken, kol?.id, commentsFetcher]);

  useEffect(() => {
    setPreviewPost(null);
    setCommentPost(null);
    setPage(1);
    setPostState(emptyPostState());
    setCommentState(emptyCommentState());
    if (postsOverride) {
      setPostState({ items: postsOverride, total: postsOverride.length, loading: false, error: '' });
      return undefined;
    }
    if (!apiToken || !kol?.id) return undefined;

    let cancelled = false;
    let postTimer: number | undefined;
    const cacheKey = kolPostCacheKey(apiToken, kol.id);
    const cached = KOL_POST_CACHE.get(cacheKey) || readStoredKolPosts(cacheKey);
    const hasFreshCache = cached && cached.items.length > 0 && Date.now() - cached.fetchedAt < KOL_POST_STALE_MS;
    if (cached) {
      KOL_POST_CACHE.set(cacheKey, cached);
      setPostState({ items: cached.items, total: cached.total, loading: !hasFreshCache, error: '' });
    } else {
      setPostState((current) => ({ ...current, loading: true, error: '' }));
    }
    if (!hasFreshCache) {
      postTimer = window.setTimeout(() => {
        fetchAllKolPostRows(apiToken, kol.id)
          .then(({ rows, total }) => {
            if (cancelled) return;
            const items = mapPostRows(rows);
            const nextTotal = Number(total || rows.length) || rows.length;
            const nextCache = { items, total: nextTotal, fetchedAt: Date.now() };
            KOL_POST_CACHE.set(cacheKey, nextCache);
            writeStoredKolPosts(cacheKey, nextCache);
            setPostState({
              items,
              total: nextTotal,
              loading: false,
              error: '',
            });
          })
          .catch((error) => {
            if (cancelled) return;
            if (cached) {
              setPostState({
                items: cached.items,
                total: cached.total,
                loading: false,
                error: quietMissingError(error),
              });
              return;
            }
            setPostState({
              items: [],
              total: 0,
              loading: false,
              error: quietMissingError(error),
            });
          });
      }, cached ? 180 : 0);
    } else if (cached) {
      setPostState({
        items: cached.items,
        total: cached.total,
        loading: false,
        error: '',
      });
    }

    return () => {
      cancelled = true;
      if (postTimer) window.clearTimeout(postTimer);
    };
  }, [apiToken, kol?.id, postsOverride]);

  useEffect(() => {
    if (!commentPost) return;
    setCommentState(emptyCommentState());
    loadComments(commentPost);
  }, [commentPost?.id, commentPost?.url, loadComments]);

  useEffect(() => {
    setPage(1);
  }, [contentDirection, contentSort, contentWindow, kol?.id, viltroxOnly]);

  const filteredPosts = useMemo(() => (
    filterAndSortPosts(postState.items, viltroxOnly ? 'viltrox' : 'all', contentSort, contentDirection, contentWindow)
  ), [contentDirection, contentSort, contentWindow, postState.items, viltroxOnly]);
  const pageCount = Math.max(1, Math.ceil(filteredPosts.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const visiblePosts = filteredPosts.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);
  const insights = useMemo(() => summarizePostInsights(filteredPosts), [filteredPosts]);
  const selectedProject = projects[0];

  if (!kol) {
    return <div className="mykol-employee-content-empty">请选择一个 KOL 查看真实内容。</div>;
  }

  return (
    <div className="mykol-employee-content">
      <header className="mykol-employee-content__head">
        <div>
          <span>VILTROX 相关内容</span>
          <h3>{kol.name}</h3>
          <p>{subtitle || (viltroxOnly ? '全平台 Viltrox 内容' : '全平台全部内容')} · {filteredPosts.length} / {postState.total || postState.items.length} 条</p>
        </div>
        {kol.profileUrl ? (
          <a href={kol.profileUrl} target="_blank" rel="noreferrer" aria-label="打开主页"><ExternalLink size={17} /></a>
        ) : null}
      </header>

      <div className="mykol-employee-content__toolbar">
        <div>
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
        </div>
        <select value={contentWindow} onChange={(event) => setContentWindow(event.target.value as KolContentWindow)} aria-label="时间范围">
          {CONTENT_WINDOW_OPTIONS.map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}
        </select>
        <select value={contentDirection} onChange={(event) => setContentDirection(event.target.value as KolContentDirection)} aria-label="排序方向">
          <option value="desc">{contentSort === 'latest' ? '最新优先' : '最高优先'}</option>
          <option value="asc">{contentSort === 'latest' ? '最早优先' : '最低优先'}</option>
        </select>
      </div>

      <div className="mykol-employee-content__insights">
        <span><b>{displayCount(insights.totalViews)}</b> 播放</span>
        <span><b>{displayCount(insights.totalLikes)}</b> 点赞</span>
        <span><b>{displayCount(insights.totalComments)}</b> 评论</span>
        <span><b>{insights.engagement ? `${insights.engagement.toFixed(2)}%` : '—'}</b> 互动率</span>
        <span><b>{insights.viltroxCount}</b> Viltrox</span>
      </div>

      {postState.loading ? <div className="mykol-employee-content-empty">正在读取真实 KOL posts。</div> : null}
      {postState.error ? <div className="mykol-warning">{postState.error}</div> : null}
      {commentState.error ? <div className="mykol-warning">{commentState.error}</div> : null}

      {visiblePosts.length ? (
        <div className="vkpi-my-kol-content-list mykol-employee-post-grid">
          {visiblePosts.map((post) => (
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
                <span className="vkpi-my-kol-content-card__kind">{mediaBadge(post, kol.platform)}</span>
                <KolMediaSlot post={post} platform={kol.platform} apiToken={apiToken} compact />
              </div>
              <div className="vkpi-my-kol-content-card__body">
                <h3 title={post.title}>{conciseText(post.title, 88)}</h3>
                <p title={postOneLine(post)}>{conciseText(postOneLine(post), 86)}</p>
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
      ) : !postState.loading ? (
        <div className="mykol-employee-content-empty">
          {viltroxOnly
            ? `无 Viltrox 内容（共 ${postState.total || postState.items.length} 条已采集${typeof analyzedCount === 'number' ? ` / ${analyzedCount} 条已分析` : ''}）。`
            : '该 KOL 暂无已缓存内容。'}
          {viltroxOnly ? <small>关掉「Viltrox 相关」可查看该 KOL 全部内容；不自动回退,避免误导。</small> : null}
          {selectedProject ? <small>项目线索：{selectedProject.campaign || selectedProject.productName || selectedProject.stage}</small> : null}
        </div>
      ) : null}

      <footer className="mykol-employee-content__pager">
        <span>显示 {filteredPosts.length ? `${(currentPage - 1) * PAGE_SIZE + 1}-${Math.min(currentPage * PAGE_SIZE, filteredPosts.length)}` : '0'} / {filteredPosts.length}</span>
        <div>
          <button type="button" disabled={currentPage <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>上一页</button>
          <b>{currentPage} / {pageCount}</b>
          <button type="button" disabled={currentPage >= pageCount} onClick={() => setPage((value) => Math.min(pageCount, value + 1))}>下一页</button>
        </div>
      </footer>

      {previewPost ? <KolMediaLightbox post={previewPost} platform={kol.platform} apiToken={apiToken} onClose={() => setPreviewPost(null)} /> : null}
      {commentPost ? (
        <CommentsModal
          comments={commentState.items}
          loading={commentState.loading}
          onClose={() => setCommentPost(null)}
          onReload={() => loadComments(commentPost)}
          post={commentPost}
        />
      ) : null}
    </div>
  );
}
