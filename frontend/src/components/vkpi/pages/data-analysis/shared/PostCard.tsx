import type { Row } from '../utils/types';
import {
  postAccountName, postPlatform, postTitle, postUrl, rowNumber, rowString,
} from '../utils/rowAccessors';
import { compact, platformClass, platformDisplay, platformInitial, prettyDate } from '../utils/platformHelpers';

interface PostCardProps {
  post: Row;
  accounts: Row[];
  onViewAnalytics: () => void;
}

export function PostCard({ post, accounts, onViewAnalytics }: PostCardProps) {
  const platform = postPlatform(post, accounts);
  const caption = postTitle(post);
  const thumb = rowString(post, ['thumbnail_url', 'cover_url', 'image_url']);
  const url = postUrl(post);
  const views = rowNumber(post, ['views', 'view_count', 'video_views']);
  const likes = rowNumber(post, ['likes', 'like_count']);
  const comments = rowNumber(post, ['comments', 'comment_count']);
  const account = postAccountName(post, accounts);
  const followers = rowNumber(post, ['followers', 'follower_count']);

  return (
    <article className="da-post-card">
      <header className="da-post-card__header">
        <div className={`da-post-card__avatar ${platformClass(platform)}`}>
          {platformInitial(platform)}
          <span className="da-post-card__avatar-platform">{platformInitial(platform)}</span>
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="da-post-card__handle">{account}</div>
          <div className="da-post-card__followers">
            {followers ? `${compact(followers)} followers` : `${platformDisplay(platform)} 账号`}
          </div>
          <div className="da-post-card__time">
            {prettyDate(rowString(post, ['published_at', 'posted_at', 'created_at']))}
          </div>
        </div>
        <button
          className="da-post-card__external"
          type="button"
          disabled={!url}
          onClick={() => { if (url) window.open(url, '_blank', 'noopener,noreferrer'); }}
          aria-label="打开原帖"
        >↗</button>
      </header>
      <div className="da-post-card__media">
        <span className="da-post-card__media-icon">▶</span>
        {thumb ? (
          <img src={thumb} alt="post thumbnail" loading="lazy" />
        ) : (
          <div className={`da-post-card__media-fallback ${platformClass(platform)}`}>
            {platformInitial(platform)}
          </div>
        )}
      </div>
      <p className="da-post-card__caption">
        {caption.length > 130 ? `${caption.slice(0, 130)}… ` : caption}
        <span className="da-post-card__see-more">查看更多</span>
      </p>
      <div className="da-post-card__tags">
        <button type="button" className="da-post-card__manual-tag">Tag post ▾</button>
        <span className="da-post-card__ai-tag">
          ✨ {rowString(post, ['content_pillar', 'pillar', 'ai_tag'], '内容信号')}
        </span>
      </div>
      <div className="da-post-card__metrics">
        <div className="da-post-card__metric"><span>Views</span><strong>{compact(views)}</strong></div>
        <div className="da-post-card__metric"><span>Likes</span><strong>{compact(likes)}</strong></div>
        <div className="da-post-card__metric"><span>Comments</span><strong>{compact(comments)}</strong></div>
      </div>
      <button className="da-post-card__view-analytics" type="button" onClick={onViewAnalytics}>
        📈 查看分析
      </button>
    </article>
  );
}
