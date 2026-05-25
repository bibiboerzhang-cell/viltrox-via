import { numberFormatter } from '../../shared/vkpiFormatters';
import { commentsForPost, compactDate, conciseText, displayCount } from './myKolMatrixData';
import type { EffectiveMyKolItem, KolCommentItem, PostPreview } from './myKolMatrixTypes';

interface MyKolCommentsModalProps {
  apiToken?: string;
  comments: KolCommentItem[];
  post: PostPreview;
  scanningKolId: string;
  selectedItem?: EffectiveMyKolItem;
  onClose: () => void;
  onRescan: (item: EffectiveMyKolItem) => void;
}

export function MyKolCommentsModal({
  apiToken,
  comments,
  post,
  scanningKolId,
  selectedItem,
  onClose,
  onRescan,
}: MyKolCommentsModalProps) {
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
              <span>{post.comments > 0 ? `评论数已同步：${numberFormatter.format(post.comments)} 条；评论正文还未缓存。` : '当前帖子暂无评论正文缓存。'}</span>
              <button
                type="button"
                onClick={() => selectedItem ? onRescan(selectedItem) : undefined}
                disabled={!selectedItem || scanningKolId === selectedItem.id || !apiToken || !selectedItem.kolId}
              >
                {selectedItem && scanningKolId === selectedItem.id ? '抓取中' : '重新抓取评论'}
              </button>
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
