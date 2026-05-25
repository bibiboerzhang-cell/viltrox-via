import { useEffect, useMemo, useState } from 'react';
import type { FormEvent } from 'react';
import { getCompetitorBrainStatus, listCompetitorBrainSignals, reviewCompetitorBrainSignal } from '../../../../services/vkpi/market-api';
import { platformDisplay } from './utils/platformHelpers';
import type { Row } from './utils/types';

interface CompetitorBrainPanelProps {
  apiToken?: string;
  onMessage: (message: string) => void;
}

function numberValue(value: unknown): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function asRecord(value: unknown): Row {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {};
}

function asList(value: unknown): Row[] {
  return Array.isArray(value) ? value as Row[] : [];
}

function compactText(value: unknown, fallback = '-'): string {
  const text = String(value ?? '').trim();
  return text || fallback;
}

function topEntries(value: unknown, limit = 6): Array<[string, number]> {
  return Object.entries(asRecord(value))
    .map(([key, count]) => [key, numberValue(count)] as [string, number])
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, limit);
}

function productText(signal: Row): string {
  const values = asList(signal.product_hints).map((item) => compactText(item, '')).filter(Boolean);
  return values.length ? values.slice(0, 4).join(', ') : '-';
}

function evidenceText(signal: Row): string {
  const evidence = asRecord(signal.evidence);
  return compactText(evidence.detail || signal.detail).slice(0, 180);
}

function reviewLabel(action: string): string {
  if (action === 'ready') return '标记 ready';
  if (action === 'rejected') return '拒绝';
  if (action === 'ignored') return '忽略';
  if (action === 'pending_review') return '退回待审';
  return action;
}

export function CompetitorBrainPanel({ apiToken, onMessage }: CompetitorBrainPanelProps) {
  const [status, setStatus] = useState<Row>({});
  const [signals, setSignals] = useState<Row[]>([]);
  const [reviewStatus, setReviewStatus] = useState('pending_review');
  const [brand, setBrand] = useState('');
  const [signalType, setSignalType] = useState('');
  const [loading, setLoading] = useState(false);
  const [busySignalId, setBusySignalId] = useState('');

  const brandEntries = useMemo(() => topEntries(status.brand_distribution), [status]);
  const typeEntries = useMemo(() => topEntries(status.signal_type_distribution), [status]);
  const reviewEntries = useMemo(() => topEntries(status.review_status_counts), [status]);

  const load = async () => {
    if (!apiToken) return;
    setLoading(true);
    try {
      const [statusResponse, signalResponse] = await Promise.all([
        getCompetitorBrainStatus(apiToken),
        listCompetitorBrainSignals(apiToken, {
          reviewStatus: reviewStatus || undefined,
          brand: brand || undefined,
          signalType: signalType || undefined,
          limit: 80,
        }),
      ]);
      setStatus(statusResponse);
      setSignals(signalResponse.signals || []);
    } catch (error) {
      onMessage(error instanceof Error ? error.message : 'Competitor Brain 读取失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [apiToken, reviewStatus, signalType]);

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    void load();
  };

  const reviewSignal = async (signal: Row, action: 'ready' | 'rejected' | 'ignored' | 'pending_review') => {
    const signalId = compactText(signal.id, '');
    if (!apiToken || !signalId) return;
    const brandLabel = compactText(signal.normalized_brand || signal.brand);
    const message = `${reviewLabel(action)}: ${brandLabel} / ${compactText(signal.signal_type)}`;
    if (typeof window !== 'undefined' && !window.confirm(`${message}\n\n该操作只会更新 review_status，不会写 canonical 竞品产品表。确认继续？`)) return;
    setBusySignalId(signalId);
    try {
      await reviewCompetitorBrainSignal(apiToken, signalId, {
        action,
        note: `frontend:${action}`,
      });
      onMessage(`${message} 已完成`);
      await load();
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '竞品信号处理失败');
    } finally {
      setBusySignalId('');
    }
  };

  return (
    <section className="da-competitor-brain">
      <header className="da-competitor-brain__header">
        <div>
          <span className="da-kicker da-kicker--light">P8 Competitor Brain</span>
          <h2>竞品脑信号</h2>
          <p>已提交的竞品观察信号、VOC 证据和内容脑品牌提及。人工处理只更新 review_status，不写 canonical 竞品产品表。</p>
        </div>
        <button className="da-black-button" type="button" disabled={!apiToken || loading} onClick={() => void load()}>
          {loading ? '读取中...' : '刷新'}
        </button>
      </header>

      <div className="da-competitor-brain__stats">
        <Metric label="Runs" value={String(status.run_count || 0)} />
        <Metric label="Signals" value={String(status.signal_count || 0)} />
        <Metric label="Pending" value={String(asRecord(status.review_status_counts).pending_review || 0)} />
        <Metric label="Latest" value={compactText(asRecord(status.latest_run).run_uid)} />
      </div>

      <div className="da-competitor-brain__distributions">
        <Distribution title="Brands" entries={brandEntries} />
        <Distribution title="Signal Types" entries={typeEntries} />
        <Distribution title="Review" entries={reviewEntries} />
      </div>

      <form className="da-competitor-brain__filters" onSubmit={submitSearch}>
        <select value={reviewStatus} onChange={(event) => setReviewStatus(event.target.value)}>
          <option value="">全部状态</option>
          <option value="pending_review">pending_review</option>
          <option value="ready">ready</option>
          <option value="rejected">rejected</option>
          <option value="ignored">ignored</option>
        </select>
        <select value={signalType} onChange={(event) => setSignalType(event.target.value)}>
          <option value="">全部类型</option>
          <option value="competitor_mention">competitor_mention</option>
          <option value="competitor_focus">competitor_focus</option>
          <option value="pricing_sensitive">pricing_sensitive</option>
          <option value="voc_issue">voc_issue</option>
          <option value="risk_watch">risk_watch</option>
        </select>
        <input value={brand} onChange={(event) => setBrand(event.target.value)} placeholder="brand" />
        <button className="da-white-button" type="submit" disabled={!apiToken || loading}>搜索</button>
      </form>

      <div className="da-table-wrap">
        <table className="da-table">
          <thead>
            <tr>
              <th>Brand</th>
              <th>Type</th>
              <th>Score</th>
              <th>平台</th>
              <th>Products</th>
              <th>Source</th>
              <th>Evidence</th>
              <th>Review</th>
            </tr>
          </thead>
          <tbody>
            {signals.length ? signals.map((signal) => {
              const currentStatus = compactText(signal.review_status, 'pending_review');
              const signalId = compactText(signal.id, '');
              const busy = busySignalId === signalId;
              const pending = currentStatus === 'pending_review';
              return (
                <tr key={String(signal.id)}>
                  <td>{compactText(signal.normalized_brand || signal.brand)}</td>
                  <td>{compactText(signal.signal_type)}</td>
                  <td>{String(signal.score ?? 0)}</td>
                  <td>{platformDisplay(signal.platform)}</td>
                  <td>{productText(signal)}</td>
                  <td>
                    {compactText(signal.source_table)}
                    {signal.source_id ? `:${String(signal.source_id)}` : ''}
                    {signal.source_row ? ` / row ${String(signal.source_row)}` : ''}
                  </td>
                  <td>{evidenceText(signal)}</td>
                  <td>
                    <div className="vkpi-chip-list">
                      <span className="vkpi-chip">{currentStatus}</span>
                      {pending ? (
                        <>
                          <button className="vkpi-mini-button" type="button" disabled={!apiToken || loading || busy} onClick={() => void reviewSignal(signal, 'ready')}>{busy ? '处理中' : 'ready'}</button>
                          <button className="vkpi-mini-button" type="button" disabled={!apiToken || loading || busy} onClick={() => void reviewSignal(signal, 'rejected')}>reject</button>
                          <button className="vkpi-mini-button" type="button" disabled={!apiToken || loading || busy} onClick={() => void reviewSignal(signal, 'ignored')}>ignore</button>
                        </>
                      ) : (
                        <button className="vkpi-mini-button" type="button" disabled={!apiToken || loading || busy} onClick={() => void reviewSignal(signal, 'pending_review')}>{busy ? '处理中' : '退回'}</button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            }) : (
              <tr>
                <td className="da-table-empty" colSpan={8}>
                  {loading ? '正在读取竞品脑信号...' : '暂无符合条件的竞品脑信号。'}
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
    <div className="da-competitor-brain__metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Distribution({ title, entries }: { title: string; entries: Array<[string, number]> }) {
  return (
    <div className="da-competitor-brain__distribution">
      <strong>{title}</strong>
      {entries.length ? entries.map(([key, count]) => (
        <span key={key}>{key}<em>{count}</em></span>
      )) : <span>none<em>0</em></span>}
    </div>
  );
}
