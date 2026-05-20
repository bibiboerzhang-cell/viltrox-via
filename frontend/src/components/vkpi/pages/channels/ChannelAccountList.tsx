import type { OfficialChannelAccount, OfficialChannelPlatform } from './channelTypes';
import { proxiedImageUrl } from '../../shared/mediaProxy';

const formatter = new Intl.NumberFormat('en-US');

function compact(value: number) {
  if (!value) return '0';
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 1 : 2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 10_000 ? 0 : 1)}K`;
  return formatter.format(value);
}

function deltaTone(delta = 0) {
  if (delta > 0) return 'is-up';
  if (delta < 0) return 'is-down';
  return '';
}

function deltaText(delta = 0) {
  return delta ? `${delta > 0 ? '+' : ''}${compact(delta)}` : '+0';
}

function viewsDeltaText(totalViews: number, delta = 0) {
  if (delta) return `播放 ${deltaText(delta)}`;
  return totalViews > 0 ? '播放已同步' : '无公开播放';
}

function fallbackInitial(account: OfficialChannelAccount) {
  return (account.displayName || account.handle || account.platformLabel || 'V').slice(0, 1).toUpperCase();
}

function syncLabel(account: OfficialChannelAccount) {
  const labels: Record<string, string> = {
    configured_pending_provider: '待同步',
    no_results: '抓取无结果',
    not_configured: '待配置',
    not_supported: '未接入补抓',
    synced: '已同步',
  };
  const status = labels[account.syncStatus] || account.syncStatus || '待同步';
  const at = account.lastSyncAt ? account.lastSyncAt.slice(0, 16).replace('T', ' ') : '';
  const suffix = account.syncStatus === 'no_results' && account.lastSyncError ? ` · ${account.lastSyncError}` : '';
  return `${at ? `${status} · ${at}` : status}${suffix}`;
}

export function ChannelAccountList({
  platform,
  selectedAccountId,
  onSelectAccount,
}: {
  platform?: OfficialChannelPlatform;
  selectedAccountId?: number | null;
  onSelectAccount: (account: OfficialChannelAccount) => void;
}) {
  if (!platform) return null;
  return (
    <section className="vkpi-channel-accounts">
      <div className="vkpi-channel-accounts__header">
        <div>
          <span>账号层</span>
          <h2>{platform.label} 官方账号</h2>
        </div>
        <strong>{formatter.format(platform.accounts.length)} 个账号</strong>
      </div>
      <div className="vkpi-channel-account-grid">
        {platform.accounts.map((account) => {
          const active = selectedAccountId === account.id;
          const avatarUrl = proxiedImageUrl(account.avatarUrl);
          return (
            <button
              key={account.id || `${account.platform}-${account.handle}`}
              type="button"
              className={`vkpi-channel-account-card${active ? ' is-active' : ''}`}
              onClick={() => onSelectAccount(account)}
            >
              <div className="vkpi-channel-account-card__avatar">
                {avatarUrl ? <img src={avatarUrl} alt="" loading="lazy" /> : <span>{fallbackInitial(account)}</span>}
              </div>
              <div className="vkpi-channel-account-card__main">
                <div className="vkpi-channel-account-card__title">
                  <h3>{account.displayName}</h3>
                  <span className="vkpi-channel-account-card__value">
                    <strong>{compact(account.totalViews)}</strong>
                    <small className={deltaTone(account.viewsDelta)}>{viewsDeltaText(account.totalViews, account.viewsDelta)}</small>
                  </span>
                </div>
                <p>
                  @{account.handle || '-'} · {formatter.format(account.followers)} 粉丝
                  <em className={deltaTone(account.followersDelta)}> {deltaText(account.followersDelta)}</em>
                  {' · '}
                  {formatter.format(account.postsCount)} 内容
                  <em className={deltaTone(account.postsDelta)}> {deltaText(account.postsDelta)}</em>
                </p>
                <div className="vkpi-channel-account-card__metrics">
                  <span>赞 {formatter.format(account.totalLikes)}</span>
                  <span>评论 {formatter.format(account.totalComments)}</span>
                  <span>互动率 {account.engagementRate ? `${account.engagementRate.toFixed(2)}%` : '-'}</span>
                </div>
                <small>{syncLabel(account)}</small>
              </div>
            </button>
          );
        })}
      </div>
    </section>
  );
}
