import type { OfficialChannelPlatform } from './channelTypes';
import { proxiedImageUrl } from '../../shared/mediaProxy';
import { PlatformLogo } from './PlatformLogo';

const formatter = new Intl.NumberFormat('en-US');

export const DEFAULT_CHANNEL_SYNC_TIMEZONE = 'Asia/Shanghai';

export const CHANNEL_SYNC_TIMEZONES = [
  { value: 'Asia/Shanghai', label: '中国' },
  { value: 'America/Los_Angeles', label: '美国西部' },
  { value: 'America/New_York', label: '美国东部' },
  { value: 'Europe/London', label: '英国' },
  { value: 'Europe/Berlin', label: '德国/中欧' },
  { value: 'Asia/Tokyo', label: '日本' },
  { value: 'Asia/Seoul', label: '韩国' },
  { value: 'Asia/Singapore', label: '新加坡' },
  { value: 'Asia/Kolkata', label: '印度' },
  { value: 'Australia/Sydney', label: '澳大利亚东部' },
  { value: 'America/Sao_Paulo', label: '巴西' },
] as const;

export function isSupportedChannelSyncTimezone(value: string) {
  return CHANNEL_SYNC_TIMEZONES.some((item) => item.value === value);
}

function timezoneLabel(value: string) {
  return CHANNEL_SYNC_TIMEZONES.find((item) => item.value === value)?.label || value;
}

function syncTimeFormatter(timeZone: string, format: 'short' | 'full' = 'short') {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone,
    year: format === 'full' ? 'numeric' : undefined,
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: format === 'full' ? '2-digit' : undefined,
    hour12: false,
    timeZoneName: format === 'full' ? 'short' : undefined,
  });
}

function compact(value: number) {
  if (!value) return '0';
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 1 : 2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 10_000 ? 0 : 1)}K`;
  return formatter.format(value);
}

function deltaPercent(current: number, delta = 0) {
  const previous = current - delta;
  if (!delta || previous <= 0) return '';
  const percent = Math.abs(delta / previous) * 100;
  return `${percent >= 10 ? percent.toFixed(1) : percent.toFixed(2)}%`;
}

function protectedTitle(item?: Pick<OfficialChannelPlatform, 'baselineProtectedLabel' | 'baselineProtectedReason'>) {
  return item?.baselineProtectedLabel || item?.baselineProtectedReason || '本轮样本小于历史累计，沿用历史值';
}

function hasProtectedField(item: Pick<OfficialChannelPlatform, 'baselineProtected' | 'baselineProtectedFields'>, field: 'followers' | 'posts' | 'views') {
  if (!item.baselineProtected) return false;
  const fields = item.baselineProtectedFields || [];
  if (!fields.length) return true;
  const aliases = {
    followers: ['followers'],
    posts: ['posts_count'],
    views: ['total_views'],
  }[field];
  return aliases.some((alias) => fields.includes(alias));
}

function deltaLabel(current: number, delta = 0, baselineProtected = false) {
  if (baselineProtected && !delta) return '基线保护';
  if (!delta) return '较上次 0';
  const direction = delta > 0 ? '↑' : '↓';
  const percent = deltaPercent(current, delta);
  return `较上次 ${direction} ${percent || compact(Math.abs(delta))}`;
}

function deltaText(delta = 0, baselineProtected = false) {
  if (baselineProtected && !delta) return '保护';
  return delta ? `${delta > 0 ? '+' : ''}${compact(delta)}` : '基线';
}

function viewsValue(totalViews: number, unavailable?: boolean) {
  return unavailable ? '-' : compact(totalViews);
}

function viewsDeltaLabel(totalViews: number, delta = 0, unavailable?: boolean, baselineProtected = false) {
  if (unavailable) return '公开播放不可用';
  if (baselineProtected && !delta) return '基线保护';
  if (delta) return `播放 ${delta > 0 ? '+' : ''}${compact(delta)}`;
  return totalViews > 0 ? '播放已同步' : '无公开播放';
}

function deltaTone(delta = 0, baselineProtected = false) {
  if (baselineProtected && !delta) return 'is-protected';
  if (delta > 0) return 'is-up';
  if (delta < 0) return 'is-down';
  return '';
}

function syncTimeLabel(value?: string, timeZone = DEFAULT_CHANNEL_SYNC_TIMEZONE) {
  if (!value) return '本次未同步';
  const date = new Date(value);
  if (!Number.isNaN(date.getTime())) return `本次 ${syncTimeFormatter(timeZone).format(date)}`;
  return `本次 ${value.slice(0, 16).replace('T', ' ')}`;
}

function syncTimeTitle(value?: string, timeZone = DEFAULT_CHANNEL_SYNC_TIMEZONE) {
  if (!value) return '暂无同步时间';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const local = syncTimeFormatter(timeZone, 'full').format(date);
  const utc = syncTimeFormatter('UTC', 'full').format(date);
  return `${timezoneLabel(timeZone)}：${local}；UTC：${utc}；原始：${value}`;
}

function PlatformSkeletons() {
  return (
    <>
      {['fb', 'ig', 'rd', 'tt', 'x', 'yt'].map((item) => (
        <div className="vkpi-channel-platform-card vkpi-channel-platform-card--skeleton" key={item} aria-hidden="true">
          <span className="vkpi-skeleton vkpi-skeleton-avatar" />
          <div className="vkpi-channel-platform-card__body">
            <span className="vkpi-skeleton vkpi-skeleton-line is-medium" />
            <span className="vkpi-skeleton vkpi-skeleton-line is-long" />
            <div className="vkpi-channel-avatar-stack">
              <span className="vkpi-skeleton vkpi-skeleton-avatar is-round" />
              <span className="vkpi-skeleton vkpi-skeleton-avatar is-round" />
              <span className="vkpi-skeleton vkpi-skeleton-avatar is-round" />
            </div>
          </div>
          <div className="vkpi-channel-platform-card__metrics">
            <span className="vkpi-skeleton vkpi-skeleton-line is-short" />
            <span className="vkpi-skeleton vkpi-skeleton-line is-medium" />
            <span className="vkpi-skeleton vkpi-skeleton-line is-medium" />
          </div>
        </div>
      ))}
    </>
  );
}

export function ChannelPlatformMatrix({
  platforms,
  selectedPlatform,
  loading,
  error,
  accountCount,
  postCount,
  totalViews,
  onSelectPlatform,
  onOpenBindings,
  bindingCount,
  syncTimezone = DEFAULT_CHANNEL_SYNC_TIMEZONE,
  onSyncTimezoneChange,
}: {
  platforms: OfficialChannelPlatform[];
  selectedPlatform: string;
  loading?: boolean;
  error?: string;
  accountCount: number;
  postCount: number;
  totalViews: number;
  onSelectPlatform: (platform: string) => void;
  onOpenBindings?: () => void;
  bindingCount?: number;
  syncTimezone?: string;
  onSyncTimezoneChange?: (timeZone: string) => void;
}) {
  const totalFollowers = platforms.reduce((sum, platform) => sum + platform.totalFollowers, 0);
  const totalFollowersDelta = platforms.reduce((sum, platform) => sum + (platform.followersDelta || 0), 0);
  const totalPostsDelta = platforms.reduce((sum, platform) => sum + (platform.postsDelta || 0), 0);
  const totalViewsDelta = platforms.reduce((sum, platform) => sum + (platform.viewsDelta || 0), 0);
  const totalFollowersProtected = !totalFollowersDelta && platforms.some((platform) => hasProtectedField(platform, 'followers'));
  const totalPostsProtected = !totalPostsDelta && platforms.some((platform) => hasProtectedField(platform, 'posts'));
  const totalViewsProtected = !totalViewsDelta && platforms.some((platform) => hasProtectedField(platform, 'views'));
  const syncedAccounts = platforms.reduce(
    (sum, platform) => sum + platform.accounts.filter((account) => account.syncStatus === 'synced').length,
    0,
  );
  const averageViews = postCount ? Math.round(totalViews / postCount) : 0;
  const summaryMetrics = [
    { label: '账号', value: formatter.format(accountCount) },
    { label: '已同步', value: formatter.format(syncedAccounts) },
    { label: '平台', value: formatter.format(platforms.length) },
    { label: '内容', value: formatter.format(postCount), delta: totalPostsDelta, protected: totalPostsProtected },
    { label: '粉丝', value: compact(totalFollowers), delta: totalFollowersDelta, protected: totalFollowersProtected },
    { label: '播放', value: compact(totalViews), delta: totalViewsDelta, protected: totalViewsProtected, primary: true },
    { label: '篇均播放', value: compact(averageViews) },
  ];
  return (
    <section className="vkpi-channel-matrix">
      <div className="vkpi-channel-matrix__header">
        <div>
          <div className="vkpi-channel-matrix__title-row">
            <button className="vkpi-channel-matrix-trigger" type="button" onClick={onOpenBindings}>
              <span>官方账号矩阵</span>
              <h2>平台总览</h2>
              <em>{formatter.format(bindingCount ?? accountCount)} 条绑定</em>
            </button>
            <label className="vkpi-channel-timezone">
              <span>时区</span>
              <select value={syncTimezone} onChange={(event) => onSyncTimezoneChange?.(event.target.value)} aria-label="同步时间显示时区">
                {CHANNEL_SYNC_TIMEZONES.map((item) => (
                  <option key={item.value} value={item.value}>{item.label}</option>
                ))}
              </select>
            </label>
          </div>
          {selectedPlatform ? (
            <button className="vkpi-channel-filter-reset" type="button" onClick={() => onSelectPlatform('')}>
              查看全部平台
            </button>
          ) : null}
        </div>
        <div className="vkpi-channel-matrix__totals">
          {summaryMetrics.map((metric) => (
            <span className={`vkpi-channel-summary-metric${metric.primary ? ' is-primary' : ''}`} key={metric.label}>
              <span className="vkpi-channel-summary-metric__main">
                <strong>{metric.value}</strong>
                <span>{metric.label}</span>
              </span>
              {'delta' in metric ? <small className={deltaTone(metric.delta, metric.protected)}>{deltaText(metric.delta, metric.protected)}</small> : null}
            </span>
          ))}
        </div>
      </div>
      {error ? <div className="vkpi-inline-message">{error}</div> : null}
      <div className="vkpi-channel-platforms">
        {loading && !platforms.length ? <PlatformSkeletons /> : null}
        {platforms.map((platform) => {
          const active = selectedPlatform === platform.platform;
          const avatars = platform.accounts.map((account) => proxiedImageUrl(account.avatarUrl)).filter(Boolean).slice(0, 4);
          const followerDelta = platform.followersDelta || platform.accounts.reduce((sum, account) => sum + (account.followersDelta || 0), 0);
          const viewsDelta = platform.viewsDelta || platform.accounts.reduce((sum, account) => sum + (account.viewsDelta || 0), 0);
          const viewsUnavailable = Boolean(platform.viewsUnavailable);
          const followerProtected = !followerDelta && hasProtectedField(platform, 'followers');
          const viewsProtected = !viewsDelta && hasProtectedField(platform, 'views');
          const platformProtectedTitle = protectedTitle(platform);
          return (
            <button
              type="button"
              className={`vkpi-channel-platform-card${active ? ' is-active' : ''}`}
              key={platform.platform}
              onClick={() => onSelectPlatform(platform.platform)}
            >
              <PlatformLogo platform={platform.platform} label={platform.label} />
              <div className="vkpi-channel-platform-card__body">
                <h3>{platform.label}</h3>
                <p>
                  {formatter.format(platform.accounts.length)} 账号 · {formatter.format(platform.totalPosts)} 内容
                  {platform.baselineProtected ? <span className="vkpi-channel-baseline-pill" title={platformProtectedTitle}>基线保护</span> : null}
                </p>
                <div className="vkpi-channel-avatar-stack" aria-label={`${platform.label} 账号头像`}>
                  {avatars.length ? avatars.map((avatar, index) => <img key={`${platform.platform}-${index}`} src={avatar} alt="" loading="lazy" />) : <span>暂无头像</span>}
                </div>
              </div>
              <div className="vkpi-channel-platform-card__metrics">
                <strong title={viewsUnavailable ? platform.viewsUnavailableReason : undefined}>{viewsValue(platform.totalViews, viewsUnavailable)}</strong>
                <span>{compact(platform.totalFollowers)} 粉丝</span>
                <em className={deltaTone(followerDelta, followerProtected)} title={followerProtected ? platformProtectedTitle : undefined}>
                  {deltaLabel(platform.totalFollowers, followerDelta, followerProtected)}
                </em>
                <small title={syncTimeTitle(platform.lastSyncAt, syncTimezone)}>{syncTimeLabel(platform.lastSyncAt, syncTimezone)}</small>
                <small className={deltaTone(viewsUnavailable ? 0 : viewsDelta, viewsProtected)} title={viewsProtected ? platformProtectedTitle : platform.viewsUnavailableReason}>
                  {viewsDeltaLabel(platform.totalViews, viewsDelta, viewsUnavailable, viewsProtected)}
                </small>
              </div>
            </button>
          );
        })}
      </div>
    </section>
  );
}
