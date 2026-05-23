import type { OfficialChannelAccount, OfficialChannelPlatform } from './channelTypes';
import { proxiedImageUrl } from '../../shared/mediaProxy';

const formatter = new Intl.NumberFormat('en-US');

interface StaffSummary {
  staffId: number;
  staffName: string;
  staffEmail: string;
  staffAvatarUrl: string;
  staffRole: string;
  accountCount: number;
  platformCount: number;
  followers: number;
  followersDelta: number;
  posts: number;
  postsDelta: number;
  views: number;
  viewsDelta: number;
  protectedFields: Set<'followers' | 'posts' | 'views'>;
  protectedReasons: Set<string>;
  platforms: string[];
  topAccount?: OfficialChannelAccount;
}

function compact(value: number) {
  if (!value) return '0';
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 1 : 2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 10_000 ? 0 : 1)}K`;
  return formatter.format(value);
}

function deltaTone(delta = 0, baselineProtected = false) {
  if (baselineProtected && !delta) return 'is-protected';
  if (delta > 0) return 'is-up';
  if (delta < 0) return 'is-down';
  return '';
}

function deltaText(delta = 0, baselineProtected = false) {
  if (baselineProtected && !delta) return '基线保护';
  return delta ? `${delta > 0 ? '+' : ''}${compact(delta)}` : '基线';
}

function protectedTitle(reasons: Set<string>) {
  return Array.from(reasons).filter(Boolean).join('；') || '本轮样本小于历史累计，沿用历史值';
}

function protectedFields(account: OfficialChannelAccount) {
  if (!account.baselineProtected) return [];
  const fields = account.baselineProtectedFields || [];
  if (!fields.length) return ['followers', 'posts', 'views'] as const;
  const result: Array<'followers' | 'posts' | 'views'> = [];
  if (fields.includes('followers')) result.push('followers');
  if (fields.includes('posts_count')) result.push('posts');
  if (fields.includes('total_views')) result.push('views');
  return result;
}

function initial(summary: StaffSummary) {
  return (summary.staffName || summary.staffEmail || 'S').slice(0, 1).toUpperCase();
}

function buildStaffRows(platforms: OfficialChannelPlatform[]) {
  const byStaff = new Map<number, StaffSummary & { platformSet: Set<string> }>();
  platforms.forEach((platform) => {
    platform.accounts.forEach((account) => {
      const staffId = account.staffId || 0;
      const row = byStaff.get(staffId) || {
        staffId,
        staffName: account.staffName || account.staffEmail || `Staff ${staffId || '-'}`,
        staffEmail: account.staffEmail,
        staffAvatarUrl: account.staffAvatarUrl,
        staffRole: account.staffRole,
        accountCount: 0,
        platformCount: 0,
        followers: 0,
        followersDelta: 0,
        posts: 0,
        postsDelta: 0,
        views: 0,
        viewsDelta: 0,
        protectedFields: new Set<'followers' | 'posts' | 'views'>(),
        protectedReasons: new Set<string>(),
        platforms: [],
        platformSet: new Set<string>(),
      };
      row.accountCount += 1;
      row.followers += account.followers;
      row.followersDelta += account.followersDelta || 0;
      row.posts += account.postsCount;
      row.postsDelta += account.postsDelta || 0;
      row.views += account.totalViews;
      row.viewsDelta += account.viewsDelta || 0;
      protectedFields(account).forEach((field) => row.protectedFields.add(field));
      if (account.baselineProtected) {
        row.protectedReasons.add(account.baselineProtectedLabel || account.baselineProtectedReason || '本轮样本小于历史累计，沿用历史值');
      }
      row.platformSet.add(account.platformLabel || platform.label);
      if (!row.topAccount || account.totalViews > row.topAccount.totalViews) {
        row.topAccount = account;
      }
      byStaff.set(staffId, row);
    });
  });
  return Array.from(byStaff.values())
    .map((row) => ({ ...row, platformCount: row.platformSet.size, platforms: Array.from(row.platformSet).sort() }))
    .sort((a, b) => b.views - a.views || b.accountCount - a.accountCount || a.staffName.localeCompare(b.staffName));
}

export function ChannelStaffProgress({
  platforms,
  selectedStaffId,
  onSelectStaff,
  compactMode = false,
}: {
  platforms: OfficialChannelPlatform[];
  selectedStaffId?: number | null;
  onSelectStaff: (staffId: number | null) => void;
  compactMode?: boolean;
}) {
  const rows = buildStaffRows(platforms);
  const totals = rows.reduce(
    (acc, row) => ({
      accounts: acc.accounts + row.accountCount,
      followers: acc.followers + row.followers,
      followersDelta: acc.followersDelta + row.followersDelta,
      posts: acc.posts + row.posts,
      postsDelta: acc.postsDelta + row.postsDelta,
      views: acc.views + row.views,
      viewsDelta: acc.viewsDelta + row.viewsDelta,
      protectedFields: new Set([...acc.protectedFields, ...row.protectedFields]),
      protectedReasons: new Set([...acc.protectedReasons, ...row.protectedReasons]),
    }),
    {
      accounts: 0,
      followers: 0,
      followersDelta: 0,
      posts: 0,
      postsDelta: 0,
      views: 0,
      viewsDelta: 0,
      protectedFields: new Set<'followers' | 'posts' | 'views'>(),
      protectedReasons: new Set<string>(),
    },
  );
  const totalPlatforms = new Set(platforms.map((platform) => platform.label || platform.platform)).size;
  const averageViews = totals.posts ? Math.round(totals.views / totals.posts) : 0;
  const summaryMetrics = [
    { label: '账号', value: formatter.format(totals.accounts) },
    { label: '负责人', value: formatter.format(rows.length) },
    { label: '平台', value: formatter.format(totalPlatforms) },
    { label: '内容', value: formatter.format(totals.posts), delta: totals.postsDelta, protected: totals.protectedFields.has('posts') && !totals.postsDelta },
    { label: '粉丝', value: compact(totals.followers), delta: totals.followersDelta, protected: totals.protectedFields.has('followers') && !totals.followersDelta },
    { label: '播放', value: compact(totals.views), delta: totals.viewsDelta, protected: totals.protectedFields.has('views') && !totals.viewsDelta, primary: true },
    { label: '篇均播放', value: compact(averageViews) },
  ];
  const totalProtectedTitle = protectedTitle(totals.protectedReasons);
  return (
    <section className={`vkpi-channel-staff${compactMode ? ' vkpi-channel-staff--compact' : ''}`}>
      <div className="vkpi-channel-staff__header">
        <div>
          <span>{compactMode ? '团队矩阵' : '负责人层'}</span>
          <h2>{compactMode ? '负责人进度' : '员工账号进度'}</h2>
          {selectedStaffId != null ? (
            <button className="vkpi-channel-filter-reset" type="button" onClick={() => onSelectStaff(null)}>
              查看全部负责人
            </button>
          ) : null}
        </div>
        <div className="vkpi-channel-staff__totals">
          {summaryMetrics.map((metric) => (
            <span className={`vkpi-channel-summary-metric${metric.primary ? ' is-primary' : ''}`} key={metric.label}>
              <span className="vkpi-channel-summary-metric__main">
                <strong>{metric.value}</strong>
                <span>{metric.label}</span>
              </span>
              {'delta' in metric ? <small className={deltaTone(metric.delta, metric.protected)} title={metric.protected ? totalProtectedTitle : undefined}>{deltaText(metric.delta, metric.protected)}</small> : null}
            </span>
          ))}
        </div>
      </div>
      <div className="vkpi-channel-staff-grid">
        {rows.map((row) => {
          const avatarUrl = proxiedImageUrl(row.staffAvatarUrl);
          const viewsProtected = row.protectedFields.has('views') && !row.viewsDelta;
          const rowProtectedTitle = protectedTitle(row.protectedReasons);
          return (
            <button
              type="button"
              className={`vkpi-channel-staff-card${selectedStaffId === row.staffId ? ' is-active' : ''}`}
              key={row.staffId || row.staffName}
              onClick={() => onSelectStaff(row.staffId)}
            >
              <div className="vkpi-channel-staff-card__avatar">
                {avatarUrl ? <img src={avatarUrl} alt="" loading="lazy" /> : <span>{initial(row)}</span>}
              </div>
              <div className="vkpi-channel-staff-card__main">
                <h3>{row.staffName}</h3>
                <p>{row.staffRole || 'staff'} · {formatter.format(row.accountCount)} 账号 · {formatter.format(row.platformCount)} 平台</p>
                <small>{row.platforms.join(' / ') || '-'} · Top {row.topAccount?.displayName || '-'}</small>
              </div>
              <div className="vkpi-channel-staff-card__value">
                <strong>{compact(row.views)}</strong>
                <small className={deltaTone(row.viewsDelta, viewsProtected)} title={viewsProtected ? rowProtectedTitle : undefined}>{deltaText(row.viewsDelta, viewsProtected)}</small>
              </div>
            </button>
          );
        })}
      </div>
    </section>
  );
}
