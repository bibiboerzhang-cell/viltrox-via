import React from 'react';
import {
  updatePreferenceSettings,
  listPreferenceSettings,
  updateNotificationSettings,
  listNotificationSettings,
  listFeatureFlags,
  listPlatformCrawlSettings,
  listBudgetSettings,
  getControlStatus,
  getCommentAlertSettings,
  getSyncOverview,
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
  boolLabel,
  confirmHighRiskSettingChange,
  formNumber,
  moneyLabel,
  numberValue,
  platformBlockedReason,
  rowEnabled,
  settingChangeLine,
  summarizeSettingChange,
  updateStaffPermissions,
  createStaffActivationLink,
  createExistingStaffActivationLink,
  createStaffPasswordResetLink,
} from '../../../../domains/settings';
import type {
  VkpiSyncOverview,
  VkpiStaffActivationLinkResponse,
  VkpiStaffInviteCapabilities,
  VkpiStaffPasswordResetLinkResponse,
} from '../../../../domains/settings';
import type { VkpiDashboardData, VkpiProductCatalogItem, VkpiStaffMember } from '../../vkpiTypes';
import { permissionsForTemplate, type StaffPermissionMap } from './staffPermissionTemplates';

type ApiKeyDraft = { account_name: string; provider: string; key: string; daily_quota: string; enabled: boolean };

interface SettingsActionsDeps {
  apiToken?: string;
  data: VkpiDashboardData;
  onInviteStaff?: (payload: { email: string; name?: string; role: string; vkpiPermission: 'none' | 'read' | 'write'; permissions?: StaffPermissionMap; permissionTemplate?: string }) => Promise<void>;
  onUpsertProductCost?: (payload: { productSku: string; productName?: string; unitCostUsd: number; note?: string; active?: boolean }) => Promise<void>;
  onRefreshData?: () => void | Promise<void>;
  // state values
  email: string;
  name: string;
  role: string;
  permission: 'none' | 'read' | 'write';
  invitePermissionTemplate: string;
  costSku: string;
  costProductName: string;
  unitCostUsd: string;
  costNote: string;
  commentAlertSettings: Record<string, unknown>;
  schedulerTasks: Array<Record<string, unknown>>;
  schedulerStatus: Record<string, unknown>;
  apiKeyPool: Array<Record<string, unknown>>;
  keyDraft: ApiKeyDraft;
  inviteCapabilities: VkpiStaffInviteCapabilities | null;
  landingPage: string;
  dateRangeDefault: string;
  tableDensity: string;
  rowsPerPage: string;
  compactMode: boolean;
  rightPanelOpen: boolean;
  emailEnabled: boolean;
  inAppEnabled: boolean;
  dailyDigestEnabled: boolean;
  weeklySummaryEnabled: boolean;
  stalledProjectEnabled: boolean;
  claimActivityEnabled: boolean;
  attributionAlertEnabled: boolean;
  costAlertEnabled: boolean;
  systemAlertEnabled: boolean;
  quietHoursStart: string;
  quietHoursEnd: string;
  // setters / helpers from container
  hydratePreference: (row: Record<string, unknown> | null | undefined) => void;
  hydrateNotification: (row: Record<string, unknown> | null | undefined) => void;
  setBusy: (value: boolean) => void;
  setMessage: (value: string) => void;
  setSettingsError: (value: string) => void;
  setFeatureFlags: (value: Array<Record<string, unknown>>) => void;
  setPlatformCrawl: (value: Array<Record<string, unknown>>) => void;
  setBudgetSettings: (value: Array<Record<string, unknown>>) => void;
  setControlStatus: (value: Record<string, unknown>) => void;
  setCommentAlertSettings: (value: Record<string, unknown>) => void;
  setSyncOverview: (value: VkpiSyncOverview | null) => void;
  setSchedulerTasks: (value: Array<Record<string, unknown>>) => void;
  setSchedulerStatus: (value: Record<string, unknown>) => void;
  setApiKeyPool: (value: Array<Record<string, unknown>>) => void;
  setKeyDraft: (value: ApiKeyDraft) => void;
  setPreferenceList: (value: Array<Record<string, unknown>>) => void;
  setNotificationList: (value: Array<Record<string, unknown>>) => void;
  setCostSku: (value: string) => void;
  setCostProductName: (value: string) => void;
  setUnitCostUsd: (value: string) => void;
  setCostNote: (value: string) => void;
  setSelectedCatalogProduct: (value: VkpiProductCatalogItem | null) => void;
  setActivationLink: (value: VkpiStaffActivationLinkResponse | null) => void;
  setActivationCopied: (value: boolean) => void;
  setEmail: (value: string) => void;
  setName: (value: string) => void;
  setSelectedStaffForPermissions: (updater: (prev: VkpiStaffMember | null) => VkpiStaffMember | null) => void;
}

export function createSettingsActions(deps: SettingsActionsDeps) {
  const {
    apiToken, data, onInviteStaff, onUpsertProductCost, onRefreshData,
    email, name, role, permission, invitePermissionTemplate,
    costSku, costProductName, unitCostUsd, costNote, commentAlertSettings,
    schedulerTasks, schedulerStatus, apiKeyPool, keyDraft, inviteCapabilities,
    landingPage, dateRangeDefault, tableDensity, rowsPerPage, compactMode, rightPanelOpen,
    emailEnabled, inAppEnabled, dailyDigestEnabled, weeklySummaryEnabled, stalledProjectEnabled,
    claimActivityEnabled, attributionAlertEnabled, costAlertEnabled, systemAlertEnabled,
    quietHoursStart, quietHoursEnd,
    hydratePreference, hydrateNotification,
    setBusy, setMessage, setSettingsError, setFeatureFlags, setPlatformCrawl, setBudgetSettings,
    setControlStatus, setCommentAlertSettings, setSyncOverview, setSchedulerTasks, setSchedulerStatus,
    setApiKeyPool, setKeyDraft, setPreferenceList, setNotificationList,
    setCostSku, setCostProductName, setUnitCostUsd, setCostNote, setSelectedCatalogProduct,
    setActivationLink, setActivationCopied, setEmail, setName, setSelectedStaffForPermissions,
  } = deps;

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

  const copyActivationLink = async (activationLink: VkpiStaffActivationLinkResponse | null) => {
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
    if (member) setSelectedStaffForPermissions(() => member);
  };

  return {
    selectCatalogProduct,
    toggleFeatureFlag,
    toggleSchedulerTask,
    saveApiKey,
    removeApiKey,
    toggleApiKey,
    togglePlatformCrawl,
    saveBudgetSetting,
    saveCommentAlertSettings,
    savePreferences,
    saveNotifications,
    runMorningSync,
    submitInvite,
    saveStaffPermissionMatrix,
    createSelectedStaffActivationLink,
    createSelectedStaffPasswordResetLink,
    submitProductCost,
    copyActivationLink,
    openStaffPermissionDrawer,
  };
}
