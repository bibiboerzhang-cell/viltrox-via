import { useState } from 'react';
import type { Row } from '../utils/types';
import {
  findAccountForPost, postAccountName, postPlatform, postTitle, postUrl, rowNumber, rowString,
} from '../utils/rowAccessors';
import { proxiedImageUrl, proxiedVideoUrl, redirectedVideoUrl } from '../utils/mediaProxy';
import { accountAvatarUrl, postThumbnailUrl, postVideoUrl } from '../utils/mediaFields';
import { compact, platformClass, platformDisplay, platformInitial, prettyDate } from '../utils/platformHelpers';

interface PostCardProps {
  post: Row;
  accounts: Row[];
  onViewAnalytics: () => void;
}

export function PostCard({ post, accounts, onViewAnalytics }: PostCardProps) {
  const platform = postPlatform(post, accounts);
  const caption = postTitle(post);
  const thumb = proxiedImageUrl(postThumbnailUrl(post));
  const rawVideoUrl = postVideoUrl(post);
  const primaryVideoUrl = proxiedVideoUrl(rawVideoUrl);
  const fallbackVideoUrl = redirectedVideoUrl(rawVideoUrl);
  const [useVideoFallback, setUseVideoFallback] = useState(false);
  const videoUrl = useVideoFallback && fallbackVideoUrl ? fallbackVideoUrl : primaryVideoUrl;
  const url = postUrl(post);
  const views = rowNumber(post, ['views', 'view_count', 'video_views']);
  const likes = rowNumber(post, ['likes', 'like_count']);
  const comments = rowNumber(post, ['comments', 'comment_count']);
  const matchedAccount = findAccountForPost(post, accounts);
  const account = postAccountName(post, accounts);
  const accountAvatar = proxiedImageUrl(accountAvatarUrl(matchedAccount));
  const followers = rowNumber(post, ['followers', 'follower_count'])
    ?? rowNumber(matchedAccount, ['followers', 'follower_count']);
  const captionIsTruncated = caption.length > 130;

  return (
    <article className="da-post-card">
      <header className="da-post-card__header">
        <div className={`da-post-card__avatar ${platformClass(platform)}`}>
          {accountAvatar ? <img src={accountAvatar} alt="" loading="lazy" /> : platformInitial(platform)}
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
        {videoUrl ? (
          <video
            src={videoUrl}
            poster={thumb || undefined}
            controls
            preload="metadata"
            playsInline
            onError={() => {
              if (!useVideoFallback && fallbackVideoUrl && fallbackVideoUrl !== videoUrl) {
                setUseVideoFallback(true);
              }
            }}
          />
        ) : thumb ? (
          <img src={thumb} alt="post thumbnail" loading="lazy" />
        ) : (
          <div className={`da-post-card__media-fallback ${platformClass(platform)}`}>
            {platformInitial(platform)}
          </div>
        )}
        {!videoUrl ? <span className="da-post-card__media-icon">▶</span> : null}
      </div>
      <p className="da-post-card__caption">
        {captionIsTruncated ? `${caption.slice(0, 130)}… ` : caption}
        {captionIsTruncated && url ? (
          <button
            className="da-post-card__see-more"
            type="button"
            onClick={() => window.open(url, '_blank', 'noopener,noreferrer')}
          >打开原帖</button>
        ) : null}
      </p>
      <div className="da-post-card__tags">
        <span className="da-post-card__manual-tag">
          {rowString(post, ['manual_tag', 'tag_label'], '未手动标注')}
        </span>
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
        📈 打开账号分析
      </button>
    </article>
  );
}
