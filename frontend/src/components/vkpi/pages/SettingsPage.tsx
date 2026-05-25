import React, { useEffect, useState } from 'react';
import { buildApiUrl } from '../../../lib/api';
import { frontendBuildInfo, shortBuildSha } from '../../../lib/buildInfo';
import {
  getCommentAlertSettings,
  getControlStatus,
  getRbacStatus,
  listBudgetSettings,
  listFeatureFlags,
  listPlatformCrawlSettings,
  listProviderStatuses,
  runVkpiAutomation,
  updateBudgetSettings,
  updateCommentAlertSettings,
  updateFeatureFlags,
  updatePlatformCrawlSettings,
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
import {
  BudgetSettingsTable,
} from './settings/SettingsControlPanels';
import { permissionsForTemplate, vkpiPermissionFromTemplate, type StaffPermissionMap } from './settings/staffPermissionTemplates';
import { SettingsRulesPanel, type SettingsRulesTab } from './settings/SettingsRulesPanel';
import {
  EmployeeSettingsView,
  SettingsLoadingStrip,
  SettingsModule,
  type SettingsModuleKey,
} from './settings/SettingsPage.fragments';
import { SettingsStaffPanel } from './settings/SettingsStaffPanel';
import { SettingsSkuPanel } from './settings/SettingsSkuPanel';
import { SettingsStatusPanel } from './settings/SettingsStatusPanel';
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
  timeLabel,
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
  const [settingsError, setSettingsError] = useState('');
  const [settingsLoading, setSettingsLoading] = useState(false);
  const [expandedSection, setExpandedSection] = useState<'status' | 'sku' | 'staff' | 'funds' | 'rules' | null>('status');
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
  const isManager = viewMode === 'manager';

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
        const [providerResponse, rbacResponse, flagsResponse, crawlResponse, budgetResponse, controlResponse, commentAlertResponse, inviteCapabilitiesResponse, syncOverviewResponse] = await Promise.all([
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
    setMessage('激活链接已生成，复制后发给员工。');
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
          setMessage('员工邀请已发送。');
        } catch (error) {
          const rawMessage = error instanceof Error ? error.message : String(error);
          if (!rawMessage.includes('Email delivery unavailable')) throw error;
          await createManualActivationLink();
        }
      } else {
        await createManualActivationLink();
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '员工邀请失败');
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
      setMessage('员工深度权限已保存。');
      await onRefreshData?.();
      const refreshedMember = data.staffMembers.find((item) => item.id === staffId);
      if (refreshedMember) setSelectedStaffForPermissions({ ...refreshedMember, permissions });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '员工深度权限保存失败');
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
      setMessage(response.email_sent ? '密码重置邮件已发送。' : '密码重置链接已生成，请复制给员工。');
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

  const providerCount = providers.length;
  const providerConfiguredCount = providers.filter((row) => boolValue(row.configured, false)).length;
  const providerNames = providers
    .map((row) => String(row.label || row.provider || row.name || '').trim())
    .filter(Boolean)
    .slice(0, 6);
  const apiStatusText = providerCount
    ? `${providerConfiguredCount} / ${providerCount} 已配置`
    : '读取中';
  const apiStatusDetail = providerNames.length ? providerNames.join(' / ') : 'Claude / Gemini / OpenAI / Apify / YouTube';
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
  const systemHealth = settingsError || providerError || rbacStatusError || dailySync?.ack_required ? '需要处理' : 'healthy';
  const versionSummary = frontendAsset
    ? `${frontendAsset} · ${timeLabel(versionCheckedAt)}`
    : `${shortBuildSha(frontendBuildInfo.gitSha)} · ${timeLabel(frontendBuildInfo.builtAt)}`;
  const inviteMode = inviteCapabilities?.email_available ? 'email' : 'manual_link';
  const canInviteStaff = inviteMode === 'email'
    ? Boolean(onInviteStaff)
    : Boolean(apiToken && (inviteCapabilities?.manual_activation_link_available ?? true));
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
        {renderSettingsModule('status', `${apiStatusText} · 同步 ${syncTime} / ${syncGuardText} · KOL ${kolRefreshGateText} · ${systemHealth} · 版本 ${versionSummary}`, (
          <SettingsStatusPanel
            apiStatusDetail={apiStatusDetail}
            apiStatusText={apiStatusText}
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
            onReloadVersionStatus={() => void reloadVersionStatus()}
          />
        ))}
        {renderSettingsModule('sku', `${skuCount} 个 SKU · 镜头 ${lensCount} · 闪光灯 ${lightingCount} · 转接环 ${adapterCount}`, (
          <SettingsSkuPanel
            costSku={costSku}
            costProductName={costProductName}
            unitCostUsd={unitCostUsd}
            costNote={costNote}
            selectedCatalogProduct={selectedCatalogProduct}
            busy={busy}
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
          />
        ))}
        {renderSettingsModule('staff', `${data.staffMembers.length} 人 · 邀请 / 权限`, (
          <SettingsStaffPanel
            members={data.staffMembers}
            email={email}
            name={name}
            role={role}
            permission={permission}
            permissionTemplate={invitePermissionTemplate}
            busy={busy}
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
          />
        ))}
        {renderSettingsModule('funds', `$${totalSpentUsd.toLocaleString('en-US')} / $${totalBudgetUsd.toLocaleString('en-US')}`, (
          <BudgetSettingsTable
            budgetSettings={budgetSettings}
            busy={busy}
            rowEnabled={rowEnabled}
            onSaveBudgetSetting={(event, row) => void saveBudgetSetting(event, row)}
          />
        ))}
        {renderSettingsModule('rules', '功能 / 抓取 / 告警 / 同步', (
          <SettingsRulesPanel
            apiToken={apiToken}
            busy={busy}
            candidateLimitPerStaff={String(syncPolicy.candidate_limit_per_staff || 100)}
            commentAlertSettings={commentAlertSettings}
            failureRateThresholdLabel={percentLabel(dailySync?.failure_rate_threshold ?? 0.1)}
            featureFlags={featureFlags}
            platformCrawl={platformCrawl}
            rowEnabled={rowEnabled}
            rulesTab={rulesTab}
            syncGuardText={syncGuardText}
            syncTime={syncTime}
            syncTimezone={String(syncPolicy.timezone || 'Asia/Shanghai')}
            onRunMorningSync={() => void runMorningSync()}
            onRulesTabChange={setRulesTab}
            onSaveCommentAlertSettings={(event) => void saveCommentAlertSettings(event)}
            onToggleFeatureFlag={(row) => void toggleFeatureFlag(row)}
            onTogglePlatformCrawl={(row) => void togglePlatformCrawl(row)}
          />
        ))}
      </div>
      {selectedStaffForPermissions ? (
        <StaffPermissionDrawer
          member={data.staffMembers.find((item) => item.id === selectedStaffForPermissions.id) || selectedStaffForPermissions}
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
