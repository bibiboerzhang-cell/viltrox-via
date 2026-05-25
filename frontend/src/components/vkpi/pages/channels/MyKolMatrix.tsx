import { useEffect, useMemo, useRef, useState } from 'react';
import { claimKol, getKolComments, getKolProfile, releaseKolClaim, scanKolAccount, updateMarketingKol } from '../../../../domains/channels';
import type { VkpiDashboardData, VkpiKolProfile } from '../../vkpiTypes';
import { platformDisplay, safeNumber } from '../../shared/vkpiDataUtils';
import { numberFormatter } from '../../shared/vkpiFormatters';
import { proxiedImageUrl } from '../../shared/mediaProxy';
import { MyKolAccounts } from './MyKolAccounts';
import { MyKolContentLayer } from './MyKolContentLayer';
import { MyKolCommentsModal } from './MyKolCommentsModal';
import { KolMediaLightbox } from './MyKolMedia';
import { MyKolDiscoveryBridge } from './MyKolDiscoveryBridge';
import { MyKolPlatformCards } from './MyKolPlatformCards';
import { buildMyKolItems, buildPlatformMetrics, contactDraftFor, contactItems, displayCount, fetchAllKolPostRows, filterAndSortPosts, funnelStageFor, latestSnapshotPosts, mapCommentRows, mapPostRows, platformFilterFromRaw, postPreviews, searchMatches, summarize, summarizePostInsights, textField } from './myKolMatrixData';
import { FUNNEL_STAGES, PLATFORM_OPTIONS, VIEW_TABS, type ContactDraft, type EffectiveMyKolItem, type FunnelStageKey, type KolCommentState, type KolContentDirection, type KolContentFilter, type KolContentSort, type KolContentWindow, type KolPostState, type MyKolView, type PlatformFilter, type PostPreview } from './myKolMatrixTypes';
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
          <MyKolAccounts
            collapsed={accountLayerCollapsed}
            contactOverrides={contactOverrides}
            devicePopoverId={devicePopoverId}
            items={filteredItems}
            kolPosts={kolPosts}
            kolProfiles={kolProfiles}
            onCloseDevicePopover={() => setDevicePopoverId('')}
            onSelectItem={setSelectedKolId}
            onToggleCollapsed={() => setAccountLayerCollapsed((value) => !value)}
            onToggleDevicePopover={(id) => setDevicePopoverId((current) => (current === id ? '' : id))}
            platformFilter={platformFilter}
            selectedItem={selectedItem}
          />

          {selectedItem ? (
            <MyKolContentLayer
              apiToken={apiToken}
              busyKolId={busyKolId}
              collapsed={contentLayerCollapsed}
              contentDirection={contentDirection}
              contentFilter={contentFilter}
              contentSort={contentSort}
              contentWindow={contentWindow}
              editingContactId={editingContactId}
              onCancelContactEdit={() => setEditingContactId('')}
              onCommentPost={setCommentPost}
              onContactDraftChange={(itemId, draft) => setContactDrafts((current) => ({ ...current, [itemId]: draft }))}
              onContentDirectionChange={setContentDirection}
              onContentFilterChange={setContentFilter}
              onContentSortChange={setContentSort}
              onContentWindowChange={setContentWindow}
              onPreviewPost={setPreviewPost}
              onSaveContact={(item) => void saveContact(item)}
              onScanAccount={(item) => void scanAccount(item)}
              onStartContactEdit={startContactEdit}
              onToggleCollapsed={() => setContentLayerCollapsed((value) => !value)}
              onToggleFollow={(item) => void toggleFollow(item)}
              postInsights={selectedPostInsights}
              posts={selectedPosts}
              savingContactId={savingContactId}
              scanningKolId={scanningKolId}
              selectedAvatar={selectedAvatar}
              selectedCommentState={selectedCommentState}
              selectedContacts={selectedContacts}
              selectedContentLabel={selectedContentLabel}
              selectedDraft={selectedDraft}
              selectedFollowerLabel={selectedFollowerLabel}
              selectedItem={selectedItem}
              selectedPostState={selectedPostState}
              selectedProfileLoading={selectedProfileLoading}
              selectedTotalPosts={selectedTotalPosts}
            />
          ) : null}

          {commentPost ? (
            <MyKolCommentsModal
              apiToken={apiToken}
              comments={selectedComments}
              onClose={() => setCommentPost(null)}
              onRescan={(item) => void scanAccount(item)}
              post={commentPost}
              scanningKolId={scanningKolId}
              selectedItem={selectedItem}
            />
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
