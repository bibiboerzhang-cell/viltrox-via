// frontend/src/components/vkpi/panels/KolPoolPanel.tsx
//
// R59: KOL Pool 管理面板
// P3.5F: 候选池可点击详情 + 数据缺口展示。
// P3.6G: 候选池决策视图，把数据完整度、适配度和下一步动作放到列表层。

import { useEffect, useMemo, useState } from 'react';

import { CardHeader } from '../shared/CardHeader';
import { PlatformPill } from '../shared/PlatformPill';
import { Avatar } from '../shared/Avatar';

import {
  CoverageChip,
  DecisionCell,
  KolPoolSkeletonRows,
  RefreshStateNotice,
} from './KolPoolPanel.listParts';
import { KolPoolDetailDrawer } from './KolPoolPanel.parts';
import type { KolPoolIntelligenceCard, KolPoolItem, KolPoolPanelProps, KolPoolRefreshState } from './KolPoolPanel.types';
import {
  canEnrich,
  decisionProfile,
  formatNumber,
  formatPercent,
  formatScore,
  getDataGaps,
  hasNumber,
  mergeItems,
  refreshStateLabel,
  summarizeCoverage,
  toVkpiPlatform,
} from './KolPoolPanel.utils';

export function KolPoolPanel({ apiToken, onListPool, onGetItem, onGetIntelligenceCard, onEnrichItem, onBatchEnrich, onPromoteToMain, onOpenImport }: KolPoolPanelProps) {
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
  const [intelligenceCard, setIntelligenceCard] = useState<KolPoolIntelligenceCard | null>(null);
  const [intelligenceLoading, setIntelligenceLoading] = useState(false);

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
    setIntelligenceCard(null);
    if (!onGetItem && !onGetIntelligenceCard) return;
    setDetailLoading(true);
    setIntelligenceLoading(Boolean(onGetIntelligenceCard));
    setError('');
    try {
      if (onGetItem) {
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
      }
      if (onGetIntelligenceCard) {
        try {
          const card = await onGetIntelligenceCard(item.id);
          setIntelligenceCard(card);
        } catch (cardError) {
          setIntelligenceCard({
            decision_support: { readiness: 'partial', gaps: ['intelligence_card_unavailable'] },
            dimensions11: { status: 'unavailable', error: (cardError as Error).message || '读取失败' },
          });
        }
      }
    } catch (err) {
      setError((err as Error).message || '详情加载失败');
    } finally {
      setDetailLoading(false);
      setIntelligenceLoading(false);
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
      <p className="vkpi-muted">从 Apify / CSV / 推广计划表导入的候选资产池；它不是 Daily Top100 全量成员候选。</p>

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
            intelligenceCard={intelligenceCard}
            intelligenceLoading={intelligenceLoading}
            onClose={() => {
              setSelectedItem(null);
              setIntelligenceCard(null);
            }}
            onEnrich={onEnrichItem && canEnrich(selectedItem) ? () => void handleEnrich(selectedItem, 24) : undefined}
          enriching={enrichingId === selectedItem.id}
          onPromoteToMain={onPromoteToMain ? () => void handlePromoteToMain(selectedItem) : undefined}
          linking={linkingId === selectedItem.id}
        />
      )}
    </div>
  );
}
