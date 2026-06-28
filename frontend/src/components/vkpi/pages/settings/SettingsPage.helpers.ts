import { frontendBuildInfo, shortBuildSha } from '../../../../lib/buildInfo';
import { boolValue, numberValue, timeLabel } from '../../../../domains/settings';
import type { VkpiStaffInviteCapabilities } from '../../../../domains/settings';
import type { VkpiSyncOverview } from '../../../../domains/settings';
import type { VkpiDashboardData, VkpiProductCatalogItem, VkpiStaffMember } from '../../vkpiTypes';

// 取目录最新档案,但权限以「当前选中成员」为准(保存后已写入真值)——
// 防止 staff-directory 刷新返回的空 permissions 把刚授权的内容覆盖成空白。
export function resolveDrawerMember(
  staffMembers: VkpiStaffMember[],
  selected: VkpiStaffMember,
): VkpiStaffMember {
  const fresh = staffMembers.find((item) => item.id === selected.id);
  if (!fresh) return selected;
  const freshPerms = fresh.permissions || {};
  const selectedPerms = selected.permissions || {};
  const permissions = Object.keys(freshPerms).length ? freshPerms : selectedPerms;
  return { ...fresh, permissions };
}

interface SettingsDerivedInput {
  providers: Array<Record<string, unknown>>;
  budgetSettings: Array<Record<string, unknown>>;
  syncOverview: VkpiSyncOverview | null;
  productCatalog: VkpiProductCatalogItem[];
  data: VkpiDashboardData;
  syncPolicy: Record<string, unknown>;
  kolRefresh: Record<string, unknown>;
  kolRefreshBatchPlan: Record<string, unknown>;
  schedulerStatus: Record<string, unknown>;
  schedulerTasks: Array<Record<string, unknown>>;
  settingsError: string;
  providerError: string;
  rbacStatusError: string;
  frontendAsset: string;
  versionCheckedAt: string;
  inviteCapabilities: VkpiStaffInviteCapabilities | null;
  onInviteStaff?: unknown;
  apiToken?: string;
}

export function computeSettingsDerived({
  providers,
  budgetSettings,
  syncOverview,
  productCatalog,
  data,
  syncPolicy,
  kolRefresh,
  kolRefreshBatchPlan,
  schedulerStatus,
  schedulerTasks,
  settingsError,
  providerError,
  rbacStatusError,
  frontendAsset,
  versionCheckedAt,
  inviteCapabilities,
  onInviteStaff,
  apiToken,
}: SettingsDerivedInput) {
  const providerCount = providers.length;
  const providerConfiguredCount = providers.filter((row) => boolValue(row.configured, false)).length;
  const providerNames = providers
    .map((row) => String(row.label || row.provider || row.name || '').trim())
    .filter(Boolean)
    .slice(0, 6);
  const apiStatusText = providerCount
    ? `${providerConfiguredCount} / ${providerCount} 已配置`
    : '读取中';
  const apiStatusDetail = providerNames.length ? providerNames.join(' / ') : '对话引擎 / 多模态引擎 / 通用引擎 / Apify / YouTube';
  const totalBudgetUsd = budgetSettings.reduce((sum, row) => sum + numberValue(row.monthly_limit_usd), 0);
  const totalSpentUsd = budgetSettings.reduce((sum, row) => sum + numberValue(row.current_month_spent), 0);
  const dailySync = syncOverview?.daily_sync || null;
  const syncHealth = dailySync?.latest_summary?.health || dailySync?.latest_run?.health || {};
  const syncRequested = numberValue(syncHealth.total_requested);
  const syncErrors = numberValue(syncHealth.total_errors);
  const syncFailureRate = numberValue(syncHealth.failure_rate);
  const syncGuardText = dailySync?.ack_required ? '需 ack' : dailySync?.error ? '状态异常' : '可运行';
  const syncLastRun = dailySync?.latest_summary || dailySync?.latest_run || null;
  const syncLastRunStatus = String(syncLastRun?.status || (dailySync ? 'never_run' : '读取中'));
  const syncAck = dailySync?.latest_ack || null;
  const syncAckReason = String(syncAck?.reason || '');
  const skuCount = productCatalog.length || data.productCosts.length;
  const lensCount = productCatalog.filter((product) => ['Lens', 'Cine Lens'].includes(product.categoryMain)).length;
  const lightingCount = productCatalog.filter((product) => product.categoryMain === 'Lighting/Flash').length;
  const adapterCount = productCatalog.filter((product) => product.categoryMain === 'Adapter').length;
  const syncTime = String(syncPolicy.daily_sync_time || '08:00');
  const kolRefreshMode = String(kolRefresh.mode || 'searchable_records_only');
  const kolRefreshGateEnabled = boolValue(kolRefresh.provider_gate_enabled, false);
  const kolRefreshTotal = numberValue(kolRefresh.kol_pool_total);
  const kolRefreshHot = numberValue(kolRefresh.hot_count);
  const kolRefreshWarm = numberValue(kolRefresh.warm_count);
  const kolRefreshCold = numberValue(kolRefresh.cold_count);
  const kolRefreshActiveTasks = numberValue(kolRefresh.active_on_demand_tasks);
  const kolRefreshBatchTargets = numberValue(kolRefreshBatchPlan.target_count);
  const kolRefreshBatchCount = numberValue(kolRefreshBatchPlan.batch_count);
  const kolRefreshBatchConcurrency = numberValue(kolRefreshBatchPlan.max_concurrent_runs, 2);
  const kolRefreshGateText = kolRefreshGateEnabled ? '按需刷新已启用' : '仅记录/查询';
  const schedulerTaskTotal = numberValue(schedulerStatus.total, schedulerTasks.length);
  const schedulerTaskEnabled = numberValue(schedulerStatus.enabled, schedulerTasks.filter((row) => boolValue(row.enabled, false)).length);
  const systemHealth = settingsError || providerError || rbacStatusError || dailySync?.ack_required ? '需要处理' : 'healthy';
  const versionSummary = frontendAsset
    ? `${frontendAsset} · ${timeLabel(versionCheckedAt)}`
    : `${shortBuildSha(frontendBuildInfo.gitSha)} · ${timeLabel(frontendBuildInfo.builtAt)}`;
  const inviteMode: 'email' | 'manual_link' = inviteCapabilities?.email_available ? 'email' : 'manual_link';
  const canInviteStaff = inviteMode === 'email'
    ? Boolean(onInviteStaff)
    : Boolean(apiToken && (inviteCapabilities?.manual_activation_link_available ?? true));

  return {
    providerCount,
    providerConfiguredCount,
    providerNames,
    apiStatusText,
    apiStatusDetail,
    totalBudgetUsd,
    totalSpentUsd,
    dailySync,
    syncHealth,
    syncRequested,
    syncErrors,
    syncFailureRate,
    syncGuardText,
    syncLastRun,
    syncLastRunStatus,
    syncAck,
    syncAckReason,
    skuCount,
    lensCount,
    lightingCount,
    adapterCount,
    syncTime,
    kolRefreshMode,
    kolRefreshGateEnabled,
    kolRefreshTotal,
    kolRefreshHot,
    kolRefreshWarm,
    kolRefreshCold,
    kolRefreshActiveTasks,
    kolRefreshBatchTargets,
    kolRefreshBatchCount,
    kolRefreshBatchConcurrency,
    kolRefreshGateText,
    schedulerTaskTotal,
    schedulerTaskEnabled,
    systemHealth,
    versionSummary,
    inviteMode,
    canInviteStaff,
  };
}
