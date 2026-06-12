import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Bell, CheckCircle2, ChevronDown, ChevronUp, Heart, Search } from 'lucide-react';
import type { OfficialChannelAccount, OfficialChannelPlatform } from '../channels/channelTypes';
import { useOfficialChannelMatrix } from '../channels/useOfficialChannelMatrix';
import type { VkpiDashboardData, VkpiKolOption, VkpiPageKey, VkpiProjectRow } from '../../vkpiTypes';
import { platformDisplay, safeNumber } from '../../shared/vkpiDataUtils';
import { proxiedImageUrl } from '../../shared/mediaProxy';
import { OfficialContentLayer } from './OfficialContentLayer';
import { EmployeeKolContentLayer } from './EmployeeKolContentLayer';
import { PoolEvidenceContent } from './PoolEvidenceContent';
import { projectDate } from '../channels/myKolMatrixData';
import type { FunnelStageKey, PlatformFilter, PostPreview } from '../channels/myKolMatrixTypes';
import './myKolPage.css';
import './myKolTeamMatrix.css';

interface MyKolPageProps {
  apiToken?: string;
  viewMode: 'manager' | 'employee';
  data: VkpiDashboardData;
  userName?: string;
  userRole?: string;
  onRefreshData?: () => void;
  onSelectPage?: (page: VkpiPageKey) => void;
}

type StaffCard = {
  id: string;
  name: string;
  role: string;
  avatar?: string;
  focus?: string;
  accent?: string;
  accounts: OfficialChannelAccount[];
  projects: VkpiProjectRow[];
};

const knownStaffDisplay = [
  { id: 'display-kevin', name: 'Kevin Chen', role: 'Marketing Director', focus: '焦点: 全局', accent: '#fb7185' },
  { id: 'display-maya', name: 'Maya Liu', role: 'Sr. KOL Manager', focus: '焦点: 135mm LAB · CineGear', accent: '#a855f7' },
  { id: 'display-tom', name: 'Tom Chen', role: 'KOL Manager', focus: '焦点: 56mm 复推', accent: '#06b6d4' },
  { id: 'display-jianbo', name: 'Jianbo Z', role: 'Founder', focus: '焦点: 全局 + 战略', accent: '#10b981' },
];

const platformAccent: Record<string, string> = {
  facebook: '#2f80ed',
  instagram: '#ec4899',
  reddit: '#f97316',
  tiktok: '#06d6d6',
  x: '#94a3b8',
  twitter: '#94a3b8',
  youtube: '#ff1744',
};

const employeePlatformOrder: Exclude<PlatformFilter, 'all'>[] = ['YouTube', 'Instagram', 'TikTok', 'Facebook', 'Reddit', 'X'];

const employeeFunnelStages: Array<{ key: FunnelStageKey; label: string }> = [
  { key: 'claimed', label: '已认领' },
  { key: 'contacted', label: '已联系' },
  { key: 'replied', label: '已回复' },
  { key: 'agreed', label: '已合作' },
  { key: 'shipped', label: '已发货' },
  { key: 'received', label: '已到货' },
  { key: 'published', label: '已发布' },
];

function employeeFunnelStage(projects: VkpiProjectRow[]): FunnelStageKey {
  if (!projects.length) return 'claimed';
  const latest = [...projects].sort((left, right) => projectDate(right) - projectDate(left))[0];
  const stage = latest?.stage || '';
  if (stage === 'contacted') return 'contacted';
  if (stage === 'replied') return 'replied';
  if (stage === 'agreed') return 'agreed';
  if (stage === 'shipped') return 'shipped';
  if (stage === 'received') return 'received';
  if (['content_published', 'published', 'released', 'measured', 'closed'].includes(stage)) return 'published';
  return 'claimed';
}

function compactNumber(value: number | null | undefined) {
  const next = safeNumber(value);
  if (!next) return '—';
  return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: next >= 1000 ? 1 : 0 }).format(next);
}

function signedNumber(value: number | null | undefined, unit = '') {
  const next = safeNumber(value);
  if (!next) return '';
  const sign = next > 0 ? '+' : '';
  return `${sign}${compactNumber(next)}${unit}`;
}

function Delta({ value, unit = '' }: { value?: number; unit?: string }) {
  const label = signedNumber(value, unit);
  return label ? <small>{label}</small> : null;
}

function initials(name: string) {
  return name
    .split(/\s+/)
    .map((part) => part[0])
    .filter(Boolean)
    .slice(0, 2)
    .join('')
    .toUpperCase() || 'K';
}

function staffIdentityKey(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '');
}

function staffFirstToken(value: string) {
  return staffIdentityKey(value.split(/\s+/)[0] || value);
}

function matchesKnownStaff(card: StaffCard, known: typeof knownStaffDisplay[number]) {
  const cardName = staffIdentityKey(card.name);
  const knownName = staffIdentityKey(known.name);
  const knownFirst = staffFirstToken(known.name);
  return Boolean(knownFirst && (cardName === knownName || cardName === knownFirst || knownName.includes(cardName) || cardName.includes(knownFirst)));
}

function isGenericStaffShell(card: StaffCard) {
  const name = staffIdentityKey(card.name);
  return (name === 'admin' || name === 'staff' || name === 'staffuser') && !card.accounts.length && !card.projects.length;
}

function staffDisplayRole(actual: string, fallback: string) {
  const normalized = actual.trim().toLowerCase();
  return normalized && !['admin', 'readonly', 'staff'].includes(normalized) ? actual : fallback;
}

function isAdminLike(card: Pick<StaffCard, 'name' | 'role'>) {
  const value = `${card.name} ${card.role}`.toLowerCase();
  return value.includes('admin') || value.includes('director') || value.includes('founder') || value.includes('kevin') || value.includes('jianbo');
}

function staffFocusLine(card: StaffCard) {
  if (card.focus) return card.focus;
  const value = `${card.name} ${card.role}`.toLowerCase();
  if (value.includes('maya')) return '焦点: 135mm LAB · CineGear';
  if (value.includes('tom')) return '焦点: 56mm 复推';
  if (value.includes('founder') || value.includes('director') || value.includes('admin')) return '焦点: 全局';
  const platformNames = Array.from(new Set(card.accounts.map((account) => platformDisplay(account.platform)))).slice(0, 2);
  if (platformNames.length) return `焦点: ${platformNames.join(' · ')}`;
  return '焦点: 待接入';
}

function statusLabel(value: string) {
  const labels: Record<string, string> = {
    configured_pending_provider: '待同步',
    no_results: '无结果',
    not_configured: '待配置',
    not_supported: '未接入',
    official_readonly: '只读',
    synced: '已同步',
  };
  return labels[value] || value || '待接入';
}

function normalizeHandle(value: string | undefined) {
  return String(value || '').trim().toLowerCase().replace(/^@/, '').replace(/[\s_-]+/g, '.');
}

function isOwnedMatrixLike(kol: VkpiDashboardData['kolOptions'][number]) {
  const handle = normalizeHandle(kol.handle || kol.name);
  return [
    'viltrox',
    'viltrox.official',
    'viltroxofficial',
    'viltrox.global',
    'viltrox.cine',
    'viltrox.flash',
    'viltrox.us',
    'viltrox.usa',
    'viltrox.community',
    'viltrox.thailand',
  ].includes(handle);
}

// Temporary until kolOptions carries content counts from the posts endpoint.
const contentReadyDefaultKolIds = new Set(['110', '2741', '2742', '3015', '3603']);

function preferredEmployeeKolItem<T extends { kol: { id: string } }>(items: T[]) {
  return items.find((item) => contentReadyDefaultKolIds.has(String(item.kol.id))) || items[0];
}

function MyKolSkeleton() {
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

function TeamMatrix({ cards, pendingCount }: { cards: StaffCard[]; pendingCount: number }) {
  const [page, setPage] = useState(0);
  const allAccounts = cards.flatMap((card) => card.accounts);
  const totalPosts = allAccounts.reduce((sum, account) => sum + safeNumber(account.postsCount), 0);
  const totalFollowers = allAccounts.reduce((sum, account) => sum + safeNumber(account.followers), 0);
  const totalViews = allAccounts.reduce((sum, account) => sum + safeNumber(account.totalViews), 0);
  const totalPostsDelta = allAccounts.reduce((sum, account) => sum + safeNumber(account.postsDelta), 0);
  const totalFollowersDelta = allAccounts.reduce((sum, account) => sum + safeNumber(account.followersDelta), 0);
  const totalViewsDelta = allAccounts.reduce((sum, account) => sum + safeNumber(account.viewsDelta), 0);
  const platformCount = new Set(allAccounts.map((account) => account.platform)).size;
  const contractCount = cards.reduce((sum, card) => sum + card.projects.length, 0);
  const pageSize = 4;
  const pageCount = Math.max(1, Math.ceil(cards.length / pageSize));
  const safePage = Math.min(page, pageCount - 1);
  const visibleCards = cards.slice(safePage * pageSize, safePage * pageSize + pageSize);
  const rangeStart = cards.length ? safePage * pageSize + 1 : 0;
  const rangeEnd = Math.min((safePage + 1) * pageSize, cards.length);

  useEffect(() => {
    setPage(0);
  }, [cards.length]);

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
            <span><b>{platformCount || '待接入'}</b> 平台</span>
            <span><b>{compactNumber(totalPosts)}</b> 内容 <Delta value={totalPostsDelta} /></span>
            <span><b>{compactNumber(totalFollowers)}</b> 粉丝 <Delta value={totalFollowersDelta} /></span>
            <span><b>{compactNumber(totalViews)}</b> 播放 <Delta value={totalViewsDelta} /></span>
            <span><b>{contractCount || '待接入'}</b> 签约</span>
            <span><b>{pendingCount}</b> 待定</span>
          </div>
          {cards.length ? (
            <div className="mykol-team-page-mini" aria-label="团队矩阵分页">
              <span>{rangeStart}-{rangeEnd} / {cards.length}</span>
              <button disabled={safePage === 0} type="button" onClick={() => setPage((current) => Math.max(0, current - 1))}>‹</button>
              <b>{safePage + 1}</b>
              <button disabled={safePage >= pageCount - 1} type="button" onClick={() => setPage((current) => Math.min(pageCount - 1, current + 1))}>›</button>
            </div>
          ) : null}
        </div>
      </header>
      {cards.length ? (
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
            return (
              <article className="mykol-staff-card" key={card.id}>
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
                  <span><em>账号</em><b>{card.accounts.length ? `${card.accounts.length} · ${platforms} 平台` : '待接入'}</b></span>
                  <span><em>粉丝</em><b>{compactNumber(followers)}</b>{followersDelta ? <small>{signedNumber(followersDelta)}</small> : null}</span>
                  <span><em>管 KOL</em><b>{card.projects.length ? `${card.projects.length} 人` : '待接入'}</b>{pending ? <small className="is-warn">{pending} 待定</small> : null}</span>
                  <span><em>KOL 视频</em><b>{posts ? `${compactNumber(posts)} · ${compactNumber(views)}` : '待接入'}</b></span>
                </div>
                <div className="mykol-platform-tags">
                  {platformTags.map((platform) => (
                    <span
                      key={platform}
                      style={{ '--tag-accent': platformAccent[platform] || '#8b5cf6' } as React.CSSProperties}
                    >
                      {platformDisplay(platform)}
                    </span>
                  ))}
                  {!platformTags.length ? <span>账号待接入</span> : null}
                </div>
              </article>
            );
          })}
          </div>
          {cards.length ? (
            <footer className="mykol-team-pagination">
              <span>显示 {rangeStart}-{rangeEnd} / {cards.length} 名负责人</span>
              <div>
                <button disabled={safePage === 0} type="button" onClick={() => setPage((current) => Math.max(0, current - 1))}>上一页</button>
                <b>{safePage + 1} / {pageCount}</b>
                <button disabled={safePage >= pageCount - 1} type="button" onClick={() => setPage((current) => Math.min(pageCount - 1, current + 1))}>下一页</button>
              </div>
            </footer>
          ) : null}
        </>
      ) : (
        <div className="mykol-empty">暂无团队账号矩阵。等待官方矩阵接口返回 staff 归属。</div>
      )}
    </section>
  );
}

function OfficialMatrix({
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

  return (
    <section className="mykol-panel">
      <header className="mykol-section-head">
        <div>
          <h2><span className="is-blue" />官方账号矩阵 <em>/ 平台总览</em></h2>
        </div>
        <div className="mykol-chip-row">
          <span><b>{matrix.accountCount}</b> 账号</span>
          <span><b>{syncedAccounts}</b> 已同步</span>
          <span><b>{matrix.platforms.length}</b> 平台</span>
          <span><b>{compactNumber(matrix.postCount)}</b> 内容 <Delta value={totalPostsDelta} /></span>
          <span><b>{compactNumber(totalFollowers)}</b> 粉丝 <Delta value={totalFollowersDelta} /></span>
          <span><b>{compactNumber(matrix.totalViews)}</b> 播放 <Delta value={totalViewsDelta} /></span>
          <span><b>{compactNumber(avgViews)}</b> 篇均</span>
        </div>
      </header>
      {matrix.loading && !matrix.platforms.length ? <MyKolSkeleton /> : null}
      {matrix.error ? <div className="mykol-warning">{matrix.error}</div> : null}
      <div className="mykol-platform-grid">
        {matrix.platforms.map((platform) => {
          const accent = platformAccent[platform.platform] || '#8b5cf6';
          const active = selectedPlatform?.platform === platform.platform;
          return (
            <button
              className={`mykol-platform-card ${active ? 'is-active' : ''}`}
              key={platform.platform}
              style={{ '--accent': accent } as React.CSSProperties}
              type="button"
              onClick={() => onSelectPlatform(platform.platform)}
            >
              <span className="mykol-platform-icon">{platform.label.slice(0, 1)}</span>
              <b>{platform.label}</b>
              <em>{platform.accounts.length} 账号 · {compactNumber(platform.totalPosts)} 内容</em>
              <strong>{platform.viewsUnavailable ? '—' : compactNumber(platform.totalViews)}</strong>
              <small>{platform.viewsUnavailable ? platform.viewsUnavailableReason || '公开播放不可用' : `${compactNumber(platform.totalFollowers)} 粉丝`}</small>
              <i>{platform.baselineProtected ? '基线保护' : statusLabel(platform.accounts[0]?.syncStatus || '')}</i>
              <div className="mykol-platform-card__footer">
                <span>{platform.lastSyncAt ? `本次 ${new Date(platform.lastSyncAt).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}` : '同步时间待接入'}</span>
                <span>{signedNumber(platform.viewsDelta, ' 播放') || '较上次 0'}</span>
              </div>
              <div className="mykol-platform-card__accounts">
                {platform.accounts.slice(0, 5).map((account) => <span key={account.id}>{initials(account.displayName)}</span>)}
              </div>
            </button>
          );
        })}
      </div>
      <div className="mykol-drill-grid">
        <div className="mykol-account-list">
          {(selectedPlatform?.accounts || []).map((account) => (
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
        <OfficialContentLayer
          account={(selectedPlatform?.accounts || []).find((account) => account.id === selectedAccountId)}
          apiToken={apiToken}
        />
      </div>
    </section>
  );
}

function EmployeeKolLibrary({ apiToken, data, viewMode }: { apiToken?: string; data: VkpiDashboardData; viewMode: 'manager' | 'employee' }) {
  const [query, setQuery] = useState('');
  const [platformFilter, setPlatformFilter] = useState<PlatformFilter>('all');
  const [selectedKolId, setSelectedKolId] = useState('');
  const [activeView, setActiveView] = useState<'watchlist' | 'funnel'>('watchlist');
  const [activeFunnelStage, setActiveFunnelStage] = useState<FunnelStageKey>('claimed');
  const [viltroxOnly, setViltroxOnly] = useState(true);
  // 从哪发起回哪去(2026-06-12):泳道点开账号分析任务 → 切回 MY KOL 并定位该收藏行
  // (pending key 由 TaskProgressBoard 写入;挂载时也消费一次,覆盖"先切页后挂载"的时序)。
  useEffect(() => {
    const consumePending = () => {
      try {
        const pending = window.localStorage.getItem('vkpi:pending-mykol-pool-id');
        if (!pending) return;
        window.localStorage.removeItem('vkpi:pending-mykol-pool-id');
        setActiveView('watchlist');
        setSelectedKolId(`pool:${pending}`);
      } catch { /* localStorage 不可用时忽略定位 */ }
    };
    consumePending();
    window.addEventListener('vkpi:open-mykol-kol', consumePending);
    return () => window.removeEventListener('vkpi:open-mykol-kol', consumePending);
  }, []);
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
  const [poolFavorites, setPoolFavorites] = useState<Array<Record<string, unknown>>>([]);
  const [favError, setFavError] = useState('');
  useEffect(() => {
    if (!apiToken) return;
    let cancelled = false;
    import('../../../../domains/kol').then(({ listKolPoolFavorites }) =>
      listKolPoolFavorites(apiToken).then((resp) => {
        if (!cancelled) setPoolFavorites((resp.items || []) as Array<Record<string, unknown>>);
      }),
    ).catch((err) => { if (!cancelled) setFavError(String((err as Error)?.message || '收藏读取失败').slice(0, 100)); });
    return () => { cancelled = true; };
  }, [apiToken]);

  const items = useMemo(() => {
    const base = data.kolOptions.filter((kol) => !isOwnedMatrixLike(kol)).map((kol) => {
      const projects = projectByKol.get(kol.id) || projectByKol.get(`${kol.platform}:${kol.handle}`) || [];
      return { kol, projects, funnelStage: employeeFunnelStage(projects), poolId: null as number | null, poolFit: null as number | null };
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
      const rawAssignments = Array.isArray(fav.projects_json) ? (fav.projects_json as Array<Record<string, unknown>>) : [];
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
      return { kol, projects, funnelStage: employeeFunnelStage(projects), poolId: Number(fav.kol_pool_id), poolFit: fav.viltrox_fit_score == null ? null : Number(fav.viltrox_fit_score) };
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
  const filteredItems = useMemo(() => activeItems.filter(({ kol }) => {
    const matchesPlatform = platformFilter === 'all' || kol.platform === platformFilter;
    const haystack = `${kol.name} ${kol.handle}`.toLowerCase();
    return matchesPlatform && haystack.includes(query.trim().toLowerCase());
  }), [activeItems, platformFilter, query]);
  const preferredItem = preferredEmployeeKolItem(filteredItems);
  const selectedItem = filteredItems.find((item) => item.kol.id === selectedKolId) || preferredItem;
  const totalViews = items.reduce((sum, item) => (
    sum + item.projects.reduce((inner, project) => inner + safeNumber(project.views), 0)
  ), 0);
  const totalClicks = items.reduce((sum, item) => (
    sum + item.projects.reduce((inner, project) => inner + safeNumber(project.clicks), 0)
  ), 0);

  useEffect(() => {
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
          <h2><span className="is-cyan" />{viewMode === 'manager' ? 'MY KOL 库' : '员工 KOL 库'} <em>/ 真实项目与自然人待接入</em></h2>
          <p>{viewMode === 'manager' ? '管理层视角 · 当前用现有 KOL/project 数据过渡' : '员工视角 · 当前仅展示可见 KOL 与项目内容'}</p>
        </div>
        <div className="mykol-chip-row">
          <span><b>{items.length}</b> Total KOL</span>
          <span><b>{data.projects.length}</b> 项目</span>
          <span><b>{compactNumber(totalViews)}</b> Viltrox 播放</span>
          <span><b>{compactNumber(totalClicks)}</b> Viltrox 点击</span>
        </div>
      </header>
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
      </div>
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
      {filteredItems.length ? (
        <div className="mykol-library-grid">
          <div className="mykol-kol-list">
            {favError ? (
              <div style={{ border: '1px solid rgba(244,63,94,0.3)', background: 'rgba(244,63,94,0.08)', borderRadius: 8, padding: '6px 10px', fontSize: 10.5, color: '#fca5a5', marginBottom: 6 }}>
                收藏读取失败:{favError} —— 列表可能缺收藏项,请刷新或报值班。
              </div>
            ) : null}
            {filteredItems.map(({ kol, projects, poolId, poolFit }) => (
              <button className={`mykol-kol-row ${selectedItem?.kol.id === kol.id ? 'is-active' : ''}`} key={kol.id} type="button" onClick={() => setSelectedKolId(kol.id)}>
                <span className="mykol-avatar">{kol.avatar ? <img src={proxiedImageUrl(kol.avatar)} alt="" /> : initials(kol.name)}</span>
                <div>
                  <h3>{kol.name}</h3>
                  <p>{kol.handle || 'handle 待接入'} · {platformDisplay(kol.platform)} · {kol.followerLabel || '粉丝待接入'} · {poolId ? `${poolFit != null ? `Fit ${poolFit.toFixed(1)}` : 'Fit —'}${projects.length ? ` · ${projects.length} 项目` : ''}` : `${projects.length} 项目`}</p>
                </div>
                <strong>{poolId ? '收藏' : kol.activeClaimId ? '长期合作' : projects.length ? '进行中' : '待定'}</strong>
              </button>
            ))}
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


export function MyKolPage({ apiToken, viewMode, data, userName, onRefreshData }: MyKolPageProps) {
  const matrix = useOfficialChannelMatrix(apiToken);
  const [selectedPlatformKey, setSelectedPlatformKey] = useState('');
  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(null);
  const selectedPlatform = matrix.platforms.find((platform) => platform.platform === selectedPlatformKey) || matrix.platforms[0];

  useEffect(() => {
    if (!selectedPlatformKey && matrix.platforms[0]) {
      setSelectedPlatformKey(matrix.platforms[0].platform);
    }
  }, [matrix.platforms, selectedPlatformKey]);

  useEffect(() => {
    if (!selectedPlatform) {
      setSelectedAccountId(null);
      return;
    }
    if (!selectedPlatform.accounts.some((account) => account.id === selectedAccountId)) {
      setSelectedAccountId(selectedPlatform.accounts[0]?.id ?? null);
    }
  }, [selectedAccountId, selectedPlatform]);

  const staffCards = useMemo<StaffCard[]>(() => {
    const accounts = matrix.platforms.flatMap((platform) => platform.accounts);
    const projectsByOwner = new Map<string, VkpiProjectRow[]>();
    data.projects.forEach((project) => {
      if (!project.ownerId) return;
      projectsByOwner.set(project.ownerId, [...(projectsByOwner.get(project.ownerId) || []), project]);
    });
    const staff = data.staffMembers.length ? data.staffMembers : accounts.map((account) => ({
      id: String(account.staffId || account.staffEmail || account.staffName),
      name: account.staffName || '未分配',
      email: account.staffEmail || '',
      role: account.staffRole || '',
      active: account.staffActive,
      avatarUrl: account.staffAvatarUrl,
      vkpiPermission: 'read' as const,
    }));
    const seen = new Set<string>();
    const baseCards = staff.filter((member) => {
      if (seen.has(member.id)) return false;
      seen.add(member.id);
      return true;
    }).map((member) => ({
      id: member.id,
      name: member.name,
      role: member.role || 'KOL Manager',
      avatar: member.avatarUrl,
      accounts: accounts.filter((account) => String(account.staffId) === member.id || account.staffEmail === member.email),
      projects: projectsByOwner.get(member.id) || [],
    }));
    const consumedBaseIds = new Set<string>();
    const orderedKnownCards = knownStaffDisplay.map((known) => {
      const matched = baseCards.find((card) => !consumedBaseIds.has(card.id) && matchesKnownStaff(card, known));
      if (matched) {
        consumedBaseIds.add(matched.id);
        return {
          ...matched,
          name: matched.name === 'Jianbo' ? 'Jianbo Z' : matched.name,
          role: staffDisplayRole(matched.role, known.role),
          focus: known.focus,
          accent: known.accent,
        };
      }
      return {
        ...known,
        accounts: [] as OfficialChannelAccount[],
        projects: [] as VkpiProjectRow[],
      };
    });
    const remainingRealCards = baseCards.filter((card) => !consumedBaseIds.has(card.id) && !isGenericStaffShell(card));
    return [...orderedKnownCards, ...remainingRealCards];
  }, [data.projects, data.staffMembers, matrix.platforms]);

  const pendingCount = matrix.platforms.flatMap((platform) => platform.accounts).filter((account) => (
    account.syncStatus !== 'synced' && account.syncStatus !== 'official_readonly'
  )).length;

  return (
    <main className="mykol-page">
      <header className="mykol-hero">
        <div>
          <h1><Heart size={18} fill="currentColor" /> MY KOL <span>/ {viewMode === 'manager' ? '团队矩阵 / 账号管理' : '我的 KOL'}</span></h1>
          <p>{viewMode === 'manager' ? `管理层视角 · ${staffCards.length || '待接入'} 名负责人 · ${data.kolOptions.length || '待接入'} 个 KOL` : `${userName || '员工'} · 只看自己负责的数据`}</p>
        </div>
        <div className="mykol-hero-actions">
          <span className={pendingCount ? 'is-pending' : ''}><Bell size={14} /> {pendingCount ? `${pendingCount} 个待定` : '无待定'}</span>
          <button type="button" onClick={onRefreshData}><CheckCircle2 size={14} /> 刷新数据</button>
        </div>
      </header>
      {viewMode === 'manager' ? <TeamMatrix cards={staffCards} pendingCount={pendingCount} /> : null}
      {viewMode === 'employee' ? <EmployeeKolLibrary apiToken={apiToken} data={data} viewMode={viewMode} /> : null}
      <OfficialMatrix
        apiToken={apiToken}
        matrix={matrix}
        selectedPlatform={selectedPlatform}
        selectedAccountId={selectedAccountId}
        onSelectPlatform={setSelectedPlatformKey}
        onSelectAccount={(account) => setSelectedAccountId(account.id)}
      />
      {viewMode === 'manager' ? <EmployeeKolLibrary apiToken={apiToken} data={data} viewMode={viewMode} /> : null}
    </main>
  );
}
