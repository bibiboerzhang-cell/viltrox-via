import { useEffect, useMemo, useRef, useState } from 'react';
import { claimKol, getKolComments, getKolProfile, releaseKolClaim, scanKolAccount, updateMarketingKol } from '../../../../domains/channels';
import type { VkpiDashboardData, VkpiKolProfile } from '../../vkpiTypes';
import { platformDisplay, safeNumber } from '../../shared/vkpiDataUtils';
import { numberFormatter } from '../../shared/vkpiFormatters';
import { proxiedImageUrl } from '../../shared/mediaProxy';
import { KolMediaLightbox, KolMediaSlot, mediaBadge } from './MyKolMedia';
import { MyKolDiscoveryBridge } from './MyKolDiscoveryBridge';
import { MyKolPlatformCards } from './MyKolPlatformCards';
import { buildMyKolItems, buildPlatformMetrics, categoryForPost, commentsForPost, compactContactValue, compactDate, conciseText, contactDraftFor, contactItems, displayCount, fetchAllKolPostRows, filterAndSortPosts, funnelStageFor, initials, latestSnapshotPosts, mapCommentRows, mapPostRows, platformFilterFromRaw, postPreviews, searchMatches, summarize, summarizePostInsights, textField } from './myKolMatrixData';
import { CONTENT_FILTER_OPTIONS, CONTENT_SORT_OPTIONS, CONTENT_WINDOW_OPTIONS, FUNNEL_STAGES, PLATFORM_OPTIONS, VIEW_TABS, type ContactDraft, type EffectiveMyKolItem, type FunnelStageKey, type KolCommentState, type KolContentDirection, type KolContentFilter, type KolContentSort, type KolContentWindow, type KolPostState, type MyKolView, type PlatformFilter, type PostPreview } from './myKolMatrixTypes';
import './channelKols.css';

interface MyKolMatrixProps {
  apiToken?: string;
  data: VkpiDashboardData;
  initialPlatform?: string;
  onDiscoverPlatform?: (platform: PlatformFilter) => void;
  onRefreshData?: () => void;
}

export function MyKolMatrix({ apiToken, data, initialPlatform, onDiscoverPlatform, onRefreshData }: MyKolMatrixProps) {
  const [activeView, setActiveView] = useState<MyKolView>('watchlist');
  const [activeFunnelStage, setActiveFunnelStage] = useState<FunnelStageKey>('claimed');
  const [selectedKolId, setSelectedKolId] = useState('');
  const [query, setQuery] = useState('');
  const [platformFilter, setPlatformFilter] = useState<PlatformFilter>('all');
  const [subStatusFilter, setSubStatusFilter] = useState('all');
  const [viltroxRelated, setViltroxRelated] = useState(true);
  const [releasedClaimIds, setReleasedClaimIds] = useState<Set<string>>(() => new Set());
  const [claimedKolIds, setClaimedKolIds] = useState<Set<string>>(() => new Set());
  const [busyKolId, setBusyKolId] = useState('');
  const [scanningKolId, setScanningKolId] = useState('');
  const [savingContactId, setSavingContactId] = useState('');
  const [editingContactId, setEditingContactId] = useState('');
  const [contactDrafts, setContactDrafts] = useState<Record<string, ContactDraft>>({});
  const [contactOverrides, setContactOverrides] = useState<Record<string, Partial<ContactDraft>>>({});
  const [kolProfiles, setKolProfiles] = useState<Record<string, VkpiKolProfile>>({});
  const [kolPosts, setKolPosts] = useState<Record<string, KolPostState>>({});
  const [kolComments, setKolComments] = useState<Record<string, KolCommentState>>({});
  const [contentSort, setContentSort] = useState<KolContentSort>('latest');
  const [contentDirection, setContentDirection] = useState<KolContentDirection>('desc');
  const [contentWindow, setContentWindow] = useState<KolContentWindow>('all');
  const [contentFilter, setContentFilter] = useState<KolContentFilter>('all');
  const [previewPost, setPreviewPost] = useState<PostPreview | null>(null);
  const [commentPost, setCommentPost] = useState<PostPreview | null>(null);
  const [devicePopoverId, setDevicePopoverId] = useState('');
  const [accountLayerCollapsed, setAccountLayerCollapsed] = useState(false);
  const [contentLayerCollapsed, setContentLayerCollapsed] = useState(false);
  const [loadingProfileIds, setLoadingProfileIds] = useState<Set<string>>(() => new Set());
  const [requestedProfileIds, setRequestedProfileIds] = useState<Set<string>>(() => new Set());
  const [message, setMessage] = useState('');
  const appliedInitialPlatformRef = useRef('');
  const rawItems = useMemo(() => buildMyKolItems(data), [data]);

  const items = useMemo<EffectiveMyKolItem[]>(() => rawItems.map((item) => {
    const isReleased = item.activeClaimId ? releasedClaimIds.has(item.activeClaimId) : false;
    const isClaimedNow = item.kolId ? claimedKolIds.has(item.kolId) : false;
    return {
      ...item,
      isFollowed: Boolean((item.activeClaimId && !isReleased) || isClaimedNow),
      funnelStage: funnelStageFor(item),
    };
  }).filter((item) => item.isFollowed || item.projectCount > 0), [claimedKolIds, rawItems, releasedClaimIds]);

  const watchlistCount = items.filter((item) => item.isFollowed).length;
  const funnelCount = items.filter((item) => item.projectCount > 0 || item.isFollowed).length;
  const funnelCounts = useMemo(() => FUNNEL_STAGES.reduce<Record<FunnelStageKey, number>>((counts, stage) => {
    counts[stage.key] = items.filter((item) => item.funnelStage === stage.key).length;
    return counts;
  }, { claimed: 0, contacted: 0, replied: 0, agreed: 0, shipped: 0, received: 0, published: 0, measured: 0 }), [items]);

  const subStatusOptions = useMemo(() => {
    const labels = new Set<string>();
    items.forEach((item) => {
      if (activeView === 'watchlist' && !item.isFollowed) return;
      if (activeView === 'funnel' && item.funnelStage !== activeFunnelStage) return;
      if (item.subStatus) labels.add(item.subStatus);
    });
    return Array.from(labels).sort((left, right) => left.localeCompare(right));
  }, [activeFunnelStage, activeView, items]);

  const platformBaseItems = useMemo(() => items.filter((item) => (
    (activeView === 'watchlist' ? item.isFollowed : item.funnelStage === activeFunnelStage)
    && searchMatches(item, query)
    && (subStatusFilter === 'all' || item.subStatus === subStatusFilter)
  )), [activeFunnelStage, activeView, items, query, subStatusFilter]);

  const filteredItems = useMemo(() => platformBaseItems.filter((item) => (
    platformFilter === 'all' || item.platform === platformFilter
  )), [platformBaseItems, platformFilter]);
  const selectedItem = useMemo(() => (
    filteredItems.find((item) => item.id === selectedKolId) || filteredItems[0]
  ), [filteredItems, selectedKolId]);

  const totals = useMemo(() => summarize(filteredItems), [filteredItems]);
  const platformMetrics = useMemo(() => buildPlatformMetrics(platformBaseItems), [platformBaseItems]);
  const selectedPlatformMetric = useMemo(() => (
    platformMetrics.find((entry) => entry.platform === platformFilter)
  ), [platformFilter, platformMetrics]);
  const maxFunnelCount = useMemo(() => Math.max(1, ...FUNNEL_STAGES.map((stage) => funnelCounts[stage.key])), [funnelCounts]);
  const viewLabel = viltroxRelated ? 'Viltrox播放' : '账号播放';
  const clickLabel = viltroxRelated ? 'Viltrox点击' : '总点击';
  const selectedProfile = selectedItem?.kolId ? kolProfiles[selectedItem.kolId] : undefined;
  const selectedPostState = selectedItem?.kolId ? kolPosts[selectedItem.kolId] : undefined;
  const selectedCommentState = selectedItem?.kolId ? kolComments[selectedItem.kolId] : undefined;
  const selectedSummary = selectedProfile?.summary || {};
  const profilePosts = useMemo(() => postPreviews(selectedProfile), [selectedProfile]);
  const selectedSourcePosts = selectedPostState?.items?.length ? selectedPostState.items : profilePosts;
  const selectedRawPosts = useMemo(() => latestSnapshotPosts(selectedSourcePosts, selectedProfile), [selectedProfile, selectedSourcePosts]);
  const selectedPosts = useMemo(() => (
    filterAndSortPosts(selectedRawPosts, contentFilter, contentSort, contentDirection, contentWindow)
  ), [contentDirection, contentFilter, contentSort, contentWindow, selectedRawPosts]);
  const selectedPostInsights = useMemo(() => summarizePostInsights(selectedRawPosts, selectedProfile), [selectedProfile, selectedRawPosts]);
  const selectedComments = selectedCommentState?.items || [];
  const selectedTotalPosts = selectedRawPosts.length || selectedPostState?.total || safeNumber(selectedSummary.content_count);
  const selectedProfileLoading = selectedItem?.kolId ? !selectedProfile && loadingProfileIds.has(selectedItem.kolId) : false;
  const selectedAvatar = selectedItem ? proxiedImageUrl(textField(selectedProfile?.kol, 'avatar_url') || selectedItem.avatar) : '';
  const selectedFollowerLabel = selectedItem && displayCount(selectedSummary.follower_count || 0) !== '0' ? displayCount(selectedSummary.follower_count) : selectedItem?.followers || '0';
  const selectedContentLabel = selectedItem && displayCount(selectedSummary.content_count || 0) !== '0' ? displayCount(selectedSummary.content_count) : selectedItem?.contentCount || '0';
  const selectedContacts = selectedItem ? contactItems(selectedItem, selectedProfile, contactOverrides[selectedItem.id]) : [];
  const selectedDraft = selectedItem ? contactDrafts[selectedItem.id] || contactDraftFor(selectedItem, selectedProfile, contactOverrides[selectedItem.id]) : undefined;

  useEffect(() => {
    if (!initialPlatform) return;
    if (appliedInitialPlatformRef.current === initialPlatform) return;
    appliedInitialPlatformRef.current = initialPlatform;
    const nextPlatform = platformFilterFromRaw(initialPlatform);
    setPlatformFilter(nextPlatform);
  }, [initialPlatform]);

  useEffect(() => {
    if (!filteredItems.length) {
      if (selectedKolId) setSelectedKolId('');
      return;
    }
    if (!filteredItems.some((item) => item.id === selectedKolId)) {
      setSelectedKolId(filteredItems[0].id);
    }
  }, [filteredItems, selectedKolId]);

  useEffect(() => {
    if (!apiToken) return;
    const ids = Array.from(new Set(filteredItems.map((item) => item.kolId).filter(Boolean))).slice(0, 8)
      .filter((id) => !kolProfiles[id] && !loadingProfileIds.has(id) && !requestedProfileIds.has(id));
    if (!ids.length) return;
    setRequestedProfileIds((current) => {
      const next = new Set(current);
      ids.forEach((id) => next.add(id));
      return next;
    });
    setLoadingProfileIds((current) => {
      const next = new Set(current);
      ids.forEach((id) => next.add(id));
      return next;
    });
    ids.forEach((kolId) => {
      getKolProfile(apiToken, kolId)
        .then((profile) => {
          setKolProfiles((current) => ({ ...current, [kolId]: profile }));
        })
        .catch(() => undefined)
        .finally(() => {
          setLoadingProfileIds((current) => {
            const next = new Set(current);
            next.delete(kolId);
            return next;
          });
        });
    });
  }, [apiToken, filteredItems, kolProfiles, loadingProfileIds, requestedProfileIds]);

  useEffect(() => {
    setCommentPost(null);
    setPreviewPost(null);
    setDevicePopoverId('');
    setContentFilter('all');
  }, [selectedItem?.id]);

  useEffect(() => {
    if (!apiToken || !selectedItem?.kolId) return;
    const kolId = selectedItem.kolId;
    const postState = kolPosts[kolId];
    if (!postState?.requested && !postState?.loading) {
      setKolPosts((current) => ({
        ...current,
        [kolId]: { items: current[kolId]?.items || [], total: current[kolId]?.total || 0, loading: true, requested: true, error: '' },
      }));
      fetchAllKolPostRows(apiToken, kolId)
        .then((response) => {
          setKolPosts((current) => ({
            ...current,
            [kolId]: {
              items: mapPostRows(response.rows),
              total: response.total,
              loading: false,
              requested: true,
              error: '',
            },
          }));
        })
        .catch((error) => {
          setKolPosts((current) => ({
            ...current,
            [kolId]: {
              items: current[kolId]?.items || [],
              total: current[kolId]?.total || 0,
              loading: false,
              requested: true,
              error: error instanceof Error ? error.message : '内容加载失败',
            },
          }));
        });
    }

    const commentState = kolComments[kolId];
    if (!commentState?.requested && !commentState?.loading) {
      setKolComments((current) => ({
        ...current,
        [kolId]: { items: current[kolId]?.items || [], total: current[kolId]?.total || 0, loading: true, requested: true, error: '' },
      }));
      getKolComments(apiToken, kolId, { limit: 100 })
        .then((response) => {
          const rows = Array.isArray(response.items) ? response.items : [];
          setKolComments((current) => ({
            ...current,
            [kolId]: {
              items: mapCommentRows(rows),
              total: safeNumber(response.page?.total) || rows.length,
              loading: false,
              requested: true,
              error: '',
            },
          }));
        })
        .catch((error) => {
          setKolComments((current) => ({
            ...current,
            [kolId]: {
              items: current[kolId]?.items || [],
              total: current[kolId]?.total || 0,
              loading: false,
              requested: true,
              error: error instanceof Error ? error.message : '评论加载失败',
            },
          }));
        });
    }
  }, [apiToken, kolComments, kolPosts, selectedItem?.kolId]);

  const selectView = (view: MyKolView) => {
    setActiveView(view);
    setSubStatusFilter('all');
  };

  const toggleFollow = async (item: EffectiveMyKolItem) => {
    if (!apiToken || !item.kolId) {
      setMessage('缺少登录 token 或 KOL ID，不能更新关注状态。');
      return;
    }
    setBusyKolId(item.id);
    setMessage('');
    try {
      if (item.isFollowed && item.activeClaimId && !releasedClaimIds.has(item.activeClaimId)) {
        await releaseKolClaim(apiToken, item.activeClaimId, 'employee_unfollow');
        setReleasedClaimIds((current) => new Set(current).add(item.activeClaimId || ''));
        setClaimedKolIds((current) => {
          const next = new Set(current);
          next.delete(item.kolId);
          return next;
        });
        setMessage(`已取消关注：${item.name}`);
      } else if (!item.isFollowed) {
        await claimKol(apiToken, item.kolId);
        setClaimedKolIds((current) => new Set(current).add(item.kolId));
        setMessage(`已关注：${item.name}`);
      }
      onRefreshData?.();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '关注状态更新失败');
    } finally {
      setBusyKolId('');
    }
  };

  const startContactEdit = (item: EffectiveMyKolItem) => {
    setEditingContactId(item.id);
    setContactDrafts((current) => ({
      ...current,
      [item.id]: contactDraftFor(item, kolProfiles[item.kolId], contactOverrides[item.id]),
    }));
  };

  const saveContact = async (item: EffectiveMyKolItem) => {
    const draft = contactDrafts[item.id];
    if (!apiToken || !item.kolId || !draft) {
      setMessage('缺少登录 token 或 KOL ID，不能保存联系方式。');
      return;
    }
    setSavingContactId(item.id);
    setMessage('');
    try {
      await updateMarketingKol(apiToken, item.kolId, {
        contactEmail: draft.contactEmail.trim(),
        contactPhone: draft.contactPhone.trim(),
        profileUrl: draft.profileUrl.trim(),
      });
      setContactOverrides((current) => ({ ...current, [item.id]: draft }));
      setEditingContactId('');
      setMessage(`已保存联系方式：${item.name}`);
      onRefreshData?.();
      const profile = await getKolProfile(apiToken, item.kolId).catch(() => null);
      if (profile) setKolProfiles((current) => ({ ...current, [item.kolId]: profile }));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '联系方式保存失败');
    } finally {
      setSavingContactId('');
    }
  };

  const scanAccount = async (item: EffectiveMyKolItem) => {
    if (!apiToken || !item.kolId) {
      setMessage('缺少登录 token 或 KOL ID，不能抓取账号。');
      return;
    }
    setScanningKolId(item.id);
    setMessage(`正在抓取 ${platformDisplay(item.platform)} 账号数据：${item.handle}`);
    try {
      await scanKolAccount(apiToken, item.kolId, 24);
      const [profile, postsResponse, commentsResponse] = await Promise.all([
        getKolProfile(apiToken, item.kolId),
        fetchAllKolPostRows(apiToken, item.kolId).catch(() => null),
        getKolComments(apiToken, item.kolId, { limit: 100 }).catch(() => null),
      ]);
      setKolProfiles((current) => ({ ...current, [item.kolId]: profile }));
      if (postsResponse) {
        setKolPosts((current) => ({
          ...current,
          [item.kolId]: { items: mapPostRows(postsResponse.rows), total: postsResponse.total, loading: false, requested: true, error: '' },
        }));
      }
      if (commentsResponse) {
        const rows = Array.isArray(commentsResponse.items) ? commentsResponse.items : [];
        setKolComments((current) => ({
          ...current,
          [item.kolId]: { items: mapCommentRows(rows), total: safeNumber(commentsResponse.page?.total) || rows.length, loading: false, requested: true, error: '' },
        }));
      }
      setMessage(`已抓取账号数据：${item.name}`);
      onRefreshData?.();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '账号抓取失败');
    } finally {
      setScanningKolId('');
    }
  };

  return (
    <section className="vkpi-my-kol-matrix">
      <header className="vkpi-my-kol-matrix__header">
        <div>
          <span>员工KOL库</span>
          <h2>我的KOL</h2>
          <em>{numberFormatter.format(items.length)} 个KOL</em>
        </div>
        <div className="vkpi-my-kol-matrix__totals">
          <span><strong>{numberFormatter.format(totals.kols)}</strong><small>KOL</small></span>
          <span><strong>{numberFormatter.format(totals.projects)}</strong><small>项目</small></span>
          <span className="is-primary"><strong>{displayCount(totals.views)}</strong><small>{viewLabel}</small></span>
          <span><strong>{displayCount(totals.clicks)}</strong><small>{clickLabel}</small></span>
        </div>
      </header>

      <div className="vkpi-my-kol-tabs" role="tablist" aria-label="我的KOL视图">
        {VIEW_TABS.map((view) => (
          <button
            aria-selected={activeView === view.key}
            className={`vkpi-my-kol-tab${activeView === view.key ? ' is-active' : ''}`}
            key={view.key}
            onClick={() => selectView(view.key)}
            role="tab"
            type="button"
          >
            <span>{view.label}</span>
            <strong>{numberFormatter.format(view.key === 'watchlist' ? watchlistCount : funnelCount)}</strong>
          </button>
        ))}
      </div>

      {activeView === 'funnel' ? (
        <div className="vkpi-my-kol-funnel" aria-label="KOL 合作漏斗">
          {FUNNEL_STAGES.map((stage, index) => {
            const count = funnelCounts[stage.key];
            const active = activeFunnelStage === stage.key;
            const meterWidth = count ? Math.max(12, Math.round((count / maxFunnelCount) * 100)) : 0;
            return (
              <button
                className={`vkpi-my-kol-funnel__row${active ? ' is-active' : ''}`}
                key={stage.key}
                onClick={() => {
                  setActiveFunnelStage(stage.key);
                  setSubStatusFilter('all');
                }}
                type="button"
              >
                <span className="vkpi-my-kol-funnel__step">{String(index + 1).padStart(2, '0')}</span>
                <span className="vkpi-my-kol-funnel__meta"><b>{stage.label}</b><strong>{numberFormatter.format(count)}</strong></span>
                <span className="vkpi-my-kol-funnel__meter" aria-hidden="true"><i style={{ width: `${meterWidth}%` }} /></span>
              </button>
            );
          })}
        </div>
      ) : null}

      <div className="vkpi-my-kol-toolbar" aria-label="我的KOL筛选">
        <label className="vkpi-my-kol-search">
          <span>搜索</span>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索 KOL / handle" />
        </label>
        <label>
          <span>平台</span>
          <select value={platformFilter} onChange={(event) => setPlatformFilter(event.target.value as PlatformFilter)}>
            {PLATFORM_OPTIONS.map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}
          </select>
        </label>
        <label>
          <span>子状态</span>
          <select value={subStatusFilter} onChange={(event) => setSubStatusFilter(event.target.value)}>
            <option value="all">全部</option>
            {subStatusOptions.map((status) => <option key={status} value={status}>{status}</option>)}
          </select>
        </label>
        <label className="vkpi-my-kol-switch">
          <input checked={viltroxRelated} onChange={(event) => setViltroxRelated(event.target.checked)} type="checkbox" />
          <span aria-hidden="true" />
          <em>Viltrox相关</em>
        </label>
      </div>

      <MyKolPlatformCards
        activePlatform={platformFilter}
        metrics={platformMetrics}
        onSelect={setPlatformFilter}
        viewLabel={viewLabel}
      />

      <MyKolDiscoveryBridge
        filteredCount={filteredItems.length}
        metric={selectedPlatformMetric}
        onDiscoverPlatform={onDiscoverPlatform}
        platform={platformFilter}
      />

      {message ? <div className="vkpi-my-kol-message">{message}</div> : null}

      {filteredItems.length ? (
        <>
          <section className="vkpi-my-kol-accounts">
            <div className="vkpi-my-kol-accounts__header">
              <div>
                <span>账号层</span>
                <h3>{platformFilter === 'all' ? '我的 KOL 账号' : `${platformFilter} KOL 账号`}</h3>
              </div>
              <div className="vkpi-my-kol-section-actions">
                <strong>{numberFormatter.format(filteredItems.length)} 个账号</strong>
                <button
                  aria-expanded={!accountLayerCollapsed}
                  className="vkpi-my-kol-section-toggle"
                  onClick={() => setAccountLayerCollapsed((value) => !value)}
                  type="button"
                >
                  {accountLayerCollapsed ? '展开' : '折叠'}
                </button>
              </div>
            </div>
            {accountLayerCollapsed ? (
              <div className="vkpi-my-kol-section-collapsed">账号层已折叠 · {numberFormatter.format(filteredItems.length)} 个账号</div>
            ) : (
            <div className="vkpi-my-kol-account-grid">
              {filteredItems.map((item) => {
                const profile = kolProfiles[item.kolId];
                const contacts = contactItems(item, profile, contactOverrides[item.id]);
                const avatar = proxiedImageUrl(textField(profile?.kol, 'avatar_url') || item.avatar);
                const summary = profile?.summary || {};
                const followerLabel = displayCount(summary.follower_count || 0) !== '0' ? displayCount(summary.follower_count) : item.followers;
                const contentLabel = displayCount(summary.content_count || 0) !== '0' ? displayCount(summary.content_count) : item.contentCount;
                const accountPosts = latestSnapshotPosts(kolPosts[item.kolId]?.items?.length ? kolPosts[item.kolId].items : postPreviews(profile), profile);
                const accountInsights = summarizePostInsights(accountPosts, profile);
                const active = selectedItem?.id === item.id;
                return (
                  <article
                    className={`vkpi-my-kol-account-card${active ? ' is-active' : ''}`}
                    key={item.id}
                    onClick={() => setSelectedKolId(item.id)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') setSelectedKolId(item.id);
                    }}
                    role="button"
                    tabIndex={0}
                  >
                    <div className="vkpi-my-kol-account-card__avatar">
                      {avatar ? <img src={avatar} alt="" loading="lazy" /> : <span>{initials(item.name)}</span>}
                    </div>
                    <div className="vkpi-my-kol-account-card__main">
                      <div className="vkpi-my-kol-account-card__title">
                        <h3>{item.name}</h3>
                        <strong>{followerLabel}</strong>
                      </div>
                      <p>{item.handle} · {platformDisplay(item.platform)} · {contentLabel} 内容</p>
                      <div className="vkpi-my-kol-account-card__metrics">
                        <span>播放 {displayCount(accountInsights.totalViews)}</span>
                        <span>评论 {displayCount(accountInsights.totalComments)}</span>
                        <span>互动率 {accountInsights.engagement ? `${accountInsights.engagement.toFixed(2)}%` : '-'}</span>
                      </div>
                      <div className="vkpi-my-kol-account-card__chips">
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            setDevicePopoverId((current) => (current === item.id ? '' : item.id));
                          }}
                        >
                          设备分析
                        </button>
                        <span>V内容 {numberFormatter.format(accountInsights.viltroxCount)}</span>
                        <span>竞品 {numberFormatter.format(accountInsights.competitorCount)}</span>
                        <span>均播 {displayCount(accountInsights.avgViews)}</span>
                      </div>
                      {devicePopoverId === item.id ? (
                        <div className="vkpi-my-kol-account-popover" onClick={(event) => event.stopPropagation()} role="dialog" aria-label={`${item.name} 设备与内容分析`}>
                          <header>
                            <strong>设备与内容分析</strong>
                            <button type="button" onClick={() => setDevicePopoverId('')} aria-label="关闭">×</button>
                          </header>
                          <dl>
                            <div><dt>设备使用</dt><dd>{accountInsights.gearLabel}</dd></div>
                            <div><dt>Viltrox内容</dt><dd>{numberFormatter.format(accountInsights.viltroxCount)}</dd></div>
                            <div><dt>竞品内容</dt><dd>{numberFormatter.format(accountInsights.competitorCount)}</dd></div>
                            <div><dt>其它内容</dt><dd>{numberFormatter.format(accountInsights.otherCount)}</dd></div>
                            <div><dt>最后抓取</dt><dd>{accountInsights.scanLabel}</dd></div>
                            <div><dt>平均播放</dt><dd>{displayCount(accountInsights.avgViews)}</dd></div>
                            <div><dt>互动率</dt><dd>{accountInsights.engagement ? `${accountInsights.engagement.toFixed(2)}%` : '-'}</dd></div>
                          </dl>
                        </div>
                      ) : null}
                      <small>{profile ? '已抓取账号数据' : '待抓取账号数据'} · {item.subStatus} · {item.isFollowed ? '已关注' : '未关注'} · {contacts.length ? `联系 ${contacts.length}` : '待补联系方式'}</small>
                    </div>
                  </article>
                );
              })}
            </div>
            )}
          </section>

          {selectedItem ? (
            <section className="vkpi-my-kol-content-layer">
              <header className="vkpi-my-kol-content-layer__header">
                <div className="vkpi-my-kol-content-layer__identity">
                  <div className="vkpi-my-kol-content-layer__avatar">
                    {selectedAvatar ? <img src={selectedAvatar} alt="" loading="lazy" /> : <span>{initials(selectedItem.name)}</span>}
                  </div>
                  <div>
                    <span>内容层</span>
                    <h3>{selectedItem.name}</h3>
                    <p>{selectedItem.handle} · {selectedFollowerLabel} 粉丝 · {selectedContentLabel} 内容</p>
                  </div>
                </div>
                <div className="vkpi-my-kol-section-actions">
                  <strong>{selectedFollowerLabel}</strong>
                  <button
                    aria-expanded={!contentLayerCollapsed}
                    className="vkpi-my-kol-section-toggle"
                    onClick={() => setContentLayerCollapsed((value) => !value)}
                    type="button"
                  >
                    {contentLayerCollapsed ? '展开' : '折叠'}
                  </button>
                </div>
              </header>

              {contentLayerCollapsed ? (
                <div className="vkpi-my-kol-section-collapsed">内容层已折叠 · {numberFormatter.format(selectedTotalPosts)} 条历史内容</div>
              ) : (
              <>
              <div className="vkpi-my-kol-content-toolbar">
                <div className="vkpi-my-kol-content-toolbar__sort" aria-label="KOL内容排序">
                  {CONTENT_SORT_OPTIONS.map((option) => (
                    <button
                      className={contentSort === option.key ? 'is-active' : ''}
                      key={option.key}
                      onClick={() => setContentSort(option.key)}
                      type="button"
                    >
                      {option.label}
                    </button>
                  ))}
                  <select value={contentWindow} onChange={(event) => setContentWindow(event.target.value as KolContentWindow)} aria-label="时间范围">
                    {CONTENT_WINDOW_OPTIONS.map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}
                  </select>
                  <select value={contentDirection} onChange={(event) => setContentDirection(event.target.value as KolContentDirection)} aria-label="排序方向">
                    <option value="desc">{contentSort === 'latest' ? '最新优先' : '最高优先'}</option>
                    <option value="asc">{contentSort === 'latest' ? '最早优先' : '最低优先'}</option>
                  </select>
                </div>
                <div className="vkpi-my-kol-content-toolbar__actions">
                  <button type="button" onClick={() => startContactEdit(selectedItem)}>{selectedContacts.length ? '编辑联系方式' : '补联系方式'}</button>
                  <button type="button" disabled={scanningKolId === selectedItem.id || !apiToken || !selectedItem.kolId} onClick={() => void scanAccount(selectedItem)}>
                    {scanningKolId === selectedItem.id ? '抓取中' : `抓取${platformDisplay(selectedItem.platform)}`}
                  </button>
                  <button
                    className={selectedItem.isFollowed ? 'is-danger' : ''}
                    disabled={busyKolId === selectedItem.id || !apiToken || !selectedItem.kolId}
                    type="button"
                    onClick={() => void toggleFollow(selectedItem)}
                  >
                    {!selectedItem.kolId ? '缺KOL ID' : selectedItem.isFollowed ? '不关注' : '关注'}
                  </button>
                </div>
                <span>{selectedProfileLoading || selectedPostState?.loading ? '加载中' : selectedPosts.length ? `1-${numberFormatter.format(selectedPosts.length)} / ${numberFormatter.format(selectedTotalPosts)}` : '0 / 0'}</span>
              </div>

              <div className="vkpi-my-kol-content-insights" aria-label="KOL内容分析">
                <span><b>设备使用</b>{selectedPostInsights.gearLabel}</span>
                <span><b>Viltrox内容</b>{numberFormatter.format(selectedPostInsights.viltroxCount)}</span>
                <span><b>竞品内容</b>{numberFormatter.format(selectedPostInsights.competitorCount)}</span>
                <span><b>其它内容</b>{numberFormatter.format(selectedPostInsights.otherCount)}</span>
                <span><b>最后抓取</b>{selectedPostInsights.scanLabel}</span>
                <span><b>平均播放</b>{displayCount(selectedPostInsights.avgViews)}</span>
                <span><b>互动率</b>{selectedPostInsights.engagement ? `${selectedPostInsights.engagement.toFixed(2)}%` : '-'}</span>
              </div>

              <div className="vkpi-my-kol-content-filters" aria-label="内容分类筛选">
                {CONTENT_FILTER_OPTIONS.map((option) => (
                  <button
                    className={contentFilter === option.key ? 'is-active' : ''}
                    key={option.key}
                    onClick={() => setContentFilter(option.key)}
                    type="button"
                  >
                    {option.label}
                  </button>
                ))}
              </div>

              <div className="vkpi-my-kol-content-contacts">
                <span>联系</span>
                {selectedContacts.length ? selectedContacts.map((contact) => (
                  contact.url ? (
                    <a href={contact.url} key={`${contact.label}-${contact.value}`} target="_blank" rel="noreferrer">
                      <b>{contact.label}</b>{compactContactValue(contact.value)}
                    </a>
                  ) : (
                    <em key={`${contact.label}-${contact.value}`}>
                      <b>{contact.label}</b>{compactContactValue(contact.value)}
                    </em>
                  )
                )) : <em><b>暂无</b>未补联系方式</em>}
              </div>

              {editingContactId === selectedItem.id && selectedDraft ? (
                <div className="vkpi-my-kol-contact-editor">
                  <label><span>邮箱</span><input value={selectedDraft.contactEmail} onChange={(event) => setContactDrafts((current) => ({ ...current, [selectedItem.id]: { ...selectedDraft, contactEmail: event.target.value } }))} placeholder="email@example.com" /></label>
                  <label><span>手机号 / WhatsApp</span><input value={selectedDraft.contactPhone} onChange={(event) => setContactDrafts((current) => ({ ...current, [selectedItem.id]: { ...selectedDraft, contactPhone: event.target.value } }))} placeholder="+1 ..." /></label>
                  <label><span>主页</span><input value={selectedDraft.profileUrl} onChange={(event) => setContactDrafts((current) => ({ ...current, [selectedItem.id]: { ...selectedDraft, profileUrl: event.target.value } }))} placeholder="https://..." /></label>
                  <div>
                    <button className="vkpi-my-kol-action" disabled={savingContactId === selectedItem.id || !apiToken || !selectedItem.kolId} type="button" onClick={() => void saveContact(selectedItem)}>保存</button>
                    <button className="vkpi-my-kol-action is-muted" type="button" onClick={() => setEditingContactId('')}>取消</button>
                  </div>
                </div>
              ) : null}

              {selectedPosts.length ? (
                <div className="vkpi-my-kol-content-list">
                  {selectedPosts.map((post) => (
                    <article className="vkpi-my-kol-content-card" key={post.id}>
                      <div
                        className="vkpi-my-kol-content-card__media"
                        onClick={() => setPreviewPost(post)}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' || event.key === ' ') setPreviewPost(post);
                        }}
                        role="button"
                        tabIndex={0}
                      >
                        <span className={`vkpi-my-kol-content-card__badge is-${categoryForPost(post)}`}>
                          {categoryForPost(post) === 'viltrox' ? 'Viltrox相关' : categoryForPost(post) === 'competitor' ? '竞品相关' : '其它内容'}
                        </span>
                        <span className="vkpi-my-kol-content-card__kind">{mediaBadge(post, selectedItem.platform)}</span>
                        <KolMediaSlot post={post} platform={selectedItem.platform} compact />
                      </div>
                      <div className="vkpi-my-kol-content-card__body">
                        <h3 title={post.title}>{conciseText(post.title, 96)}</h3>
                        {post.gearMentions.length ? <p>设备：{post.gearMentions.join(' / ')}</p> : <p>设备待识别</p>}
                        <div className="vkpi-my-kol-content-card__metrics">
                          <span><strong>播放</strong>{displayCount(post.views)}</span>
                          <span><strong>点赞</strong>{displayCount(post.likes)}</span>
                          <button type="button" onClick={() => setCommentPost(post)}><strong>评论</strong>{displayCount(post.comments)}</button>
                          <span><strong>分享</strong>{displayCount(post.shares)}</span>
                        </div>
                      </div>
                      <footer className="vkpi-my-kol-content-card__footer">
                        <small>{compactDate(post.publishedAt)}</small>
                        <div>
                          <button type="button" onClick={() => setCommentPost(post)}>评论明细</button>
                          {post.url ? <a href={post.url} target="_blank" rel="noreferrer">打开原帖</a> : null}
                        </div>
                      </footer>
                    </article>
                  ))}
                </div>
              ) : (
                <div className="vkpi-my-kol-content-empty">
                  <span>{selectedPostState?.loading ? '主页内容加载中。' : '暂无符合筛选条件的主页视频 / 帖子样本。'}</span>
                  <button type="button" onClick={() => void scanAccount(selectedItem)} disabled={scanningKolId === selectedItem.id || !apiToken || !selectedItem.kolId}>
                    {scanningKolId === selectedItem.id ? '抓取中' : '抓取主页内容'}
                  </button>
                </div>
              )}
              {selectedPostState?.error ? <div className="vkpi-my-kol-message">{selectedPostState.error}</div> : null}
              {selectedCommentState?.error ? <div className="vkpi-my-kol-message">{selectedCommentState.error}</div> : null}
              </>
              )}
            </section>
          ) : null}

          {commentPost ? (
            <div className="vkpi-my-kol-comment-modal" role="dialog" aria-modal="true" aria-label="KOL评论明细">
              <section>
                <header>
                  <div>
                    <span>评论层</span>
                    <h3>{conciseText(commentPost.title, 72)}</h3>
                    <p>{displayCount(commentPost.comments)} 条公开评论 · {commentsForPost(commentPost, selectedComments).length} 条正文缓存</p>
                  </div>
                  <button type="button" onClick={() => setCommentPost(null)}>关闭</button>
                </header>
                <div className="vkpi-my-kol-comment-list">
                  {commentsForPost(commentPost, selectedComments).length ? commentsForPost(commentPost, selectedComments).map((comment) => (
                    <article key={comment.id}>
                      <header><strong>{comment.author}</strong><span>赞 {numberFormatter.format(comment.likes)}</span></header>
                      <p>{comment.text}</p>
                      <small>{comment.intentTags.length ? comment.intentTags.join(' / ') : comment.sentiment} · {compactDate(comment.createdAt)}</small>
                    </article>
                  )) : (
                    <div className="vkpi-my-kol-content-empty">
                      <span>{commentPost.comments > 0 ? `评论数已同步：${numberFormatter.format(commentPost.comments)} 条；评论正文还未缓存。` : '当前帖子暂无评论正文缓存。'}</span>
                      <button type="button" onClick={() => selectedItem ? void scanAccount(selectedItem) : undefined} disabled={!selectedItem || scanningKolId === selectedItem.id || !apiToken || !selectedItem.kolId}>
                        {selectedItem && scanningKolId === selectedItem.id ? '抓取中' : '重新抓取评论'}
                      </button>
                    </div>
                  )}
                </div>
                <footer>
                  {commentPost.url ? <a href={commentPost.url} target="_blank" rel="noreferrer">打开原帖</a> : <span />}
                  <button type="button" onClick={() => setCommentPost(null)}>完成</button>
                </footer>
              </section>
            </div>
          ) : null}
          {previewPost && selectedItem ? (
            <KolMediaLightbox post={previewPost} platform={selectedItem.platform} onClose={() => setPreviewPost(null)} />
          ) : null}
        </>
      ) : (
        <div className="vkpi-my-kol-empty">
          {activeView === 'watchlist' ? '当前没有关注 KOL。去搜索红人后点“关注”，这里才会出现。' : '当前漏斗阶段没有 KOL。'}
        </div>
      )}
    </section>
  );
}
