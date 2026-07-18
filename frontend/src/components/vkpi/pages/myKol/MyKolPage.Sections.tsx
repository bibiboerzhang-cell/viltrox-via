import React, { useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import type { OfficialChannelAccount, OfficialChannelPlatform } from '../channels/channelTypes';
import { PlatformLogo } from './platformLogos';
import { useOfficialChannelMatrix } from '../channels/useOfficialChannelMatrix';
import { platformDisplay, safeNumber } from '../../shared/vkpiDataUtils';
import { proxiedImageUrl } from '../../shared/mediaProxy';
import { OfficialContentLayer } from './OfficialContentLayer';
import {
  ACCOUNT_GROUPS,
  COLLAPSE_KEY_OFFICIAL,
  COLLAPSE_KEY_TEAM,
  type StaffCard,
  accountGroupKey,
  compactNumber,
  initials,
  isAdminLike,
  platformAccent,
  readCollapse,
  signedNumber,
  staffFocusLine,
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

// EmployeeKolLibrary(MY KOL 库)已整段平移至 MyKolPage.Sections2.tsx(千行卫兵还债,纯机械搬迁,零行为变化);
// re-export 保持对外 import 路径不破(MyKolBoardPage.embeds.tsx 等仍从本文件取)。
export { EmployeeKolLibrary } from './MyKolPage.Sections2';
