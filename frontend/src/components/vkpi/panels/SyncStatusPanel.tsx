// frontend/src/components/vkpi/panels/SyncStatusPanel.tsx
//
// R60: Sync 状态监控面板
//
// 加到 SettingsPage 现有 control-status tab 区域,或者独立小卡片.
// 不重新设计 settings UI,只加一个新 panel 复用现有.
//
// 数据来源:
//   GET  /api/admin/vkpi/sync/overview
//   GET  /api/admin/vkpi/sync/industry/failures
//   POST /api/admin/vkpi/sync/trigger/{job_name}  (admin 权限)

import { useEffect, useState } from 'react';

interface SyncOverview {
  industry?: {
    total_accounts: number;
    sync_status_breakdown: Record<string, number>;
    last_24h_success: number;
    last_24h_failed: number;
    platforms: Array<{
      platform: string;
      total_accounts: number;
      ok_count: number;
      failed_count: number;
      ok_rate: number;
    }>;
  };
  shopify?: {
    last_run_at: string | null;
    last_run_status: string;
    recent_runs: Array<Record<string, unknown>>;
  };
  cron_jobs?: Record<string, { last_run_at: string | null; status: string; detail?: string }>;
  platform_settings?: Record<string, unknown>;
  summary?: {
    overall_health: 'healthy' | 'degraded' | 'down';
    issues: Array<{ severity: string; category: string; message: string }>;
    checked_at: string;
  };
}

interface SyncStatusPanelProps {
  apiToken: string;
  isAdmin?: boolean;  // 是否显示手动触发按钮 (admin only)
  onLoadOverview: () => Promise<SyncOverview>;
  onTriggerSync?: (jobName: string) => Promise<void>;
}

export function SyncStatusPanel({ apiToken, isAdmin = false, onLoadOverview, onTriggerSync }: SyncStatusPanelProps) {
  const [overview, setOverview] = useState<SyncOverview>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [triggering, setTriggering] = useState<string | null>(null);

  async function loadOverview() {
    if (!apiToken) {
      setError('未登录');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const result = await onLoadOverview();
      setOverview(result);
    } catch (err) {
      setError((err as Error).message || '加载失败');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadOverview();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiToken]);

  async function handleTrigger(jobName: string) {
    if (!onTriggerSync) return;
    setTriggering(jobName);
    try {
      await onTriggerSync(jobName);
      // 等 2 秒让 cron 启动,再刷新
      setTimeout(() => void loadOverview(), 2000);
    } catch (err) {
      setError((err as Error).message || '触发失败');
    } finally {
      setTriggering(null);
    }
  }

  const summary = overview.summary;
  const healthBadgeClass = (() => {
    switch (summary?.overall_health) {
      case 'healthy': return 'is-success';
      case 'degraded': return 'is-warning';
      case 'down': return 'is-danger';
      default: return '';
    }
  })();

  return (
    <div className="vkpi-card">
      <div className="vkpi-card-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h3>Sync 状态监控</h3>
          <p style={{ fontSize: 12, color: 'var(--vkpi-color-text-muted)' }}>
            实时聚合 industry / Shopify / cron / 平台设置
          </p>
        </div>
        <div>
          {summary && (
            <span className={`vkpi-chip ${healthBadgeClass}`} style={{ marginRight: 8 }}>
              {summary.overall_health}
            </span>
          )}
          <button className="vkpi-button vkpi-button--small" type="button" onClick={() => void loadOverview()} disabled={loading}>
            {loading ? '加载中…' : '刷新'}
          </button>
        </div>
      </div>

      {error && (
        <div className="vkpi-alert vkpi-alert--error" style={{ marginTop: 12 }}>
          {error}
        </div>
      )}

      {/* 整体健康 issues */}
      {summary && summary.issues.length > 0 && (
        <div style={{ marginTop: 12 }}>
          {summary.issues.map((issue, idx) => (
            <div
              key={idx}
              className={`vkpi-alert vkpi-alert--${issue.severity === 'critical' ? 'error' : 'warning'}`}
              style={{ marginTop: 4, fontSize: 13 }}
            >
              [{issue.category}] {issue.message}
            </div>
          ))}
        </div>
      )}

      {/* Industry 同步 */}
      <section style={{ marginTop: 16 }}>
        <h4 style={{ fontSize: 14, marginBottom: 8 }}>Industry 同步</h4>
        {overview.industry ? (
          <>
            <div className="vkpi-form-row" style={{ gap: 16, fontSize: 13 }}>
              <div>账号总数: <strong>{overview.industry.total_accounts}</strong></div>
              <div>24h 成功: <strong style={{ color: 'green' }}>{overview.industry.last_24h_success}</strong></div>
              <div>24h 失败: <strong style={{ color: overview.industry.last_24h_failed > 0 ? 'red' : 'inherit' }}>{overview.industry.last_24h_failed}</strong></div>
            </div>
            
            {overview.industry.platforms.length > 0 && (
              <table className="vkpi-table" style={{ fontSize: 12, marginTop: 8 }}>
                <thead>
                  <tr><th>平台</th><th>账号</th><th>OK</th><th>失败</th><th>成功率</th></tr>
                </thead>
                <tbody>
                  {overview.industry.platforms.map((p) => (
                    <tr key={p.platform}>
                      <td>{p.platform}</td>
                      <td>{p.total_accounts}</td>
                      <td style={{ color: 'green' }}>{p.ok_count}</td>
                      <td style={{ color: p.failed_count > 0 ? 'red' : 'inherit' }}>{p.failed_count}</td>
                      <td>{(p.ok_rate * 100).toFixed(1)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        ) : (
          <div className="vkpi-empty">无数据</div>
        )}
      </section>

      {/* Cron Jobs */}
      <section style={{ marginTop: 16 }}>
        <h4 style={{ fontSize: 14, marginBottom: 8 }}>Cron Jobs</h4>
        {overview.cron_jobs ? (
          <table className="vkpi-table" style={{ fontSize: 12 }}>
            <thead>
              <tr><th>Job</th><th>最后运行</th><th>状态</th>{isAdmin && <th>操作</th>}</tr>
            </thead>
            <tbody>
              {Object.entries(overview.cron_jobs).map(([job, info]) => (
                <tr key={job}>
                  <td><code>{job}</code></td>
                  <td>{info.last_run_at || '从未运行'}</td>
                  <td>
                    <span className={`vkpi-chip ${info.status === 'success' ? 'is-success' : info.status === 'failed' ? 'is-danger' : ''}`}>
                      {info.status}
                    </span>
                  </td>
                  {isAdmin && (
                    <td>
                      <button
                        className="vkpi-button vkpi-button--small"
                        type="button"
                        onClick={() => void handleTrigger(job)}
                        disabled={triggering === job}
                      >
                        {triggering === job ? '触发中…' : '手动触发'}
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="vkpi-empty">无数据</div>
        )}
      </section>

      {/* Shopify */}
      {overview.shopify && (
        <section style={{ marginTop: 16 }}>
          <h4 style={{ fontSize: 14, marginBottom: 8 }}>Shopify 同步</h4>
          <div className="vkpi-form-row" style={{ fontSize: 13 }}>
            <div>最后运行: {overview.shopify.last_run_at || '从未运行'}</div>
            <div>状态: <span className={`vkpi-chip ${overview.shopify.last_run_status === 'ok' ? 'is-success' : ''}`}>
              {overview.shopify.last_run_status}
            </span></div>
          </div>
        </section>
      )}

      {summary && (
        <p style={{ fontSize: 11, color: 'var(--vkpi-color-text-muted)', marginTop: 12 }}>
          检查时间: {summary.checked_at}
        </p>
      )}
    </div>
  );
}
