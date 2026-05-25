import type { Dispatch, SetStateAction } from 'react';
import type { VkpiPlatform } from '../../vkpiTypes';
import { Avatar } from '../../shared/Avatar';
import { creatorPlatformOptions, platformLabels } from '../../shared/vkpiConstants';
import { platformFromRaw } from '../../shared/vkpiDataUtils';

interface UiKol {
  id: string;
  name: string;
  handle: string;
  platform: VkpiPlatform;
  avatar?: string;
  followerCount: number;
  followerLabel: string;
  country: string;
  topic: string;
  status: string;
  contactCount: number;
  hasCollaboration: boolean;
  riskLabel: string;
  grade: string;
  score: number;
  sourceKind?: 'kol' | 'platform_search' | 'kol_pool';
}

type SearchStepStatus = 'pending' | 'active' | 'done' | 'error';

interface SearchProgressStep {
  key: string;
  label: string;
  detail: string;
  status: SearchStepStatus;
}

interface SearchProgressState {
  visible: boolean;
  title: string;
  percent: number;
  steps: SearchProgressStep[];
}

interface SearchHistoryItem {
  id: string;
  query: string;
  platform: string;
  mode: string;
  resultCount: number;
  status: string;
  searchedAt: string;
}

interface SearchFilters {
  platform: string;
  level: string;
  grade: string;
  collab: string;
  risk: string;
  freshness: string;
}

function formatHistoryTime(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  const deltaSeconds = Math.max(0, Math.floor((Date.now() - parsed.getTime()) / 1000));
  if (deltaSeconds < 60) return '刚刚';
  const minutes = Math.floor(deltaSeconds / 60);
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${parsed.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })} ${parsed.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`;
}

function searchHistoryPlatformLabel(value: string): string {
  return value === 'all' ? '全部平台' : platformLabels[platformFromRaw(value)] || value;
}

function isCandidateKol(kol?: UiKol) {
  return Boolean(kol?.sourceKind === 'platform_search' || kol?.sourceKind === 'kol_pool' || String(kol?.id || '').startsWith('candidate:') || String(kol?.id || '').startsWith('pool:'));
}

export function SearchProgress({ progress }: { progress: SearchProgressState }) {
  if (!progress.visible) return null;
  return (
    <section className="vkpi-discover-search-progress" aria-live="polite">
      <div className="vkpi-discover-search-progress__head">
        <strong>{progress.title}</strong>
        <span>{progress.percent}%</span>
      </div>
      <div className="vkpi-discover-search-progress__bar"><i style={{ width: `${progress.percent}%` }} /></div>
      <div className="vkpi-discover-search-progress__steps">
        {progress.steps.map((step) => (
          <span className={`is-${step.status}`} key={step.key} title={step.detail}>
            <b>{step.label}</b>
            <em>{step.detail}</em>
          </span>
        ))}
      </div>
    </section>
  );
}

function DiscoverCandidateSkeletons({ count = 5 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }).map((_, index) => (
        <div className="vkpi-discover-kol vkpi-discover-kol--skeleton" key={index} aria-hidden="true">
          <span className="vkpi-skeleton vkpi-skeleton-avatar is-round" />
          <div className="vkpi-discover-kol__main">
            <span className="vkpi-skeleton vkpi-skeleton-line is-medium" />
            <span className="vkpi-skeleton vkpi-skeleton-line is-long" />
            <div>
              <span className="vkpi-skeleton vkpi-skeleton-pill" />
              <span className="vkpi-skeleton vkpi-skeleton-pill" />
              <span className="vkpi-skeleton vkpi-skeleton-pill" />
            </div>
          </div>
          <div className="vkpi-discover-kol__score">
            <span className="vkpi-skeleton vkpi-skeleton-line is-short" />
            <span className="vkpi-skeleton vkpi-skeleton-pill" />
          </div>
        </div>
      ))}
    </>
  );
}

function KolCard({ kol, active, onClick }: { kol: UiKol; active: boolean; onClick: () => void }) {
  const level = kol.followerCount >= 500000 ? 'top' : kol.followerCount >= 50000 ? 'mid' : 'tail';
  return (
    <button className={`vkpi-discover-kol ${active ? 'is-active' : ''}`} type="button" onClick={onClick}>
      <Avatar name={kol.name || kol.handle} src={kol.avatar} size="sm" />
      <div className="vkpi-discover-kol__main">
        <strong>{kol.handle}</strong>
        <span>{platformLabels[kol.platform] || kol.platform} · {kol.followerLabel} 粉 · {kol.country} · {kol.topic}</span>
        <div>
          <em>{level}</em>
          {kol.sourceKind === 'kol_pool' ? <em className="is-purple">历史档案</em> : isCandidateKol(kol) ? <em className="is-good">平台搜索</em> : null}
          <em>{kol.status}</em>
          {kol.hasCollaboration ? <em className="is-good">合作过</em> : <em>未合作</em>}
          {kol.contactCount ? <em className="is-purple">联系方式 {kol.contactCount}</em> : null}
          {kol.riskLabel ? <em className="is-warn">{kol.riskLabel}</em> : null}
        </div>
      </div>
      <div className="vkpi-discover-kol__score">
        <strong>{kol.score || '-'}</strong>
        <span>{kol.grade}</span>
      </div>
    </button>
  );
}

export function SearchPanel({
  localQuery,
  setLocalQuery,
  filters,
  setFilters,
  visibleKols,
  selectedKolId,
  searchProgress,
  searchHistory,
  onSelect,
  onHistorySelect,
}: {
  localQuery: string;
  setLocalQuery: (value: string) => void;
  filters: SearchFilters;
  setFilters: Dispatch<SetStateAction<SearchFilters>>;
  visibleKols: UiKol[];
  selectedKolId: string;
  searchProgress: SearchProgressState;
  searchHistory: SearchHistoryItem[];
  onSelect: (kolId: string) => void;
  onHistorySelect: (item: SearchHistoryItem) => void;
}) {
  const setFilter = (key: keyof SearchFilters, value: string) => setFilters((previous) => ({ ...previous, [key]: value }));
  const runningSearch = searchProgress.visible && searchProgress.percent < 100;
  return (
    <section className="vkpi-discover-panel">
      <div className="vkpi-discover-panel__header">
        <div>
          <h3>主动搜索结果</h3>
          <span>{runningSearch ? '平台搜索运行中，候选会逐条出现。' : `${visibleKols.length} 个档案/候选；平台搜索结果需建档后才有完整画像`}</span>
        </div>
      </div>
      {searchHistory.length ? (
        <div className="vkpi-discover-search-history" aria-label="搜索历史">
          <div className="vkpi-discover-search-history__head">
            <strong>搜索历史</strong>
            <span>点一下复搜</span>
          </div>
          <div className="vkpi-discover-search-history__items">
            {searchHistory.slice(0, 6).map((item) => (
              <button key={item.id} type="button" onClick={() => onHistorySelect(item)} title={item.query}>
                <b>{item.query}</b>
                <span>{searchHistoryPlatformLabel(item.platform)} · {item.resultCount} 条 · {formatHistoryTime(item.searchedAt)}</span>
              </button>
            ))}
          </div>
        </div>
      ) : null}
      <div className="vkpi-discover-searchbox">
        <input value={localQuery} onChange={(event) => setLocalQuery(event.target.value)} placeholder="在当前结果内筛选 handle / 国家 / 主题" />
        <div className="vkpi-discover-quick">
          {['35mm 街拍', 'YouTube Review', '美国', '联系方式'].map((item) => (
            <button key={item} type="button" onClick={() => setLocalQuery(item)}>{item}</button>
          ))}
        </div>
      </div>
      <div className="vkpi-discover-filters">
        <select value={filters.platform} onChange={(event) => setFilter('platform', event.target.value)} aria-label="平台筛选">
          <option value="all">全部平台</option>
          {creatorPlatformOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
        <select value={filters.level} onChange={(event) => setFilter('level', event.target.value)} aria-label="红人层级">
          <option value="all">全部层级</option>
          <option value="top">头部</option>
          <option value="mid">中腰部</option>
          <option value="tail">长尾</option>
        </select>
        <select value={filters.grade} onChange={(event) => setFilter('grade', event.target.value)} aria-label="评分">
          <option value="all">全部评分</option>
          {['S', 'A', 'B', 'C', 'D'].map((grade) => <option key={grade} value={grade}>Grade {grade}</option>)}
        </select>
        <select value={filters.collab} onChange={(event) => setFilter('collab', event.target.value)} aria-label="合作状态">
          <option value="all">合作状态</option>
          <option value="yes">合作过</option>
          <option value="no">未合作</option>
        </select>
        <select value={filters.risk} onChange={(event) => setFilter('risk', event.target.value)} aria-label="风险">
          <option value="all">风险状态</option>
          <option value="clean">无风险</option>
          <option value="has">有风险</option>
        </select>
        <select value={filters.freshness} onChange={(event) => setFilter('freshness', event.target.value)} aria-label="数据新鲜度">
          <option value="all">全部新鲜度</option>
          <option value="stale">需刷新</option>
        </select>
      </div>
      <div className="vkpi-discover-list">
        {visibleKols.length ? (
          <>
            {visibleKols.map((kol) => (
              <KolCard key={kol.id} kol={kol} active={selectedKolId === kol.id} onClick={() => onSelect(kol.id)} />
            ))}
            {runningSearch ? <DiscoverCandidateSkeletons count={Math.max(1, Math.min(3, 5 - visibleKols.length))} /> : null}
          </>
        ) : runningSearch ? (
          <DiscoverCandidateSkeletons />
        ) : <div className="vkpi-discover-empty">当前筛选下没有红人。请用顶部搜索从真实接口加载，或切到候选池。</div>}
      </div>
    </section>
  );
}
