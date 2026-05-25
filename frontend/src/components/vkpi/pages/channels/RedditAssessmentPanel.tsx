import { useEffect, useState } from 'react';
import { getRedditChannelAssessment } from '../../../../domains/channels';
import type { OfficialChannelAccount, RedditAssessmentPost, RedditAssessmentResponse } from './channelTypes';

type Row = Record<string, unknown>;

const formatter = new Intl.NumberFormat('en-US');

function compact(value: number) {
  if (!value) return '0';
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 10_000 ? 0 : 1)}K`;
  return formatter.format(value);
}

function text(value: unknown, fallback = '') {
  return String(value ?? fallback).trim();
}

function numberValue(value: unknown) {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function rows(value: unknown): Row[] {
  return Array.isArray(value) ? value.filter((item): item is Row => Boolean(item) && typeof item === 'object') : [];
}

function mapPost(row: Row): RedditAssessmentPost {
  return {
    id: text(row.id || row.source_id || row.url),
    sourceId: text(row.source_id || row.sourceId),
    title: text(row.title, 'Reddit 内容'),
    url: text(row.url),
    mediaUrl: text(row.media_url || row.mediaUrl),
    videoUrl: text(row.video_url || row.videoUrl),
    imageUrls: [],
    mediaUrls: [],
    mediaType: text(row.media_type || row.mediaType),
    mediaKind: text(row.media_kind || row.mediaKind),
    postedAt: text(row.posted_at || row.postedAt),
    views: numberValue(row.views),
    likes: numberValue(row.likes),
    comments: numberValue(row.comments),
    shares: numberValue(row.shares),
    assessmentScore: numberValue(row.assessment_score || row.assessmentScore),
    assessmentCategory: text(row.assessment_category || row.assessmentCategory),
    assessmentLabel: text(row.assessment_label || row.assessmentLabel),
    score: numberValue(row.score),
    upvoteRatio: numberValue(row.upvote_ratio || row.upvoteRatio),
    author: text(row.author),
    flair: text(row.flair),
    subreddit: text(row.subreddit),
  };
}

function mapResponse(row: Row): RedditAssessmentResponse {
  const account = (row.account && typeof row.account === 'object' ? row.account : {}) as Row;
  const summary = (row.summary && typeof row.summary === 'object' ? row.summary : {}) as Row;
  return {
    channelId: numberValue(row.channel_id || row.channelId),
    source: text(row.source),
    account: {
      id: numberValue(account.id),
      handle: text(account.handle),
      displayName: text(account.display_name || account.displayName || account.handle, 'Reddit'),
      subscribers: numberValue(account.subscribers),
      postsCount: numberValue(account.posts_count || account.postsCount),
      lastSyncAt: text(account.last_sync_at || account.lastSyncAt),
    },
    summary: {
      posts: numberValue(summary.posts),
      recordsTotal: numberValue(summary.records_total || summary.recordsTotal || summary.posts),
      analysisWindow: text(summary.analysis_window || summary.analysisWindow || 'year'),
      comments: numberValue(summary.comments),
      recordComments: numberValue(summary.record_comments || summary.recordComments || summary.comments),
      score: numberValue(summary.score),
      qualityCount: numberValue(summary.quality_count || summary.qualityCount),
      attentionCount: numberValue(summary.attention_count || summary.attentionCount),
    },
    distribution: rows(row.distribution).map((item) => ({ key: text(item.key), label: text(item.label), count: numberValue(item.count) })),
    latestQuality: rows(row.latest_quality || row.latestQuality).map(mapPost),
    needsAttention: rows(row.needs_attention || row.needsAttention).map(mapPost),
    items: rows(row.items).map(mapPost),
  };
}

function PostLine({ post }: { post: RedditAssessmentPost }) {
  return (
    <a className="vkpi-reddit-post-line" href={post.url || '#'} target="_blank" rel="noreferrer">
      <strong title={post.title}>{post.title}</strong>
      <span>{post.assessmentLabel || '未评估'} · 分 {formatter.format(post.assessmentScore)} · 赞 {formatter.format(post.likes || post.score)} · 评论 {formatter.format(post.comments)}</span>
    </a>
  );
}

export function RedditAssessmentPanel({ account, apiToken }: { account?: OfficialChannelAccount; apiToken?: string }) {
  const [payload, setPayload] = useState<RedditAssessmentResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    if (!apiToken || !account?.id || account.platform.toLowerCase() !== 'reddit') {
      setPayload(null);
      setError('');
      return;
    }
    setLoading(true);
    setError('');
    getRedditChannelAssessment(apiToken, account.id)
      .then((response) => {
        if (!cancelled) setPayload(mapResponse(response));
      })
      .catch((requestError) => {
        if (!cancelled) setError(requestError instanceof Error ? requestError.message : 'Reddit 评估加载失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [account?.id, account?.platform, apiToken]);

  if (!account || account.platform.toLowerCase() !== 'reddit') return null;
  const assessedPosts = payload?.summary.posts || account.postsCount;
  const recordPosts = payload?.summary.recordsTotal || account.postsCount;
  const assessedComments = payload?.summary.comments || account.totalComments;
  const recordLabel = recordPosts > assessedPosts ? `今年 ${formatter.format(assessedPosts)} 帖 · 记录 ${formatter.format(recordPosts)} 条` : `${formatter.format(assessedPosts)} 帖`;

  return (
    <section className="vkpi-reddit-assessment">
      <button type="button" className="vkpi-reddit-assessment__summary" onClick={() => setOpen(true)}>
        <div>
          <span>Reddit 站内评估</span>
          <h2>r/{account.handle || payload?.account.handle || 'VILTROX_GLOBAL'}</h2>
          <p>{loading ? '评估加载中' : error || `${compact(payload?.account.subscribers || account.followers)} 订阅 · ${recordLabel} · ${formatter.format(assessedComments)} 评论`}</p>
        </div>
        <div className="vkpi-reddit-assessment__chips">
          {(payload?.distribution || []).map((item) => <span key={item.key}>{item.label} {formatter.format(item.count)}</span>)}
        </div>
      </button>
      {open ? (
        <div className="vkpi-comment-modal" role="dialog" aria-modal="true" aria-label="Reddit 站内评估" onClick={() => setOpen(false)}>
          <section className="vkpi-comment-modal__panel vkpi-reddit-modal__panel" onClick={(event) => event.stopPropagation()}>
            <header className="vkpi-comment-modal__header">
              <div>
                <span>Reddit · r/{account.handle}</span>
                <h3>内容评估与风险分类</h3>
              </div>
              <button type="button" onClick={() => setOpen(false)} aria-label="关闭">×</button>
            </header>
            <div className="vkpi-reddit-modal__stats">
              <span>{recordLabel}</span>
              <span>{formatter.format(payload?.summary.comments || 0)} 评论</span>
              <span>{compact(payload?.summary.score || 0)} score</span>
              <span>{payload?.account.lastSyncAt || account.lastSyncAt || '-'}</span>
            </div>
            <div className="vkpi-reddit-modal__body">
              <section>
                <h4>评分分布</h4>
                <div className="vkpi-reddit-assessment__chips">
                  {(payload?.distribution || []).map((item) => <span key={item.key}>{item.label} {formatter.format(item.count)}</span>)}
                </div>
              </section>
              <section>
                <h4>优质内容</h4>
                {(payload?.latestQuality || []).length ? payload?.latestQuality.map((post) => <PostLine key={post.id || post.url} post={post} />) : <p>暂无优质内容命中。</p>}
              </section>
              <section>
                <h4>需要关注</h4>
                {(payload?.needsAttention || []).length ? payload?.needsAttention.map((post) => <PostLine key={post.id || post.url} post={post} />) : <p>暂无风险内容命中。</p>}
              </section>
            </div>
          </section>
        </div>
      ) : null}
    </section>
  );
}
