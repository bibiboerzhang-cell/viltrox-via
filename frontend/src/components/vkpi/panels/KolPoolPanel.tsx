// frontend/src/components/vkpi/panels/KolPoolPanel.tsx
//
// R59: KOL Pool 管理面板
// P3.5F: 候选池可点击详情 + 数据缺口展示。
// P3.6G: 候选池决策视图，把数据完整度、适配度和下一步动作放到列表层。

import { useEffect, useMemo, useState } from 'react';

import { CardHeader } from '../shared/CardHeader';
import { PlatformPill } from '../shared/PlatformPill';
import { Avatar } from '../shared/Avatar';
import type { VkpiPlatform } from '../vkpiTypes';

interface KolPoolFreshness {
  kol_pool_id: number;
  tier: string;
  tier_reason?: string;
  last_refresh_at?: string;
  last_refresh_status?: string;
  threshold_days?: number;
  days_old?: number | null;
  needs_refresh?: boolean;
  reason?: string;
  search_count_30d?: number;
  last_searched_at?: string;
}

interface KolPoolRefreshState {
  triggered: boolean;
  reason: string;
  task_id?: string;
  task_type?: string;
  lock_key?: string;
  message?: string;
  provider_calls_enabled?: boolean;
  freshness?: KolPoolFreshness;
  search_marker?: {
    tier?: string;
    tier_reason?: string;
    search_count_30d?: number;
    last_searched_at?: string;
  };
}

interface KolPoolItem {
  id: number;
  pool_uid: string;
  platform: string;
  handle: string;
  profile_url?: string;
  display_name?: string;
  avatar_url?: string;
  bio?: string;
  email?: string;
  followers?: number;
  following?: number;
  posts_count?: number;
  avg_views?: number;
  avg_likes?: number;
  avg_comments?: number;
  engagement_rate?: number;
  primary_topic?: string;
  content_style?: string;
  production_quality?: string;
  viltrox_fit_score?: number;
  viltrox_fit_reason?: string;
  linked_main_kol_id?: number | null;
  source_type?: string;
  source_ref?: string;
  raw_platform_data?: string | Record<string, unknown>;
  recommended_product_lines_json?: string | unknown[];
  potential_concerns_json?: string | unknown[];
  brand_collaborations_json?: string | unknown[];
  created_at?: string;
  updated_at?: string;
  last_seen_at?: string;
  sync_status?: string;
  freshness?: KolPoolFreshness;
  refresh?: KolPoolRefreshState;
}

interface KolPoolPanelProps {
  apiToken: string;
  onListPool: (params: { search?: string; platform?: string; limit?: number; dataStatus?: string; sortBy?: string; enrichable?: boolean; refreshIfStale?: boolean }) => Promise<{ items?: KolPoolItem[]; refresh?: KolPoolRefreshState }>;
  onGetItem?: (kolPoolId: number) => Promise<{ item?: KolPoolItem; freshness?: KolPoolFreshness; refresh?: KolPoolRefreshState }>;
  onEnrichItem?: (kolPoolId: number, maxPosts?: number) => Promise<{
    item?: KolPoolItem;
    sync_status?: string;
    provider_status?: string;
    message?: string;
    posts_sampled?: number;
  }>;
  onBatchEnrich?: (payload: {
    ids?: number[];
    platform?: string;
    query?: string;
    dataStatus?: string;
    limit?: number;
    maxPosts?: number;
  }) => Promise<{
    attempted?: number;
    enriched?: number;
    complete?: number;
    partial?: Array<Record<string, unknown>>;
    skipped?: Array<Record<string, unknown>>;
    errors?: Array<Record<string, unknown>>;
    items?: KolPoolItem[];
    capped?: boolean;
  }>;
  onPromoteToMain?: (kolPoolId: number) => Promise<{
    linked?: boolean;
    mode?: string;
    main_kol_id?: number | null;
    item?: KolPoolItem;
  }>;
  onOpenImport?: () => void;
}

const ENRICHABLE_PLATFORMS = new Set([
  'youtube',
  'instagram',
  'tiktok',
  'xiaohongshu',
  'x',
  'bilibili',
  'facebook',
  'reddit',
]);

export function KolPoolPanel({ apiToken, onListPool, onGetItem, onEnrichItem, onBatchEnrich, onPromoteToMain, onOpenImport }: KolPoolPanelProps) {
  const [items, setItems] = useState<KolPoolItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [platform, setPlatform] = useState('');
  const [dataStatus, setDataStatus] = useState('');
  const [sortBy, setSortBy] = useState('fit');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [linkingId, setLinkingId] = useState<number | null>(null);
  const [enrichingId, setEnrichingId] = useState<number | null>(null);
  const [batchBusy, setBatchBusy] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(() => new Set());
  const [selectedItem, setSelectedItem] = useState<KolPoolItem | null>(null);
  const [listRefreshState, setListRefreshState] = useState<KolPoolRefreshState | null>(null);

  async function loadList() {
    if (!apiToken) {
      setError('未登录');
      return;
    }
    setLoading(true);
    setError('');
    setMessage('');
    setListRefreshState(null);
    try {
      const result = await onListPool({ search, platform, dataStatus, sortBy, limit: 150, refreshIfStale: Boolean(search.trim()) });
      const nextItems = result.items || [];
      if (result.refresh) {
        setListRefreshState(result.refresh);
        setMessage(refreshStateLabel(result.refresh));
      }
      setItems(nextItems);
      setSelectedIds((current) => new Set(nextItems.filter((item) => current.has(item.id)).map((item) => item.id)));
      if (selectedItem) {
        const refreshed = nextItems.find((item) => item.id === selectedItem.id);
        if (refreshed) setSelectedItem((current) => ({ ...current, ...refreshed }));
      }
    } catch (err) {
      setError((err as Error).message || '加载失败');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadList();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiToken]);

  async function openDetail(item: KolPoolItem) {
    setSelectedItem(item);
    if (!onGetItem) return;
    setDetailLoading(true);
    setError('');
    try {
      const result = await onGetItem(item.id);
      if (result.item) {
        setSelectedItem({
          ...result.item,
          freshness: result.freshness || result.refresh?.freshness,
          refresh: result.refresh,
        });
      }
      if (result.refresh) {
        setMessage(refreshStateLabel(result.refresh));
      }
    } catch (err) {
      setError((err as Error).message || '详情加载失败');
    } finally {
      setDetailLoading(false);
    }
  }

  async function handlePromoteToMain(item: KolPoolItem) {
    if (!onPromoteToMain) {
      setError('自动创建/链接主表功能未配置；请从红人搜索认领或重新加载最新前端。');
      return;
    }
    setLinkingId(item.id);
    setError('');
    setMessage('');
    try {
      const result = await onPromoteToMain(item.id);
      if (result.item) {
        setItems((current) => current.map((row) => (row.id === item.id ? { ...row, ...result.item } : row)));
        setSelectedItem((current) => (current?.id === item.id ? { ...current, ...result.item } : current));
      }
      const action = result.mode === 'matched' ? '匹配并链接' : result.mode === 'created' ? '创建并链接' : '链接';
      setMessage(`${action}主表完成：#${result.main_kol_id || result.item?.linked_main_kol_id || '-'}`);
      await loadList();
    } catch (err) {
      setError((err as Error).message || '创建/链接主表失败');
    } finally {
      setLinkingId(null);
    }
  }

  async function handleEnrich(item: KolPoolItem, maxPosts = 12) {
    if (!onEnrichItem) {
      setError('补齐功能未配置');
      return;
    }
    if (!canEnrich(item)) {
      setError(`${item.platform || 'unknown'} 平台暂未接入真实补齐链路`);
      return;
    }
    setEnrichingId(item.id);
    setError('');
    try {
      const result = await onEnrichItem(item.id, maxPosts);
      if (result.message && result.sync_status !== 'synced') {
        setError(result.message);
      }
      if (result.item) {
        setItems((current) => current.map((row) => (row.id === item.id ? { ...row, ...result.item } : row)));
        setSelectedItem((current) => (current?.id === item.id ? { ...current, ...result.item } : current));
      }
    } catch (err) {
      setError((err as Error).message || '补齐数据失败');
    } finally {
      setEnrichingId(null);
    }
  }

  async function handleBatchEnrich(mode: 'selected' | 'filtered') {
    if (!onBatchEnrich) {
      setError('批量补齐功能未配置');
      return;
    }
    const ids = mode === 'selected' ? Array.from(selectedIds) : [];
    if (mode === 'selected' && ids.length === 0) {
      setError('先选择要补齐的候选');
      return;
    }
    setBatchBusy(true);
    setError('');
    setMessage('');
    try {
      const result = await onBatchEnrich({
        ids,
        platform,
        query: search,
        dataStatus: dataStatus || 'missing',
        limit: mode === 'selected' ? Math.min(ids.length, 5) : 3,
        maxPosts: 6,
      });
      if (result.items?.length) {
        setItems((current) => mergeItems(current, result.items || []));
        setSelectedItem((current) => {
          if (!current) return current;
          const refreshed = result.items?.find((item) => item.id === current.id);
          return refreshed ? { ...current, ...refreshed } : current;
        });
      }
      const skipped = result.skipped?.length || 0;
      const errors = result.errors?.length || 0;
      const partial = result.partial?.length || 0;
      setMessage(`批量补齐完成：尝试 ${result.attempted || 0} 条，写入 ${result.enriched || 0} 条，完整 ${result.complete || 0} 条，仍缺数据 ${partial} 条，跳过 ${skipped} 条，错误 ${errors} 条${result.capped ? '（已按上限截断）' : ''}。`);
      if (errors) setError('部分候选补齐失败，详情需要看后端日志或返回 errors。');
    } catch (err) {
      setError((err as Error).message || '批量补齐失败');
    } finally {
      setBatchBusy(false);
    }
  }

  function toggleSelected(id: number) {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleVisibleSelected() {
    setSelectedIds((current) => {
      const visible = items.map((item) => item.id);
      const allSelected = visible.length > 0 && visible.every((id) => current.has(id));
      const next = new Set(current);
      visible.forEach((id) => {
        if (allSelected) next.delete(id);
        else next.add(id);
      });
      return next;
    });
  }

  const platformOptions = ['', 'instagram', 'tiktok', 'youtube', 'xiaohongshu', 'x', 'bilibili', 'facebook', 'reddit'];
  const coverage = useMemo(() => summarizeCoverage(items), [items]);
  const topCandidate = useMemo(() => items.find((item) => hasNumber(item.viltrox_fit_score) || hasNumber(item.avg_views) || hasNumber(item.followers)), [items]);

  return (
    <div className="vkpi-card vkpi-kol-pool-panel">
      <CardHeader title="KOL Pool 候选池" />
      <p className="vkpi-muted">从 Apify / CSV / 推广计划表导入的候选资产池；它不是 Daily Top100 全量员工候选。</p>

      <div className="vkpi-kol-pool-toolbar">
        <input
          className="vkpi-input"
          placeholder="搜索 handle / display_name / bio"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') void loadList();
          }}
        />
        <select className="vkpi-input" value={platform} onChange={(event) => setPlatform(event.target.value)}>
          {platformOptions.map((p) => (
            <option key={p || 'all'} value={p}>{p || '全部平台'}</option>
          ))}
        </select>
        <select className="vkpi-input" value={dataStatus} onChange={(event) => setDataStatus(event.target.value)}>
          <option value="">全部数据状态</option>
          <option value="missing">只看缺数据</option>
          <option value="complete">只看已完整</option>
        </select>
        <select className="vkpi-input" value={sortBy} onChange={(event) => setSortBy(event.target.value)}>
          <option value="fit">适配度优先</option>
          <option value="followers">粉丝优先</option>
          <option value="avg_views">平均播放优先</option>
          <option value="engagement_rate">互动率优先</option>
          <option value="missing">缺口最多优先</option>
          <option value="updated">最近更新</option>
        </select>
        <button className="vkpi-button" type="button" onClick={() => void loadList()} disabled={loading}>
          {loading ? '加载中…' : '刷新'}
        </button>
        {onOpenImport && (
          <button className="vkpi-button vkpi-button--primary" type="button" onClick={onOpenImport}>一键导入</button>
        )}
      </div>

      <div className="vkpi-kol-pool-coverage" aria-label="KOL Pool 数据覆盖情况">
        <CoverageChip label="头像" value={coverage.avatar} total={coverage.total} />
        <CoverageChip label="平均播放" value={coverage.avgViews} total={coverage.total} />
        <CoverageChip label="互动率" value={coverage.engagement} total={coverage.total} />
        <CoverageChip label="适配度" value={coverage.fit} total={coverage.total} />
      </div>

      <div className="vkpi-kol-pool-decision-strip">
        <div>
          <span className="vkpi-eyebrow">当前筛选</span>
          <strong>{items.length} 个候选 · {coverage.complete} 个完整 · {coverage.missing} 个待补齐</strong>
          <p>点击任意候选行打开详情；优先补齐缺头像、平均播放、互动率的候选，再决定是否链接到主表。</p>
        </div>
        {topCandidate ? (
          <button className="vkpi-button vkpi-button--small" type="button" onClick={() => void openDetail(topCandidate)}>
            查看当前最高优先级: {topCandidate.display_name || topCandidate.handle}
          </button>
        ) : (
          <span className="vkpi-chip vkpi-chip--muted">暂无可排序候选</span>
        )}
      </div>

      <div className="vkpi-kol-pool-bulkbar">
        <span>{selectedIds.size ? `已选择 ${selectedIds.size} 条` : '未选择候选'}</span>
        <button className="vkpi-button vkpi-button--small" type="button" onClick={toggleVisibleSelected} disabled={!items.length}>
          {items.length && items.every((item) => selectedIds.has(item.id)) ? '取消当前页' : '选择当前页'}
        </button>
        {onBatchEnrich && (
          <>
            <button className="vkpi-button vkpi-button--small vkpi-button--primary" type="button" onClick={() => void handleBatchEnrich('selected')} disabled={batchBusy || selectedIds.size === 0}>
              {batchBusy ? '补齐中…' : '补齐选中（最多 5）'}
            </button>
            <button className="vkpi-button vkpi-button--small" type="button" onClick={() => void handleBatchEnrich('filtered')} disabled={batchBusy}>
              当前筛选前 3 条补齐
            </button>
          </>
        )}
      </div>

      {error && <div className="vkpi-alert vkpi-alert--error" style={{ marginBottom: 12 }}>{error}</div>}
      {message && <div className="vkpi-alert" style={{ marginBottom: 12 }}>{message}</div>}
      {listRefreshState && <RefreshStateNotice refresh={listRefreshState} />}

      {!loading && items.length === 0 ? (
        <div className="vkpi-empty">暂无候选 KOL。点击“一键导入”从 Apify / CSV 导入。</div>
      ) : (
        <div className="vkpi-table-wrap">
          <table className="vkpi-table vkpi-kol-pool-table">
            <thead>
              <tr>
                <th style={{ width: 58 }}></th>
                <th style={{ width: 42 }}></th>
                <th>Handle / Name</th>
                <th>决策</th>
                <th>平台</th>
                <th>粉丝</th>
                <th>平均播放</th>
                <th>互动率</th>
                <th>适配度</th>
                <th>数据缺口</th>
                <th>状态</th>
                <th>来源</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {loading && !items.length ? (
                <KolPoolSkeletonRows />
              ) : items.map((item) => {
                const gaps = getDataGaps(item);
                const enrichable = canEnrich(item);
                const decision = decisionProfile(item);
                return (
                  <tr
                    key={item.id}
                    className={selectedItem?.id === item.id ? 'is-selected' : ''}
                    onClick={() => void openDetail(item)}
                    title={`点击查看 ${item.display_name || item.handle || item.id} 的候选详情`}
                  >
                    <td><Avatar src={item.avatar_url} name={item.display_name || item.handle} size="sm" /></td>
                    <td onClick={(event) => event.stopPropagation()}>
                      <input
                        aria-label={`选择 ${item.handle || item.display_name || item.id}`}
                        type="checkbox"
                        checked={selectedIds.has(item.id)}
                        onChange={() => toggleSelected(item.id)}
                      />
                    </td>
                    <td>
                      <div className="vkpi-kol-pool-name">{item.handle || '—'}</div>
                      {item.display_name && <div className="vkpi-kol-pool-subtitle">{item.display_name}</div>}
                      <div className="vkpi-kol-pool-mini">
                        <span>{item.sync_status || 'unknown'}</span>
                        {item.profile_url && <span>主页已记录</span>}
                        {item.bio && <span>简介已记录</span>}
                      </div>
                    </td>
                    <td><DecisionCell decision={decision} /></td>
                    <td><PlatformPill platform={toVkpiPlatform(item.platform)} /></td>
                    <td>{formatNumber(item.followers)}</td>
                    <td>{formatNumber(item.avg_views)}</td>
                    <td>{formatPercent(item.engagement_rate)}</td>
                    <td>{formatScore(item.viltrox_fit_score)}</td>
                    <td>
                      {gaps.length ? (
                        <span className="vkpi-chip vkpi-chip--warn">缺 {gaps.length} 项</span>
                      ) : (
                        <span className="vkpi-chip is-success">完整</span>
                      )}
                    </td>
                    <td>
                      {item.linked_main_kol_id ? (
                        <span className="vkpi-chip is-success">已链接 #{item.linked_main_kol_id}</span>
                      ) : (
                        <span className="vkpi-chip">候选</span>
                      )}
                    </td>
                    <td className="vkpi-kol-pool-source">{item.source_type || '—'}</td>
                    <td onClick={(event) => event.stopPropagation()}>
                      <div className="vkpi-table-actions">
                        <button className="vkpi-button vkpi-button--small" type="button" onClick={() => void openDetail(item)}>详情</button>
                        {item.profile_url && (
                          <button className="vkpi-button vkpi-button--small" type="button" onClick={() => window.open(item.profile_url, '_blank', 'noopener,noreferrer')}>
                            打开主页
                          </button>
                        )}
                        {onEnrichItem && enrichable && (
                          <button className="vkpi-button vkpi-button--small" type="button" onClick={() => void handleEnrich(item)} disabled={enrichingId === item.id}>
                            {enrichingId === item.id ? '补齐中…' : '补齐数据'}
                          </button>
                        )}
                        {onEnrichItem && !enrichable && <span className="vkpi-chip vkpi-chip--muted">暂不支持补齐</span>}
                        {!item.linked_main_kol_id && onPromoteToMain && (
                          <button className="vkpi-button vkpi-button--small" type="button" onClick={() => void handlePromoteToMain(item)} disabled={linkingId === item.id}>
                            {linkingId === item.id ? '处理中…' : '自动入主表'}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {selectedItem && (
        <KolPoolDetailDrawer
          item={selectedItem}
          loading={detailLoading}
          onClose={() => setSelectedItem(null)}
          onEnrich={onEnrichItem && canEnrich(selectedItem) ? () => void handleEnrich(selectedItem, 24) : undefined}
          enriching={enrichingId === selectedItem.id}
          onPromoteToMain={onPromoteToMain ? () => void handlePromoteToMain(selectedItem) : undefined}
          linking={linkingId === selectedItem.id}
        />
      )}
    </div>
  );
}

function KolPoolSkeletonRows() {
  return (
    <>
      {[0, 1, 2, 3, 4, 5].map((item) => (
        <tr className="vkpi-kol-pool-skeleton-row" key={item} aria-hidden="true">
          <td><span className="vkpi-skeleton vkpi-skeleton-avatar" /></td>
          <td><span className="vkpi-skeleton vkpi-skeleton-pill" /></td>
          <td>
            <span className="vkpi-skeleton vkpi-skeleton-line is-medium" />
            <span className="vkpi-skeleton vkpi-skeleton-line is-long" />
          </td>
          <td><span className="vkpi-skeleton vkpi-skeleton-line is-medium" /></td>
          <td><span className="vkpi-skeleton vkpi-skeleton-pill" /></td>
          <td><span className="vkpi-skeleton vkpi-skeleton-line is-short" /></td>
          <td><span className="vkpi-skeleton vkpi-skeleton-line is-short" /></td>
          <td><span className="vkpi-skeleton vkpi-skeleton-line is-short" /></td>
          <td><span className="vkpi-skeleton vkpi-skeleton-line is-short" /></td>
          <td><span className="vkpi-skeleton vkpi-skeleton-pill" /></td>
          <td><span className="vkpi-skeleton vkpi-skeleton-pill" /></td>
          <td><span className="vkpi-skeleton vkpi-skeleton-line is-medium" /></td>
          <td><span className="vkpi-skeleton vkpi-skeleton-line is-long" /></td>
        </tr>
      ))}
    </>
  );
}

function KolPoolDetailDrawer({
  item,
  loading,
  onClose,
  onEnrich,
  enriching,
  onPromoteToMain,
  linking,
}: {
  item: KolPoolItem;
  loading: boolean;
  onClose: () => void;
  onEnrich?: () => void;
  enriching?: boolean;
  onPromoteToMain?: () => void;
  linking?: boolean;
}) {
  const raw = parseMaybeJson(item.raw_platform_data);
  const products = collectList(item.recommended_product_lines_json, raw, ['product', 'product_name', '产品', 'Product', 'sku', 'SKU']);
  const owners = collectList(undefined, raw, ['owner', '负责人', 'staff', 'assignee', 'manager', '负责员工']);
  const notes = collectList(undefined, raw, ['notes', '备注', 'comment', '合作备注', 'status', '状态']);
  const gaps = getDataGaps(item);
  const profileUrl = item.profile_url || getString(raw, ['profile_url', 'url', 'channelUrl', '主页', '主页 URL']);
  const priority = candidatePriority(item);
  const decision = decisionProfile(item);
  const readiness = metricReadiness(item);

  return (
    <aside className="vkpi-drawer vkpi-kol-pool-drawer" aria-label="KOL Pool 详情">
      <div className="vkpi-drawer__header">
        <div className="vkpi-kol-pool-drawer-title">
          <Avatar src={item.avatar_url} name={item.display_name || item.handle} size="md" />
          <div>
            <span className="vkpi-eyebrow">候选池 · {String(item.platform || 'other').toUpperCase()}</span>
            <h2>{item.display_name || item.handle || '未命名 KOL'}</h2>
            <p>@{item.handle || '—'} · {item.source_type || 'unknown'} · {item.source_ref || '无来源标记'}</p>
          </div>
        </div>
        <button className="vkpi-icon-button" type="button" onClick={onClose} aria-label="关闭">×</button>
      </div>

      {loading && <div className="vkpi-alert">正在读取完整详情…</div>}
      {item.refresh && <RefreshStateNotice refresh={item.refresh} />}

      <section className={`vkpi-kol-pool-decision-card ${priority.tone}`}>
        <div>
          <span className="vkpi-eyebrow">决策摘要</span>
          <strong>{priority.label}</strong>
          <p>{priority.reason}</p>
        </div>
        <div className="vkpi-kol-pool-decision-actions">
          {profileUrl && <a className="vkpi-button vkpi-button--small" href={profileUrl} target="_blank" rel="noreferrer">打开平台主页 ↗</a>}
          {onEnrich && <button className="vkpi-button vkpi-button--small vkpi-button--primary" type="button" onClick={onEnrich} disabled={enriching}>{enriching ? '补齐中…' : '补齐数据'}</button>}
        </div>
      </section>

      <section className="vkpi-kol-pool-readiness-card">
        <div>
          <span className="vkpi-eyebrow">决策优先级</span>
          <strong>{decision.label}</strong>
          <p>{decision.reason}</p>
        </div>
        <div className="vkpi-kol-pool-readiness-score">
          <span>{decision.score}</span>
          <small>readiness</small>
        </div>
      </section>

      <section className="vkpi-detail-grid">
        <InfoTile label="粉丝" value={formatNumber(item.followers)} />
        <InfoTile label="平均播放" value={formatNumber(item.avg_views)} />
        <InfoTile label="互动率" value={formatPercent(item.engagement_rate)} />
        <InfoTile label="适配度" value={formatScoreValue(item.viltrox_fit_score)} />
        <InfoTile label="帖子/视频" value={formatNumber(item.posts_count)} />
        <InfoTile label="同步状态" value={item.sync_status || '—'} />
        <InfoTile label="邮箱" value={item.email || getString(raw, ['email', 'Email', '邮箱']) || '—'} />
      </section>

      <section className="vkpi-card vkpi-alert-detail-section">
        <h3>判断信息</h3>
        <div className="vkpi-chip-list">
          {profileUrl ? <a className="vkpi-chip" href={profileUrl} target="_blank" rel="noreferrer">打开平台主页 ↗</a> : <span className="vkpi-chip vkpi-chip--warn">缺主页 URL</span>}
          {products.length ? products.map((value) => <span key={`product-${value}`} className="vkpi-chip">产品: {value}</span>) : <span className="vkpi-chip vkpi-chip--warn">缺产品线</span>}
          {owners.length ? owners.map((value) => <span key={`owner-${value}`} className="vkpi-chip">负责: {value}</span>) : <span className="vkpi-chip vkpi-chip--warn">缺负责人</span>}
          {item.linked_main_kol_id ? <span className="vkpi-chip is-success">已链接主表 #{item.linked_main_kol_id}</span> : <span className="vkpi-chip">未链接主表</span>}
        </div>
        {item.viltrox_fit_reason && <p className="vkpi-help-text">{item.viltrox_fit_reason}</p>}
      </section>

      <section className="vkpi-card vkpi-alert-detail-section">
        <h3>可操作下一步</h3>
        <div className="vkpi-kol-pool-next-grid">
          <ActionHint done={!gaps.includes('头像') && !gaps.includes('平均播放') && !gaps.includes('互动率')} label="数据可判断" hint="头像、平均播放、互动率齐全后再决策。" />
          <ActionHint done={Boolean(item.linked_main_kol_id)} label="主表链接" hint="自动匹配已有主表；无匹配时创建主表记录。" />
          <ActionHint done={Boolean(profileUrl)} label="平台复核" hint="打开平台主页确认账号真实性。" />
          <ActionHint done={products.length > 0} label="产品匹配" hint="需要明确适配产品线，便于后续项目创建。" />
        </div>
      </section>

      <section className="vkpi-card vkpi-alert-detail-section">
        <h3>四维判断</h3>
        <div className="vkpi-kol-pool-readiness-grid">
          {readiness.map((row) => (
            <div key={row.label} className={row.ready ? 'is-ok' : 'is-missing'}>
              <strong>{row.label}</strong>
              <span>{row.value}</span>
              <small>{row.reason}</small>
            </div>
          ))}
        </div>
      </section>

      <section className="vkpi-card vkpi-alert-detail-section">
        <h3>数据缺口</h3>
        <div className="vkpi-kol-pool-gap-grid">
          {['头像', '平均播放', '互动率', '适配度'].map((label) => (
            <div key={label} className={gaps.includes(label) ? 'is-missing' : 'is-ok'}>
              <strong>{label}</strong>
              <span>{gaps.includes(label) ? '待补齐' : '已有'}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="vkpi-card vkpi-alert-detail-section">
        <h3>原始备注 / 合作记录</h3>
        {notes.length ? (
          <ul className="vkpi-kol-pool-notes">
            {notes.map((note, index) => <li key={`${note}-${index}`}>{note}</li>)}
          </ul>
        ) : (
          <p className="vkpi-help-text">当前导入记录没有可读备注。后续 P3.6 会把历史合作、负责人和产品字段做成标准字段。</p>
        )}
        <details className="vkpi-raw-details">
          <summary>查看原始导入/抓取 JSON</summary>
          <pre className="vkpi-code-block">{JSON.stringify(raw || {}, null, 2)}</pre>
        </details>
      </section>

      <div className="vkpi-kol-pool-drawer-actions">
        {onEnrich && <button className="vkpi-button vkpi-button--primary" type="button" onClick={onEnrich} disabled={enriching}>{enriching ? '真实补齐中…' : '补齐头像 / 指标'}</button>}
        {onPromoteToMain && !item.linked_main_kol_id && <button className="vkpi-button vkpi-button--primary" type="button" onClick={onPromoteToMain} disabled={linking}>{linking ? '处理中…' : '自动创建/链接主表'}</button>}
        <button className="vkpi-button" type="button" onClick={onClose}>关闭</button>
      </div>
    </aside>
  );
}

function DecisionCell({ decision }: { decision: ReturnType<typeof decisionProfile> }) {
  return (
    <div className={`vkpi-kol-pool-decision-cell ${decision.tone}`}>
      <div>
        <strong>{decision.label}</strong>
        <span>{decision.nextAction}</span>
      </div>
      <em>{decision.score}</em>
    </div>
  );
}

function CoverageChip({ label, value, total }: { label: string; value: number; total: number }) {
  const ratio = total ? Math.round((value / total) * 100) : 0;
  return <span className={ratio === 100 ? 'vkpi-chip is-success' : 'vkpi-chip'}>{label} {value}/{total} · {ratio}%</span>;
}

function InfoTile({ label, value }: { label: string; value: string }) {
  return <div className="vkpi-info-tile"><span>{label}</span><strong>{value}</strong></div>;
}

function ActionHint({ done, label, hint }: { done: boolean; label: string; hint: string }) {
  return (
    <div className={done ? 'is-ok' : 'is-missing'}>
      <strong>{done ? '已就绪' : '待处理'} · {label}</strong>
      <span>{hint}</span>
    </div>
  );
}

function RefreshStateNotice({ refresh }: { refresh: KolPoolRefreshState }) {
  const freshness = refresh.freshness;
  const tone = refresh.triggered ? ' is-info' : refresh.reason === 'on_demand_refresh_disabled' ? ' is-warning' : '';
  return (
    <div className={`vkpi-alert${tone}`} style={{ marginBottom: 12 }}>
      <strong>{refreshStateLabel(refresh)}</strong>
      {freshness && (
        <div className="vkpi-help-text">
          层级 {freshness.tier || 'cold'} · 阈值 {freshness.threshold_days ?? '-'} 天 ·
          {freshness.days_old === null || freshness.days_old === undefined ? ' 从未刷新' : ` 已 ${freshness.days_old} 天`} ·
          搜索计数 {refresh.search_marker?.search_count_30d ?? freshness.search_count_30d ?? 0}
        </div>
      )}
      {refresh.task_id && <div className="vkpi-help-text">后台任务: {refresh.task_id}</div>}
    </div>
  );
}

function refreshStateLabel(refresh: KolPoolRefreshState): string {
  if (refresh.triggered) return '旧数据已返回，后台刷新已排队。';
  if (refresh.reason === 'on_demand_refresh_disabled') return '数据新鲜度已检查，按需刷新尚未启用。';
  if (refresh.reason === 'fresh') return '数据仍在新鲜度窗口内。';
  if (refresh.reason === 'job_queue_unavailable') return '数据较旧，但后台队列当前不可用。';
  if (refresh.reason === 'not_enqueueable') return refresh.message || '该账号暂不支持按需刷新。';
  if (refresh.reason === 'not_requested') return '已读取现有记录，未请求后台刷新。';
  return refresh.message || `刷新状态: ${refresh.reason || 'unknown'}`;
}

function getDataGaps(item: KolPoolItem): string[] {
  const gaps: string[] = [];
  if (!item.avatar_url) gaps.push('头像');
  if (!hasNumber(item.avg_views)) gaps.push('平均播放');
  if (!hasNumber(item.engagement_rate)) gaps.push('互动率');
  if (!hasNumber(item.viltrox_fit_score)) gaps.push('适配度');
  return gaps;
}

function canEnrich(item: KolPoolItem): boolean {
  return ENRICHABLE_PLATFORMS.has(String(item.platform || '').toLowerCase());
}

function summarizeCoverage(items: KolPoolItem[]) {
  const avatar = items.filter((item) => Boolean(item.avatar_url)).length;
  const avgViews = items.filter((item) => hasNumber(item.avg_views)).length;
  const engagement = items.filter((item) => hasNumber(item.engagement_rate)).length;
  const fit = items.filter((item) => hasNumber(item.viltrox_fit_score)).length;
  const complete = items.filter((item) => getDataGaps(item).length === 0).length;
  return {
    total: items.length,
    avatar,
    avgViews,
    engagement,
    fit,
    complete,
    missing: items.length - complete,
  };
}

function decisionProfile(item: KolPoolItem): { score: number; label: string; reason: string; nextAction: string; tone: string } {
  const gaps = getDataGaps(item);
  const fit = numberValue(item.viltrox_fit_score);
  const followers = numberValue(item.followers);
  const avgViews = numberValue(item.avg_views);
  const engagement = numberValue(item.engagement_rate);
  const dataScore = Math.max(0, 40 - gaps.length * 10);
  const fitScore = fit === null ? 0 : Math.min(30, fit * 0.3);
  const scaleScore = Math.min(15, Math.log10(Math.max(1, followers || 0) + 1) * 3);
  const actionScore = Math.min(15, (avgViews ? Math.log10(avgViews + 1) * 2 : 0) + (engagement ? engagement * 1.2 : 0));
  const score = Math.round(dataScore + fitScore + scaleScore + actionScore);
  if (gaps.length >= 2) {
    return {
      score,
      label: '先补数据',
      reason: `缺 ${gaps.join(' / ')}，现在不适合直接决策。`,
      nextAction: canEnrich(item) ? '补齐数据' : '人工补字段',
      tone: 'is-warn',
    };
  }
  if (!item.linked_main_kol_id && score >= 70) {
    return {
      score,
      label: '可入主表',
      reason: '核心指标足够，适合进入主表后做项目/沟通跟进。',
      nextAction: '自动入主表',
      tone: 'is-ok',
    };
  }
  if (score >= 60) {
    return {
      score,
      label: '人工复核',
      reason: '指标基本可判断，建议打开主页检查内容风格和粉丝真实性。',
      nextAction: '打开主页',
      tone: 'is-neutral',
    };
  }
  return {
    score,
    label: '低优先级',
    reason: '当前指标不足以支持优先推进，可保留观察或等待负责人指定。',
    nextAction: '保留观察',
    tone: 'is-muted',
  };
}

function candidatePriority(item: KolPoolItem): { label: string; reason: string; tone: string } {
  const gaps = getDataGaps(item);
  if (gaps.length) {
    return {
      label: `先补齐 ${gaps.join(' / ')}`,
      reason: '当前数据还不足以判断合作优先级；建议先跑真实补齐，再决定是否链接到主表。',
      tone: 'is-warn',
    };
  }
  const score = numberValue(item.viltrox_fit_score);
  const engagement = numberValue(item.engagement_rate);
  if ((score !== null && score >= 70) || (engagement !== null && engagement >= 3)) {
    return {
      label: '可进入人工复核',
      reason: '核心指标已经齐全，适合打开平台主页复核内容风格、粉丝真实性和产品匹配。',
      tone: 'is-ok',
    };
  }
  return {
    label: '数据完整但优先级一般',
    reason: '可保留在候选池，除非负责人或产品线强匹配，否则不建议立即推进项目。',
    tone: 'is-neutral',
  };
}

function metricReadiness(item: KolPoolItem): Array<{ label: string; value: string; reason: string; ready: boolean }> {
  const followers = numberValue(item.followers);
  const avgViews = numberValue(item.avg_views);
  const engagement = numberValue(item.engagement_rate);
  const fit = numberValue(item.viltrox_fit_score);
  return [
    {
      label: '规模',
      value: formatNumber(item.followers),
      reason: followers === null ? '缺粉丝数据' : followers >= 10_000 ? '规模可参考' : '规模较小，适合作长尾观察',
      ready: followers !== null,
    },
    {
      label: '内容表现',
      value: formatNumber(item.avg_views),
      reason: avgViews === null ? '缺平均播放' : avgViews >= 5_000 ? '有内容验证' : '播放规模偏低',
      ready: avgViews !== null,
    },
    {
      label: '互动',
      value: formatPercent(item.engagement_rate),
      reason: engagement === null ? '缺互动率' : engagement >= 2 ? '互动可用' : '互动偏弱',
      ready: engagement !== null,
    },
    {
      label: '产品适配',
      value: formatScoreValue(item.viltrox_fit_score),
      reason: fit === null ? '缺适配评分' : fit >= 70 ? '适配度较高' : '适配度一般，需要人工看内容',
      ready: fit !== null,
    },
  ];
}

function mergeItems(current: KolPoolItem[], updates: KolPoolItem[]): KolPoolItem[] {
  const byId = new Map(updates.map((item) => [item.id, item]));
  return current.map((item) => {
    const update = byId.get(item.id);
    return update ? { ...item, ...update } : item;
  });
}

function parseMaybeJson(value: unknown): Record<string, unknown> {
  if (!value) return {};
  if (typeof value === 'object' && !Array.isArray(value)) return value as Record<string, unknown>;
  if (typeof value !== 'string') return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

function parseMaybeList(value: unknown): string[] {
  if (!value) return [];
  if (Array.isArray(value)) return value.map((item) => stringifyValue(item)).filter(Boolean);
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value);
      if (Array.isArray(parsed)) return parsed.map((item) => stringifyValue(item)).filter(Boolean);
    } catch {
      return value.split(/[;,，、\n]/).map((item) => item.trim()).filter(Boolean);
    }
  }
  return [stringifyValue(value)].filter(Boolean);
}

function collectList(jsonValue: unknown, raw: Record<string, unknown>, keys: string[]): string[] {
  const values = parseMaybeList(jsonValue);
  for (const key of keys) values.push(...parseMaybeList(raw[key]));
  return Array.from(new Set(values.map((item) => item.trim()).filter(Boolean))).slice(0, 8);
}

function getString(raw: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = raw[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return '';
}

function stringifyValue(value: unknown): string {
  if (value === undefined || value === null) return '';
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>;
    const preferred = record.name || record.label || record.value || record.product || record.owner || record.note;
    if (preferred) return stringifyValue(preferred);
    return JSON.stringify(value);
  }
  return String(value);
}

function numberValue(value: unknown): number | null {
  if (value === undefined || value === null || value === '') return null;
  const next = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(next) ? next : null;
}

function hasNumber(value: unknown): boolean {
  return numberValue(value) !== null;
}

function formatNumber(value: unknown): string {
  const next = numberValue(value);
  if (next === null) return '—';
  if (next >= 1_000_000) return `${(next / 1_000_000).toFixed(1)}M`;
  if (next >= 1_000) return `${(next / 1_000).toFixed(1)}K`;
  return String(Math.round(next));
}

function formatPercent(value: unknown): string {
  const next = numberValue(value);
  if (next === null) return '—';
  return `${next.toFixed(2)}%`;
}

function formatScore(value: unknown) {
  const next = numberValue(value);
  return next === null ? '—' : <span className="vkpi-chip">{next.toFixed(1)}</span>;
}

function formatScoreValue(value: unknown): string {
  const next = numberValue(value);
  return next === null ? '—' : `${next.toFixed(1)}/100`;
}

function toVkpiPlatform(platform: string): VkpiPlatform {
  const normalized = String(platform || '').toLowerCase();
  const map: Record<string, VkpiPlatform> = {
    instagram: 'Instagram',
    tiktok: 'TikTok',
    youtube: 'YouTube',
    xiaohongshu: 'XHS',
    xhs: 'XHS',
    x: 'X',
    twitter: 'X',
    bilibili: 'Bilibili',
    facebook: 'Facebook',
    reddit: 'Reddit',
    threads: 'Threads',
    pinterest: 'Pinterest',
    website: 'Website',
  };
  return map[normalized] || 'Other';
}
