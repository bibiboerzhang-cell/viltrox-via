import React, { useEffect, useState } from 'react';
import { buildApiUrl } from '../../../lib/api';
import { frontendBuildInfo } from '../../../lib/buildInfo';
import {
  getCommentAlertSettings,
  getControlStatus,
  getRbacStatus,
  getPreferenceSettings,
  updatePreferenceSettings,
  listPreferenceSettings,
  getNotificationSettings,
  updateNotificationSettings,
  listNotificationSettings,
  listBudgetSettings,
  listFeatureFlags,
  listPlatformCrawlSettings,
  listProviderStatuses,
  listSchedulerTasks,
  runVkpiAutomation,
  setSchedulerTaskEnabled,
  updateBudgetSettings,
  updateCommentAlertSettings,
  updateFeatureFlags,
  updatePlatformCrawlSettings,
  listApiKeyPool,
  upsertApiKey,
  deleteApiKey,
} from '../../../domains/settings';
import {
  getStaffInviteCapabilities,
  updateStaffPermissions,
  createStaffActivationLink,
  createExistingStaffActivationLink,
  createStaffPasswordResetLink,
} from '../../../domains/settings';
import type { VkpiStaffActivationLinkResponse, VkpiStaffInviteCapabilities, VkpiStaffPasswordResetLinkResponse } from '../../../domains/settings';
import { listProductCatalog } from '../../../domains/products';
import { getSyncOverview, type VkpiSyncOverview } from '../../../domains/settings';
import type {
  VkpiDashboardData,
  VkpiProductCatalogItem,
  VkpiStaffMember,
} from '../vkpiTypes';
import { PageShell } from './PageShell';
import { StaffPermissionDrawer } from './settings/StaffPermissionDrawer';
import { permissionsForTemplate, vkpiPermissionFromTemplate, type StaffPermissionMap } from './settings/staffPermissionTemplates';
import { type SettingsRulesTab } from './settings/SettingsRulesPanel';
import {
  EmployeeSettingsView,
  SettingsLoadingStrip,
  SettingsModule,
  type SettingsModuleKey,
} from './settings/SettingsPage.fragments';
import { SettingsCompanyZone, SettingsPersonalZone } from './settings/SettingsPage.Zones';
import { computeSettingsDerived, resolveDrawerMember } from './settings/SettingsPage.helpers';
import {
  boolLabel,
  boolValue,
  confirmHighRiskSettingChange,
  currentFrontendAsset,
  formNumber,
  moneyLabel,
  numberValue,
  percentLabel,
  platformBlockedReason,
  rowEnabled,
  settingChangeLine,
  summarizeSettingChange,
} from '../../../domains/settings';
import type { BackendBuildInfo } from '../../../domains/settings';

interface SettingsPageProps {
  data: VkpiDashboardData;
  viewMode: 'manager' | 'employee';
  apiToken?: string;
  onInviteStaff?: (payload: { email: string; name?: string; role: string; vkpiPermission: 'none' | 'read' | 'write'; permissions?: StaffPermissionMap; permissionTemplate?: string }) => Promise<void>;
  onUpdateStaffPermission?: (staffId: string, permission: 'none' | 'read' | 'write') => Promise<void>;
  onUpsertProductCost?: (payload: { productSku: string; productName?: string; unitCostUsd: number; note?: string; active?: boolean }) => Promise<void>;
  onOpenStaffProfile?: (staffId: string, fallback?: Partial<VkpiStaffMember>) => void | Promise<void>;
  onRefreshData?: () => void | Promise<void>;
}

export function SettingsPage({ data, viewMode, apiToken, onInviteStaff, onUpsertProductCost, onRefreshData }: SettingsPageProps) {
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [role, setRole] = useState('employee');
  const [permission, setPermission] = useState<'none' | 'read' | 'write'>('write');
  const [invitePermissionTemplate, setInvitePermissionTemplate] = useState('employee_workspace');
  const [costSku, setCostSku] = useState('');
  const [costProductName, setCostProductName] = useState('');
  const [unitCostUsd, setUnitCostUsd] = useState('');
  const [costNote, setCostNote] = useState('');
  const [productCatalog, setProductCatalog] = useState<VkpiProductCatalogItem[]>([]);
  const [productCatalogLoading, setProductCatalogLoading] = useState(false);
  const [productCatalogError, setProductCatalogError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [providers, setProviders] = useState<Array<Record<string, unknown>>>([]);
  const [providerError, setProviderError] = useState('');
  const [rbacStatus, setRbacStatus] = useState<Record<string, unknown>>({});
  const [rbacStatusError, setRbacStatusError] = useState('');
  const [featureFlags, setFeatureFlags] = useState<Array<Record<string, unknown>>>([]);
  const [platformCrawl, setPlatformCrawl] = useState<Array<Record<string, unknown>>>([]);
  const [budgetSettings, setBudgetSettings] = useState<Array<Record<string, unknown>>>([]);
  const [commentAlertSettings, setCommentAlertSettings] = useState<Record<string, unknown>>({});
  const [controlStatus, setControlStatus] = useState<Record<string, unknown>>({});
  const [syncOverview, setSyncOverview] = useState<VkpiSyncOverview | null>(null);
  const [schedulerTasks, setSchedulerTasks] = useState<Array<Record<string, unknown>>>([]);
  const [schedulerStatus, setSchedulerStatus] = useState<Record<string, unknown>>({});
  const [apiKeyPool, setApiKeyPool] = useState<Array<Record<string, unknown>>>([]);
  const [keyDraft, setKeyDraft] = useState<{ account_name: string; provider: string; key: string; daily_quota: string; enabled: boolean }>({ account_name: '', provider: 'gemini', key: '', daily_quota: '', enabled: true });
  const [settingsError, setSettingsError] = useState('');
  const [settingsLoading, setSettingsLoading] = useState(false);
  const [expandedSection, setExpandedSection] = useState<'status' | 'sku' | 'staff' | 'funds' | 'rules' | 'scheduler' | 'apikeys' | 'goaffpro' | 'preference' | 'notification' | null>('status');
  const [rulesTab, setRulesTab] = useState<SettingsRulesTab>('platform');
  const [productSearch, setProductSearch] = useState('');
  const [selectedCatalogProduct, setSelectedCatalogProduct] = useState<VkpiProductCatalogItem | null>(null);
  const [inviteCapabilities, setInviteCapabilities] = useState<VkpiStaffInviteCapabilities | null>(null);
  const [inviteCapabilitiesError, setInviteCapabilitiesError] = useState('');
  const [activationLink, setActivationLink] = useState<VkpiStaffActivationLinkResponse | null>(null);
  const [activationCopied, setActivationCopied] = useState(false);
  const [selectedStaffForPermissions, setSelectedStaffForPermissions] = useState<VkpiStaffMember | null>(null);
  const [backendBuild, setBackendBuild] = useState<BackendBuildInfo | null>(null);
  const [versionCheckedAt, setVersionCheckedAt] = useState('');
  const [frontendAsset, setFrontendAsset] = useState('');
  // 个人偏好(恢复 8b174068 下线的面板;后端 /settings/preferences 一直在)
  const [landingPage, setLandingPage] = useState('dashboard');
  const [dateRangeDefault, setDateRangeDefault] = useState('7d');
  const [tableDensity, setTableDensity] = useState('comfortable');
  const [rowsPerPage, setRowsPerPage] = useState('25');
  const [compactMode, setCompactMode] = useState(false);
  const [rightPanelOpen, setRightPanelOpen] = useState(false);
  const [preferenceList, setPreferenceList] = useState<Array<Record<string, unknown>>>([]);
  // 通知配置(v3 存储模式:只保存偏好,不实发)
  const [emailEnabled, setEmailEnabled] = useState(false);
  const [inAppEnabled, setInAppEnabled] = useState(true);
  const [dailyDigestEnabled, setDailyDigestEnabled] = useState(false);
  const [weeklySummaryEnabled, setWeeklySummaryEnabled] = useState(false);
  const [stalledProjectEnabled, setStalledProjectEnabled] = useState(false);
  const [claimActivityEnabled, setClaimActivityEnabled] = useState(false);
  const [attributionAlertEnabled, setAttributionAlertEnabled] = useState(false);
  const [costAlertEnabled, setCostAlertEnabled] = useState(false);
  const [systemAlertEnabled, setSystemAlertEnabled] = useState(false);
  const [quietHoursStart, setQuietHoursStart] = useState('');
  const [quietHoursEnd, setQuietHoursEnd] = useState('');
  const [notificationList, setNotificationList] = useState<Array<Record<string, unknown>>>([]);
  const isManager = viewMode === 'manager';

  const hydratePreference = (row: Record<string, unknown> | null | undefined) => {
    const prefs = ((row?.preferences as Record<string, unknown>) || {});
    setLandingPage(String(prefs.landing_page || 'dashboard'));
    setDateRangeDefault(String(prefs.date_range_default || '7d'));
    setTableDensity(String(prefs.table_density || 'comfortable'));
    setRowsPerPage(String(prefs.rows_per_page ?? '25'));
    setCompactMode(boolValue(prefs.compact_mode, false));
    setRightPanelOpen(boolValue(prefs.right_panel_open, false));
  };

  const hydrateNotification = (row: Record<string, unknown> | null | undefined) => {
    const s = ((row?.settings as Record<string, unknown>) || {});
    setEmailEnabled(boolValue(s.email_enabled, false));
    setInAppEnabled(boolValue(s.in_app_enabled, true));
    setDailyDigestEnabled(boolValue(s.daily_digest_enabled, false));
    setWeeklySummaryEnabled(boolValue(s.weekly_summary_enabled, false));
    setStalledProjectEnabled(boolValue(s.stalled_project_enabled, false));
    setClaimActivityEnabled(boolValue(s.claim_activity_enabled, false));
    setAttributionAlertEnabled(boolValue(s.attribution_alert_enabled, false));
    setCostAlertEnabled(boolValue(s.cost_alert_enabled, false));
    setSystemAlertEnabled(boolValue(s.system_alert_enabled, false));
    setQuietHoursStart(String(s.quiet_hours_start || ''));
    setQuietHoursEnd(String(s.quiet_hours_end || ''));
  };

  const reloadVersionStatus = async () => {
    setFrontendAsset(currentFrontendAsset());
    setVersionCheckedAt(new Date().toISOString());
    try {
      const response = await fetch(buildApiUrl(`/health?client_build=${encodeURIComponent(frontendBuildInfo.gitSha)}`), {
        credentials: 'same-origin',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      });
      const payload = response.ok ? await response.json() : null;
      setBackendBuild(payload?.build || null);
    } catch {
      setBackendBuild(null);
    }
  };

  useEffect(() => {
    void reloadVersionStatus();
  }, []);

  useEffect(() => {
    if (!isManager || !apiToken) return;
    let cancelled = false;
    const load = async () => {
      setSettingsLoading(true);
      setProviderError('');
      setSettingsError('');
      try {
        const [providerResponse, rbacResponse, flagsResponse, crawlResponse, budgetResponse, controlResponse, commentAlertResponse, inviteCapabilitiesResponse, syncOverviewResponse, schedulerResponse, apiKeyPoolResponse] = await Promise.all([
          listProviderStatuses(apiToken),
          getRbacStatus(apiToken).catch((error) => {
            setRbacStatusError(error instanceof Error ? error.message : 'RBAC 状态读取失败');
            return {};
          }),
          listFeatureFlags(apiToken),
          listPlatformCrawlSettings(apiToken),
          listBudgetSettings(apiToken),
          getControlStatus(apiToken),
          getCommentAlertSettings(apiToken),
          getStaffInviteCapabilities(apiToken).catch((error) => {
            setInviteCapabilitiesError(error instanceof Error ? error.message : '邀请能力读取失败');
            return null;
          }),
          getSyncOverview(apiToken).catch(() => null),
          listSchedulerTasks(apiToken).catch(() => ({ tasks: [] as Array<Record<string, unknown>>, status: {} as Record<string, unknown> })),
          listApiKeyPool(apiToken).catch(() => ({ keys: [] as Array<Record<string, unknown>> })),
        ]);
        if (!cancelled) {
          setProviders(providerResponse.providers || []);
          setRbacStatus(rbacResponse || {});
          setFeatureFlags(flagsResponse.flags || []);
          setPlatformCrawl(crawlResponse.platforms || []);
          setBudgetSettings(budgetResponse.budgets || []);
          setControlStatus(controlResponse || {});
          setCommentAlertSettings(commentAlertResponse.settings || {});
          setInviteCapabilities(inviteCapabilitiesResponse);
          setSyncOverview(syncOverviewResponse);
          setSchedulerTasks((schedulerResponse.tasks as Array<Record<string, unknown>>) || []);
          setSchedulerStatus((schedulerResponse.status as Record<string, unknown>) || {});
          setApiKeyPool((apiKeyPoolResponse.keys as Array<Record<string, unknown>>) || []);
        }
      } catch (error) {
        if (!cancelled) setSettingsError(error instanceof Error ? error.message : '系统设置读取失败');
      } finally {
        if (!cancelled) setSettingsLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [apiToken, isManager]);

  useEffect(() => {
    if (!isManager || !apiToken) return;
    let cancelled = false;
    const loadProductCatalog = async () => {
      setProductCatalogLoading(true);
      setProductCatalogError('');
      try {
        const response = await listProductCatalog(apiToken, {
          categories: ['Lens', 'Cine Lens', 'Lighting', 'Lighting/Flash', 'Adapter', 'Macro Extension Tube', 'Accessories', 'Uv Filter', 'Monitor', 'Battery', 'Product'],
          limit: 500,
        });
        if (!cancelled) setProductCatalog(response.products || []);
      } catch (error) {
        if (!cancelled) setProductCatalogError(error instanceof Error ? error.message : '产品目录读取失败');
      } finally {
        if (!cancelled) setProductCatalogLoading(false);
      }
    };
    void loadProductCatalog();
    return () => {
      cancelled = true;
    };
  }, [apiToken, isManager]);

  // 个人偏好 + 通知配置(恢复的两面板;后端端点一直在)
  useEffect(() => {
    if (!isManager || !apiToken) return;
    let cancelled = false;
    const loadPrefs = async () => {
      try {
        const [prefResp, notifResp, prefListResp, notifListResp] = await Promise.all([
          getPreferenceSettings(apiToken),
          getNotificationSettings(apiToken),
          listPreferenceSettings(apiToken).catch(() => ({ preferences: [] as Array<Record<string, unknown>> })),
          listNotificationSettings(apiToken).catch(() => ({ notification_settings: [] as Array<Record<string, unknown>> })),
        ]);
        if (cancelled) return;
        hydratePreference(prefResp.preference);
        hydrateNotification(notifResp.notification_settings);
        setPreferenceList((prefListResp.preferences as Array<Record<string, unknown>>) || []);
        setNotificationList((notifListResp.notification_settings as Array<Record<string, unknown>>) || []);
      } catch (error) {
        if (!cancelled) setSettingsError(error instanceof Error ? error.message : '偏好 / 通知读取失败');
      }
    };
    void loadPrefs();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiToken, isManager]);

  const reloadSystemSettings = async () => {
    if (!apiToken) return;
    setSettingsError('');
    const [flagsResponse, crawlResponse, budgetResponse, controlResponse, commentAlertResponse, syncOverviewResponse] = await Promise.all([
      listFeatureFlags(apiToken),
      listPlatformCrawlSettings(apiToken),
      listBudgetSettings(apiToken),
      getControlStatus(apiToken),
      getCommentAlertSettings(apiToken),
      getSyncOverview(apiToken).catch(() => null),
    ]);
    setFeatureFlags(flagsResponse.flags || []);
    setPlatformCrawl(crawlResponse.platforms || []);
    setBudgetSettings(budgetResponse.budgets || []);
    setControlStatus(controlResponse || {});
    setCommentAlertSettings(commentAlertResponse.settings || {});
    setSyncOverview(syncOverviewResponse);
  };

  const syncPolicy = (controlStatus.sync_policy || {}) as Record<string, unknown>;
  const kolRefresh = (controlStatus.kol_refresh || {}) as Record<string, unknown>;
  const kolRefreshBatchPlan = (kolRefresh.apify_batch_plan || {}) as Record<string, unknown>;
  const selectCatalogProduct = (product: VkpiProductCatalogItem) => {
    setSelectedCatalogProduct(product);
    setCostSku(product.sku);
    setCostProductName(product.marketingName || product.modelName);
    if (product.priceUsd !== null && product.priceUsd !== undefined) setUnitCostUsd(String(product.priceUsd));
    setMessage(`已填入 ${product.sku}，参考价格 ${product.priceUsd == null ? '未定价' : `$${product.priceUsd.toLocaleString('en-US')}`}。`);
  };

  const toggleFeatureFlag = async (row: Record<string, unknown>) => {
    if (!apiToken) return;
    setBusy(true);
    try {
      await updateFeatureFlags(apiToken, [{ flag_key: row.flag_key, enabled: !rowEnabled(row), description: row.description }]);
      await reloadSystemSettings();
      setMessage('功能开关已更新。');
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : '功能开关更新失败');
    } finally {
      setBusy(false);
    }
  };

  const toggleSchedulerTask = async (taskKey: string, enabled: boolean) => {
    if (!apiToken || !taskKey) return;
    setSettingsError('');
    setBusy(true);
    try {
      const response = await setSchedulerTaskEnabled(apiToken, taskKey, enabled);
      const refreshed = await listSchedulerTasks(apiToken).catch(() => ({ tasks: schedulerTasks, status: schedulerStatus }));
      setSchedulerTasks((refreshed.tasks as Array<Record<string, unknown>>) || []);
      setSchedulerStatus((refreshed.status as Record<string, unknown>) || (response.status as Record<string, unknown>) || {});
      setMessage(`定时任务 ${taskKey} 已${enabled ? '开启' : '关闭'}(仅标记，本期不自动执行）。`);
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : '定时任务开关更新失败');
    } finally {
      setBusy(false);
    }
  };

  // 多账号 API key 池(设置位,7月手动填轮转)。key 单向:只写入/不回显;系统不自动轮转(worker 未接)。
  const refreshApiKeyPool = async () => {
    if (!apiToken) return;
    const resp = await listApiKeyPool(apiToken).catch(() => ({ keys: apiKeyPool }));
    setApiKeyPool((resp.keys as Array<Record<string, unknown>>) || []);
  };

  const saveApiKey = async () => {
    if (!apiToken) return;
    setSettingsError('');
    setBusy(true);
    try {
      await upsertApiKey(apiToken, {
        account_name: keyDraft.account_name,
        provider: keyDraft.provider,
        key: keyDraft.key,
        daily_quota: Number(keyDraft.daily_quota || 0),
        enabled: keyDraft.enabled,
      });
      await refreshApiKeyPool();
      setKeyDraft({ account_name: '', provider: 'gemini', key: '', daily_quota: '', enabled: true });
      setMessage('API key 已保存(密文入库,前端不回显)。');
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : 'API key 保存失败');
    } finally {
      setBusy(false);
    }
  };

  const removeApiKey = async (id: number) => {
    if (!apiToken || !id) return;
    setSettingsError('');
    setBusy(true);
    try {
      await deleteApiKey(apiToken, id);
      await refreshApiKeyPool();
      setMessage('API key 已删除。');
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : 'API key 删除失败');
    } finally {
      setBusy(false);
    }
  };

  const toggleApiKey = async (row: Record<string, unknown>) => {
    if (!apiToken) return;
    setSettingsError('');
    setBusy(true);
    try {
      // 不带 key 字段 = 保留旧密文,只切换启用状态。
      await upsertApiKey(apiToken, {
        id: row.id,
        account_name: row.account_name,
        provider: row.provider,
        daily_quota: Number(row.daily_quota || 0),
        enabled: !Boolean(row.enabled),
      });
      await refreshApiKeyPool();
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : 'API key 状态更新失败');
    } finally {
      setBusy(false);
    }
  };

  const togglePlatformCrawl = async (row: Record<string, unknown>) => {
    if (!apiToken) return;
    setSettingsError('');
    const nextEnabled = !rowEnabled(row, 'crawl_enabled');
    const payload = {
      platform: row.platform,
      crawl_enabled: nextEnabled,
      daily_account_limit: row.daily_account_limit,
      posts_per_account: row.posts_per_account,
      crawl_evening: row.crawl_evening,
      crawl_followers: row.crawl_followers,
      monthly_budget_usd: row.monthly_budget_usd,
    };
    const lines = [
      `平台: ${String(row.platform || '-')}`,
      settingChangeLine('抓取开关', boolLabel(rowEnabled(row, 'crawl_enabled')), boolLabel(nextEnabled)),
      `状态: ${platformBlockedReason({ ...row, crawl_enabled: nextEnabled })}`,
    ];
    if (!confirmHighRiskSettingChange('确认更新平台抓取开关', lines)) return;
    setBusy(true);
    try {
      await updatePlatformCrawlSettings(apiToken, [payload]);
      await reloadSystemSettings();
      setMessage(summarizeSettingChange('平台抓取开关已更新', lines));
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : '平台抓取更新失败');
    } finally {
      setBusy(false);
    }
  };

  const saveBudgetSetting = async (event: React.FormEvent<HTMLFormElement>, row: Record<string, unknown>) => {
    event.preventDefault();
    if (!apiToken) return;
    const form = new FormData(event.currentTarget);
    setSettingsError('');
    const payload = {
      budget_key: row.budget_key,
      enabled: form.get('enabled') === 'on',
      monthly_limit_usd: formNumber(form, 'monthly_limit_usd', numberValue(row.monthly_limit_usd)),
      current_month_spent: numberValue(row.current_month_spent),
      alert_threshold_pct: formNumber(form, 'alert_threshold_pct', numberValue(row.alert_threshold_pct, 80)),
    };
    const lines = [
      `预算项: ${String(row.budget_key || '-')}`,
      settingChangeLine('启用状态', boolLabel(rowEnabled(row)), boolLabel(payload.enabled)),
      settingChangeLine('月度上限 USD', moneyLabel(row.monthly_limit_usd), moneyLabel(payload.monthly_limit_usd)),
      `当前月已花费: ${moneyLabel(row.current_month_spent)}`,
      settingChangeLine('告警阈值', `${numberValue(row.alert_threshold_pct, 80)}%`, `${payload.alert_threshold_pct}%`),
    ];
    if (!confirmHighRiskSettingChange('确认保存预算控制', lines)) return;
    setBusy(true);
    try {
      await updateBudgetSettings(apiToken, [payload]);
      await reloadSystemSettings();
      setMessage(summarizeSettingChange('预算控制已保存', lines));
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : '预算控制保存失败');
    } finally {
      setBusy(false);
    }
  };

  const saveCommentAlertSettings = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!apiToken) return;
    const form = new FormData(event.currentTarget);
    setBusy(true);
    try {
      await updateCommentAlertSettings(apiToken, {
        enabled: form.get('enabled') === 'on',
        window_days: formNumber(form, 'window_days', numberValue(commentAlertSettings.window_days, 7)),
        min_negative: formNumber(form, 'min_negative', numberValue(commentAlertSettings.min_negative, 3)),
        min_critical: formNumber(form, 'min_critical', numberValue(commentAlertSettings.min_critical, 2)),
        min_hostile: formNumber(form, 'min_hostile', numberValue(commentAlertSettings.min_hostile, 1)),
      });
      await reloadSystemSettings();
      setMessage('评论风险告警阈值已保存。');
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : '评论风险告警阈值保存失败');
    } finally {
      setBusy(false);
    }
  };

  const savePreferences = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!apiToken) return;
    setBusy(true);
    try {
      const response = await updatePreferenceSettings(apiToken, {
        landing_page: landingPage,
        date_range_default: dateRangeDefault,
        table_density: tableDensity,
        rows_per_page: rowsPerPage,
        compact_mode: compactMode,
        right_panel_open: rightPanelOpen,
      });
      hydratePreference(response.preference);
      const listResp = await listPreferenceSettings(apiToken).catch(() => ({ preferences: [] as Array<Record<string, unknown>> }));
      setPreferenceList((listResp.preferences as Array<Record<string, unknown>>) || []);
      setMessage('个人偏好已保存。');
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : '个人偏好保存失败');
    } finally {
      setBusy(false);
    }
  };

  const saveNotifications = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!apiToken) return;
    setBusy(true);
    try {
      const response = await updateNotificationSettings(apiToken, {
        email_enabled: emailEnabled,
        in_app_enabled: inAppEnabled,
        daily_digest_enabled: dailyDigestEnabled,
        weekly_summary_enabled: weeklySummaryEnabled,
        stalled_project_enabled: stalledProjectEnabled,
        claim_activity_enabled: claimActivityEnabled,
        attribution_alert_enabled: attributionAlertEnabled,
        cost_alert_enabled: costAlertEnabled,
        system_alert_enabled: systemAlertEnabled,
        quiet_hours_start: quietHoursStart,
        quiet_hours_end: quietHoursEnd,
      });
      hydrateNotification(response.notification_settings);
      const listResp = await listNotificationSettings(apiToken).catch(() => ({ notification_settings: [] as Array<Record<string, unknown>> }));
      setNotificationList((listResp.notification_settings as Array<Record<string, unknown>>) || []);
      setMessage('通知配置已保存。');
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : '通知配置保存失败');
    } finally {
      setBusy(false);
    }
  };

  const runMorningSync = async () => {
    if (!apiToken) return;
    setBusy(true);
    try {
      const response = await runVkpiAutomation(apiToken, 'daily_outreach_digest_only', { limit: 100, max_videos: 50, period_days: 1 });
      setMessage(`08:00 同步任务已手动执行：${String(response.status || 'ok')}`);
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : '同步任务执行失败');
    } finally {
      setBusy(false);
    }
  };

  const invitePermissions = (): StaffPermissionMap => ({
    ...permissionsForTemplate(invitePermissionTemplate),
    vkpi: permission,
  });

  const createManualActivationLink = async () => {
    if (!apiToken || !email.trim()) return null;
    const response = await createStaffActivationLink(apiToken, {
      email: email.trim(),
      name: name.trim() || undefined,
      role,
      vkpiPermission: permission,
      permissions: invitePermissions(),
      permissionTemplate: invitePermissionTemplate,
    });
    setActivationLink(response);
    setActivationCopied(false);
    setEmail('');
    setName('');
    setMessage('激活链接已生成，复制后发给成员。');
    await onRefreshData?.();
    return response;
  };

  const submitInvite = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!email.trim()) return;
    setBusy(true);
    try {
      setActivationLink(null);
      setActivationCopied(false);
      const shouldSendEmail = Boolean(inviteCapabilities?.email_available && onInviteStaff);
      if (shouldSendEmail && onInviteStaff) {
        try {
          await onInviteStaff({
            email: email.trim(),
            name: name.trim() || undefined,
            role,
            vkpiPermission: permission,
            permissions: invitePermissions(),
            permissionTemplate: invitePermissionTemplate,
          });
          setEmail('');
          setName('');
          setMessage('成员邀请已发送。');
        } catch (error) {
          const rawMessage = error instanceof Error ? error.message : String(error);
          if (!rawMessage.includes('Email delivery unavailable')) throw error;
          await createManualActivationLink();
        }
      } else {
        await createManualActivationLink();
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '成员邀请失败');
    } finally {
      setBusy(false);
    }
  };

  const copyActivationLink = async () => {
    const url = String(activationLink?.activation_url || '');
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      setActivationCopied(true);
      setMessage('激活链接已复制。');
    } catch {
      setActivationCopied(false);
      setMessage('浏览器不允许自动复制，请手动选中链接复制。');
    }
  };

  const openStaffPermissionDrawer = (staffId: string, fallback?: Partial<VkpiStaffMember>) => {
    const member = data.staffMembers.find((item) => item.id === staffId) || (fallback as VkpiStaffMember | undefined);
    if (member) setSelectedStaffForPermissions(member);
  };

  const saveStaffPermissionMatrix = async (staffId: string, permissions: StaffPermissionMap) => {
    if (!apiToken) throw new Error('当前没有登录 token，无法保存权限。');
    setBusy(true);
    try {
      await updateStaffPermissions(apiToken, staffId, permissions);
      setMessage('成员深度权限已保存。');
      // 用函数式更新保留「当前选中成员」并写入刚保存的权限——不要读渲染闭包里的旧 data,
      // 否则 onRefreshData 重拉(staff-directory 旧版不带 permissions_json)后,抽屉会被空权限覆盖、
      // 看起来「授权全没了」。先就地保住已选成员+已存权限,再让刷新静默对齐真值。
      setSelectedStaffForPermissions((prev) =>
        prev && prev.id === staffId ? { ...prev, permissions } : prev,
      );
      await onRefreshData?.();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '成员深度权限保存失败');
      throw error;
    } finally {
      setBusy(false);
    }
  };

  const createSelectedStaffActivationLink = async (member: VkpiStaffMember): Promise<VkpiStaffActivationLinkResponse | null> => {
    if (!apiToken || !member.id) throw new Error('缺少账号 ID 或登录 token。');
    setBusy(true);
    try {
      const response = await createExistingStaffActivationLink(apiToken, member.id);
      setMessage('激活链接已生成。');
      await onRefreshData?.();
      return response;
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '激活链接生成失败');
      throw error;
    } finally {
      setBusy(false);
    }
  };

  const createSelectedStaffPasswordResetLink = async (staffId: string): Promise<VkpiStaffPasswordResetLinkResponse | null> => {
    if (!apiToken) throw new Error('当前没有登录 token，无法重置密码。');
    setBusy(true);
    try {
      const response = await createStaffPasswordResetLink(apiToken, staffId);
      setMessage(response.email_sent ? '密码重置邮件已发送。' : '密码重置链接已生成，请复制给成员。');
      return response;
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '密码重置链接生成失败');
      throw error;
    } finally {
      setBusy(false);
    }
  };

  const submitProductCost = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!onUpsertProductCost || !costSku.trim()) return;
    setBusy(true);
    try {
      await onUpsertProductCost({
        productSku: costSku.trim(),
        productName: costProductName.trim() || undefined,
        unitCostUsd: Number(unitCostUsd || 0),
        note: costNote.trim() || undefined,
        active: true,
      });
      setCostSku('');
      setCostProductName('');
      setUnitCostUsd('');
      setCostNote('');
      setMessage('SKU 成本已保存。');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'SKU 保存失败');
    } finally {
      setBusy(false);
    }
  };

  const {
    apiStatusText, apiStatusDetail, totalBudgetUsd, totalSpentUsd, dailySync,
    syncHealth, syncRequested, syncErrors, syncFailureRate, syncGuardText,
    syncLastRun, syncLastRunStatus, syncAck, syncAckReason, skuCount, lensCount,
    lightingCount, adapterCount, syncTime, kolRefreshMode, kolRefreshGateEnabled,
    kolRefreshTotal, kolRefreshHot, kolRefreshWarm, kolRefreshCold,
    kolRefreshActiveTasks, kolRefreshBatchTargets, kolRefreshBatchCount,
    kolRefreshBatchConcurrency, kolRefreshGateText, schedulerTaskTotal,
    schedulerTaskEnabled, systemHealth, versionSummary, inviteMode, canInviteStaff,
  } = computeSettingsDerived({
    providers, budgetSettings, syncOverview, productCatalog, data, syncPolicy,
    kolRefresh, kolRefreshBatchPlan, schedulerStatus, schedulerTasks,
    settingsError, providerError, rbacStatusError, frontendAsset,
    versionCheckedAt, inviteCapabilities, onInviteStaff, apiToken,
  });
  const renderSettingsModule = (
    key: SettingsModuleKey,
    subtitle: string,
    children: React.ReactNode,
  ) => (
    <SettingsModule
      moduleKey={key}
      open={expandedSection === key}
      subtitle={subtitle}
      onToggle={() => setExpandedSection(expandedSection === key ? null : key)}
    >
      {children}
    </SettingsModule>
  );

  if (!isManager) {
    return <EmployeeSettingsView message={message} settingsError={settingsError} />;
  }

  return (
    <PageShell title="系统设置">
      {message ? <div className="vkpi-inline-message">{message}</div> : null}
      {settingsError ? <div className="vkpi-inline-message">{settingsError}</div> : null}
      <SettingsLoadingStrip settingsLoading={settingsLoading} catalogLoading={productCatalogLoading} />
      <div className="vkpi-settings-clean">
        <SettingsCompanyZone
          renderModule={renderSettingsModule}
          apiToken={apiToken}
          busy={busy}
          apiStatusText={apiStatusText}
          apiStatusDetail={apiStatusDetail}
          backendBuild={backendBuild}
          dailySync={dailySync as Record<string, unknown> | null}
          frontendAsset={frontendAsset}
          kolRefresh={kolRefresh}
          kolRefreshActiveTasks={kolRefreshActiveTasks}
          kolRefreshBatchConcurrency={kolRefreshBatchConcurrency}
          kolRefreshBatchCount={kolRefreshBatchCount}
          kolRefreshBatchTargets={kolRefreshBatchTargets}
          kolRefreshCold={kolRefreshCold}
          kolRefreshGateEnabled={kolRefreshGateEnabled}
          kolRefreshGateText={kolRefreshGateText}
          kolRefreshHot={kolRefreshHot}
          kolRefreshMode={kolRefreshMode}
          kolRefreshTotal={kolRefreshTotal}
          kolRefreshWarm={kolRefreshWarm}
          providers={providers}
          settingsLoading={settingsLoading}
          syncAck={syncAck as Record<string, unknown> | null}
          syncAckReason={syncAckReason}
          syncErrors={syncErrors}
          syncFailureRate={syncFailureRate}
          syncGuardText={syncGuardText}
          syncHealth={syncHealth}
          syncLastRun={syncLastRun as Record<string, unknown> | null}
          syncLastRunStatus={syncLastRunStatus}
          syncRequested={syncRequested}
          syncTime={syncTime}
          systemHealth={systemHealth}
          totalBudgetUsd={totalBudgetUsd}
          totalSpentUsd={totalSpentUsd}
          versionCheckedAt={versionCheckedAt}
          versionSummary={versionSummary}
          onReloadVersionStatus={() => void reloadVersionStatus()}
          skuCount={skuCount}
          lensCount={lensCount}
          lightingCount={lightingCount}
          adapterCount={adapterCount}
          costSku={costSku}
          costProductName={costProductName}
          unitCostUsd={unitCostUsd}
          costNote={costNote}
          selectedCatalogProduct={selectedCatalogProduct}
          canUpsert={Boolean(onUpsertProductCost)}
          productCatalog={productCatalog}
          productCatalogLoading={productCatalogLoading}
          productCatalogError={productCatalogError}
          productSearch={productSearch}
          onCostSkuChange={setCostSku}
          onCostProductNameChange={setCostProductName}
          onUnitCostUsdChange={setUnitCostUsd}
          onCostNoteChange={setCostNote}
          onProductSearchChange={setProductSearch}
          onSelectProduct={selectCatalogProduct}
          onSubmitProductCost={submitProductCost}
          members={data.staffMembers}
          email={email}
          name={name}
          role={role}
          permission={permission}
          permissionTemplate={invitePermissionTemplate}
          canInvite={canInviteStaff}
          inviteMode={inviteMode}
          inviteCapabilities={inviteCapabilities}
          inviteCapabilitiesError={inviteCapabilitiesError}
          activationLink={activationLink}
          activationCopied={activationCopied}
          onEmailChange={setEmail}
          onNameChange={setName}
          onRoleChange={setRole}
          onPermissionChange={setPermission}
          onPermissionTemplateChange={(value) => {
            setInvitePermissionTemplate(value);
            setPermission(vkpiPermissionFromTemplate(value));
          }}
          onCopyActivationLink={() => void copyActivationLink()}
          onSubmitInvite={submitInvite}
          onSelectStaff={openStaffPermissionDrawer}
          budgetSettings={budgetSettings}
          rowEnabled={rowEnabled}
          onSaveBudgetSetting={(event, row) => void saveBudgetSetting(event, row)}
          candidateLimitPerStaff={String(syncPolicy.candidate_limit_per_staff || 100)}
          commentAlertSettings={commentAlertSettings}
          failureRateThresholdLabel={percentLabel(dailySync?.failure_rate_threshold ?? 0.1)}
          featureFlags={featureFlags}
          platformCrawl={platformCrawl}
          rulesTab={rulesTab}
          syncTimezone={String(syncPolicy.timezone || 'Asia/Shanghai')}
          onRunMorningSync={() => void runMorningSync()}
          onRulesTabChange={setRulesTab}
          onSaveCommentAlertSettings={(event) => void saveCommentAlertSettings(event)}
          onToggleFeatureFlag={(row) => void toggleFeatureFlag(row)}
          onTogglePlatformCrawl={(row) => void togglePlatformCrawl(row)}
          schedulerTasks={schedulerTasks}
          schedulerStatus={schedulerStatus}
          schedulerTaskTotal={schedulerTaskTotal}
          schedulerTaskEnabled={schedulerTaskEnabled}
          onToggleSchedulerTask={(taskKey, enabled) => void toggleSchedulerTask(taskKey, enabled)}
          apiKeyPool={apiKeyPool}
          keyDraft={keyDraft}
          onKeyDraftChange={setKeyDraft}
          onToggleApiKey={(row) => void toggleApiKey(row)}
          onRemoveApiKey={(id) => void removeApiKey(id)}
          onSaveApiKey={() => void saveApiKey()}
        />
        <SettingsPersonalZone
          renderModule={renderSettingsModule}
          busy={busy}
          apiToken={apiToken}
          landingPage={landingPage}
          dateRangeDefault={dateRangeDefault}
          tableDensity={tableDensity}
          rowsPerPage={rowsPerPage}
          compactMode={compactMode}
          rightPanelOpen={rightPanelOpen}
          preferenceList={preferenceList}
          onLandingPageChange={setLandingPage}
          onDateRangeDefaultChange={setDateRangeDefault}
          onTableDensityChange={setTableDensity}
          onRowsPerPageChange={setRowsPerPage}
          onCompactModeChange={setCompactMode}
          onRightPanelOpenChange={setRightPanelOpen}
          onSubmitPreferences={(event) => void savePreferences(event)}
          emailEnabled={emailEnabled}
          inAppEnabled={inAppEnabled}
          dailyDigestEnabled={dailyDigestEnabled}
          weeklySummaryEnabled={weeklySummaryEnabled}
          stalledProjectEnabled={stalledProjectEnabled}
          claimActivityEnabled={claimActivityEnabled}
          attributionAlertEnabled={attributionAlertEnabled}
          costAlertEnabled={costAlertEnabled}
          systemAlertEnabled={systemAlertEnabled}
          quietHoursStart={quietHoursStart}
          quietHoursEnd={quietHoursEnd}
          notificationList={notificationList}
          boolValue={boolValue}
          onEmailEnabledChange={setEmailEnabled}
          onInAppEnabledChange={setInAppEnabled}
          onDailyDigestEnabledChange={setDailyDigestEnabled}
          onWeeklySummaryEnabledChange={setWeeklySummaryEnabled}
          onStalledProjectEnabledChange={setStalledProjectEnabled}
          onClaimActivityEnabledChange={setClaimActivityEnabled}
          onAttributionAlertEnabledChange={setAttributionAlertEnabled}
          onCostAlertEnabledChange={setCostAlertEnabled}
          onSystemAlertEnabledChange={setSystemAlertEnabled}
          onQuietHoursStartChange={setQuietHoursStart}
          onQuietHoursEndChange={setQuietHoursEnd}
          onSubmitNotifications={(event) => void saveNotifications(event)}
        />
      </div>
      {selectedStaffForPermissions ? (
        <StaffPermissionDrawer
          member={resolveDrawerMember(data.staffMembers, selectedStaffForPermissions)}
          busy={busy}
          onClose={() => setSelectedStaffForPermissions(null)}
          onSavePermissions={saveStaffPermissionMatrix}
          onCreateActivationLink={createSelectedStaffActivationLink}
          onCreatePasswordResetLink={createSelectedStaffPasswordResetLink}
        />
      ) : null}
    </PageShell>
  );
}
