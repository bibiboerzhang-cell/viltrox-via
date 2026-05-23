import { useState } from 'react';
import type { FormEvent } from 'react';
import { searchVkpi } from '../../../../services/vkpi.ui-api';
import { proxiedImageUrl } from '../../shared/mediaProxy';
import type { Row } from './utils/types';

interface NaturalSearchPanelProps {
  apiToken?: string;
  onMessage: (message: string) => void;
}

function compactText(value: unknown, fallback = '-'): string {
  const text = String(value ?? '').trim();
  return text || fallback;
}

function asRecord(value: unknown): Row {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {};
}

function asRecordArray(value: unknown): Row[] {
  return Array.isArray(value) ? value.map(asRecord).filter((row) => Object.keys(row).length > 0) : [];
}

function firstText(...values: unknown[]): string {
  for (const value of values) {
    const text = String(value ?? '').trim();
    if (text) return text;
  }
  return '';
}

function formatCount(value: unknown): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) return '';
  return new Intl.NumberFormat('en', { notation: numeric >= 10000 ? 'compact' : 'standard', maximumFractionDigits: 1 }).format(numeric);
}

function initials(value: unknown): string {
  const text = compactText(value, '?');
  return text.slice(0, 1).toUpperCase();
}

export function NaturalSearchPanel({ apiToken, onMessage }: NaturalSearchPanelProps) {
  const [query, setQuery] = useState('godox pricing');
  const [items, setItems] = useState<Row[]>([]);
  const [meta, setMeta] = useState<Row>({});
  const [loading, setLoading] = useState(false);

  const runSearch = async () => {
    if (!apiToken || !query.trim()) return;
    setLoading(true);
    try {
      const response = await searchVkpi(apiToken, query.trim(), 20);
      setItems(response.items || []);
      setMeta(response as Row);
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '自然搜索失败');
    } finally {
      setLoading(false);
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void runSearch();
  };

  return (
    <section className="da-natural-search">
      <header className="da-natural-search__header">
        <div>
          <span className="da-kicker da-kicker--light">P9 Search</span>
          <h2>自然搜索</h2>
          <p>KOL、Memory、推荐、竞品信号和告警的确定性搜索。当前不调用 provider。</p>
        </div>
      </header>

      <form className="da-natural-search__bar" onSubmit={submit}>
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索 V-KPI" />
        <button className="da-black-button" type="submit" disabled={!apiToken || loading || !query.trim()}>
          {loading ? '搜索中...' : '搜索'}
        </button>
      </form>

      <div className="da-natural-search__meta">
        <span>total <strong>{String(meta.total ?? items.length)}</strong></span>
        <span>provider <strong>{String(Boolean(meta.provider_calls))}</strong></span>
        <span>write <strong>{String(Boolean(meta.write_db))}</strong></span>
        <span>tokens <strong>{Array.isArray(meta.tokens) ? meta.tokens.join(', ') : '-'}</strong></span>
      </div>

      <div className="da-table-wrap">
        <table className="da-table">
          <thead>
            <tr>
              <th>Type</th>
              <th>Score</th>
              <th>Source</th>
              <th>Title</th>
              <th>Evidence</th>
            </tr>
          </thead>
          <tbody>
            {items.length ? items.map((item, index) => {
              const evidence = asRecord(item.evidence);
              const avatarUrl = firstText(item.avatar_url, evidence.avatar_url);
              const recentPosts = asRecordArray(item.recent_posts).length
                ? asRecordArray(item.recent_posts)
                : asRecordArray(evidence.recent_posts);
              const handle = firstText(item.handle, evidence.handle);
              const platform = firstText(item.platform, evidence.platform);
              const profileUrl = firstText(item.profile_url, evidence.profile_url);
              const followers = firstText(item.followers, evidence.followers);
              const evidenceText = compactText(
                evidence.detail || evidence.body || evidence.fact_value_text || evidence.bio || evidence.handle || evidence.identity_key,
              ).slice(0, 180);
              return (
                <tr key={`${String(item.source_table)}-${String(item.source_id)}-${index}`}>
                  <td>{compactText(item.result_type)}</td>
                  <td>{String(item.score ?? 0)}</td>
                  <td>{compactText(item.source_table)}:{compactText(item.source_id)}</td>
                  <td>
                    <div className="da-natural-search__entity">
                      {avatarUrl ? (
                        <img src={proxiedImageUrl(avatarUrl)} alt="" loading="lazy" referrerPolicy="no-referrer" />
                      ) : (
                        <span className="da-natural-search__avatar-fallback">{initials(item.title)}</span>
                      )}
                      <div>
                        <strong>{compactText(item.title)}</strong>
                        <span>
                          {[handle ? `@${handle.replace(/^@/, '')}` : '', platform, followers ? `${formatCount(followers)} followers` : ''].filter(Boolean).join(' · ')}
                        </span>
                        {profileUrl ? <a href={profileUrl} target="_blank" rel="noreferrer">Open profile</a> : null}
                      </div>
                    </div>
                  </td>
                  <td>
                    <div className="da-natural-search__evidence-text">{evidenceText}</div>
                    {recentPosts.length ? (
                      <div className="da-natural-search__posts">
                        {recentPosts.slice(0, 3).map((post, postIndex) => {
                          const postTitle = compactText(post.title || post.post_url || post.url, 'Untitled post');
                          const postUrl = firstText(post.post_url, post.url);
                          const postMetric = formatCount(post.views) || formatCount(post.likes) || formatCount(post.comments);
                          const postMetricLabel = post.views ? 'views' : post.likes ? 'likes' : post.comments ? 'comments' : '';
                          return postUrl ? (
                            <a key={`${postUrl}-${postIndex}`} href={postUrl} target="_blank" rel="noreferrer">
                              <span>{postTitle}</span>
                              {postMetric ? <em>{postMetric} {postMetricLabel}</em> : null}
                            </a>
                          ) : (
                            <span key={`${postTitle}-${postIndex}`}>
                              <span>{postTitle}</span>
                              {postMetric ? <em>{postMetric} {postMetricLabel}</em> : null}
                            </span>
                          );
                        })}
                      </div>
                    ) : null}
                  </td>
                </tr>
              );
            }) : (
              <tr>
                <td className="da-table-empty" colSpan={5}>
                  {loading ? '正在搜索...' : '暂无搜索结果。'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
