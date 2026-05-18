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
  posts: number;
  views: number;
  platforms: string[];
  topAccount?: OfficialChannelAccount;
}

function compact(value: number) {
  if (!value) return '0';
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 1 : 2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 10_000 ? 0 : 1)}K`;
  return formatter.format(value);
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
        posts: 0,
        views: 0,
        platforms: [],
        platformSet: new Set<string>(),
      };
      row.accountCount += 1;
      row.followers += account.followers;
      row.posts += account.postsCount;
      row.views += account.totalViews;
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
      posts: acc.posts + row.posts,
      views: acc.views + row.views,
    }),
    { accounts: 0, followers: 0, posts: 0, views: 0 },
  );
  const totalPlatforms = new Set(platforms.map((platform) => platform.label || platform.platform)).size;
  const averageViews = totals.posts ? Math.round(totals.views / totals.posts) : 0;
  const summaryMetrics = [
    { label: '账号', value: formatter.format(totals.accounts) },
    { label: '负责人', value: formatter.format(rows.length) },
    { label: '平台', value: formatter.format(totalPlatforms) },
    { label: '内容', value: formatter.format(totals.posts) },
    { label: '粉丝', value: compact(totals.followers) },
    { label: '播放', value: compact(totals.views), primary: true },
    { label: '篇均播放', value: compact(averageViews) },
  ];
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
              <strong>{metric.value}</strong>
              <span>{metric.label}</span>
            </span>
          ))}
        </div>
      </div>
      <div className="vkpi-channel-staff-grid">
        {rows.map((row) => {
          const avatarUrl = proxiedImageUrl(row.staffAvatarUrl);
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
              <strong>{compact(row.views)}</strong>
            </button>
          );
        })}
      </div>
    </section>
  );
}
