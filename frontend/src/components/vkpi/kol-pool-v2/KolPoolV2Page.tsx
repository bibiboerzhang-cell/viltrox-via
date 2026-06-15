import { useEffect, useMemo, useState } from 'react';
import {
  getKolPoolEvidenceSummary,
  getKolPoolIntelligenceCard,
  getKolPoolItem,
  getKolPoolSummary,
  listKolPool,
  promoteKolPoolToMain,
  type VkpiKolPoolEvidenceSummary,
  type VkpiKolPoolIntelligenceCard,
} from '../../../domains/kol';
import { rows, type Row } from '../../../domains/dashboard';
import { Avatar } from '../shared/Avatar';
import {
  CANDIDATE_CLASS_INFO,
  dataGaps,
  deriveCandidateClass,
  deriveFreshness,
  deriveMainClass,
  formatCount,
  formatPercent,
  formatScore,
  kolDisplayName,
  numberValue,
  summarizeKolPool,
  textValue,
  type CandidateClass,
  type KolPoolV2Item,
} from './kolPoolV2Model';
import { V2ShellTopbar } from '../v2-shell/V2ShellTopbar';
import type { VkpiPageKey } from '../vkpiTypes';
import './kol-pool-v2.css';

interface KolPoolV2PageProps {
  apiToken?: string;
  userName?: string;
  userRole?: string;
  userAvatar?: string;
  onSelectPage?: (page: VkpiPageKey) => void;
  onSignOut?: () => Promise<void> | void;
}

function metricValue(ready: boolean, value: string, empty = '无信号'): string {
  if (!ready) return '--';
  return value === '0' ? empty : value;
}

function compactMetric(ready: boolean, hasSignal: boolean, value: string): string {
  if (!ready) return '--';
  return hasSignal ? metricValue(true, value) : '--';
}

function daysSince(value?: string | null): number | null {
  if (!value) return null;
  const time = new Date(value).getTime();
  if (!Number.isFinite(time)) return null;
  return (Date.now() - time) / 86_400_000;
}

function formatSignedPercent(value: unknown): string {
  const next = numberValue(value);
  if (next === null) return '待接入';
  return `${next > 0 ? '+' : ''}${next.toFixed(1)}%`;
}

function trendTone(item: KolPoolV2Item): 'green' | 'amber' | 'slate' {
  const score = numberValue(item.trend_score);
  if (score === null) return item.trend_topic ? 'amber' : 'slate';
  if (score >= 70) return 'green';
  if (score >= 40) return 'amber';
  return 'slate';
}

function summaryRows(summary: Row): Row[] {
  const candidates = [
    rows(summary.coverage_countries),
    rows(summary.country_distribution),
    rows(summary.countries),
    rows(summary.by_country),
  ];
  return candidates.find((items) => items.length) || [];
}

function evidenceRows(evidence?: VkpiKolPoolEvidenceSummary | null): Row[] {
  if (!evidence) return [];
  const rawEvidence = evidence as unknown as Row;
  return rows(evidence.summaries).length ? rows(evidence.summaries) : rows(rawEvidence.evidence_backlinks);
}

function intelligenceRows(card?: VkpiKolPoolIntelligenceCard | null): Row[] {
  if (!card) return [];
  return rows(card.evidence_index);
}

function CandidateChip({ item }: { item: KolPoolV2Item }) {
  const classKey = deriveCandidateClass(item);
  const info = CANDIDATE_CLASS_INFO[classKey];
  return <span className={`kol-pool-v2-chip is-${info.tone}`}>{info.short}</span>;
}

function PlatformPill({ value }: { value: unknown }) {
  return <span className="kol-pool-v2-platform">{textValue(value, '未知平台')}</span>;
}

function FitCell({ item }: { item: KolPoolV2Item }) {
  const score = numberValue(item.viltrox_fit_score);
  const width = score === null ? 0 : Math.max(4, Math.min(100, score));
  return (
    <div className="kol-pool-v2-fit">
      <span><i style={{ width: `${width}%` }} /></span>
      <b>{formatScore(item.viltrox_fit_score)}</b>
      <em>{formatSignedPercent(item.fit_score_delta_7d)}</em>
    </div>
  );
}

function KolPoolTrendPulse() {
  return (
    <section className="kol-pool-v2-pulse">
      <div>
        <span>Trend Pulse</span>
        <strong>待接入 trend endpoint</strong>
      </div>
      <i /><i /><i /><i />
    </section>
  );
}

function KolPoolDetailDrawer({
  item,
  loading,
  evidence,
  intelligenceCard,
  promoting,
  onClose,
  onPromote,
}: {
  item: KolPoolV2Item;
  loading: boolean;
  evidence?: VkpiKolPoolEvidenceSummary | null;
  intelligenceCard?: VkpiKolPoolIntelligenceCard | null;
  promoting: boolean;
  onClose: () => void;
  onPromote: () => void;
}) {
  const gaps = dataGaps(item);
  const evidenceList = evidenceRows(evidence);
  const cardEvidence = intelligenceRows(intelligenceCard);
  const summaryEvidence = evidenceList.length ? evidenceList : cardEvidence;
  return (
    <aside className="kol-pool-v2-drawer" aria-label="KOL Pool 详情">
      <header>
        <div>
          <span>KOL Pool Detail</span>
          <h2>{kolDisplayName(item)}</h2>
          <p>{textValue(item.platform, '未知平台')} · {textValue(item.country, '地区待接入')}</p>
        </div>
        <button type="button" onClick={onClose} aria-label="关闭">×</button>
      </header>

      <section className="kol-pool-v2-detail-hero">
        <Avatar name={kolDisplayName(item)} src={item.avatar_url} size="lg" />
        <div>
          <CandidateChip item={item} />
          <strong>{item.handle || item.pool_uid || `#${item.id}`}</strong>
          <span>{item.profile_url ? '主页已记录' : '主页待接入'} · {item.sync_status || 'sync status 待接入'}</span>
        </div>
      </section>

      <section className="kol-pool-v2-detail-grid">
        <div><span>V6 Fit</span><b>{formatScore(item.viltrox_fit_score)}</b></div>
        <div><span>粉丝</span><b>{formatCount(item.followers)}</b></div>
        <div><span>平均播放</span><b>{formatCount(item.avg_views)}</b></div>
        <div><span>Real ER</span><b>{formatPercent(item.real_engagement_rate ?? item.engagement_rate)}</b></div>
        <div><span>Validation</span><b>待评估</b></div>
        <div><span>设备</span><b>{textValue(item.device_primary, '待接入')}</b></div>
      </section>

      <section className="kol-pool-v2-section">
        <h3>下一步</h3>
        {gaps.length ? <p>数据缺口：{gaps.join(' / ')}。当前不做假评分。</p> : <p>核心字段已可读，可进入人工复核。</p>}
        {deriveMainClass(item) === 'legacy' ? (
          <button type="button" onClick={onPromote} disabled={promoting}>
            {promoting ? '处理中...' : '转入主库'}
          </button>
        ) : <span className="kol-pool-v2-chip is-green">已链接主库 #{item.linked_main_kol_id}</span>}
      </section>

      <section className="kol-pool-v2-section">
        <h3>证据</h3>
        {loading ? <p>读取证据中...</p> : summaryEvidence.length ? summaryEvidence.slice(0, 8).map((row, index) => (
          <article key={`${textValue(row.source || row.label, 'evidence')}-${index}`}>
            <b>{textValue(row.title || row.label || row.source, `证据 ${index + 1}`)}</b>
            <span>{textValue(row.source || row.ref || row.kind, 'kol-pool evidence')}</span>
            <p>{textValue(row.summary || row.value || row.reason || row.text, '证据行已返回，摘要字段待统一')}</p>
          </article>
        )) : <p>暂无证据行</p>}
      </section>
    </aside>
  );
}

export function KolPoolV2Page({
  apiToken,
  userName = 'Viltrox 成员',
  userRole = '营销运营',
  userAvatar,
  onSelectPage,
  onSignOut,
}: KolPoolV2PageProps) {
  const [items, setItems] = useState<KolPoolV2Item[]>([]);
  const [summary, setSummary] = useState<Row>({});
  const [search, setSearch] = useState('');
  const [platform, setPlatform] = useState('');
  const [country, setCountry] = useState('');
  const [classFilter, setClassFilter] = useState<CandidateClass | ''>('');
  const [searchMode, setSearchMode] = useState<'balanced' | 'precision' | 'discovery'>('balanced');
  const [sortBy, setSortBy] = useState('fit');
  const [selectedItem, setSelectedItem] = useState<KolPoolV2Item | null>(null);
  const [evidence, setEvidence] = useState<VkpiKolPoolEvidenceSummary | null>(null);
  const [intelligenceCard, setIntelligenceCard] = useState<VkpiKolPoolIntelligenceCard | null>(null);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [promotingId, setPromotingId] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [loaded, setLoaded] = useState(false);

  async function loadPool() {
    if (!apiToken) {
      setLoaded(false);
      setItems([]);
      return;
    }
    setLoading(true);
    setError('');
    try {
      const [listResult, summaryResult] = await Promise.allSettled([
        listKolPool(apiToken, { search, platform, limit: 150, sortBy, refreshIfStale: false }),
        getKolPoolSummary(apiToken),
      ]);
      if (listResult.status === 'fulfilled') setItems((listResult.value.items || []) as KolPoolV2Item[]);
      else throw listResult.reason;
      if (summaryResult.status === 'fulfilled') setSummary(summaryResult.value);
      setLoaded(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'KOL Pool 加载失败');
      setLoaded(false);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadPool();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiToken]);

  async function openDetail(item: KolPoolV2Item) {
    if (!apiToken) return;
    setSelectedItem(item);
    setEvidence(null);
    setIntelligenceCard(null);
    setDetailLoading(true);
    try {
      const [detailResult, evidenceResult, cardResult] = await Promise.allSettled([
        getKolPoolItem(apiToken, item.id, false),
        getKolPoolEvidenceSummary(apiToken, item.id),
        getKolPoolIntelligenceCard(apiToken, item.id),
      ]);
      if (detailResult.status === 'fulfilled') {
        const detail = detailResult.value;
        setSelectedItem({ ...(detail.item as KolPoolV2Item), freshness: detail.freshness || detail.refresh?.freshness, refresh: detail.refresh });
      }
      if (evidenceResult.status === 'fulfilled') setEvidence(evidenceResult.value);
      if (cardResult.status === 'fulfilled') setIntelligenceCard(cardResult.value);
    } finally {
      setDetailLoading(false);
    }
  }

  async function promoteSelected() {
    if (!apiToken || !selectedItem) return;
    setPromotingId(selectedItem.id);
    setError('');
    try {
      const result = await promoteKolPoolToMain(apiToken, selectedItem.id);
      if (result.item) setSelectedItem({ ...selectedItem, ...(result.item as KolPoolV2Item) });
      await loadPool();
    } catch (err) {
      setError(err instanceof Error ? err.message : '转入主库失败');
    } finally {
      setPromotingId(null);
    }
  }

  const filteredItems = useMemo(() => {
    const q = search.trim().toLowerCase();
    const ranked = items.filter((item) => {
      if (platform && String(item.platform).toLowerCase() !== platform) return false;
      if (country && String(item.country || '').toLowerCase() !== country) return false;
      if (classFilter && deriveCandidateClass(item) !== classFilter) return false;
      if (!q) return true;
      return [item.handle, item.display_name, item.bio, item.country, item.source_type].join(' ').toLowerCase().includes(q);
    });
    return ranked.sort((a, b) => {
      if (sortBy === 'followers') return (numberValue(b.followers) ?? -1) - (numberValue(a.followers) ?? -1);
      if (sortBy === 'views') return (numberValue(b.avg_views) ?? -1) - (numberValue(a.avg_views) ?? -1);
      if (sortBy === 'er') return (numberValue(b.real_engagement_rate ?? b.engagement_rate) ?? -1) - (numberValue(a.real_engagement_rate ?? a.engagement_rate) ?? -1);
      return (numberValue(b.viltrox_fit_score) ?? -1) - (numberValue(a.viltrox_fit_score) ?? -1);
    });
  }, [classFilter, country, items, platform, search, sortBy]);

  const poolSummary = useMemo(() => summarizeKolPool(items), [items]);
  const countries = useMemo(() => summaryRows(summary).slice(0, 5), [summary]);
  const platforms = Array.from(new Set(items.map((item) => String(item.platform || '').toLowerCase()).filter(Boolean))).sort();
  const countryOptions = Array.from(new Set(items.map((item) => String(item.country || '').toLowerCase()).filter(Boolean))).sort();
  const classCounts = useMemo(() => (Object.keys(CANDIDATE_CLASS_INFO) as CandidateClass[]).reduce((acc, key) => {
    acc[key] = items.filter((item) => deriveCandidateClass(item) === key).length;
    return acc;
  }, {} as Record<CandidateClass, number>), [items]);
  const hasCreatedAt = items.some((item) => daysSince(item.created_at) !== null);
  const new7dCount = items.filter((item) => {
    const age = daysSince(item.created_at);
    return age !== null && age <= 7;
  }).length;
  const hasTrendSignal = items.some((item) => numberValue(item.trend_score) !== null || Boolean(item.trend_topic));
  const highTrendCount = items.filter((item) => {
    const score = numberValue(item.trend_score);
    return score !== null ? score >= 70 : Boolean(item.trend_topic);
  }).length;

  return (
    <div className="kol-pool-v2">
      <V2ShellTopbar
        apiToken={apiToken}
        pageTitle="KOL Pool"
        subtitle="Qualified / Legacy / 数据新鲜度 / 证据"
        userName={userName}
        userRole={userRole}
        userAvatar={userAvatar}
        onSelectPage={onSelectPage}
        onSignOut={onSignOut}
      />

      <header className="kol-pool-v2-header">
        <div>
          <span>KOL Pool</span>
          <h1>KOL 资产池</h1>
          <p>Qualified / Legacy / 数据新鲜度 / 证据</p>
        </div>
        <div className="kol-pool-v2-status">
          <b>{loaded ? '真实 API' : '待登录'}</b>
          <span>{loading ? '读取中' : loaded ? '只读列表已加载' : '无信号'}</span>
        </div>
      </header>

      <section className="kol-pool-v2-kpis">
        <button type="button" onClick={() => setClassFilter('')}><span>Pool 总数</span><b>{metricValue(loaded, formatCount(poolSummary.total))}</b><em>kol-pool list</em></button>
        <button type="button"><span>新发现</span><b>{compactMetric(loaded, hasCreatedAt, formatCount(new7dCount))}</b><em>{hasCreatedAt ? 'created_at · 7d' : '待接入 created_at'}</em></button>
        <button type="button" onClick={() => setClassFilter('legacy-stale')}><span>待补全</span><b>{metricValue(loaded, formatCount(poolSummary.missing))}</b><em>不假造完整度</em></button>
        <button type="button"><span>平均 V6 Fit</span><b>{poolSummary.avgFit === null ? '--' : formatScore(poolSummary.avgFit)}</b><em>{poolSummary.avgFit === null ? '数据不足' : '真实字段'}</em></button>
        <button type="button"><span>本周高 Trend</span><b>{compactMetric(loaded, hasTrendSignal, formatCount(highTrendCount))}</b><em>{hasTrendSignal ? 'trend_score / trend_topic' : '待接入 trend endpoint'}</em></button>
        <button type="button"><span>月度估算 Reach</span><b>--</b><em>待接入 monthly reach</em></button>
      </section>

      <KolPoolTrendPulse />

      {countries.length ? (
        <section className="kol-pool-v2-coverage">
          <strong>海外覆盖</strong>
          {countries.map((row, index) => (
            <span key={`${textValue(row.country || row.code || row.name, 'country')}-${index}`}>
              {textValue(row.country || row.country_code || row.code || row.name)} · {formatCount(row.count || row.kol_count || row.value)}
            </span>
          ))}
        </section>
      ) : null}

      <section className="kol-pool-v2-search">
        <input value={search} onChange={(event) => setSearch(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void loadPool(); }} placeholder="搜索 KOL / handle / 国家 / bio" />
        <div className="kol-pool-v2-search-modes" role="group" aria-label="搜索模式">
          {[
            { key: 'balanced', label: '平衡', hint: '老 15 · 新 15' },
            { key: 'precision', label: '精准', hint: '老 20 · 新 10' },
            { key: 'discovery', label: '探索', hint: '老 10 · 新 20' },
          ].map((mode) => (
            <button
              key={mode.key}
              type="button"
              className={searchMode === mode.key ? 'is-active' : ''}
              title="当前仅保留前端状态，真实配比等待搜索端点支持"
              onClick={() => setSearchMode(mode.key as typeof searchMode)}
            >
              <b>{mode.label}</b><span>{mode.hint}</span>
            </button>
          ))}
        </div>
        <button type="button" onClick={() => void loadPool()} disabled={loading}>{loading ? '读取中...' : '读取最新'}</button>
      </section>

      <section className="kol-pool-v2-classbar">
        <button type="button" className={!classFilter ? 'is-active' : ''} onClick={() => setClassFilter('')}>
          全部 <span>{loaded ? items.length : '--'}</span>
        </button>
        {(Object.keys(CANDIDATE_CLASS_INFO) as CandidateClass[]).map((key) => (
          <button key={key} type="button" className={classFilter === key ? 'is-active' : ''} onClick={() => setClassFilter(classFilter === key ? '' : key)}>
            {CANDIDATE_CLASS_INFO[key].label} <span>{loaded ? classCounts[key] : '--'}</span>
          </button>
        ))}
      </section>

      <section className="kol-pool-v2-filters">
        <select value={platform} onChange={(event) => setPlatform(event.target.value)}>
          <option value="">全部平台</option>
          {platforms.map((item) => <option key={item} value={item}>{item}</option>)}
        </select>
        <select value={country} onChange={(event) => setCountry(event.target.value)}>
          <option value="">全部国家</option>
          {countryOptions.map((item) => <option key={item} value={item}>{item.toUpperCase()}</option>)}
        </select>
        <select value={sortBy} onChange={(event) => setSortBy(event.target.value)}>
          <option value="fit">V6 Fit</option>
          <option value="followers">粉丝</option>
          <option value="views">平均播放</option>
          <option value="er">Real ER</option>
        </select>
        <button type="button" onClick={() => { setPlatform(''); setCountry(''); setClassFilter(''); setSearch(''); }}>清空筛选</button>
      </section>

      {error ? <div className="kol-pool-v2-alert">{error}</div> : null}

      <section className="kol-pool-v2-table-card">
        <div className="kol-pool-v2-table-head">
          <span>显示 {loaded ? filteredItems.length : '--'} / {loaded ? items.length : '--'} 个 KOL</span>
          <b>{classFilter ? CANDIDATE_CLASS_INFO[classFilter].label : '全部候选'}</b>
        </div>
        <div className="kol-pool-v2-table-wrap">
          <table>
            <thead>
              <tr>
                <th>KOL · 平台</th>
                <th>粉丝 / 真实%</th>
                <th>Real ER</th>
                <th>类型 · Geo</th>
                <th>Trend</th>
                <th>设备</th>
                <th>V6 Fit</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {filteredItems.map((item) => (
                <tr key={item.id} className={selectedItem?.id === item.id ? 'is-selected' : ''} onClick={() => void openDetail(item)}>
                  <td>
                    <div className="kol-pool-v2-profile">
                      <span className={`kol-pool-v2-freshness is-${deriveFreshness(item)}`} />
                      <Avatar name={kolDisplayName(item)} src={item.avatar_url} size="sm" />
                      <span>
                        <b>{kolDisplayName(item)}</b>
                        <em><PlatformPill value={item.platform} /> <CandidateChip item={item} /></em>
                      </span>
                    </div>
                  </td>
                  <td><b className="kol-pool-v2-num">{formatCount(item.followers)}</b><small>{formatPercent(item.real_followers_pct)} 真实</small></td>
                  <td><b className="kol-pool-v2-num">{formatPercent(item.real_engagement_rate ?? item.engagement_rate)}</b><small>raw {formatPercent(item.raw_engagement_rate ?? item.engagement_rate)}</small></td>
                  <td><b>{textValue(item.audience_type, '待评估')}</b><small>{textValue(item.country, 'Geo 待接入')}</small></td>
                  <td><span className={`kol-pool-v2-trend is-${trendTone(item)}`}>{formatScore(item.trend_score)}</span><small>{textValue(item.trend_topic, '待接入')}</small></td>
                  <td><b>{textValue(item.device_primary, '待接入')}</b><small>升级窗口：{textValue(item.device_upgrade_window, '待评估')}</small></td>
                  <td><FitCell item={item} /></td>
                  <td>{dataGaps(item).length ? <span className="kol-pool-v2-chip is-orange">缺 {dataGaps(item).length}</span> : <span className="kol-pool-v2-chip is-green">完整</span>}</td>
                </tr>
              ))}
              {!filteredItems.length ? (
                <tr><td colSpan={8}><div className="kol-pool-v2-empty">{loaded ? '暂无匹配候选' : '待接入 / 无信号'}</div></td></tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>

      {selectedItem ? (
        <KolPoolDetailDrawer
          item={selectedItem}
          loading={detailLoading}
          evidence={evidence}
          intelligenceCard={intelligenceCard}
          promoting={promotingId === selectedItem.id}
          onClose={() => setSelectedItem(null)}
          onPromote={() => void promoteSelected()}
        />
      ) : null}
    </div>
  );
}

export default KolPoolV2Page;
