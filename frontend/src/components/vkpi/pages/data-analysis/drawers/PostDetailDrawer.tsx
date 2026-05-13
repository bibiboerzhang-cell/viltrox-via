import { useState } from 'react';

import type { Row } from '../utils/types';
import {
  findAccountForPost,
  postAccountName,
  postPlatform,
  postTitle,
  postUrl,
  rowNumber,
  rowString,
} from '../utils/rowAccessors';
import { platformExternalUrl, proxiedImageUrl, proxiedVideoUrl, redirectedVideoUrl } from '../utils/mediaProxy';
import { accountAvatarUrl, postPlatformUrl, postThumbnailUrl, postVideoUrl } from '../utils/mediaFields';
import { compact, platformClass, platformDisplay, platformInitial, prettyDate } from '../utils/platformHelpers';

interface PostDetailDrawerProps {
  post: Row | null;
  accounts: Row[];
  analysis?: Row | null;
  analysisBusy?: boolean;
  analysisError?: string;
  onClose: () => void;
  onAnalyze: (post: Row) => void;
}

function postEngagement(post: Row): number {
  return (rowNumber(post, ['likes', 'like_count']) || 0)
    + (rowNumber(post, ['comments', 'comment_count']) || 0)
    + (rowNumber(post, ['shares', 'share_count']) || 0)
    + (rowNumber(post, ['saves', 'save_count']) || 0);
}

function analysisText(analysis?: Row | null): string {
  if (!analysis) return '';
  const scrape = (analysis.scrape || {}) as Row;
  const fields = [
    rowString(analysis, ['summary', 'recommendation', 'reasoning']),
    rowString(scrape, ['title', 'description']),
    rowString(analysis, ['status']) ? `status: ${rowString(analysis, ['status'])}` : '',
    rowNumber(analysis, ['quality_score']) !== null ? `quality_score: ${rowNumber(analysis, ['quality_score'])}` : '',
  ].filter(Boolean);
  return fields.length ? fields.join('\n') : JSON.stringify(analysis, null, 2).slice(0, 1200);
}

export function PostDetailDrawer({
  post,
  accounts,
  analysis,
  analysisBusy = false,
  analysisError = '',
  onClose,
  onAnalyze,
}: PostDetailDrawerProps) {
  const [useVideoFallback, setUseVideoFallback] = useState(false);
  const [videoUnavailable, setVideoUnavailable] = useState(false);

  if (!post) return null;

  const matchedAccount = findAccountForPost(post, accounts);
  const platform = postPlatform(post, accounts);
  const account = postAccountName(post, accounts);
  const accountAvatar = proxiedImageUrl(accountAvatarUrl(matchedAccount));
  const title = postTitle(post);
  const rawOriginalUrl = postUrl(post) || postPlatformUrl(post);
  const originalUrl = platformExternalUrl(rawOriginalUrl);
  const thumbnail = proxiedImageUrl(postThumbnailUrl(post));
  const rawVideoUrl = postVideoUrl(post);
  const proxiedUrl = proxiedVideoUrl(rawVideoUrl);
  const fallbackUrl = redirectedVideoUrl(rawVideoUrl);
  const videoUrl = videoUnavailable ? '' : (useVideoFallback && fallbackUrl ? fallbackUrl : proxiedUrl);
  const views = rowNumber(post, ['views', 'view_count', 'video_views', 'play_count']);
  const likes = rowNumber(post, ['likes', 'like_count']);
  const comments = rowNumber(post, ['comments', 'comment_count']);
  const shares = rowNumber(post, ['shares', 'share_count']);
  const saves = rowNumber(post, ['saves', 'save_count']);
  const publishedAt = rowString(post, ['published_at', 'posted_at', 'created_at']);
  const analysisSummary = analysisText(analysis);

  return (
    <aside className="da-post-detail da-post-detail--open" aria-label="单帖详情">
      <header className="da-post-detail__header">
        <div className={`da-post-detail__avatar ${platformClass(platform)}`}>
          {accountAvatar ? <img src={accountAvatar} alt="" loading="lazy" /> : platformInitial(platform)}
        </div>
        <div>
          <span>单帖分析 · {platformDisplay(platform)}</span>
          <h3>{account}</h3>
          <p>{prettyDate(publishedAt)}</p>
        </div>
        <button type="button" onClick={onClose} aria-label="关闭单帖详情">×</button>
      </header>

      <div className="da-post-detail__body">
        <div className="da-post-detail__media">
          {videoUrl ? (
            <video
              src={videoUrl}
              poster={thumbnail || undefined}
              controls
              preload="metadata"
              playsInline
              onError={() => {
                if (!useVideoFallback && fallbackUrl && fallbackUrl !== videoUrl) {
                  setUseVideoFallback(true);
                } else {
                  setVideoUnavailable(true);
                }
              }}
            />
          ) : thumbnail ? (
            <img src={thumbnail} alt="post thumbnail" loading="lazy" />
          ) : (
            <div className={`da-post-detail__media-fallback ${platformClass(platform)}`}>{platformInitial(platform)}</div>
          )}
          {videoUnavailable ? <span className="da-post-detail__warning">视频链接失效，请打开原帖查看。</span> : null}
        </div>

        <div className="da-post-detail__actions">
          <button
            className="da-black-button"
            type="button"
            disabled={!originalUrl}
            onClick={() => { if (originalUrl) window.open(originalUrl, '_blank', 'noopener,noreferrer'); }}
          >打开原帖</button>
          <button
            className="da-white-button"
            type="button"
            disabled={!originalUrl || analysisBusy}
            onClick={() => onAnalyze(post)}
          >{analysisBusy ? '分析中...' : '运行单帖分析'}</button>
        </div>

        <section className="da-post-detail__section">
          <h4>内容</h4>
          <p>{title}</p>
        </section>

        <section className="da-post-detail__metrics">
          <div><span>Views</span><strong>{compact(views)}</strong></div>
          <div><span>Likes</span><strong>{compact(likes)}</strong></div>
          <div><span>Comments</span><strong>{compact(comments)}</strong></div>
          <div><span>Shares</span><strong>{compact(shares)}</strong></div>
          <div><span>Saves</span><strong>{compact(saves)}</strong></div>
          <div><span>Engagement</span><strong>{compact(postEngagement(post))}</strong></div>
        </section>

        <section className="da-post-detail__section">
          <h4>单帖分析结果</h4>
          {analysisError ? <p className="da-post-detail__error">{analysisError}</p> : null}
          {analysisSummary ? (
            <pre>{analysisSummary}</pre>
          ) : (
            <p className="da-muted-copy">点击“运行单帖分析”后，将调用真实 URL 分析链路并展示结果；不会展示假分析。</p>
          )}
        </section>
      </div>
    </aside>
  );
}
