import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, ChevronUp, Search } from 'lucide-react';
import type { OfficialChannelAccount, OfficialChannelPlatform } from '../channels/channelTypes';
import { PlatformLogo } from './platformLogos';
import { useOfficialChannelMatrix } from '../channels/useOfficialChannelMatrix';
import type { VkpiDashboardData, VkpiKolOption, VkpiProjectRow } from '../../vkpiTypes';
import { platformDisplay, safeNumber } from '../../shared/vkpiDataUtils';
import { proxiedImageUrl } from '../../shared/mediaProxy';
import { OfficialContentLayer } from './OfficialContentLayer';
import { EmployeeKolContentLayer } from './EmployeeKolContentLayer';
import { PoolEvidenceContent } from './PoolEvidenceContent';
import type { FunnelStageKey, PlatformFilter } from '../channels/myKolMatrixTypes';
import {
  ACCOUNT_GROUPS,
  COLLAPSE_KEY_OFFICIAL,
  COLLAPSE_KEY_TEAM,
  type StaffCard,
  accountGroupKey,
  compactNumber,
  employeeFunnelStage,
  employeeFunnelStages,
  employeePlatformOrder,
  initials,
  isAdminLike,
  isOwnedMatrixLike,
  platformAccent,
  preferredEmployeeKolItem,
  readCollapse,
  signedNumber,
  staffFocusLine,
  staffIdentityKey,
  statusLabel,
  writeCollapse,
} from './MyKolPage.helpers';

export function Delta({ value, unit = '' }: { value?: number; unit?: string }) {
  const label = signedNumber(value, unit);
  return label ? <small>{label}</small> : null;
}

export function MyKolSkeleton() {
  return (
    <div className="mykol-skeleton-grid" aria-label="MY KOL 加载中">
      {[0, 1, 2, 3].map((item) => (
        <article className="mykol-skeleton-card" key={item}>
          <span />
          <b />
          <i />
        </article>
      ))}
    </div>
  );
}

// 【M2】批量操作条内小按钮的统一内联样式(遵循本文件既有内联样式先例,不动共享 CSS)。
// 色值走 --ds-* token(浅深两主题自适配;此前写死暗色 rgba,浅色下是突兀深灰块)。
const batchBtnStyle: React.CSSProperties = {
  border: '1px solid var(--ds-line-strong)', borderRadius: 8, background: 'var(--ds-panel)',
  color: 'var(--ds-text-2)', padding: '4px 9px', fontSize: 10.5, fontWeight: 700, cursor: 'pointer', whiteSpace: 'nowrap',
};

// A1 直达 KOL Pool 抽屉:全仓同款 localStorage + window 事件管道(照抄 TaskProgressBoard)。
function openManagedKol(kolPoolId: number) {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem('vkpi:pending-kolpool-open-id', String(kolPoolId));
  window.dispatchEvent(new CustomEvent('vkpi:open-kol-pool-item', { detail: { kolPoolId } }));
}

export function TeamMatrix({
  cards,
  pendingCount,
  selectedStaffId,
  onSelectStaff,
}: {
  cards: StaffCard[];
  pendingCount: number;
  selectedStaffId: string | null;
  onSelectStaff: (card: StaffCard) => void;
}) {
  const [page, setPage] = useState(0);
  const [collapsed, setCollapsed] = useState(() => readCollapse(COLLAPSE_KEY_TEAM, false));
  // A1:管 KOL 数字点开的名单(每次只展开一张卡);A4:空卡折叠行的展开态
  const [managedOpenId, setManagedOpenId] = useState<string | null>(null);
  const [showFolded, setShowFolded] = useState(false);
  const toggleCollapsed = () => setCollapsed((value) => {
    const next = !value;
    writeCollapse(COLLAPSE_KEY_TEAM, next);
    return next;
  });
  const allAccounts = cards.flatMap((card) => card.accounts);
  const totalPosts = allAccounts.reduce((sum, account) => sum + safeNumber(account.postsCount), 0);
  const totalFollowers = allAccounts.reduce((sum, account) => sum + safeNumber(account.followers), 0);
  const totalViews = allAccounts.reduce((sum, account) => sum + safeNumber(account.totalViews), 0);
  const totalPostsDelta = allAccounts.reduce((sum, account) => sum + safeNumber(account.postsDelta), 0);
  const totalFollowersDelta = allAccounts.reduce((sum, account) => sum + safeNumber(account.followersDelta), 0);
  const totalViewsDelta = allAccounts.reduce((sum, account) => sum + safeNumber(account.viewsDelta), 0);
  const platformCount = new Set(allAccounts.map((account) => account.platform)).size;
  const contractCount = cards.reduce((sum, card) => sum + card.projects.length, 0);
  // A4:默认只渲染「有账号或有分管 KOL」的负责人卡;其余折叠成一行,点开可看
  const activeCards = cards.filter((card) => card.accounts.length > 0 || (card.managed?.managedKolCount || 0) > 0);
  const foldedCards = cards.filter((card) => !(card.accounts.length > 0 || (card.managed?.managedKolCount || 0) > 0));
  const pageSize = 4;
  const pageCount = Math.max(1, Math.ceil(activeCards.length / pageSize));
  const safePage = Math.min(page, pageCount - 1);
  const visibleCards = activeCards.slice(safePage * pageSize, safePage * pageSize + pageSize);
  const rangeStart = activeCards.length ? safePage * pageSize + 1 : 0;
  const rangeEnd = Math.min((safePage + 1) * pageSize, activeCards.length);

  useEffect(() => {
    setPage(0);
  }, [activeCards.length]);

  return (
    <section className="mykol-panel mykol-team-panel">
      <header className="mykol-section-head">
        <div>
          <h2><span />团队矩阵 <em>/ 负责人进度</em></h2>
        </div>
        <div className="mykol-team-head-right">
          <div className="mykol-chip-row">
            <span><b>{allAccounts.length}</b> 账号</span>
            <span><b>{cards.length}</b> 负责人</span>
            <span><b>{platformCount || '暂无'}</b> 平台</span>
            <span><b>{compactNumber(totalPosts)}</b> 内容 <Delta value={totalPostsDelta} /></span>
            <span><b>{compactNumber(totalFollowers)}</b> 粉丝 <Delta value={totalFollowersDelta} /></span>
            <span><b>{compactNumber(totalViews)}</b> 播放 <Delta value={totalViewsDelta} /></span>
            <span><b>{contractCount || '暂无'}</b> 签约</span>
            <span><b>{pendingCount}</b> 待定</span>
          </div>
          {activeCards.length && !collapsed ? (
            <div className="mykol-team-page-mini" aria-label="团队矩阵分页">
              <span>{rangeStart}-{rangeEnd} / {activeCards.length}</span>
              <button disabled={safePage === 0} type="button" onClick={() => setPage((current) => Math.max(0, current - 1))}>‹</button>
              <b>{safePage + 1}</b>
              <button disabled={safePage >= pageCount - 1} type="button" onClick={() => setPage((current) => Math.min(pageCount - 1, current + 1))}>›</button>
            </div>
          ) : null}
          <button
            className={`mykol-toggle ${collapsed ? '' : 'is-active'}`}
            type="button"
            aria-expanded={!collapsed}
            onClick={toggleCollapsed}
          >
            {collapsed ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
            {collapsed ? '展开矩阵' : '收起矩阵'}
          </button>
        </div>
      </header>
      {cards.length && !collapsed ? (
        <>
          <div className="mykol-team-grid">
          {visibleCards.map((card) => {
            const views = card.accounts.reduce((sum, account) => sum + safeNumber(account.totalViews), 0);
            const viewsDelta = card.accounts.reduce((sum, account) => sum + safeNumber(account.viewsDelta), 0);
            const followers = card.accounts.reduce((sum, account) => sum + safeNumber(account.followers), 0);
            const followersDelta = card.accounts.reduce((sum, account) => sum + safeNumber(account.followersDelta), 0);
            const posts = card.accounts.reduce((sum, account) => sum + safeNumber(account.postsCount), 0);
            const platforms = new Set(card.accounts.map((account) => account.platform)).size;
            const pending = card.accounts.filter((account) => account.syncStatus !== 'synced' && account.syncStatus !== 'official_readonly').length;
            const platformTags = Array.from(new Set(card.accounts.map((account) => account.platform))).slice(0, 4);
            const isSelected = selectedStaffId === card.id;
            // A1/A2:管 KOL = 收藏∪认领∪项目在役 的真实分管数;
            // 有官方账号的卡(Jianbo)粉丝/视频保持全局大数,员工卡显示自己分管 KOL 的聚合。
            const managed = card.managed;
            const managedCount = managed?.managedKolCount || 0;
            const managedOpen = managedOpenId === card.id && Boolean(managed?.managedKols.length);
            const staffFollowers = card.accounts.length ? followers : (managed?.managedFollowers || 0);
            const staffFollowersDelta = card.accounts.length ? followersDelta : 0;
            const videoLabel = card.accounts.length
              ? (posts ? `${compactNumber(posts)} · ${compactNumber(views)}` : '暂无')
              : (managed?.managedVideoCount ? `${compactNumber(managed.managedVideoCount)} 条` : '暂无');
            return (
              <article
                className={`mykol-staff-card mykol-staff-card--clickable ${isSelected ? 'is-selected' : ''}`}
                key={card.id}
                role="button"
                tabIndex={0}
                aria-pressed={isSelected}
                onClick={() => onSelectStaff(card)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    onSelectStaff(card);
                  }
                }}
              >
                <div className="mykol-staff-card__top">
                  <div className="mykol-staff-card__identity">
                    <span
                      className="mykol-avatar"
                      style={card.accent ? { '--avatar-accent': card.accent } as React.CSSProperties : undefined}
                    >
                      {card.avatar ? <img src={proxiedImageUrl(card.avatar)} alt="" /> : initials(card.name)}
                    </span>
                    <div>
                      <h3>
                        {card.name}
                        {isAdminLike(card) ? <em>ADMIN</em> : null}
                      </h3>
                      <p>{card.role || 'KOL Manager'}</p>
                      <small>{staffFocusLine(card)}</small>
                    </div>
                  </div>
                  <div className="mykol-staff-card__reach">
                    <strong>{compactNumber(views)}</strong>
                    {viewsDelta ? <small>{signedNumber(viewsDelta, ' 播放')}</small> : null}
                  </div>
                </div>
                <div className="mykol-staff-stats">
                  <span><em>账号</em><b>{card.accounts.length ? `${card.accounts.length} · ${platforms} 平台` : '暂无'}</b></span>
                  <span><em>粉丝</em><b>{compactNumber(staffFollowers)}</b>{staffFollowersDelta ? <small>{signedNumber(staffFollowersDelta)}</small> : null}</span>
                  <span>
                    <em>管 KOL</em>
                    {managedCount ? (
                      <button
                        className={`mykol-managed-toggle ${managedOpen ? 'is-open' : ''}`}
                        type="button"
                        aria-expanded={managedOpen}
                        title="点击展开分管 KOL 名单"
                        onClick={(event) => {
                          event.stopPropagation();
                          setManagedOpenId((current) => (current === card.id ? null : card.id));
                        }}
                        onKeyDown={(event) => event.stopPropagation()}
                      >
                        {managedCount} 人 {managedOpen ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                      </button>
                    ) : (
                      <b>暂无</b>
                    )}
                    {pending ? <small className="is-warn">{pending} 待定</small> : null}
                  </span>
                  <span><em>KOL 视频</em><b>{videoLabel}</b></span>
                </div>
                {managedOpen && managed ? (
                  <div
                    className="mykol-managed-list"
                    aria-label={`${card.name} 分管 KOL 名单`}
                    role="presentation"
                    onClick={(event) => event.stopPropagation()}
                    onKeyDown={(event) => event.stopPropagation()}
                  >
                    {managed.managedKols.map((kol) => (
                      <button key={kol.kolPoolId} type="button" onClick={() => openManagedKol(kol.kolPoolId)}>
                        <i>{initials(kol.displayName || kol.handle || 'K')}</i>
                        <span>
                          <b>{kol.handle || kol.displayName || '—'}</b>
                          <em>{platformDisplay(kol.platform)}</em>
                        </span>
                        <strong>{kol.fit != null ? `Fit ${kol.fit.toFixed(1)}` : 'Fit —'}</strong>
                      </button>
                    ))}
                    {managed.managedKolCount > managed.managedKols.length ? (
                      <p>共 {managed.managedKolCount} 人 · 显示 fit 最高的前 {managed.managedKols.length} 位</p>
                    ) : null}
                  </div>
                ) : null}
                <div className="mykol-platform-tags">
                  {platformTags.map((platform) => (
                    <span
                      key={platform}
                      style={{ '--tag-accent': platformAccent[platform] || 'var(--ds-accent-2)' } as React.CSSProperties}
                    >
                      {platformDisplay(platform)}
                    </span>
                  ))}
                  {!platformTags.length ? <span>账号暂无</span> : null}
                </div>
              </article>
            );
          })}
          </div>
          {activeCards.length ? (
            <footer className="mykol-team-pagination">
              <span>显示 {rangeStart}-{rangeEnd} / {activeCards.length} 名负责人</span>
              <div>
                <button disabled={safePage === 0} type="button" onClick={() => setPage((current) => Math.max(0, current - 1))}>上一页</button>
                <b>{safePage + 1} / {pageCount}</b>
                <button disabled={safePage >= pageCount - 1} type="button" onClick={() => setPage((current) => Math.min(pageCount - 1, current + 1))}>下一页</button>
              </div>
            </footer>
          ) : (
            <div className="mykol-empty">负责人均暂无账号或分管 KOL。</div>
          )}
          {/* A4:无账号且无分管 KOL 的负责人默认折叠成一行,点开可见(不渲染成空幻影卡) */}
          {foldedCards.length ? (
            <div className="mykol-team-folded">
              <button
                type="button"
                aria-expanded={showFolded}
                onClick={() => setShowFolded((value) => !value)}
              >
                另 {foldedCards.length} 人暂无分管{showFolded ? '(收起)' : '(点开)'}
              </button>
              {showFolded ? (
                <div className="mykol-team-folded__chips">
                  {foldedCards.map((card) => (
                    <span key={card.id}>
                      <i>{initials(card.name)}</i>
                      {card.name}
                      <em>{card.role || 'KOL Manager'}</em>
                    </span>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}
        </>
      ) : !cards.length ? (
        <div className="mykol-empty">暂无团队账号矩阵。等待官方矩阵接口返回 staff 归属。</div>
      ) : null}
    </section>
  );
}

export function OfficialMatrix({
  apiToken,
  matrix,
  selectedPlatform,
  selectedAccountId,
  onSelectPlatform,
  onSelectAccount,
}: {
  apiToken?: string;
  matrix: ReturnType<typeof useOfficialChannelMatrix>;
  selectedPlatform: OfficialChannelPlatform | undefined;
  selectedAccountId: number | null;
  onSelectPlatform: (platform: string) => void;
  onSelectAccount: (account: OfficialChannelAccount) => void;
}) {
  const syncedAccounts = matrix.platforms.flatMap((platform) => platform.accounts).filter((account) => (
    account.syncStatus === 'synced' || account.syncStatus === 'official_readonly'
  )).length;
  const totalFollowers = matrix.platforms.reduce((sum, platform) => sum + safeNumber(platform.totalFollowers), 0);
  const totalPostsDelta = matrix.platforms.reduce((sum, platform) => sum + safeNumber(platform.postsDelta), 0);
  const totalFollowersDelta = matrix.platforms.reduce((sum, platform) => sum + safeNumber(platform.followersDelta), 0);
  const totalViewsDelta = matrix.platforms.reduce((sum, platform) => sum + safeNumber(platform.viewsDelta), 0);
  const avgViews = matrix.postCount ? Math.round(matrix.totalViews / matrix.postCount) : 0;
  const [collapsed, setCollapsed] = useState(() => readCollapse(COLLAPSE_KEY_OFFICIAL, false));
  const toggleCollapsed = () => setCollapsed((value) => {
    const next = !value;
    writeCollapse(COLLAPSE_KEY_OFFICIAL, next);
    return next;
  });

  const groupedAccounts = useMemo(() => {
    const accounts = selectedPlatform?.accounts || [];
    return ACCOUNT_GROUPS.map((group) => ({
      ...group,
      accounts: accounts.filter((account) => accountGroupKey(account) === group.key),
    })).filter((group) => group.accounts.length);
  }, [selectedPlatform]);

  return (
    <section className="mykol-panel mykol-official-panel">
      <header className="mykol-section-head">
        <div>
          <h2><span className="is-blue" />官方账号矩阵 <em>/ 平台总览</em></h2>
        </div>
        <div className="mykol-official-head-right">
          <div className="mykol-chip-row">
            <span><b>{matrix.accountCount}</b> 账号</span>
            <span><b>{syncedAccounts}</b> 已同步</span>
            <span><b>{matrix.platforms.length}</b> 平台</span>
            <span><b>{compactNumber(matrix.postCount)}</b> 内容 <Delta value={totalPostsDelta} /></span>
            <span><b>{compactNumber(totalFollowers)}</b> 粉丝 <Delta value={totalFollowersDelta} /></span>
            <span><b>{compactNumber(matrix.totalViews)}</b> 播放 <Delta value={totalViewsDelta} /></span>
            <span><b>{compactNumber(avgViews)}</b> 篇均</span>
          </div>
          <button
            className={`mykol-toggle ${collapsed ? '' : 'is-active'}`}
            type="button"
            aria-expanded={!collapsed}
            onClick={toggleCollapsed}
          >
            {collapsed ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
            {collapsed ? '展开矩阵' : '收起矩阵'}
          </button>
        </div>
      </header>
      {matrix.loading && !matrix.platforms.length ? <MyKolSkeleton /> : null}
      {matrix.error ? <div className="mykol-warning">{matrix.error}</div> : null}
      {collapsed ? (
        matrix.platforms.length ? (
          <div className="mykol-platform-summary" aria-label="官方账号矩阵速览">
            {matrix.platforms.slice(0, 6).map((platform) => (
              <button
                className="mykol-platform-summary__item"
                key={platform.platform}
                type="button"
                onClick={() => { onSelectPlatform(platform.platform); toggleCollapsed(); }}
              >
                <PlatformLogo platform={platform.platform} size={18} />
                <b>{platform.label}</b>
                <em>{platform.accounts.length} 账号 · {compactNumber(platform.totalPosts)} 内容</em>
              </button>
            ))}
          </div>
        ) : null
      ) : (
        <>
          <div className="mykol-platform-grid mykol-platform-grid--compact">
            {matrix.platforms.map((platform) => {
              const accent = platformAccent[platform.platform] || 'var(--ds-accent-2)';
              const active = selectedPlatform?.platform === platform.platform;
              return (
                <button
                  className={`mykol-platform-card mykol-platform-card--compact ${active ? 'is-active' : ''}`}
                  key={platform.platform}
                  style={{ '--accent': accent } as React.CSSProperties}
                  type="button"
                  onClick={() => onSelectPlatform(platform.platform)}
                >
                  <span className="mykol-platform-icon"><PlatformLogo platform={platform.platform} size={24} /></span>
                  <b>{platform.label}</b>
                  <em>{platform.accounts.length} 账号 · {compactNumber(platform.totalPosts)} 内容</em>
                  <strong>{platform.viewsUnavailable ? '—' : compactNumber(platform.totalViews)}</strong>
                  <small>{platform.viewsUnavailable ? platform.viewsUnavailableReason || '公开播放不可用' : `${compactNumber(platform.totalFollowers)} 粉丝`}</small>
                  <i>{platform.baselineProtected ? '基线保护' : statusLabel(platform.accounts[0]?.syncStatus || '')}</i>
                </button>
              );
            })}
          </div>
          <div className="mykol-drill-grid">
            <div className="mykol-account-list">
              {groupedAccounts.map((group) => (
                <div className="mykol-account-group" key={group.key}>
                  <div className="mykol-account-group__head">{group.label} <b>{group.accounts.length}</b></div>
                  {group.accounts.map((account) => (
                    <button
                      className={`mykol-account-row ${selectedAccountId === account.id ? 'is-active' : ''}`}
                      key={account.id}
                      type="button"
                      onClick={() => onSelectAccount(account)}
                    >
                      <span className="mykol-avatar is-small">{account.avatarUrl ? <img src={proxiedImageUrl(account.avatarUrl)} alt="" /> : initials(account.displayName)}</span>
                      <span>
                        <b>{account.displayName}</b>
                        <em>{account.handle || '官方账号'} · {statusLabel(account.syncStatus)} · {compactNumber(account.postsCount)} 内容</em>
                      </span>
                      <strong>{account.viewsUnavailable ? '无公开播放' : compactNumber(account.totalViews)}</strong>
                    </button>
                  ))}
                </div>
              ))}
            </div>
            <OfficialContentLayer
              account={(selectedPlatform?.accounts || []).find((account) => account.id === selectedAccountId)}
              apiToken={apiToken}
            />
          </div>
        </>
      )}
    </section>
  );
}

export function EmployeeKolLibrary({
  apiToken,
  data,
  viewMode,
  staffFilter,
  onClearStaffFilter,
  onRefreshData,
}: {
  apiToken?: string;
  data: VkpiDashboardData;
  viewMode: 'manager' | 'employee';
  staffFilter?: { id: string; name: string } | null;
  onClearStaffFilter?: () => void;
  onRefreshData?: () => void;
}) {
  const [query, setQuery] = useState('');
  const [platformFilter, setPlatformFilter] = useState<PlatformFilter>('all');
  const [selectedKolId, setSelectedKolId] = useState('');
  const [activeView, setActiveView] = useState<'watchlist' | 'funnel'>('watchlist');
  const [activeFunnelStage, setActiveFunnelStage] = useState<FunnelStageKey>('claimed');
  const [viltroxOnly, setViltroxOnly] = useState(true);
  const [funnelCollapsed, setFunnelCollapsed] = useState(true);
  const projectByKol = useMemo(() => {
    const grouped = new Map<string, VkpiProjectRow[]>();
    data.projects.forEach((project) => {
      const key = project.kolId || `${project.platform}:${project.kolHandle}`;
      grouped.set(key, [...(grouped.get(key) || []), project]);
    });
    return grouped;
  }, [data.projects]);

  // C4-full(裁决重做):Pool 收藏直接并入库的关注列表——库即收藏的家,
  // 点开复用右侧内容层看"该 KOL 的视频"(Pool 行走 evidence 数据源)。
  // P5:关注列表此前只从 dashboard 的 kolOptions 派生,收藏永远进不来。
  // 改走统一只读端点 /api/admin/vkpi/my-kol/aggregate(pool_favorites 即真收藏源)。
  // aggregate 返回 projects(已解析数组),回填 projects_json 以兼容下游消费。
  const [poolFavorites, setPoolFavorites] = useState<Array<Record<string, unknown>>>([]);
  const [favError, setFavError] = useState('');
  const [favPage, setFavPage] = useState<{ total?: number; has_more?: boolean; next_cursor?: string | null }>();
  const [favLoadingMore, setFavLoadingMore] = useState(false);
  const [favReloadTick, setFavReloadTick] = useState(0);
  const [exporting, setExporting] = useState(false);
  // 【M3】认领可视化+释放:aggregate 同一响应就带本人 claims(vkpi_kol_claims,FK kols.id)——
  // 顺手收下,行内据此显示到期时间;释放走 /api/marketing/claims/{id}/release(后端限本人/管理层)。
  const [myClaims, setMyClaims] = useState<Array<Record<string, unknown>>>([]);
  const [releasingId, setReleasingId] = useState('');
  const [claimNote, setClaimNote] = useState('');
  // 【M3】乐观更新:点「释放」立即按已释放渲染(不等 aggregate/父级数据回流),失败回滚。
  // kolOptions 的 activeClaimId 来自 dashboard 数据源,刷新有延迟——本地集合先行遮蔽。
  const [releasedClaimIds, setReleasedClaimIds] = useState<Set<string>>(new Set());
  // 【M2】批量操作:多选模式(勾选行 → 批量入项目 / 批量导出 CSV / 批量生成受众画像)。
  // 全部复用已有端点(POST /projects/{id}/kols、audience-stats/refresh),零新后端;
  // 入项目与受众画像按 kol_pool_id 寻址,只对已落 Pool 的行生效,无 poolId 的行如实跳过并计数。
  const [batchMode, setBatchMode] = useState(false);
  const [batchSelected, setBatchSelected] = useState<Set<string>>(new Set());
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchNote, setBatchNote] = useState('');
  const [batchProjectId, setBatchProjectId] = useState('');
  // 【M2】项目下拉:dashboard 项目行 id 即 vkpi_projects.id(projectRows 口径),去重 +
  // 只留数字 id(POST /projects/{id}/kols 路由参数是 int,project_uid 兜底行进不了该端点)。
  const batchProjectOptions = useMemo(() => {
    const seen = new Set<string>();
    return data.projects.filter((project) => {
      const id = String(project.id || '');
      if (!id || seen.has(id) || !Number.isFinite(Number(id))) return false;
      seen.add(id);
      return true;
    });
  }, [data.projects]);
  useEffect(() => {
    if (!apiToken) return;
    let cancelled = false;
    import('../../../../services/vkpi/kol-api').then(({ getMyKolAggregate }) =>
      getMyKolAggregate(apiToken, { mode: 'summary', favoritesLimit: 50 }).then((resp) => {
        if (cancelled) return;
        const favorites = (resp.pool_favorites || []).map((fav) => {
          const row = fav as Record<string, unknown>;
          // 下游按 projects_json(字符串或数组)消费;aggregate 已给 projects 数组,回填之。
          return row.projects_json != null ? row : { ...row, projects_json: row.projects ?? [] };
        });
        setPoolFavorites(favorites as Array<Record<string, unknown>>);
        setFavPage(resp.pool_favorites_page);
        setMyClaims(Array.isArray(resp.claims) ? (resp.claims as Array<Record<string, unknown>>) : []);
        setFavError('');
      }),
    ).catch((err) => { if (!cancelled) setFavError(String((err as Error)?.message || '收藏读取失败').slice(0, 100)); });
    return () => { cancelled = true; };
  }, [apiToken, favReloadTick]);

  const loadMoreFavorites = async () => {
    const cursor = favPage?.next_cursor;
    if (!apiToken || !cursor || favLoadingMore) return;
    setFavLoadingMore(true);
    setFavError('');
    try {
      const { getMyKolAggregate } = await import('../../../../services/vkpi/kol-api');
      const resp = await getMyKolAggregate(apiToken, {
        mode: 'summary',
        favoritesLimit: 50,
        favoritesCursor: cursor,
      });
      const favorites = (resp.pool_favorites || []).map((fav) => {
        const row = fav as Record<string, unknown>;
        return row.projects_json != null ? row : { ...row, projects_json: row.projects ?? [] };
      });
      setPoolFavorites((current) => {
        const seen = new Set(current.map((row) => String(row.kol_pool_id ?? row.id ?? '')));
        return [...current, ...favorites.filter((row) => !seen.has(String(row.kol_pool_id ?? row.id ?? '')))];
      });
      setFavPage(resp.pool_favorites_page);
    } catch (err) {
      setFavError(String((err as Error)?.message || '更多收藏加载失败').slice(0, 100));
    } finally {
      setFavLoadingMore(false);
    }
  };

  // 【M3】本人 active 认领按 kol_id 建索引(claims FK 是 kols.id,与 kolOptions 行的 id 同源)。
  const myClaimByKolId = useMemo(() => {
    const map = new Map<string, Record<string, unknown>>();
    myClaims.forEach((claim) => {
      if (String(claim.status || '').toLowerCase() !== 'active') return;
      const kolId = String(claim.kol_id ?? '');
      if (kolId) map.set(kolId, claim);
    });
    return map;
  }, [myClaims]);

  // 【M3】释放认领:确认弹窗 → 乐观按已释放渲染 → release 端点 → 重拉 aggregate(claims/收藏)
  // + 父级刷新(kolOptions 的 activeClaimId 来自 dashboard 数据源)。失败回滚乐观态并显示后端原因。
  const releaseClaim = async (claimId: string, kolName: string) => {
    if (!apiToken || !claimId || releasingId) return;
    if (!window.confirm(`确认释放对「${kolName}」的认领?释放后该 KOL 可被其他成员认领。`)) return;
    setReleasingId(claimId);
    setClaimNote('');
    // 乐观更新:先把这条认领在本地遮蔽掉(行内状态/释放按钮立即消失)。
    setReleasedClaimIds((prev) => { const next = new Set(prev); next.add(claimId); return next; });
    try {
      const { releaseKolClaim } = await import('../../../../services/vkpi/kol-api');
      await releaseKolClaim(apiToken, claimId);
      setClaimNote(`已释放「${kolName}」的认领`);
      setFavReloadTick((tick) => tick + 1);
      onRefreshData?.();
    } catch (err) {
      // 回滚乐观态:释放失败,认领状态复原。
      setReleasedClaimIds((prev) => { const next = new Set(prev); next.delete(claimId); return next; });
      setClaimNote(`释放失败:${String((err as Error)?.message || '请重试').slice(0, 80)}`);
    } finally {
      setReleasingId('');
    }
  };

  // P5:收藏在 KOL Pool 页切换,本页重获焦点/可见时重拉,保证收藏即时进库。
  useEffect(() => {
    if (!apiToken) return;
    const reload = () => setFavReloadTick((tick) => tick + 1);
    const onVisibility = () => { if (document.visibilityState === 'visible') reload(); };
    window.addEventListener('focus', reload);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      window.removeEventListener('focus', reload);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [apiToken]);

  const items = useMemo(() => {
    const base = data.kolOptions.filter((kol) => !isOwnedMatrixLike(kol)).map((kol) => {
      const projects = projectByKol.get(kol.id) || projectByKol.get(`${kol.platform}:${kol.handle}`) || [];
      // 【M2】email 只供批量导出 CSV 用(导出时按读端口径脱敏),行内展示不消费。
      return { kol, projects, funnelStage: employeeFunnelStage(projects), poolId: null as number | null, poolFit: null as number | null, isShared: false, sharedByName: '', email: String(kol.contactEmail || '') };
    });
    const seen = new Set(base.map((item) => `${String(item.kol.platform).toLowerCase()}:${String(item.kol.handle || '').toLowerCase()}`));
    const projectByKeyLower = new Map<string, VkpiProjectRow[]>();
    projectByKol.forEach((rows, key) => projectByKeyLower.set(String(key).toLowerCase(), rows));
    const pool = poolFavorites.map((fav) => {
      const platformLabel = platformDisplay(String(fav.platform || '')) as VkpiKolOption['platform'];
      const handle = String(fav.handle || '');
      const kol = {
        id: `pool:${fav.kol_pool_id}`,
        name: String(fav.display_name || handle || '—'),
        handle,
        platform: platformLabel,
        avatar: String(fav.avatar_url || '') || undefined,
        profileUrl: String(fav.profile_url || '') || undefined,
        followerLabel: fav.followers != null ? Number(fav.followers).toLocaleString() : undefined,
      } as unknown as VkpiKolOption;
      // ②(裁令重修):合作结果以后端直连 assignments(projects_json)为准——dashboard
      // 项目行只有主 KOL,平台:handle 匹配对 769 收藏几乎全 miss(浏览器实证 0 合作)。
      // 波5 R1(2026-06-12 体检):psycopg/json_agg 路径下 projects_json 以字符串到达,
      // 按数组判空恒 false 致合作直连失效——字符串先 parse。
      const rawProjectsJson = typeof fav.projects_json === 'string'
        ? (() => { try { return JSON.parse(fav.projects_json as string); } catch { return []; } })()
        : fav.projects_json;
      const rawAssignments = Array.isArray(rawProjectsJson) ? (rawProjectsJson as Array<Record<string, unknown>>) : [];
      const enriched = projectByKeyLower.get(`${String(platformLabel).toLowerCase()}:${handle.toLowerCase()}`) || [];
      const synth = rawAssignments.map((asg) => {
        const matched = enriched.find((row) => String(row.id) === String(asg.project_id));
        const stageRaw = String(asg.stage || '');
        const stage = stageRaw === 'device_sent' ? 'shipped' : stageRaw === 'content_posted' ? 'content_published' : stageRaw;
        return matched || ({
          id: String(asg.project_id),
          campaign: String(asg.project_name || `项目 ${asg.project_id}`),
          stage,
          views: null, clicks: null, orders: null, gmv: null, cost: null, roi: null,
        } as unknown as VkpiProjectRow);
      });
      const projects = synth.length ? synth : enriched;
      // 【M2】批量导出取邮箱:aggregate 的 contacts 里挑第一条邮箱类联系方式(导出时再脱敏)。
      const contactsRaw = Array.isArray(fav.contacts) ? (fav.contacts as Array<Record<string, unknown>>) : [];
      const emailContact = contactsRaw.find((contact) => (
        String(contact?.contact_type || '').toLowerCase().includes('email') || String(contact?.contact_value || '').includes('@')
      ));
      // 【M5】共享来源:aggregate 的 is_shared=true 表示这行不是本人收藏,而是经 P-GROUP-7 共享池
      // (vkpi_kol_pool_members)共享给我的只读可见行。shared_by_name = 共享人展示名
      // (aggregate 已 JOIN staff→users 回传;空 = 旧共享行 shared_by 可空容旧,如实只标来源)。
      return { kol, projects, funnelStage: employeeFunnelStage(projects), poolId: Number(fav.kol_pool_id), poolFit: fav.viltrox_fit_score == null ? null : Number(fav.viltrox_fit_score), isShared: Boolean(fav.is_shared), sharedByName: String(fav.shared_by_name || ''), email: String(emailContact?.contact_value || '') };
    }).filter((item) => !seen.has(`${String(item.kol.platform).toLowerCase()}:${String(item.kol.handle || '').toLowerCase()}`));
    return [...base, ...pool].sort((left, right) => {
      const leftProjectScore = left.projects.length + (left.kol.activeClaimId ? 1 : 0);
      const rightProjectScore = right.projects.length + (right.kol.activeClaimId ? 1 : 0);
      if (rightProjectScore !== leftProjectScore) return rightProjectScore - leftProjectScore;
      return (left.kol.name || left.kol.handle || '').localeCompare(right.kol.name || right.kol.handle || '');
    });
  }, [data.kolOptions, projectByKol, poolFavorites]);
  const funnelCounts = useMemo(() => employeeFunnelStages.reduce<Record<FunnelStageKey, number>>((counts, stage) => {
    counts[stage.key] = items.filter((item) => item.funnelStage === stage.key).length;
    return counts;
  }, { claimed: 0, contacted: 0, replied: 0, agreed: 0, shipped: 0, received: 0, published: 0, measured: 0 }), [items]);
  const maxFunnelCount = Math.max(1, ...employeeFunnelStages.map((stage) => funnelCounts[stage.key]));
  const activeItems = useMemo(() => (
    activeView === 'funnel' ? items.filter((item) => item.funnelStage === activeFunnelStage) : items
  ), [activeFunnelStage, activeView, items]);
  const platformStats = useMemo(() => {
    return employeePlatformOrder.map((platform) => {
      const platformItems = activeItems.filter((item) => item.kol.platform === platform);
      return {
        platform,
        stats: {
          kols: platformItems.length,
          projects: platformItems.reduce((sum, item) => sum + item.projects.length, 0),
          views: platformItems.reduce((sum, item) => sum + item.projects.reduce((inner, project) => inner + safeNumber(project.views), 0), 0),
        },
      };
    });
  }, [activeItems]);
  // D:点团队卡 → 按负责人过滤库。项目带 ownerId/ownerName,按 id 或归一姓名匹配。
  const staffNameKey = staffFilter ? staffIdentityKey(staffFilter.name) : '';
  const filteredItems = useMemo(() => activeItems.filter(({ kol, projects }) => {
    const matchesPlatform = platformFilter === 'all' || kol.platform === platformFilter;
    const haystack = `${kol.name} ${kol.handle}`.toLowerCase();
    const matchesStaff = !staffFilter || projects.some((project) => (
      (project.ownerId && project.ownerId === staffFilter.id) ||
      (project.ownerName && staffIdentityKey(project.ownerName) === staffNameKey)
    ));
    return matchesPlatform && matchesStaff && haystack.includes(query.trim().toLowerCase());
  }), [activeItems, platformFilter, query, staffFilter, staffNameKey]);
  const preferredItem = preferredEmployeeKolItem(filteredItems);
  const selectedItem = filteredItems.find((item) => item.kol.id === selectedKolId) || preferredItem;
  const totalViews = items.reduce((sum, item) => (
    sum + item.projects.reduce((inner, project) => inner + safeNumber(project.views), 0)
  ), 0);
  const totalClicks = items.reduce((sum, item) => (
    sum + item.projects.reduce((inner, project) => inner + safeNumber(project.clicks), 0)
  ), 0);

  // 【M2】批量选择:batchSelected 可能残留已被过滤/下架的 id,计数与执行都按当前 items 求交。
  const batchSelectedItems = useMemo(() => items.filter((item) => batchSelected.has(item.kol.id)), [batchSelected, items]);
  const allVisibleSelected = filteredItems.length > 0 && filteredItems.every((item) => batchSelected.has(item.kol.id));
  const toggleBatchSelect = (kolId: string) => {
    setBatchSelected((prev) => {
      const next = new Set(prev);
      if (next.has(kolId)) next.delete(kolId); else next.add(kolId);
      return next;
    });
  };
  const toggleSelectAllVisible = () => {
    setBatchSelected((prev) => {
      const next = new Set(prev);
      if (allVisibleSelected) filteredItems.forEach((item) => next.delete(item.kol.id));
      else filteredItems.forEach((item) => next.add(item.kol.id));
      return next;
    });
  };

  // 【M2】批量入项目:复用已有 POST /projects/{id}/kols,逐个调用便于逐条进度与部分失败统计
  // (后端幂等:重复入项返回 skipped 不炸)。完成后重拉收藏 + 父级刷新,合作漏斗立即吃到新 stage。
  const runBatchAddToProject = async () => {
    if (!apiToken || batchBusy) return;
    if (!batchProjectId) { setBatchNote('请先在下拉里选择目标项目'); return; }
    const poolTargets = batchSelectedItems.filter((item) => item.poolId);
    const skipped = batchSelectedItems.length - poolTargets.length;
    if (!poolTargets.length) { setBatchNote('选中行均未落 Pool(入项目按 kol_pool_id 寻址),无可入项'); return; }
    setBatchBusy(true);
    let ok = 0; let fail = 0; let firstError = '';
    try {
      const { addKolsToProject } = await import('../../../../services/vkpi/projects-api');
      for (let index = 0; index < poolTargets.length; index += 1) {
        setBatchNote(`批量入项目中… ${index + 1}/${poolTargets.length}`);
        try {
          await addKolsToProject(apiToken, batchProjectId, [String(poolTargets[index].poolId)]);
          ok += 1;
        } catch (err) {
          fail += 1;
          if (!firstError) firstError = String((err as Error)?.message || '').slice(0, 60);
        }
      }
    } catch (err) {
      setBatchBusy(false);
      setBatchNote(`批量入项目失败:${String((err as Error)?.message || '请重试').slice(0, 80)}`);
      return;
    }
    setBatchBusy(false);
    setBatchNote(`批量入项目完成:成功 ${ok} 个${fail ? ` · 失败 ${fail} 个${firstError ? `(首个原因:${firstError})` : ''}` : ''}${skipped ? ` · 跳过 ${skipped} 个未落 Pool 的行` : ''}`);
    if (ok) { setFavReloadTick((tick) => tick + 1); onRefreshData?.(); }
  };

  // 【M2】批量导出 CSV:纯前端拼装下载(名字/平台/handle/粉丝/fit/邮箱),
  // 邮箱照读端 pool_common._mask_email 口径脱敏(e***@d***),真值只走审计端点。
  const runBatchExportCsv = async () => {
    if (!batchSelectedItems.length || batchBusy) return;
    try {
      const { buildKolCsv, downloadCsv } = await import('./myKolBatch');
      const csv = buildKolCsv(batchSelectedItems.map((item) => ({
        name: item.kol.name || item.kol.handle || '',
        platform: String(item.kol.platform || ''),
        handle: item.kol.handle || '',
        followers: item.kol.followerLabel || '',
        fit: item.poolFit,
        email: item.email,
      })));
      downloadCsv(`my-kol-selected-${new Date().toISOString().slice(0, 10)}.csv`, csv);
      setBatchNote(`已导出 ${batchSelectedItems.length} 行 CSV(邮箱已脱敏)`);
    } catch (err) {
      setBatchNote(`导出失败:${String((err as Error)?.message || '请重试').slice(0, 80)}`);
    }
  };

  // 【M2】批量生成受众画像:复用已有 audience-stats/refresh 逐个入队
  // (YouTube 可能同步算完,IG/TT 评论不足时后端自动入队抓评论,非 error 均按「已入队」计)。
  const runBatchAudience = async () => {
    if (!apiToken || batchBusy) return;
    const poolTargets = batchSelectedItems.filter((item) => item.poolId);
    const skipped = batchSelectedItems.length - poolTargets.length;
    if (!poolTargets.length) { setBatchNote('选中行均未落 Pool(受众画像按 kol_pool_id 寻址),无可入队'); return; }
    if (poolTargets.length > 10 && !window.confirm(`选中 ${poolTargets.length} 个 KOL,受众画像逐个刷新可能耗时较久,确认继续?`)) return;
    setBatchBusy(true);
    let ok = 0; let fail = 0; let firstReason = '';
    try {
      const { refreshAudienceStats } = await import('../../../../services/vkpi/kolPool-api');
      for (let index = 0; index < poolTargets.length; index += 1) {
        setBatchNote(`受众画像入队中… ${index + 1}/${poolTargets.length}`);
        try {
          const resp = await refreshAudienceStats(apiToken, Number(poolTargets[index].poolId));
          if (String(resp?.status || '') === 'error') {
            fail += 1;
            if (!firstReason) firstReason = String(resp?.reason || '').slice(0, 60);
          } else {
            ok += 1;
          }
        } catch (err) {
          fail += 1;
          if (!firstReason) firstReason = String((err as Error)?.message || '').slice(0, 60);
        }
      }
    } catch (err) {
      setBatchBusy(false);
      setBatchNote(`受众画像入队失败:${String((err as Error)?.message || '请重试').slice(0, 80)}`);
      return;
    }
    setBatchBusy(false);
    setBatchNote(`已入队 ${ok} 个${fail ? ` · 失败 ${fail} 个${firstReason ? `(首个原因:${firstReason})` : ''}` : ''}${skipped ? ` · 跳过 ${skipped} 个未落 Pool 的行` : ''}`);
  };

  // 波5 R2(2026-06-12 体检):泳道回跳的 pool 定位曾被本重置效应吃掉(收藏未并入列表时
  // pool:N 不在 filteredItems → 被重置到首项)。pending 持引用,目标行出现才消费;持留 15s 兜底。
  const pendingPoolRef = useRef<{ id: string; until: number } | null>(null);
  useEffect(() => {
    const consume = () => {
      try {
        const pending = window.localStorage.getItem('vkpi:pending-mykol-pool-id');
        if (!pending) return;
        window.localStorage.removeItem('vkpi:pending-mykol-pool-id');
        pendingPoolRef.current = { id: `pool:${pending}`, until: Date.now() + 15000 };
        setActiveView('watchlist');
        // P5:跳转目标 pool 行必须在场——重拉收藏,避免刚收藏的人不在列表里。
        setFavReloadTick((tick) => tick + 1);
      } catch { /* localStorage 不可用忽略 */ }
    };
    consume();
    window.addEventListener('vkpi:open-mykol-kol', consume);
    return () => window.removeEventListener('vkpi:open-mykol-kol', consume);
  }, []);
  useEffect(() => {
    const pending = pendingPoolRef.current;
    if (pending && Date.now() > pending.until) pendingPoolRef.current = null;
    if (pendingPoolRef.current) {
      const target = pendingPoolRef.current.id;
      if (filteredItems.some((item) => item.kol.id === target)) {
        pendingPoolRef.current = null;
        setSelectedKolId(target);
        return;
      }
      // 目标未到场:持留期间不做默认重置,等收藏并入
      if (filteredItems.length) return;
    }
    if (!filteredItems.length) {
      setSelectedKolId('');
      return;
    }
    if (!filteredItems.some((item) => item.kol.id === selectedKolId)) {
      setSelectedKolId(preferredEmployeeKolItem(filteredItems).kol.id);
    }
  }, [filteredItems, selectedKolId]);

  return (
    <section className="mykol-panel mykol-employee-panel">
      <header className="mykol-section-head">
        <div>
          <h2><span className="is-cyan" />{viewMode === 'manager' ? 'MY KOL 库' : '我的 KOL 库'} <em>/ 真实项目与自然人(预览)</em></h2>
          <p>{viewMode === 'manager' ? '管理层视角 · 当前用现有 KOL/project 数据过渡' : '我的视角 · 当前仅展示可见 KOL 与项目内容'}</p>
        </div>
        <div className="mykol-chip-row">
          <span><b>{items.length}</b> Total KOL</span>
          <span><b>{data.projects.length}</b> 项目</span>
          <span><b>{compactNumber(totalViews)}</b> Viltrox 播放</span>
          <span><b>{compactNumber(totalClicks)}</b> Viltrox 点击</span>
        </div>
      </header>
      {staffFilter ? (
        <div className="mykol-staff-filter-bar">
          <span>正按负责人筛选 · <b>{staffFilter.name}</b> · {filteredItems.length} KOL</span>
          <button type="button" onClick={() => onClearStaffFilter?.()}>清除筛选</button>
        </div>
      ) : null}
      <div className="mykol-library-toolbar">
        <div className="mykol-tabs">
          <button
            className={activeView === 'watchlist' ? 'is-active' : ''}
            type="button"
            onClick={() => {
              setActiveView('watchlist');
              setFunnelCollapsed(true);
            }}
          >
            关注列表 <b>{items.length}</b>
          </button>
          <button
            className={activeView === 'funnel' ? 'is-active' : ''}
            type="button"
            onClick={() => {
              setActiveView('funnel');
              setFunnelCollapsed(false);
            }}
          >
            合作漏斗 <b>{items.length}</b>
          </button>
        </div>
        <label className="mykol-search">
          <Search size={15} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索 KOL / handle" />
        </label>
        <button
          className={`mykol-toggle ${funnelCollapsed ? '' : 'is-active'}`}
          type="button"
          onClick={() => setFunnelCollapsed((value) => !value)}
        >
          {funnelCollapsed ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
          {funnelCollapsed ? '展开漏斗' : '收起漏斗'}
        </button>
        <button
          aria-pressed={viltroxOnly}
          className={`mykol-toggle ${viltroxOnly ? 'is-active' : ''}`}
          type="button"
          onClick={() => setViltroxOnly((value) => !value)}
        >
          <span /> Viltrox 相关
        </button>
        <button
          className="mykol-toggle"
          type="button"
          disabled={!apiToken || exporting}
          onClick={async () => {
            if (!apiToken) return;
            setExporting(true);
            try {
              const { exportVkpiReport } = await import('../../../../services/vkpi/dashboard-api');
              const result = await exportVkpiReport(apiToken, { reportType: 'favorites', format: 'csv' });
              const url = result.downloadUrl || result.download_url;
              if (url) window.open(url, '_blank', 'noopener,noreferrer');
            } catch (err) {
              setFavError(String((err as Error)?.message || '导出失败').slice(0, 100));
            } finally {
              setExporting(false);
            }
          }}
        >
          <span /> {exporting ? '导出中…' : '导出收藏名单'}
        </button>
        <button
          aria-pressed={batchMode}
          className={`mykol-toggle ${batchMode ? 'is-active' : ''}`}
          type="button"
          onClick={() => setBatchMode((value) => {
            const next = !value;
            // 【M2】退出批量模式清空选择与提示,避免残留状态影响下次进入。
            if (!next) { setBatchSelected(new Set()); setBatchNote(''); }
            return next;
          })}
        >
          <span /> 批量操作
        </button>
      </div>
      {/* 【M2】已选计数条 + 三个批量动作(入项目/导出 CSV/受众画像),批量模式才现身 */}
      {batchMode ? (
        <div
          aria-label="批量操作条"
          style={{
            display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8, margin: '10px 14px 0',
            border: '1px solid color-mix(in srgb, var(--ds-accent) 32%, transparent)', borderRadius: 11, background: 'var(--ds-accent-soft)',
            padding: '7px 12px', fontSize: 11, color: 'var(--ds-text-2)',
          }}
        >
          <b style={{ color: 'var(--ds-accent)' }}>已选 {batchSelectedItems.length} / 可见 {filteredItems.length}</b>
          <button type="button" style={batchBtnStyle} disabled={!filteredItems.length || batchBusy} onClick={toggleSelectAllVisible}>
            {allVisibleSelected ? '取消全选' : '全选可见'}
          </button>
          <button type="button" style={batchBtnStyle} disabled={!batchSelectedItems.length || batchBusy} onClick={() => setBatchSelected(new Set())}>
            清空
          </button>
          <span style={{ width: 1, height: 16, background: 'var(--ds-line-strong)' }} />
          <select
            aria-label="批量入项目目标"
            value={batchProjectId}
            disabled={batchBusy}
            onChange={(event) => setBatchProjectId(event.target.value)}
            style={{ ...batchBtnStyle, maxWidth: 180, padding: '4px 6px' }}
          >
            <option value="">选择目标项目…</option>
            {batchProjectOptions.map((project) => (
              <option key={project.id} value={project.id}>{project.campaign || `项目 ${project.id}`}</option>
            ))}
          </select>
          <button
            type="button"
            style={{ ...batchBtnStyle, borderColor: 'color-mix(in srgb, var(--ds-accent) 40%, transparent)', color: 'var(--ds-accent)' }}
            disabled={batchBusy || !batchSelectedItems.length || !batchProjectId}
            onClick={() => void runBatchAddToProject()}
          >
            批量入项目
          </button>
          <button type="button" style={batchBtnStyle} disabled={batchBusy || !batchSelectedItems.length} onClick={() => void runBatchExportCsv()}>
            导出 CSV
          </button>
          <button type="button" style={batchBtnStyle} disabled={batchBusy || !batchSelectedItems.length} onClick={() => void runBatchAudience()}>
            批量生成受众画像
          </button>
          {batchNote ? <em style={{ fontStyle: 'normal', color: 'var(--ds-accent)' }}>{batchNote}</em> : null}
        </div>
      ) : null}
      <div className={`mykol-employee-funnel ${funnelCollapsed ? 'is-collapsed' : ''}`} aria-label="KOL 合作漏斗">
        {employeeFunnelStages.map((stage, index) => {
          const count = funnelCounts[stage.key];
          const active = activeView === 'funnel' && activeFunnelStage === stage.key;
          const width = count ? Math.max(12, Math.round((count / maxFunnelCount) * 100)) : 0;
          return (
            <button
              className={active ? 'is-active' : ''}
              key={stage.key}
              type="button"
              onClick={() => {
                setActiveView('funnel');
                setFunnelCollapsed(false);
                setActiveFunnelStage(stage.key);
                setPlatformFilter('all');
              }}
            >
              <span>{String(index + 1).padStart(2, '0')}</span>
              <b>{stage.label}</b>
              <strong>{count}</strong>
              <i><em style={{ width: `${width}%` }} /></i>
            </button>
          );
        })}
      </div>
      <div className="mykol-employee-platform-strip">
        <button className={platformFilter === 'all' ? 'is-active' : ''} type="button" onClick={() => setPlatformFilter('all')}>
          <b>全部</b><span>{activeItems.length} KOL · {compactNumber(activeItems.reduce((sum, item) => sum + item.projects.reduce((inner, project) => inner + safeNumber(project.views), 0), 0))} 播放</span>
        </button>
        {platformStats.map(({ platform, stats }) => (
          <button className={platformFilter === platform ? 'is-active' : ''} key={platform} type="button" onClick={() => setPlatformFilter(platform)}>
            <b>{platformDisplay(platform)}</b><span>{stats.kols} KOL · {stats.projects} 项目 · {compactNumber(stats.views)}</span>
          </button>
        ))}
      </div>
      {favPage ? (
        <div style={{ margin: '8px 14px 0', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, border: '1px solid var(--ds-line)', borderRadius: 9, padding: '7px 10px', fontSize: 10.5, color: 'var(--ds-text-3)' }}>
          <span>收藏已渐进展示 {poolFavorites.length} / {Number(favPage.total || poolFavorites.length)} 条</span>
          {favPage.has_more && favPage.next_cursor ? (
            <button type="button" disabled={favLoadingMore} onClick={() => void loadMoreFavorites()} style={batchBtnStyle}>
              {favLoadingMore ? '加载中…' : '加载更多 50 条'}
            </button>
          ) : <span>已到末页</span>}
        </div>
      ) : null}
      {filteredItems.length ? (
        <div className="mykol-library-grid">
          <div className="mykol-kol-list">
            {favError ? (
              <div style={{ border: '1px solid color-mix(in srgb, var(--ds-crit) 30%, transparent)', background: 'var(--ds-crit-soft)', borderRadius: 8, padding: '6px 10px', fontSize: 10.5, color: 'var(--ds-crit)', marginBottom: 6 }}>
                收藏读取失败:{favError} —— 列表可能缺收藏项,请刷新或报值班。
              </div>
            ) : null}
            {claimNote ? (
              <div style={{ border: '1px solid color-mix(in srgb, var(--ds-accent) 30%, transparent)', background: 'var(--ds-accent-soft)', borderRadius: 8, padding: '5px 10px', fontSize: 10.5, color: 'var(--ds-accent)', marginBottom: 6 }}>
                {claimNote}
              </div>
            ) : null}
            {filteredItems.map(({ kol, projects, poolId, poolFit, isShared, sharedByName }) => {
              // 【M3】认领状态:kolOptions 行自带 active_claim_id(claim_listing LEFT JOIN active 认领);
              // 到期时间只在 aggregate 的本人 claims 里有 → 有则补进 title。释放按钮对有认领的行显示,
              // 权限由后端把关(本人或管理层可释放,否则 403 → claimNote 展示原因)。
              const claimIdRaw = String(kol.activeClaimId || (myClaimByKolId.get(String(kol.id))?.id ?? '')).trim();
              // 【M3】乐观遮蔽:本会话刚释放的认领立即按「无认领」渲染(父级数据回流前不闪回)。
              const claimId = claimIdRaw && releasedClaimIds.has(claimIdRaw) ? '' : claimIdRaw;
              const myClaim = myClaimByKolId.get(String(kol.id));
              const claimExpires = myClaim && myClaim.expires_at ? String(myClaim.expires_at).slice(0, 10) : '';
              const claimTip = claimId
                ? `已认领${kol.claimOwner && kol.claimOwner !== '-' ? ` · 认领人 ${kol.claimOwner}` : ''}${claimExpires ? ` · 到期 ${claimExpires}` : ''}`
                : '';
              // 【M2】批量模式:点行 = 切换勾选(不打开右侧详情);行首渲染勾选框视觉。
              const batchChecked = batchMode && batchSelected.has(kol.id);
              return (
                <button
                  className={`mykol-kol-row ${selectedItem?.kol.id === kol.id ? 'is-active' : ''}`}
                  key={kol.id}
                  type="button"
                  onClick={() => (batchMode ? toggleBatchSelect(kol.id) : setSelectedKolId(kol.id))}
                >
                  {batchMode ? (
                    <span
                      aria-hidden="true"
                      style={{
                        flex: '0 0 auto', width: 14, height: 14, borderRadius: 4, textAlign: 'center',
                        border: `1px solid ${batchChecked ? 'var(--ds-accent)' : 'var(--ds-line-strong)'}`,
                        background: batchChecked ? 'var(--ds-accent-soft)' : 'transparent',
                        color: 'var(--ds-accent)', fontSize: 10, lineHeight: '12px',
                      }}
                    >{batchChecked ? '✓' : ''}</span>
                  ) : null}
                  <span className="mykol-avatar">{kol.avatar ? <img src={proxiedImageUrl(kol.avatar)} alt="" /> : initials(kol.name)}</span>
                  <div>
                    <h3>{kol.name}</h3>
                    <p>{kol.handle || 'handle 暂无'} · {platformDisplay(kol.platform)} · {kol.followerLabel || '粉丝暂无'} · {poolId ? `${poolFit != null ? `Fit ${poolFit.toFixed(1)}` : 'Fit —'}${projects.length ? ` · ${projects.length} 项目` : ''}` : `${projects.length} 项目`}</p>
                  </div>
                  {/* 【M5】经共享获得的行标「来自 XX 的共享」(P-GROUP-7 只读可见;共享人名来自 aggregate 的 shared_by_name,旧行可空回落「共享」) */}
                  <strong title={poolId ? (isShared ? `经共享池共享给我的 KOL(只读可见,非本人收藏)${sharedByName ? ` · 共享人 ${sharedByName}` : ''}` : '本人收藏') : claimTip || undefined}
                    style={isShared ? { borderColor: 'color-mix(in srgb, var(--ds-accent-2) 45%, transparent)', color: 'var(--ds-accent-2)' } : undefined}
                  >{poolId ? (isShared ? (sharedByName ? `来自 ${sharedByName} 的共享` : '共享') : '收藏') : claimId ? '已认领' : projects.length ? '进行中' : '待定'}</strong>
                  {/* 【M3】释放小按钮:行本体是 <button>,内嵌交互用 span+role 阻断冒泡 */}
                  {!poolId && claimId ? (
                    <span
                      role="button"
                      tabIndex={0}
                      title={`释放认领${claimTip ? `(${claimTip})` : ''}`}
                      onClick={(event) => { event.stopPropagation(); void releaseClaim(claimId, kol.name || kol.handle || 'KOL'); }}
                      onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); event.stopPropagation(); void releaseClaim(claimId, kol.name || kol.handle || 'KOL'); } }}
                      style={{
                        flex: '0 0 auto', marginLeft: 4, padding: '2px 5px', borderRadius: 7, fontSize: 9, whiteSpace: 'nowrap',
                        border: '1px solid color-mix(in srgb, var(--ds-warn) 40%, transparent)', color: releasingId === claimId ? 'var(--ds-muted)' : 'var(--ds-warn)',
                        cursor: releasingId === claimId ? 'wait' : 'pointer',
                      }}
                    >{releasingId === claimId ? '释放中…' : '释放'}</span>
                  ) : null}
                </button>
              );
            })}
          </div>
          {selectedItem && (selectedItem as { poolId?: number | null }).poolId ? (
            <PoolEvidenceContent
              apiToken={apiToken}
              kol={selectedItem.kol}
              poolId={Number((selectedItem as { poolId?: number | null }).poolId)}
              viltroxOnly={viltroxOnly}
              projects={selectedItem.projects}
            />
          ) : (
            <EmployeeKolContentLayer
              apiToken={apiToken}
              kol={selectedItem?.kol}
              projects={selectedItem?.projects || []}
              viltroxOnly={viltroxOnly}
            />
          )}
        </div>
      ) : (
        <div className="mykol-empty">暂无可见 MY KOL。等待后端 grouped endpoint 或现有项目数据返回。</div>
      )}
    </section>
  );
}
