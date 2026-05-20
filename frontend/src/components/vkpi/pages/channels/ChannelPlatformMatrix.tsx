import type { OfficialChannelPlatform } from './channelTypes';
import { proxiedImageUrl } from '../../shared/mediaProxy';
import { PlatformLogo } from './PlatformLogo';

const formatter = new Intl.NumberFormat('en-US');

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

function deltaLabel(current: number, delta = 0) {
  if (!delta) return '较上次 0';
  const direction = delta > 0 ? '↑' : '↓';
  const percent = deltaPercent(current, delta);
  return `较上次 ${direction} ${percent || compact(Math.abs(delta))}`;
}

function deltaText(delta = 0) {
  return delta ? `${delta > 0 ? '+' : ''}${compact(delta)}` : '基线';
}

function viewsDeltaLabel(totalViews: number, delta = 0) {
  if (delta) return `播放 ${delta > 0 ? '+' : ''}${compact(delta)}`;
  return totalViews > 0 ? '播放已同步' : '无公开播放';
}

function deltaTone(delta = 0) {
  if (delta > 0) return 'is-up';
  if (delta < 0) return 'is-down';
  return '';
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
}) {
  const totalFollowers = platforms.reduce((sum, platform) => sum + platform.totalFollowers, 0);
  const totalFollowersDelta = platforms.reduce((sum, platform) => sum + (platform.followersDelta || 0), 0);
  const totalPostsDelta = platforms.reduce((sum, platform) => sum + (platform.postsDelta || 0), 0);
  const totalViewsDelta = platforms.reduce((sum, platform) => sum + (platform.viewsDelta || 0), 0);
  const syncedAccounts = platforms.reduce(
    (sum, platform) => sum + platform.accounts.filter((account) => account.syncStatus === 'synced').length,
    0,
  );
  const averageViews = postCount ? Math.round(totalViews / postCount) : 0;
  const summaryMetrics = [
    { label: '账号', value: formatter.format(accountCount) },
    { label: '已同步', value: formatter.format(syncedAccounts) },
    { label: '平台', value: formatter.format(platforms.length) },
    { label: '内容', value: formatter.format(postCount), delta: totalPostsDelta },
    { label: '粉丝', value: compact(totalFollowers), delta: totalFollowersDelta },
    { label: '播放', value: compact(totalViews), delta: totalViewsDelta, primary: true },
    { label: '篇均播放', value: compact(averageViews) },
  ];
  return (
    <section className="vkpi-channel-matrix">
      <div className="vkpi-channel-matrix__header">
        <div>
          <button className="vkpi-channel-matrix-trigger" type="button" onClick={onOpenBindings}>
            <span>官方账号矩阵</span>
            <h2>平台总览</h2>
            <em>{formatter.format(bindingCount ?? accountCount)} 条绑定</em>
          </button>
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
              {'delta' in metric ? <small className={deltaTone(metric.delta)}>{deltaText(metric.delta)}</small> : null}
            </span>
          ))}
        </div>
      </div>
      {error ? <div className="vkpi-inline-message">{error}</div> : null}
      <div className="vkpi-channel-platforms">
        {loading && !platforms.length ? <div className="vkpi-empty-state">平台数据加载中。</div> : null}
        {platforms.map((platform) => {
          const active = selectedPlatform === platform.platform;
          const avatars = platform.accounts.map((account) => proxiedImageUrl(account.avatarUrl)).filter(Boolean).slice(0, 4);
          const followerDelta = platform.followersDelta || platform.accounts.reduce((sum, account) => sum + (account.followersDelta || 0), 0);
          const viewsDelta = platform.viewsDelta || platform.accounts.reduce((sum, account) => sum + (account.viewsDelta || 0), 0);
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
                <p>{formatter.format(platform.accounts.length)} 账号 · {formatter.format(platform.totalPosts)} 内容</p>
                <div className="vkpi-channel-avatar-stack" aria-label={`${platform.label} 账号头像`}>
                  {avatars.length ? avatars.map((avatar, index) => <img key={`${platform.platform}-${index}`} src={avatar} alt="" loading="lazy" />) : <span>暂无头像</span>}
                </div>
              </div>
              <div className="vkpi-channel-platform-card__metrics">
                <strong>{compact(platform.totalViews)}</strong>
                <span>{compact(platform.totalFollowers)} 粉丝</span>
                <em className={deltaTone(followerDelta)}>{deltaLabel(platform.totalFollowers, followerDelta)}</em>
                <small className={deltaTone(viewsDelta)}>{viewsDeltaLabel(platform.totalViews, viewsDelta)}</small>
              </div>
            </button>
          );
        })}
      </div>
    </section>
  );
}
