import { useEffect, useMemo, useState } from 'react';
import type { FormEvent } from 'react';
import { getContentBrainStatus, listContentBrainPosts } from '../../../../services/vkpi/market-api';
import { platformDisplay } from './utils/platformHelpers';
import type { Row } from './utils/types';

interface ContentBrainPanelProps {
  apiToken?: string;
  onMessage: (message: string) => void;
}

function numberValue(value: unknown): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function pct(value: unknown): string {
  return `${Math.round(numberValue(value) * 100)}%`;
}

function asRecord(value: unknown): Row {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {};
}

function asList(value: unknown): Row[] {
  return Array.isArray(value) ? value as Row[] : [];
}

function topEntries(value: unknown, limit = 6): Array<[string, number]> {
  return Object.entries(asRecord(value))
    .map(([key, count]) => [key, numberValue(count)] as [string, number])
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, limit);
}

function compactText(value: unknown, fallback = '-'): string {
  const text = String(value ?? '').trim();
  return text || fallback;
}

function tagText(items: Row[], key: string, fallback = '-'): string {
  const values = items.map((item) => compactText(item[key], '')).filter(Boolean);
  return values.length ? values.slice(0, 4).join(', ') : fallback;
}

export function ContentBrainPanel({ apiToken, onMessage }: ContentBrainPanelProps) {
  const [status, setStatus] = useState<Row>({});
  const [posts, setPosts] = useState<Row[]>([]);
  const [filterStatus, setFilterStatus] = useState('done');
  const [platform, setPlatform] = useState('');
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);

  const statusCounts = useMemo(() => asRecord(status.status_counts), [status]);
  const tagEntries = useMemo(() => topEntries(status.tag_distribution), [status]);
  const riskEntries = useMemo(() => topEntries(status.risk_distribution), [status]);
  const productEntries = useMemo(() => topEntries(status.product_distribution), [status]);

  const load = async () => {
    if (!apiToken) return;
    setLoading(true);
    try {
      const [statusResponse, postResponse] = await Promise.all([
        getContentBrainStatus(apiToken),
        listContentBrainPosts(apiToken, {
          status: filterStatus || undefined,
          platform: platform || undefined,
          query: query || undefined,
          limit: 80,
        }),
      ]);
      setStatus(statusResponse);
      setPosts(postResponse.posts || []);
    } catch (error) {
      onMessage(error instanceof Error ? error.message : 'Content Brain 读取失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [apiToken, filterStatus, platform]);

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    void load();
  };

  return (
    <section className="da-content-brain">
      <header className="da-content-brain__header">
        <div>
          <span className="da-kicker da-kicker--light">P6 Content Brain</span>
          <h2>内容脑分析</h2>
          <p>帖子级 / 媒体级内容标签、产品意图、风险和品牌提及。这里只读分析结果，不触发 provider。</p>
        </div>
        <button className="da-black-button" type="button" disabled={!apiToken || loading} onClick={() => void load()}>
          {loading ? '读取中...' : '刷新'}
        </button>
      </header>

      <div className="da-content-brain__stats">
        <Metric label="帖子总数" value={String(status.post_count || 0)} />
        <Metric label="已分析" value={String(status.analyzed_count || 0)} />
        <Metric label="覆盖率" value={pct(status.coverage_ratio)} />
        <Metric label="媒体资产" value={String(status.media_count || 0)} />
        <Metric label="Budget Scope" value={compactText(status.budget_scope)} />
      </div>

      <div className="da-content-brain__distributions">
        <Distribution title="Tags" entries={tagEntries} />
        <Distribution title="Risks" entries={riskEntries} />
        <Distribution title="Products" entries={productEntries} />
        <Distribution title="Status" entries={topEntries(statusCounts)} />
      </div>

      <form className="da-content-brain__filters" onSubmit={submitSearch}>
        <select value={filterStatus} onChange={(event) => setFilterStatus(event.target.value)}>
          <option value="">全部状态</option>
          <option value="done">done</option>
          <option value="pending">pending</option>
          <option value="failed">failed</option>
        </select>
        <select value={platform} onChange={(event) => setPlatform(event.target.value)}>
          <option value="">全部平台</option>
          <option value="instagram">Instagram</option>
          <option value="youtube">YouTube</option>
          <option value="tiktok">TikTok</option>
          <option value="facebook">Facebook</option>
        </select>
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索标题、正文、URL 或账号" />
        <button className="da-white-button" type="submit" disabled={!apiToken || loading}>搜索</button>
      </form>

      <div className="da-table-wrap">
        <table className="da-table">
          <thead>
            <tr>
              <th>Post</th>
              <th>平台</th>
              <th>账号</th>
              <th>状态</th>
              <th>Tags</th>
              <th>Products</th>
              <th>Risks</th>
              <th>Summary</th>
            </tr>
          </thead>
          <tbody>
            {posts.length ? posts.map((post) => (
              <tr key={String(post.id)}>
                <td>
                  {post.post_url ? <a href={String(post.post_url)} target="_blank" rel="noreferrer">#{String(post.id)}</a> : `#${String(post.id)}`}
                </td>
                <td>{platformDisplay(post.platform)}</td>
                <td>{compactText(post.account_display_name || post.account_handle)}</td>
                <td>{compactText(post.analysis_status)}</td>
                <td>{tagText(asList(post.content_tags), 'tag')}</td>
                <td>{tagText(asList(post.product_intents), 'product')}</td>
                <td>{tagText(asList(post.risk_flags), 'flag_key')}</td>
                <td>{compactText(post.ai_summary).slice(0, 180)}</td>
              </tr>
            )) : (
              <tr>
                <td className="da-table-empty" colSpan={8}>
                  {loading ? '正在读取内容脑分析...' : '暂无符合条件的内容脑结果。'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="da-content-brain__metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Distribution({ title, entries }: { title: string; entries: Array<[string, number]> }) {
  return (
    <div className="da-content-brain__distribution">
      <strong>{title}</strong>
      {entries.length ? entries.map(([key, count]) => (
        <span key={key}>{key}<em>{count}</em></span>
      )) : <span>none<em>0</em></span>}
    </div>
  );
}
