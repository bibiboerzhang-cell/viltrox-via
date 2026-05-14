import { useState } from 'react';
import type { Row } from '../utils/types';
import {
  findAccountForPost, postAccountName, postPlatform, postTitle, postUrl, rowNumber, rowString,
} from '../utils/rowAccessors';
import { platformExternalUrl, proxiedImageUrl, proxiedVideoUrl, redirectedVideoUrl } from '../utils/mediaProxy';
import { accountAvatarUrl, postThumbnailUrl, postVideoUrl } from '../utils/mediaFields';
import { compact, platformClass, platformDisplay, platformInitial, prettyDate } from '../utils/platformHelpers';

interface PostCardProps {
  post: Row;
  accounts: Row[];
  onViewAnalytics?: () => void;
  onOpenPost?: (post: Row) => void;
}

export function PostCard({ post, accounts, onViewAnalytics, onOpenPost }: PostCardProps) {
  const platform = postPlatform(post, accounts);
  const caption = postTitle(post);
  const thumb = proxiedImageUrl(postThumbnailUrl(post));
  const rawVideoUrl = postVideoUrl(post);
  const primaryVideoUrl = proxiedVideoUrl(rawVideoUrl);
  const fallbackVideoUrl = redirectedVideoUrl(rawVideoUrl);
  const [useVideoFallback, setUseVideoFallback] = useState(false);
  const [videoUnavailable, setVideoUnavailable] = useState(false);
  const videoUrl = videoUnavailable ? '' : (useVideoFallback && fallbackVideoUrl ? fallbackVideoUrl : primaryVideoUrl);
  const url = postUrl(post);
  const originalUrl = platformExternalUrl(url);
  const views = rowNumber(post, ['views', 'view_count', 'video_views']);
  const likes = rowNumber(post, ['likes', 'like_count']);
  const comments = rowNumber(post, ['comments', 'comment_count']);
  const matchedAccount = findAccountForPost(post, accounts);
  const canOpenAccount = Boolean(matchedAccount && onViewAnalytics);
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
        {originalUrl ? (
          <a
            className="da-post-card__external"
            href={originalUrl}
            target="_blank"
            rel="noopener noreferrer"
            aria-label="打开原帖"
          >↗</a>
        ) : (
          <button className="da-post-card__external" type="button" disabled aria-label="打开原帖">↗</button>
        )}
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
              } else {
                setVideoUnavailable(true);
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
        {videoUnavailable && originalUrl ? (
          <a
            className="da-post-card__media-warning"
            href={originalUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            视频链接失效，打开原帖
          </a>
        ) : null}
      </div>
      <p className="da-post-card__caption">
        {captionIsTruncated ? `${caption.slice(0, 130)}… ` : caption}
        {captionIsTruncated && originalUrl ? (
          <a
            className="da-post-card__see-more"
            href={originalUrl}
            target="_blank"
            rel="noopener noreferrer"
          >打开原帖</a>
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
      <div className="da-post-card__actions">
        <button
          className="da-post-card__view-analytics"
          type="button"
          disabled={!onOpenPost}
          title={onOpenPost ? '打开单帖详情与分析' : '单帖详情暂不可用'}
          onClick={() => onOpenPost?.(post)}
        >
          单帖详情
        </button>
        <button
          className="da-post-card__view-analytics"
          type="button"
          disabled={!canOpenAccount}
          title={canOpenAccount ? '打开该帖所属账号分析' : '该帖子未匹配到账号，不能跳转账号分析'}
          onClick={() => {
            if (canOpenAccount) onViewAnalytics?.();
          }}
        >
          账号分析
        </button>
      </div>
    </article>
  );
}
