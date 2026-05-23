import { useEffect, useMemo, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { createKolDecisionAudit, getKolPoolIntelligenceCard, searchVkpi } from '../../../../services/vkpi.ui-api';
import { proxiedImageUrl } from '../../shared/mediaProxy';
import type { Row } from './utils/types';

interface NaturalSearchPanelProps {
  apiToken?: string;
  onMessage: (message: string) => void;
}

interface NaturalSearchHistoryItem {
  id: string;
  query: string;
  resultCount: number;
  status: string;
  searchedAt: string;
}

interface SearchProgressState {
  visible: boolean;
  percent: number;
  message: string;
  activeKey: string;
  doneKeys: string[];
  errorKey?: string;
}

interface RecentContentCard {
  id: string;
  title: string;
  url: string;
  platform: string;
  handle: string;
  sourceTitle: string;
  publishedAt: string;
  views: string;
  likes: string;
  comments: string;
}

const NATURAL_SEARCH_HISTORY_KEY = 'vkpi:natural-search-history:v1';
const MAX_SEARCH_HISTORY = 8;
const SEARCH_REVEAL_BATCH_SIZE = 6;
const RECENT_CONTENT_CARD_LIMIT = 8;

const DECISION_OPTIONS = [
  { key: 'contact', label: '可联系', detail: '证据足够，适合进入沟通或项目跟进。', severity: 'medium' },
  { key: 'watch', label: '可观察', detail: '保留候选，等待更多内容或业务场景。', severity: 'low' },
  { key: 'caution', label: '谨慎', detail: '存在数据缺口或竞品风险，联系前需要复核。', severity: 'high' },
  { key: 'avoid', label: '避开', detail: '当前证据不支持推进，避免进入联系名单。', severity: 'high' },
] as const;

const SEARCH_PROGRESS_STEPS = [
  { key: 'query', label: '解析查询' },
  { key: 'pool', label: '查 KOL / Memory' },
  { key: 'evidence', label: '整理证据' },
  { key: 'render', label: '显示结果' },
] as const;

const IDLE_PROGRESS: SearchProgressState = {
  visible: false,
  percent: 0,
  message: '',
  activeKey: '',
  doneKeys: [],
};

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

function numberValue(value: unknown): number {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
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

function formatPostDate(value: unknown): string {
  const text = firstText(value);
  if (!text) return '';
  const parsed = new Date(text);
  if (Number.isNaN(parsed.getTime())) return text.slice(0, 10);
  return parsed.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function initials(value: unknown): string {
  const text = compactText(value, '?');
  return text.slice(0, 1).toUpperCase();
}

function loadSearchHistory(): NaturalSearchHistoryItem[] {
  if (typeof window === 'undefined') return [];
  try {
    const parsed = JSON.parse(window.localStorage.getItem(NATURAL_SEARCH_HISTORY_KEY) || '[]');
    if (!Array.isArray(parsed)) return [];
    return parsed.map((item) => {
      const row = asRecord(item);
      const query = firstText(row.query);
      if (!query) return null;
      return {
        id: firstText(row.id, query.toLowerCase()),
        query,
        resultCount: Number(row.resultCount) || 0,
        status: firstText(row.status, '只读'),
        searchedAt: firstText(row.searchedAt, new Date().toISOString()),
      };
    }).filter(Boolean).slice(0, MAX_SEARCH_HISTORY) as NaturalSearchHistoryItem[];
  } catch {
    return [];
  }
}

function saveSearchHistory(items: NaturalSearchHistoryItem[]) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(NATURAL_SEARCH_HISTORY_KEY, JSON.stringify(items.slice(0, MAX_SEARCH_HISTORY)));
  } catch {
    // Search history is convenience-only; private-mode storage failures should not block search.
  }
}

function historyAge(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  const deltaSeconds = Math.max(0, Math.floor((Date.now() - parsed.getTime()) / 1000));
  if (deltaSeconds < 60) return '刚刚';
  const minutes = Math.floor(deltaSeconds / 60);
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  return `${days} 天前`;
}

function recentPostsForItem(item: Row, evidence = asRecord(item.evidence)): Row[] {
  const fromItem = asRecordArray(item.recent_posts);
  if (fromItem.length) return fromItem;
  return asRecordArray(evidence.recent_posts);
}

function kolPoolIdForItem(item: Row): string {
  const evidence = asRecord(item.evidence);
  if (firstText(item.source_table) !== 'vkpi_kol_pool') return '';
  return firstText(item.source_id, item.kol_pool_id, evidence.id, evidence.kol_pool_id);
}

function evidenceRows(card: Row | null): Row[] {
  return asRecordArray(card?.evidence_index).filter((row) => firstText(row.section));
}

function cardSectionPayload(card: Row | null, section: unknown): Row {
  if (!card) return {};
  return asRecord(card[firstText(section)]);
}

function evidenceSectionLabel(value: unknown): string {
  const section = firstText(value);
  const labels: Record<string, string> = {
    freshness: 'Freshness',
    dimensions11: '11D',
    competitors: 'Competitors',
    brand_signal: 'Brand Signal',
    memory_card: 'Memory',
    product_fit: 'Product Fit',
    comment_intelligence: 'Comment',
  };
  return labels[section] || section || 'Evidence';
}

function evidencePayloadItems(payload: Row): Row[] {
  for (const key of ['evidence', 'signals', 'top', 'recent_posts', 'recent_cooperations', 'relations']) {
    const rows = asRecordArray(payload[key]);
    if (rows.length) return rows;
  }
  return [];
}

export function NaturalSearchPanel({ apiToken, onMessage }: NaturalSearchPanelProps) {
  const [query, setQuery] = useState('godox pricing');
  const [items, setItems] = useState<Row[]>([]);
  const [meta, setMeta] = useState<Row>({});
  const [loading, setLoading] = useState(false);
  const [searchHistory, setSearchHistory] = useState<NaturalSearchHistoryItem[]>(() => loadSearchHistory());
  const [visibleCount, setVisibleCount] = useState(0);
  const [searchProgress, setSearchProgress] = useState<SearchProgressState>(IDLE_PROGRESS);
  const [detailSource, setDetailSource] = useState<Row | null>(null);
  const [detailCard, setDetailCard] = useState<Row | null>(null);
  const [detailLoadingId, setDetailLoadingId] = useState('');
  const [detailError, setDetailError] = useState('');
  const [activeEvidenceSection, setActiveEvidenceSection] = useState('');
  const [decisionSubmitting, setDecisionSubmitting] = useState('');
  const [decisionMessage, setDecisionMessage] = useState('');
  const [decisionError, setDecisionError] = useState('');
  const revealTimerRef = useRef<number | null>(null);
  const progressTimerRefs = useRef<number[]>([]);
  const searchRunRef = useRef(0);

  const recordSearchHistory = (nextQuery: string, response: Row) => {
    const trimmed = nextQuery.trim();
    if (!trimmed) return;
    const resultCount = Number(response.total) || (Array.isArray(response.items) ? response.items.length : 0);
    const status = `${Boolean(response.provider_calls) ? 'provider' : '只读'} · ${Boolean(response.write_db) ? 'write' : 'no-write'}`;
    const nextItem: NaturalSearchHistoryItem = {
      id: `${trimmed.toLowerCase()}:${Date.now()}`,
      query: trimmed,
      resultCount,
      status,
      searchedAt: new Date().toISOString(),
    };
    setSearchHistory((previous) => {
      const deduped = [nextItem, ...previous.filter((item) => item.query.toLowerCase() !== trimmed.toLowerCase())].slice(0, MAX_SEARCH_HISTORY);
      saveSearchHistory(deduped);
      return deduped;
    });
  };

  const clearProgressTimers = () => {
    progressTimerRefs.current.forEach((timerId) => window.clearTimeout(timerId));
    progressTimerRefs.current = [];
  };

  const clearTimers = () => {
    if (revealTimerRef.current !== null) {
      window.clearInterval(revealTimerRef.current);
      revealTimerRef.current = null;
    }
    clearProgressTimers();
  };

  const setProgress = (progress: Partial<SearchProgressState>) => {
    setSearchProgress((previous) => ({ ...previous, visible: true, ...progress }));
  };

  const scheduleProgress = (runId: number, delayMs: number, progress: Partial<SearchProgressState>) => {
    const timerId = window.setTimeout(() => {
      if (searchRunRef.current !== runId) return;
      setProgress(progress);
    }, delayMs);
    progressTimerRefs.current.push(timerId);
  };

  const revealResults = (runId: number, resultCount: number) => {
    if (!resultCount) {
      setVisibleCount(0);
      setProgress({
        percent: 100,
        message: '没有匹配结果；没有生成假数据。',
        activeKey: 'render',
        doneKeys: ['query', 'pool', 'evidence', 'render'],
      });
      return;
    }
    const firstBatch = Math.min(SEARCH_REVEAL_BATCH_SIZE, resultCount);
    setVisibleCount(firstBatch);
    setProgress({
      percent: resultCount > firstBatch ? 82 : 100,
      message: resultCount > firstBatch ? `已返回 ${resultCount} 条，正在逐批显示。` : `已显示 ${resultCount} 条结果。`,
      activeKey: 'render',
      doneKeys: ['query', 'pool', 'evidence'],
    });
    if (resultCount <= firstBatch) {
      setProgress({
        percent: 100,
        doneKeys: ['query', 'pool', 'evidence', 'render'],
      });
      return;
    }
    revealTimerRef.current = window.setInterval(() => {
      if (searchRunRef.current !== runId) return;
      setVisibleCount((previous) => {
        const next = Math.min(resultCount, previous + SEARCH_REVEAL_BATCH_SIZE);
        if (next >= resultCount) {
          if (revealTimerRef.current !== null) {
            window.clearInterval(revealTimerRef.current);
            revealTimerRef.current = null;
          }
          setProgress({
            percent: 100,
            message: `已显示 ${resultCount} 条结果。`,
            activeKey: 'render',
            doneKeys: ['query', 'pool', 'evidence', 'render'],
          });
        } else {
          setProgress({
            percent: Math.min(98, 82 + Math.round((next / resultCount) * 16)),
            message: `已显示 ${next}/${resultCount} 条结果。`,
            activeKey: 'render',
            doneKeys: ['query', 'pool', 'evidence'],
          });
        }
        return next;
      });
    }, 90);
  };

  const runSearch = async (nextQuery = query) => {
    const trimmedQuery = nextQuery.trim();
    if (!apiToken || !trimmedQuery) return;
    clearTimers();
    const runId = Date.now();
    searchRunRef.current = runId;
    setLoading(true);
    setItems([]);
    setVisibleCount(0);
    setProgress({
      percent: 24,
      message: '正在解析关键词并准备查询本地 V-KPI 表。',
      activeKey: 'query',
      doneKeys: [],
    });
    scheduleProgress(runId, 180, {
      percent: 46,
      message: '正在查询 KOL pool、Memory、推荐、竞品信号和告警。',
      activeKey: 'pool',
      doneKeys: ['query'],
    });
    scheduleProgress(runId, 520, {
      percent: 68,
      message: '正在合并头像、最近内容和证据摘要。',
      activeKey: 'evidence',
      doneKeys: ['query', 'pool'],
    });
    try {
      const response = await searchVkpi(apiToken, trimmedQuery, 20);
      const nextItems = response.items || [];
      setItems(nextItems);
      setMeta(response as Row);
      recordSearchHistory(trimmedQuery, response as Row);
      clearProgressTimers();
      revealResults(runId, nextItems.length);
    } catch (error) {
      clearTimers();
      setProgress({
        percent: 100,
        message: '搜索失败；未写入数据。',
        activeKey: 'pool',
        doneKeys: ['query'],
        errorKey: 'pool',
      });
      onMessage(error instanceof Error ? error.message : '自然搜索失败');
    } finally {
      setLoading(false);
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void runSearch();
  };

  const selectHistory = (item: NaturalSearchHistoryItem) => {
    setQuery(item.query);
    void runSearch(item.query);
  };

  const clearHistory = () => {
    setSearchHistory([]);
    saveSearchHistory([]);
  };

  const openDetail = async (item: Row) => {
    const kolPoolId = kolPoolIdForItem(item);
    if (!apiToken || !kolPoolId) return;
    setDetailSource(item);
    setDetailCard(null);
    setDetailError('');
    setActiveEvidenceSection('');
    setDecisionMessage('');
    setDecisionError('');
    setDetailLoadingId(kolPoolId);
    try {
      const card = await getKolPoolIntelligenceCard(apiToken, kolPoolId, true);
      const rows = evidenceRows(card);
      setDetailCard(card);
      setActiveEvidenceSection(firstText(rows[0]?.section));
    } catch (error) {
      setDetailError(error instanceof Error ? error.message : '详情加载失败');
    } finally {
      setDetailLoadingId('');
    }
  };

  const closeDetail = () => {
    setDetailSource(null);
    setDetailCard(null);
    setDetailError('');
    setActiveEvidenceSection('');
    setDecisionMessage('');
    setDecisionError('');
  };

  const submitDecision = async (decision: typeof DECISION_OPTIONS[number]) => {
    if (!apiToken || !detailSource) return;
    const kolPoolId = kolPoolIdForItem(detailSource);
    if (!kolPoolId) return;
    setDecisionSubmitting(decision.key);
    setDecisionMessage('');
    setDecisionError('');
    try {
      const result = await createKolDecisionAudit(apiToken, {
        kolPoolId,
        decisionKey: decision.key,
        decisionLabel: decision.label,
        severity: decision.severity,
        rationale: decision.detail,
        sourceTable: firstText(detailSource.source_table),
        sourceId: firstText(detailSource.source_id),
        query: firstText(meta.query, query),
        evidenceSections: detailEvidenceRows.map((row) => firstText(row.section)).filter(Boolean),
        evidenceSnapshot: {
          provider_calls: Boolean(detailCard?.provider_calls),
          llm_calls: Boolean(detailCard?.llm_calls),
          write_db: Boolean(detailCard?.write_db),
          evidence_sections: detailEvidenceRows.map((row) => firstText(row.section)).filter(Boolean),
        },
        metadata: {
          scope: 'kol_decision_label_v0',
          source: 'natural_search',
          title: compactText(detailSource.title),
          handle: detailSource.handle || asRecord(detailSource.evidence).handle,
          platform: detailSource.platform || asRecord(detailSource.evidence).platform,
        },
      });
      const uid = firstText(result.decision?.decision_uid);
      setDecisionMessage(uid ? `已记录决策: ${uid}` : '已记录决策。');
    } catch (error) {
      setDecisionError(error instanceof Error ? error.message : '决策记录失败');
    } finally {
      setDecisionSubmitting('');
    }
  };

  useEffect(() => () => clearTimers(), []);

  const visibleItems = useMemo(() => {
    if (!items.length) return [];
    if (visibleCount <= 0) return items;
    return items.slice(0, Math.min(items.length, visibleCount));
  }, [items, visibleCount]);

  const recentContentCards = useMemo(() => {
    const seen = new Set<string>();
    const cards: RecentContentCard[] = [];
    for (const item of visibleItems) {
      const evidence = asRecord(item.evidence);
      const platform = firstText(item.platform, evidence.platform);
      const handle = firstText(item.handle, evidence.handle);
      const sourceTitle = compactText(item.title, handle || platform || 'KOL');
      for (const post of recentPostsForItem(item, evidence)) {
        const title = compactText(post.title || post.post_url || post.url, 'Untitled post');
        const url = firstText(post.post_url, post.url);
        const key = url || `${sourceTitle}:${title}`;
        if (!key || seen.has(key)) continue;
        seen.add(key);
        cards.push({
          id: key,
          title,
          url,
          platform,
          handle,
          sourceTitle,
          publishedAt: formatPostDate(post.published_at || post.publishedAt || post.date),
          views: formatCount(post.views),
          likes: formatCount(post.likes),
          comments: formatCount(post.comments),
        });
        if (cards.length >= RECENT_CONTENT_CARD_LIMIT) return cards;
      }
    }
    return cards;
  }, [visibleItems]);

  const detailEvidenceRows = useMemo(() => evidenceRows(detailCard), [detailCard]);
  const activeEvidenceRow = useMemo(() => {
    if (!activeEvidenceSection) return detailEvidenceRows[0] || {};
    return detailEvidenceRows.find((row) => firstText(row.section) === activeEvidenceSection) || detailEvidenceRows[0] || {};
  }, [activeEvidenceSection, detailEvidenceRows]);
  const activeEvidencePayload = useMemo(() => cardSectionPayload(detailCard, activeEvidenceRow.section), [activeEvidenceRow.section, detailCard]);
  const activeEvidenceItems = useMemo(() => evidencePayloadItems(activeEvidencePayload), [activeEvidencePayload]);

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

      {searchHistory.length ? (
        <div className="da-natural-search__history" aria-label="自然搜索历史">
          <div className="da-natural-search__history-head">
            <strong>搜索历史</strong>
            <button type="button" onClick={clearHistory}>清空</button>
          </div>
          <div className="da-natural-search__history-items">
            {searchHistory.map((item) => (
              <button key={item.id} type="button" onClick={() => selectHistory(item)} title={item.query}>
                <b>{item.query}</b>
                <span>{item.resultCount} 条 · {item.status} · {historyAge(item.searchedAt)}</span>
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {searchProgress.visible ? (
        <div className="da-natural-search__progress" aria-label="自然搜索进度">
          <div className="da-natural-search__progress-head">
            <strong>{searchProgress.message}</strong>
            <span>{searchProgress.percent}%</span>
          </div>
          <div className="da-natural-search__progress-bar">
            <span style={{ width: `${searchProgress.percent}%` }} />
          </div>
          <div className="da-natural-search__steps">
            {SEARCH_PROGRESS_STEPS.map((step) => {
              const status = searchProgress.errorKey === step.key
                ? 'error'
                : searchProgress.doneKeys.includes(step.key)
                  ? 'done'
                  : searchProgress.activeKey === step.key
                    ? 'active'
                    : 'pending';
              return <span key={step.key} className={`is-${status}`}>{step.label}</span>;
            })}
          </div>
        </div>
      ) : null}

      <div className="da-natural-search__meta">
        <span>total <strong>{String(meta.total ?? items.length)}</strong></span>
        {items.length ? <span>visible <strong>{String(visibleItems.length)}/{String(items.length)}</strong></span> : null}
        <span>provider <strong>{String(Boolean(meta.provider_calls))}</strong></span>
        <span>write <strong>{String(Boolean(meta.write_db))}</strong></span>
        <span>tokens <strong>{Array.isArray(meta.tokens) ? meta.tokens.join(', ') : '-'}</strong></span>
      </div>

      {recentContentCards.length ? (
        <div className="da-natural-search__recent" aria-label="搜索结果最近内容">
          <div className="da-natural-search__recent-head">
            <strong>最近内容</strong>
            <span>来自已有缓存，不触发刷新</span>
          </div>
          <div className="da-natural-search__recent-grid">
            {recentContentCards.map((post) => {
              const metricItems = [
                post.views ? `${post.views} views` : '',
                post.likes ? `${post.likes} likes` : '',
                post.comments ? `${post.comments} comments` : '',
              ].filter(Boolean);
              const body = (
                <>
                  <b>{post.title}</b>
                  <span>{[post.sourceTitle, post.handle ? `@${post.handle.replace(/^@/, '')}` : '', post.platform].filter(Boolean).join(' · ')}</span>
                  <em>{[post.publishedAt, ...metricItems].filter(Boolean).join(' · ') || 'cached post'}</em>
                </>
              );
              return post.url ? (
                <a key={post.id} href={post.url} target="_blank" rel="noreferrer">
                  {body}
                </a>
              ) : (
                <span key={post.id}>
                  {body}
                </span>
              );
            })}
          </div>
        </div>
      ) : items.length && !loading ? (
        <div className="da-natural-search__recent-empty">当前搜索结果没有缓存最近内容；未生成占位内容。</div>
      ) : null}

      {detailSource ? (
        <div className="da-natural-search__detail" aria-label="自然搜索详情证据">
          <div className="da-natural-search__detail-head">
            <div>
              <strong>{compactText(detailSource.title)}</strong>
              <span>
                {compactText(detailSource.source_table)}:{compactText(detailSource.source_id)}
                {detailCard ? ` · provider=${String(Boolean(detailCard.provider_calls))} · write=${String(Boolean(detailCard.write_db))}` : ''}
              </span>
            </div>
            <button type="button" onClick={closeDetail}>关闭</button>
          </div>
          {detailLoadingId ? <div className="da-natural-search__detail-state">正在读取 intelligence card...</div> : null}
          {detailError ? <div className="da-natural-search__detail-error">{detailError}</div> : null}
          {detailCard ? (
            <>
              <div className="da-natural-search__detail-grid">
                {[
                  ['11D', asRecord(detailCard.dimensions11).overall_score, asRecord(detailCard.dimensions11).status],
                  ['Competitor', asRecord(asRecord(detailCard.competitors).summary).risk_tier || asRecord(detailCard.competitors).status, asRecord(detailCard.competitors).status],
                  ['Brand', asRecord(detailCard.brand_signal).signal_count, asRecord(detailCard.brand_signal).status],
                  ['Memory', asRecord(detailCard.memory_card).status, asRecord(detailCard.memory_card).source_type],
                  ['Product Fit', asRecord(detailCard.product_fit).count, asRecord(detailCard.product_fit).status],
                  ['Evidence', detailEvidenceRows.length, 'sections'],
                ].map(([label, value, detail]) => (
                  <div key={String(label)}>
                    <strong>{String(label)}</strong>
                    <span>{compactText(value)}</span>
                    <small>{compactText(detail)}</small>
                  </div>
                ))}
              </div>
              <div className="da-natural-search__decision">
                <div>
                  <strong>决策标签</strong>
                  <span>写入专用 decision audit 表。</span>
                </div>
                <div className="da-natural-search__decision-actions">
                  {DECISION_OPTIONS.map((option) => (
                    <button
                      key={option.key}
                      type="button"
                      onClick={() => void submitDecision(option)}
                      disabled={Boolean(decisionSubmitting)}
                    >
                      {decisionSubmitting === option.key ? '记录中' : option.label}
                    </button>
                  ))}
                </div>
                {decisionMessage ? <p className="is-ok">{decisionMessage}</p> : null}
                {decisionError ? <p className="is-error">{decisionError}</p> : null}
              </div>
              {detailEvidenceRows.length ? (
                <div className="da-natural-search__evidence-tabs">
                  {detailEvidenceRows.map((row) => {
                    const section = firstText(row.section);
                    return (
                      <button
                        className={section === firstText(activeEvidenceRow.section) ? 'is-active' : ''}
                        key={section}
                        type="button"
                        onClick={() => setActiveEvidenceSection(section)}
                      >
                        <b>{compactText(row.label || evidenceSectionLabel(section))}</b>
                        <span>{compactText(row.status)} · {String(numberValue(row.evidence_count))}</span>
                      </button>
                    );
                  })}
                </div>
              ) : null}
              {firstText(activeEvidenceRow.section) ? (
                <div className="da-natural-search__evidence-panel">
                  <header>
                    <strong>{compactText(activeEvidenceRow.label || evidenceSectionLabel(activeEvidenceRow.section))}</strong>
                    <span>
                      {compactText(activeEvidenceRow.status)}
                      {' · count '}
                      {String(numberValue(activeEvidenceRow.evidence_count))}
                      {' · confidence '}
                      {compactText(activeEvidenceRow.confidence)}
                    </span>
                  </header>
                  <p>
                    {compactText(activeEvidencePayload.source || activeEvidencePayload.reason || activeEvidencePayload.method || activeEvidenceRow.source || 'stored evidence')}
                  </p>
                  {activeEvidenceItems.length ? (
                    <div className="da-natural-search__evidence-items">
                      {activeEvidenceItems.slice(0, 6).map((row, index) => {
                        const title = compactText(row.title || row.sku || row.brand || row.competitor_brand || row.project || row.product || row.signal_type || row.id, `Evidence ${index + 1}`);
                        const detail = compactText(row.reasoning || row.reason || row.detail || row.text || row.post_title || row.match_reason || row.content_link || row.source || row.status, 'stored row');
                        const url = firstText(row.source_url, row.post_url, row.url, row.content_link);
                        return url ? (
                          <a key={`${url}-${index}`} href={url} target="_blank" rel="noreferrer">
                            <b>{title}</b>
                            <span>{detail}</span>
                          </a>
                        ) : (
                          <span key={`${title}-${index}`}>
                            <b>{title}</b>
                            <span>{detail}</span>
                          </span>
                        );
                      })}
                    </div>
                  ) : (
                    <div className="da-natural-search__detail-state">这个 evidence section 没有可展开样本行。</div>
                  )}
                </div>
              ) : null}
            </>
          ) : null}
        </div>
      ) : null}

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
            {visibleItems.length ? visibleItems.map((item, index) => {
              const evidence = asRecord(item.evidence);
              const avatarUrl = firstText(item.avatar_url, evidence.avatar_url);
              const recentPosts = recentPostsForItem(item, evidence);
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
                  <td>
                    <div className="da-natural-search__source-cell">
                      <span>{compactText(item.source_table)}:{compactText(item.source_id)}</span>
                      {kolPoolIdForItem(item) ? (
                        <button type="button" onClick={() => void openDetail(item)} disabled={Boolean(detailLoadingId)}>
                          {detailLoadingId === kolPoolIdForItem(item) ? 'Loading' : 'Evidence'}
                        </button>
                      ) : null}
                    </div>
                  </td>
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
