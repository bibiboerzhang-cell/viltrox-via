import { frontendBuildInfo, shortBuildSha } from '../../../../lib/buildInfo';
import { percentLabel, numberValue, timeLabel } from '../../../../domains/settings';
import type { BackendBuildInfo } from '../../../../domains/settings';
import { InfoBlock } from '../../shared/InfoBlock';
import { SettingsApiSkeletonGrid, SettingsProviderGrid } from './SettingsPage.fragments';

type Row = Record<string, unknown>;

interface SettingsStatusPanelProps {
  apiStatusDetail: string;
  apiStatusText: string;
  backendBuild: BackendBuildInfo | null;
  dailySync: Row | null;
  frontendAsset: string;
  kolRefresh: Row;
  kolRefreshActiveTasks: number;
  kolRefreshBatchConcurrency: number;
  kolRefreshBatchCount: number;
  kolRefreshBatchTargets: number;
  kolRefreshCold: number;
  kolRefreshGateEnabled: boolean;
  kolRefreshGateText: string;
  kolRefreshHot: number;
  kolRefreshMode: string;
  kolRefreshTotal: number;
  kolRefreshWarm: number;
  providers: Row[];
  settingsLoading: boolean;
  syncAck: Row | null;
  syncAckReason: string;
  syncErrors: number;
  syncFailureRate: number;
  syncGuardText: string;
  syncHealth: Row;
  syncLastRun: Row | null;
  syncLastRunStatus: string;
  syncRequested: number;
  syncTime: string;
  systemHealth: string;
  totalBudgetUsd: number;
  totalSpentUsd: number;
  versionCheckedAt: string;
  onReloadVersionStatus: () => void;
}

export function SettingsStatusPanel({
  apiStatusDetail,
  apiStatusText,
  backendBuild,
  dailySync,
  frontendAsset,
  kolRefresh,
  kolRefreshActiveTasks,
  kolRefreshBatchConcurrency,
  kolRefreshBatchCount,
  kolRefreshBatchTargets,
  kolRefreshCold,
  kolRefreshGateEnabled,
  kolRefreshGateText,
  kolRefreshHot,
  kolRefreshMode,
  kolRefreshTotal,
  kolRefreshWarm,
  providers,
  settingsLoading,
  syncAck,
  syncAckReason,
  syncErrors,
  syncFailureRate,
  syncGuardText,
  syncHealth,
  syncLastRun,
  syncLastRunStatus,
  syncRequested,
  syncTime,
  systemHealth,
  totalBudgetUsd,
  totalSpentUsd,
  versionCheckedAt,
  onReloadVersionStatus,
}: SettingsStatusPanelProps) {
  return (
    <>
      <div className="vkpi-settings-status-grid">
        <InfoBlock label="API 服务" value={apiStatusText} />
        <InfoBlock label="服务范围" value={apiStatusDetail} />
        <InfoBlock label="同步" value={`每日 ${syncTime} · ${syncGuardText}`} />
        <InfoBlock label="KOL 分层" value={`${kolRefreshHot} hot / ${kolRefreshCold} cold`} />
        <InfoBlock label="按需刷新" value={`${kolRefreshGateText} · 任务 ${kolRefreshActiveTasks}`} />
        <InfoBlock label="本月成本" value={`$${totalSpentUsd.toLocaleString('en-US')} / $${totalBudgetUsd.toLocaleString('en-US')}`} />
        <InfoBlock label="系统" value={systemHealth} />
      </div>

      <section className="vkpi-settings-version-panel">
        <div className="vkpi-table-card__header">
          <div><h2>每日同步 Guard</h2><span>{syncLastRun ? `最近 ${timeLabel(syncLastRun.finished_at || syncLastRun.started_at)}` : '暂无运行记录'}</span></div>
        </div>
        {dailySync?.ack_required ? (
          <div className="vkpi-inline-message is-error">
            同步已暂停，需要 CLI ack 后才允许下次运行：{String((dailySync.blocking_run as Row | undefined)?.run_id || '-')}
          </div>
        ) : dailySync?.error ? (
          <div className="vkpi-inline-message is-warn">同步状态读取异常：{String(dailySync.error)}</div>
        ) : null}
        <div className="vkpi-settings-status-grid">
          <InfoBlock label="Guard 状态" value={syncGuardText} />
          <InfoBlock label="最近状态" value={syncLastRunStatus} />
          <InfoBlock label="失败率" value={`${percentLabel(syncFailureRate)} / 阈值 ${percentLabel(dailySync?.failure_rate_threshold ?? syncHealth.failure_rate_threshold ?? 0.1)}`} />
          <InfoBlock label="目标 / 错误" value={`${syncRequested.toLocaleString('en-US')} / ${syncErrors.toLocaleString('en-US')}`} />
          <InfoBlock label="KOL 错误" value={String(syncHealth.kol_errors ?? 0)} />
          <InfoBlock label="最近 ack" value={syncAckReason ? `${syncAckReason} · ${timeLabel(syncAck?.acknowledged_at)}` : '-'} />
        </div>
      </section>

      <section className="vkpi-settings-version-panel">
        <div className="vkpi-table-card__header">
          <div><h2>KOL 刷新分层</h2><span>{kolRefreshTotal ? `${kolRefreshTotal.toLocaleString('en-US')} 条历史记录` : '读取中'}</span></div>
        </div>
        <div className="vkpi-settings-status-grid">
          <InfoBlock label="当前模式" value={kolRefreshMode === 'stale_while_revalidate_enabled' ? '按需刷新已启用' : '仅记录/查询'} />
          <InfoBlock label="Provider Gate" value={kolRefreshGateEnabled ? '开启' : '关闭'} />
          <InfoBlock label="Hot / Warm / Cold" value={`${kolRefreshHot} / ${kolRefreshWarm} / ${kolRefreshCold}`} />
          <InfoBlock label="Cold 未刷新" value={String(numberValue(kolRefresh.cold_never_refreshed).toLocaleString('en-US'))} />
          <InfoBlock label="30 天搜索" value={`${numberValue(kolRefresh.searched_rows).toLocaleString('en-US')} 行 / ${numberValue(kolRefresh.search_count_30d).toLocaleString('en-US')} 次`} />
          <InfoBlock label="活跃刷新任务" value={String(kolRefreshActiveTasks)} />
          <InfoBlock label="Batch Plan" value={`${kolRefreshBatchTargets} 目标 / ${kolRefreshBatchCount} 批`} />
          <InfoBlock label="Batch 并发" value={`${kolRefreshBatchConcurrency} · plan-only`} />
        </div>
      </section>

      <section className="vkpi-settings-version-panel">
        <div className="vkpi-table-card__header">
          <div><h2>版本状态</h2><span>{versionCheckedAt ? `检查 ${timeLabel(versionCheckedAt)}` : '读取中'}</span></div>
          <button className="vkpi-button" type="button" onClick={onReloadVersionStatus}>刷新版本</button>
        </div>
        <div className="vkpi-settings-status-grid">
          <InfoBlock label="页面资源" value={frontendAsset || '-'} />
          <InfoBlock label="前端版本" value={`${shortBuildSha(frontendBuildInfo.gitSha)} · ${frontendBuildInfo.gitBranch}`} />
          <InfoBlock label="前端构建时间" value={timeLabel(frontendBuildInfo.builtAt)} />
          <InfoBlock label="后端版本" value={backendBuild ? `${shortBuildSha(backendBuild.git_sha)} · ${backendBuild.git_branch || '-'}` : '读取中'} />
          <InfoBlock label="后端构建时间" value={timeLabel(backendBuild?.build_time)} />
          <InfoBlock label="检查时间" value={timeLabel(versionCheckedAt)} />
        </div>
      </section>

      {settingsLoading && !providers.length ? (
        <SettingsApiSkeletonGrid />
      ) : (
        <SettingsProviderGrid providers={providers} />
      )}
    </>
  );
}
