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

interface AnalysisView {
  status: string;
  qualityScore: number | null;
  method: string;
  providers: string[];
  title: string;
  summary: string;
  error: string;
  raw: string;
}

function stringifyAnalysis(analysis?: Row | null): string {
  if (!analysis) return '';
  return JSON.stringify(analysis, null, 2).slice(0, 1800);
}

function analysisProviders(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map((item) => String(item || '').trim()).filter(Boolean).slice(0, 5);
  }
  if (typeof value === 'string') {
    return value.split(/[,+]/).map((item) => item.trim()).filter(Boolean).slice(0, 5);
  }
  return [];
}

function analysisView(analysis?: Row | null): AnalysisView | null {
  if (!analysis) return null;
  const scrape = (analysis.scrape || {}) as Row;
  const summary = rowString(analysis, [
    'summary',
    'analysis_summary',
    'recommendation',
    'reasoning',
    'insight',
    'result',
    'message',
  ]);
  return {
    status: rowString(analysis, ['status', 'state']) || 'done',
    qualityScore: rowNumber(analysis, ['quality_score', 'score']),
    method: rowString(analysis, ['method', 'source', 'model']) || '',
    providers: analysisProviders(analysis.providers),
    title: rowString(scrape, ['title', 'description']) || rowString(analysis, ['title']),
    summary: summary || rowString(scrape, ['description']),
    error: rowString(analysis, ['error', 'detail']),
    raw: stringifyAnalysis(analysis),
  };
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
  const structuredAnalysis = analysisView(analysis);

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
          {originalUrl ? (
            <a
              className="da-black-button"
              href={originalUrl}
              target="_blank"
              rel="noopener noreferrer"
            >打开原帖</a>
          ) : (
            <button className="da-black-button" type="button" disabled>打开原帖</button>
          )}
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
          {analysisBusy ? (
            <p className="da-post-detail__busy">
              真实 URL 分析处理中。视频或 Instagram 链路可能需要 30-90 秒；超过接口上限会显示失败原因。
            </p>
          ) : null}
          {structuredAnalysis ? (
            <div className="da-post-analysis">
              <div className="da-post-analysis__meta">
                <span>Status: {structuredAnalysis.status}</span>
                {structuredAnalysis.qualityScore !== null ? <span>Score: {structuredAnalysis.qualityScore}</span> : null}
                {structuredAnalysis.method ? <span>Method: {structuredAnalysis.method}</span> : null}
                {structuredAnalysis.providers.length ? <span>Providers: {structuredAnalysis.providers.join(' + ')}</span> : null}
              </div>
              {structuredAnalysis.summary ? (
                <article className="da-post-analysis__card">
                  <span>Summary</span>
                  <p>{structuredAnalysis.summary}</p>
                </article>
              ) : null}
              {structuredAnalysis.title && structuredAnalysis.title !== structuredAnalysis.summary ? (
                <article className="da-post-analysis__card">
                  <span>Source Text</span>
                  <p>{structuredAnalysis.title}</p>
                </article>
              ) : null}
              {structuredAnalysis.error ? <p className="da-post-detail__error">{structuredAnalysis.error}</p> : null}
              {structuredAnalysis.raw ? (
                <details className="da-post-analysis__raw">
                  <summary>查看原始返回</summary>
                  <pre>{structuredAnalysis.raw}</pre>
                </details>
              ) : null}
            </div>
          ) : (
            <p className="da-muted-copy">点击“运行单帖分析”后，将调用真实 URL 分析链路并展示结果；不会展示假分析。</p>
          )}
        </section>
      </div>
    </aside>
  );
}
