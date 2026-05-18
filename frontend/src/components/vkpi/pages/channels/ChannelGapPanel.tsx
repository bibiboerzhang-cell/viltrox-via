import { useState } from 'react';
import type { ChannelGapAccount } from './channelTypes';

const formatter = new Intl.NumberFormat('en-US');

function compact(value: number) {
  if (!value) return '0';
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 1 : 2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 10_000 ? 0 : 1)}K`;
  return formatter.format(value);
}

function summaryNumber(summary: Record<string, unknown>, key: string) {
  const value = Number(summary[key] ?? 0);
  return Number.isFinite(value) ? value : 0;
}

function issueLabels(accounts: ChannelGapAccount[]) {
  const labels = new Map<string, number>();
  accounts.forEach((account) => {
    account.issues.forEach((issue) => {
      labels.set(issue.label, (labels.get(issue.label) || 0) + 1);
    });
  });
  return Array.from(labels.entries()).sort((a, b) => b[1] - a[1]).slice(0, 4);
}

function syncStatusLabel(status: string) {
  if (status === 'no_results') return '抓取无结果';
  if (status === 'not_supported') return '未接入补抓';
  if (status === 'not_configured') return '待配置';
  if (status === 'configured_pending_provider') return '待同步';
  if (status === 'synced') return '已同步';
  return status || '待同步';
}

function providerLabel(account: ChannelGapAccount) {
  if (!account.autoRefillSupported) return '暂未接入自动补抓';
  return account.providerReady ? `${account.provider} 已就绪` : `${account.provider} 未就绪`;
}

export function ChannelGapPanel({
  accounts,
  summary,
  loading,
  error,
  onRefresh,
  compactMode = false,
}: {
  accounts: ChannelGapAccount[];
  summary: Record<string, unknown>;
  loading?: boolean;
  error?: string;
  onRefresh: () => void;
  compactMode?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const labels = issueLabels(accounts);
  const primaryIssue = labels[0];
  if (compactMode) {
    return (
      <section className="vkpi-channel-gaps vkpi-channel-gaps--compact">
        <button className="vkpi-evidence-gap-pill" type="button" onClick={() => setOpen(true)}>
          <span>证据缺口</span>
          <strong>{formatter.format(accounts.length)}</strong>
          {primaryIssue ? <em>{primaryIssue[0]} {primaryIssue[1]}</em> : <em>无明显缺口</em>}
        </button>
        {open ? (
          <div className="vkpi-glass-modal" role="dialog" aria-modal="true" aria-label="证据缺口">
            <button className="vkpi-glass-modal__backdrop" type="button" aria-label="关闭" onClick={() => setOpen(false)} />
            <section className="vkpi-glass-modal__panel">
              <header className="vkpi-glass-modal__header">
                <div>
                  <span>补数清单</span>
                  <h2>证据缺口</h2>
                </div>
                <div>
                  <button className="vkpi-mini-button" type="button" onClick={onRefresh} disabled={loading}>
                    {loading ? '检查中' : '重新检查'}
                  </button>
                  <button className="vkpi-glass-modal__close" type="button" aria-label="关闭" onClick={() => setOpen(false)}>
                    ×
                  </button>
                </div>
              </header>
              {error ? <div className="vkpi-inline-message">{error}</div> : null}
              <div className="vkpi-channel-gaps__bar">
                <div className="vkpi-channel-gaps__summary">
                  <strong>{formatter.format(accounts.length)}</strong><span>当前缺口</span>
                  <strong>{formatter.format(summaryNumber(summary, 'account_count'))}</strong><span>总账号</span>
                  <strong>{formatter.format(summaryNumber(summary, 'platform_count'))}</strong><span>平台</span>
                </div>
                <div className="vkpi-channel-gaps__chips">
                  {labels.length ? labels.map(([label, count]) => <span key={label}>{label} {count}</span>) : <span>无明显缺口</span>}
                </div>
              </div>
              {accounts.length ? (
                <div className="vkpi-channel-gap-list">
                  {accounts.map((account) => (
                    <article className="vkpi-channel-gap-card" key={`${account.platform}-${account.id}`}>
                      <div className="vkpi-channel-gap-card__main">
                        <div>
                          <h3>{account.displayName}</h3>
                          <p>{account.platformLabel} · @{account.handle || '-'} · {account.staffName}</p>
                        </div>
                        <strong>{compact(account.totalViews)}</strong>
                      </div>
                      <div className="vkpi-channel-gap-card__issues">
                        {account.issues.slice(0, 3).map((issue) => <span key={issue.key}>{issue.label}</span>)}
                      </div>
                      <div className="vkpi-channel-gap-card__meta">
                        <span>{formatter.format(account.postSampleCount)} / {formatter.format(account.postsCount)} 内容样本</span>
                        <span>{providerLabel(account)}</span>
                        <span>{syncStatusLabel(account.syncStatus)}</span>
                      </div>
                      <small>{account.recommendedAction || '待补抓'}</small>
                    </article>
                  ))}
                </div>
              ) : (
                <div className="vkpi-empty-state">{loading ? '正在检查账号素材与播放证据缺口。' : '当前筛选范围没有发现明显缺口。'}</div>
              )}
            </section>
          </div>
        ) : null}
      </section>
    );
  }
  return (
    <section className={`vkpi-channel-gaps${compactMode ? ' vkpi-channel-gaps--compact' : ''}`}>
      <div className="vkpi-channel-gaps__header">
        <div>
          <span>补数清单</span>
          <h2>素材与证据缺口</h2>
        </div>
        <button className="vkpi-mini-button" type="button" onClick={onRefresh} disabled={loading}>
          {loading ? '检查中' : '重新检查'}
        </button>
      </div>
      {error ? <div className="vkpi-inline-message">{error}</div> : null}
      <div className="vkpi-channel-gaps__bar">
        <div className="vkpi-channel-gaps__summary">
          <strong>{formatter.format(accounts.length)}</strong><span>当前缺口</span>
          <strong>{formatter.format(summaryNumber(summary, 'account_count'))}</strong><span>总账号</span>
          <strong>{formatter.format(summaryNumber(summary, 'platform_count'))}</strong><span>平台</span>
        </div>
        <div className="vkpi-channel-gaps__chips">
          {labels.length ? labels.map(([label, count]) => <span key={label}>{label} {count}</span>) : <span>无明显缺口</span>}
        </div>
      </div>
      {!compactMode && accounts.length ? (
        <div className="vkpi-channel-gap-list">
          {accounts.slice(0, 4).map((account) => (
            <article className="vkpi-channel-gap-card" key={`${account.platform}-${account.id}`}>
              <div className="vkpi-channel-gap-card__main">
                <div>
                  <h3>{account.displayName}</h3>
                  <p>{account.platformLabel} · @{account.handle || '-'} · {account.staffName}</p>
                </div>
                <strong>{compact(account.totalViews)}</strong>
              </div>
              <div className="vkpi-channel-gap-card__issues">
                {account.issues.slice(0, 2).map((issue) => <span key={issue.key}>{issue.label}</span>)}
              </div>
              <div className="vkpi-channel-gap-card__meta">
                <span>{formatter.format(account.postSampleCount)} / {formatter.format(account.postsCount)} 内容样本</span>
                <span>{providerLabel(account)}</span>
                <span>{syncStatusLabel(account.syncStatus)}</span>
              </div>
              <small>{account.recommendedAction || '待补抓'}</small>
            </article>
          ))}
        </div>
      ) : !compactMode ? (
        <div className="vkpi-empty-state">{loading ? '正在检查账号素材与播放证据缺口。' : '当前筛选范围没有发现明显缺口。'}</div>
      ) : null}
    </section>
  );
}
