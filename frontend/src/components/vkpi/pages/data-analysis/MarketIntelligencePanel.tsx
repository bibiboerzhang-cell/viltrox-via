import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { getMarketIntelligenceV0 } from '../../../../services/vkpi.ui-api';
import type { Row } from './utils/types';

interface MarketIntelligencePanelProps {
  apiToken?: string;
  onMessage: (message: string) => void;
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

function numberText(value: unknown): string {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? String(parsed) : '0';
}

export function MarketIntelligencePanel({ apiToken, onMessage }: MarketIntelligencePanelProps) {
  const [payload, setPayload] = useState<Row>({});
  const [loading, setLoading] = useState(false);

  const summary = useMemo(() => asRecord(payload.summary), [payload]);
  const hotBrands = useMemo(() => asList(payload.hot_brands).slice(0, 6), [payload]);
  const launchCandidates = useMemo(() => asList(payload.launch_candidates).slice(0, 5), [payload]);
  const commentOpportunities = useMemo(() => asList(payload.comment_opportunities).slice(0, 5), [payload]);
  const policy = useMemo(() => asRecord(payload.policy), [payload]);

  const load = async () => {
    if (!apiToken) return;
    setLoading(true);
    try {
      const response = await getMarketIntelligenceV0(apiToken, 120);
      setPayload(response);
    } catch (error) {
      onMessage(error instanceof Error ? error.message : 'Market Intelligence 读取失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [apiToken]);

  return (
    <section className="da-market-intel">
      <header className="da-market-intel__header">
        <div>
          <span className="da-kicker da-kicker--light">P5.69 Market Intelligence</span>
          <h2>市场情报 v0</h2>
          <p>从现有竞品信号表聚合新品、热点和评论机会。不抓取外部源,不调用 Apify/LLM。</p>
        </div>
        <button className="da-black-button" type="button" disabled={!apiToken || loading} onClick={() => void load()}>
          {loading ? '读取中...' : '刷新'}
        </button>
      </header>

      <div className="da-market-intel__stats">
        <Metric label="Signals" value={numberText(summary.signals_loaded)} />
        <Metric label="Launch" value={numberText(summary.launch_candidates)} />
        <Metric label="Comments" value={numberText(summary.comment_opportunities)} />
        <Metric label="High priority" value={numberText(summary.high_priority)} />
      </div>

      <div className="da-market-intel__grid">
        <IntelList
          title="Hot Brands"
          items={hotBrands}
          empty={loading ? '正在读取热点品牌...' : '暂无热点品牌。'}
          render={(item) => (
            <>
              <strong>{compactText(item.brand)}</strong>
              <span>count {numberText(item.count)} · score {numberText(item.score)}</span>
            </>
          )}
        />
        <IntelList
          title="Launch Candidates"
          items={launchCandidates}
          empty={loading ? '正在读取新品信号...' : '暂无新品候选。'}
          render={(item) => (
            <>
              <strong>{compactText(item.brand)} · {compactText(item.signal_type)}</strong>
              <span>{compactText(item.detail).slice(0, 120)}</span>
            </>
          )}
        />
        <IntelList
          title="Comment Opportunities"
          items={commentOpportunities}
          empty={loading ? '正在读取评论机会...' : '暂无评论机会。'}
          render={(item) => (
            <>
              <strong>{compactText(item.brand)} · {compactText(item.signal_type)}</strong>
              <span>{compactText(item.detail).slice(0, 120)}</span>
            </>
          )}
        />
      </div>

      <div className="da-market-intel__policy">
        <span>{policy.no_external_fetch ? 'External fetch blocked' : 'External fetch status unknown'}</span>
        <span>{policy.no_unreviewed_auto_recommendation ? 'No auto recommendation' : 'Recommendation gate unknown'}</span>
        <span>{compactText(summary.source, 'vkpi_competitor_signals')}</span>
      </div>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="da-market-intel__metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function IntelList({
  title,
  items,
  empty,
  render,
}: {
  title: string;
  items: Row[];
  empty: string;
  render: (item: Row) => ReactNode;
}) {
  return (
    <div className="da-market-intel__list">
      <strong>{title}</strong>
      {items.length ? items.map((item, index) => (
        <div className="da-market-intel__item" key={String(item.id || item.brand || index)}>
          {render(item)}
        </div>
      )) : (
        <div className="da-market-intel__empty">{empty}</div>
      )}
    </div>
  );
}
