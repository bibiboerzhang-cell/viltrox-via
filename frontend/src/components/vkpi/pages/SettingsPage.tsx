import React, { useEffect, useState } from 'react';
import { buildApiUrl } from '../../../lib/api';
import { frontendBuildInfo, shortBuildSha } from '../../../lib/buildInfo';
import {
  getCommentAlertSettings,
  getControlStatus,
  getStaffInviteCapabilities,
  getRbacStatus,
  getSyncOverview,
  listBudgetSettings,
  listFeatureFlags,
  listPlatformCrawlSettings,
  listProductCatalog,
  listProviderStatuses,
  probeProviderStatus,
  runVkpiAutomation,
  triggerSync,
  updateBudgetSettings,
  updateCommentAlertSettings,
  updateFeatureFlags,
  updatePlatformCrawlSettings,
  updateStaffPermissions,
  createStaffActivationLink,
  createExistingStaffActivationLink,
  createStaffPasswordResetLink,
} from '../../../services/vkpi.ui-api';
import type { VkpiPermissionLevel, VkpiStaffActivationLinkResponse, VkpiStaffInviteCapabilities, VkpiStaffPasswordResetLinkResponse } from '../../../services/vkpi.ui-api';
import type {
  VkpiDashboardData,
  VkpiProductCatalogItem,
  VkpiStaffMember,
} from '../vkpiTypes';
import { CardHeader } from '../shared/CardHeader';
import { InfoBlock } from '../shared/InfoBlock';
import { StaffTable } from '../tables/StaffTable';
import { PageShell } from './PageShell';
import {
  ProductCostFormCard,
  ProductCatalogPreviewCard,
  StaffInviteCard,
} from './settings/SettingsAdminCards';
import { StaffPermissionDrawer } from './settings/StaffPermissionDrawer';
import {
  BudgetSettingsTable,
  CommentAlertThresholdCard,
  FeatureFlagsPanel,
  PlatformCrawlPanel,
} from './settings/SettingsControlPanels';

interface SettingsPageProps {
  data: VkpiDashboardData;
  viewMode: 'manager' | 'employee';
  apiToken?: string;
  onInviteStaff?: (payload: { email: string; name?: string; role: string; vkpiPermission: 'none' | 'read' | 'write' }) => Promise<void>;
  onUpdateStaffPermission?: (staffId: string, permission: 'none' | 'read' | 'write') => Promise<void>;
  onUpsertProductCost?: (payload: { productSku: string; productName?: string; unitCostUsd: number; note?: string; active?: boolean }) => Promise<void>;
  onOpenStaffProfile?: (staffId: string, fallback?: Partial<VkpiStaffMember>) => void | Promise<void>;
  onRefreshData?: () => void | Promise<void>;
}

interface BackendBuildInfo {
  git_sha?: string;
  git_short_sha?: string;
  git_branch?: string;
  build_time?: string;
  client_matches_server?: boolean;
  client_build_source?: string;
}

function SettingsApiSkeletonGrid() {
  return (
    <div className="vkpi-settings-api-grid" aria-hidden="true">
      {['apify', 'openai', 'anthropic', 'google', 'resend', 'storage'].map((item) => (
        <article className="vkpi-settings-api-card vkpi-settings-api-card--skeleton" key={item}>
          <header>
            <span className="vkpi-skeleton vkpi-skeleton-line is-medium" />
            <span className="vkpi-skeleton vkpi-skeleton-pill" />
          </header>
          <span className="vkpi-skeleton vkpi-skeleton-line is-long" />
          <span className="vkpi-skeleton vkpi-skeleton-line is-short" />
        </article>
      ))}
    </div>
  );
}

function SettingsLoadingStrip({ settingsLoading, catalogLoading }: { settingsLoading: boolean; catalogLoading: boolean }) {
  if (!settingsLoading && !catalogLoading) return null;
  const label = settingsLoading && catalogLoading
    ? '正在读取系统状态和 SKU 目录'
    : settingsLoading
      ? '正在读取 API / 权限 / 规则状态'
      : '正在读取 SKU 目录';
  return (
    <div className="vkpi-settings-loading-strip" aria-live="polite">
      <div>
        <strong>{label}</strong>
        <span>{settingsLoading ? '系统配置' : '系统配置已就绪'} · {catalogLoading ? '产品目录' : '产品目录已就绪'}</span>
      </div>
      <div className="vkpi-settings-loading-strip__bar" aria-hidden="true"><span /></div>
    </div>
  );
}

export function SettingsPage({ data, viewMode, apiToken, onInviteStaff, onUpsertProductCost, onRefreshData }: SettingsPageProps) {
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [role, setRole] = useState('employee');
  const [permission, setPermission] = useState<'none' | 'read' | 'write'>('write');
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
  const [providerBusy, setProviderBusy] = useState('');
  const [providerError, setProviderError] = useState('');
  const [rbacStatus, setRbacStatus] = useState<Record<string, unknown>>({});
  const [rbacStatusError, setRbacStatusError] = useState('');
  const [rbacStatusLoading, setRbacStatusLoading] = useState(false);
  const [featureFlags, setFeatureFlags] = useState<Array<Record<string, unknown>>>([]);
  const [platformCrawl, setPlatformCrawl] = useState<Array<Record<string, unknown>>>([]);
  const [budgetSettings, setBudgetSettings] = useState<Array<Record<string, unknown>>>([]);
  const [commentAlertSettings, setCommentAlertSettings] = useState<Record<string, unknown>>({});
  const [controlStatus, setControlStatus] = useState<Record<string, unknown>>({});
  const [settingsError, setSettingsError] = useState('');
  const [settingsLoading, setSettingsLoading] = useState(false);
  const [expandedSection, setExpandedSection] = useState<'status' | 'sku' | 'staff' | 'funds' | 'rules' | null>('status');
  const [rulesTab, setRulesTab] = useState<'core' | 'platform' | 'alerts' | 'sync'>('platform');
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

  const boolValue = (value: unknown, fallback = false) => {
    if (value === undefined || value === null) return fallback;
    if (typeof value === 'string') return ['1', 'true', 'yes', 'on', 'enabled'].includes(value.toLowerCase());
    return Boolean(value);
  };

  const currentFrontendAsset = () => {
    if (typeof document === 'undefined') return '';
    const src = Array.from(document.scripts)
      .map((script) => script.src)
      .find((srcValue) => srcValue.includes('/assets/app-'));
    return src ? src.split('/').pop() || src : '';
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
        const [providerResponse, rbacResponse, flagsResponse, crawlResponse, budgetResponse, controlResponse, commentAlertResponse, inviteCapabilitiesResponse] = await Promise.all([
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
    const [flagsResponse, crawlResponse, budgetResponse, controlResponse, commentAlertResponse] = await Promise.all([
      listFeatureFlags(apiToken),
      listPlatformCrawlSettings(apiToken),
      listBudgetSettings(apiToken),
      getControlStatus(apiToken),
      getCommentAlertSettings(apiToken),
    ]);
    setFeatureFlags(flagsResponse.flags || []);
    setPlatformCrawl(crawlResponse.platforms || []);
    setBudgetSettings(budgetResponse.budgets || []);
    setControlStatus(controlResponse || {});
    setCommentAlertSettings(commentAlertResponse.settings || {});
  };

  const reloadProviders = async () => {
    if (!apiToken) return;
    setProviderBusy('all');
    setProviderError('');
    try {
      const response = await listProviderStatuses(apiToken);
      setProviders(response.providers || []);
    } catch (error) {
      setProviderError(error instanceof Error ? error.message : 'API 状态读取失败');
    } finally {
      setProviderBusy('');
    }
  };

  const reloadRbacStatus = async () => {
    if (!apiToken) return;
    setRbacStatusLoading(true);
    setRbacStatusError('');
    try {
      const response = await getRbacStatus(apiToken);
      setRbacStatus(response || {});
    } catch (error) {
      setRbacStatusError(error instanceof Error ? error.message : 'RBAC 状态读取失败');
    } finally {
      setRbacStatusLoading(false);
    }
  };

  const runProviderProbe = async (provider: string) => {
    if (!apiToken || !provider) return;
    setProviderBusy(provider);
    setProviderError('');
    try {
      await probeProviderStatus(apiToken, provider);
      const response = await listProviderStatuses(apiToken);
      setProviders(response.providers || []);
    } catch (error) {
      setProviderError(error instanceof Error ? error.message : 'API 检测失败');
    } finally {
      setProviderBusy('');
    }
  };

  const rowEnabled = (row: Record<string, unknown>, key = 'enabled') => {
    const raw = row[key];
    return raw === true || raw === 1 || raw === '1' || String(raw).toLowerCase() === 'true';
  };
  const numberValue = (value: unknown, fallback = 0) => {
    const next = Number(value ?? fallback);
    return Number.isFinite(next) ? next : fallback;
  };
  const formNumber = (form: FormData, key: string, fallback = 0) => {
    const raw = form.get(key);
    return numberValue(raw === null ? fallback : String(raw), fallback);
  };
  const platformBlockedReason = (row: Record<string, unknown>) => (
    rowEnabled(row, 'crawl_enabled') ? '已开启' : '已关闭'
  );
  const boolLabel = (value: boolean) => (value ? '开启' : '关闭');
  const moneyLabel = (value: unknown) => `$${numberValue(value).toLocaleString('en-US')}`;
  const timeLabel = (value: unknown) => {
    const raw = String(value || '').trim();
    if (!raw || raw === 'unknown') return '-';
    const date = new Date(raw);
    if (Number.isNaN(date.getTime())) return raw;
    return new Intl.DateTimeFormat('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    }).format(date);
  };
  const settingChangeLine = (label: string, before: string | number, after: string | number) => (
    `${label}: ${before} -> ${after}`
  );
  const confirmHighRiskSettingChange = (title: string, lines: string[]) => {
    void title;
    void lines;
    return true;
  };
  const summarizeSettingChange = (prefix: string, lines: string[]) => {
    const changed = lines.filter((line) => line.includes('->')).slice(0, 4);
    return `${prefix}: ${changed.join('；') || '已写入'}`;
  };
  const controlSummary = (controlStatus.summary || {}) as Record<string, unknown>;
  const syncPolicy = (controlStatus.sync_policy || {}) as Record<string, unknown>;
  const youtubeKpi = (controlStatus.youtube_kpi || {}) as Record<string, unknown>;
  const claudeProvider = providers.find((row) => ['anthropic', 'claude'].includes(String(row.provider || '').toLowerCase())) || {};
  const claudeConfigured = boolValue(claudeProvider.configured, false);
  const claudeStatus = claudeConfigured ? String(claudeProvider.latest_status || claudeProvider.status || 'unknown') : 'not_configured';
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

  const createManualActivationLink = async () => {
    if (!apiToken || !email.trim()) return null;
    const response = await createStaffActivationLink(apiToken, {
      email: email.trim(),
      name: name.trim() || undefined,
      role,
      vkpiPermission: permission,
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
          await onInviteStaff({ email: email.trim(), name: name.trim() || undefined, role, vkpiPermission: permission });
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

  const saveStaffPermissionMatrix = async (staffId: string, permissions: Record<string, VkpiPermissionLevel>) => {
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

  const platformEnabledCount = platformCrawl.filter((row) => rowEnabled(row, 'crawl_enabled')).length;
  const platformCount = platformCrawl.length;
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
  const skuCount = productCatalog.length || data.productCosts.length;
  const lensCount = productCatalog.filter((product) => ['Lens', 'Cine Lens'].includes(product.categoryMain)).length;
  const lightingCount = productCatalog.filter((product) => product.categoryMain === 'Lighting/Flash').length;
  const adapterCount = productCatalog.filter((product) => product.categoryMain === 'Adapter').length;
  const syncTime = String(syncPolicy.daily_sync_time || '08:00');
  const systemHealth = settingsError || providerError || rbacStatusError ? '需要处理' : 'healthy';
  const versionSummary = frontendAsset
    ? `${frontendAsset} · ${timeLabel(versionCheckedAt)}`
    : `${shortBuildSha(frontendBuildInfo.gitSha)} · ${timeLabel(frontendBuildInfo.builtAt)}`;
  const inviteMode = inviteCapabilities?.email_available ? 'email' : 'manual_link';
  const canInviteStaff = inviteMode === 'email'
    ? Boolean(onInviteStaff)
    : Boolean(apiToken && (inviteCapabilities?.manual_activation_link_available ?? true));
  const moduleTitle = {
    status: '当前状态',
    sku: 'SKU 录入',
    staff: '账号授权',
    funds: '资金管理',
    rules: '规则安排',
  } as const;
  const renderSettingsModule = (
    key: keyof typeof moduleTitle,
    subtitle: string,
    children: React.ReactNode,
  ) => {
    const open = expandedSection === key;
    return (
      <section className={`vkpi-settings-module ${open ? 'is-open' : 'is-collapsed'}`} key={key}>
        <button className="vkpi-settings-module__head" type="button" onClick={() => setExpandedSection(open ? null : key)}>
          <span>{moduleTitle[key]}</span>
          <em>{subtitle}</em>
          <strong>{open ? '收起' : '展开'}</strong>
        </button>
        {open ? <div className="vkpi-settings-module__body">{children}</div> : null}
      </section>
    );
  };

  if (!isManager) {
    return (
      <PageShell title="个人设置">
        {message ? <div className="vkpi-inline-message">{message}</div> : null}
        {settingsError ? <div className="vkpi-inline-message is-error">{settingsError}</div> : null}
        <section className="vkpi-card-grid vkpi-card-grid--forms">
          <section className="vkpi-card vkpi-action-card">
            <CardHeader title="当前账号" />
            <InfoBlock label="界面" value="员工视角" />
            <InfoBlock label="数据范围" value="本人项目 / 本人短链 / 本人归因" />
            <InfoBlock label="头像" value="左下角上传真人头像" />
          </section>
          <section className="vkpi-card vkpi-action-card">
            <CardHeader title="不可见项目" />
            <InfoBlock label="SKU 成本" value="管理层可见" />
            <InfoBlock label="员工授权" value="管理层可见" />
            <InfoBlock label="API Key" value="管理层可见" />
          </section>
        </section>
      </PageShell>
    );
  }

  return (
    <PageShell title="系统设置">
      {message ? <div className="vkpi-inline-message">{message}</div> : null}
      {settingsError ? <div className="vkpi-inline-message">{settingsError}</div> : null}
      <SettingsLoadingStrip settingsLoading={settingsLoading} catalogLoading={productCatalogLoading} />
      <div className="vkpi-settings-clean">
        {renderSettingsModule('status', `${apiStatusText} · 同步 ${syncTime} · ${systemHealth} · 版本 ${versionSummary}`, (
          <>
            <div className="vkpi-settings-status-grid">
              <InfoBlock label="API 服务" value={apiStatusText} />
              <InfoBlock label="服务范围" value={apiStatusDetail} />
              <InfoBlock label="同步" value={`每日 ${syncTime}`} />
              <InfoBlock label="本月成本" value={`$${totalSpentUsd.toLocaleString('en-US')} / $${totalBudgetUsd.toLocaleString('en-US')}`} />
              <InfoBlock label="系统" value={systemHealth} />
            </div>
            <section className="vkpi-settings-version-panel">
              <div className="vkpi-table-card__header">
                <div><h2>版本状态</h2><span>{versionCheckedAt ? `检查 ${timeLabel(versionCheckedAt)}` : '读取中'}</span></div>
                <button className="vkpi-button" type="button" onClick={() => void reloadVersionStatus()}>刷新版本</button>
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
              <div className="vkpi-settings-api-grid">
                {providers.map((row) => {
                  const configured = boolValue(row.configured, false);
                  const ok = boolValue(row.ok, false);
                  const keyMask = String(row.key_mask || '').trim();
                  const status = String(row.latest_status || row.status || (ok ? 'healthy' : 'not_configured'));
                  return (
                    <article className={`vkpi-settings-api-card ${configured ? 'is-configured' : 'is-empty'}`} key={String(row.provider || row.label)}>
                      <header>
                        <strong>{String(row.label || row.provider || '-')}</strong>
                        <span>{configured ? '已配置' : '未配置'}</span>
                      </header>
                      <p>{keyMask || '未读取到 key'}</p>
                      <em>{status}</em>
                    </article>
                  );
                })}
              </div>
            )}
          </>
        ))}
        {renderSettingsModule('sku', `${skuCount} 个 SKU · 镜头 ${lensCount} · 闪光灯 ${lightingCount} · 转接环 ${adapterCount}`, (
          <section className="vkpi-settings-product-row">
            <ProductCostFormCard
              costSku={costSku}
              costProductName={costProductName}
              unitCostUsd={unitCostUsd}
              costNote={costNote}
              selectedProduct={selectedCatalogProduct}
              busy={busy}
              canUpsert={Boolean(onUpsertProductCost)}
              onCostSkuChange={setCostSku}
              onCostProductNameChange={setCostProductName}
              onUnitCostUsdChange={setUnitCostUsd}
              onCostNoteChange={setCostNote}
              onSubmit={submitProductCost}
            />
            <ProductCatalogPreviewCard
              products={productCatalog}
              loading={productCatalogLoading}
              error={productCatalogError}
              query={productSearch}
              selectedSku={selectedCatalogProduct?.sku}
              onQueryChange={setProductSearch}
              onSelectProduct={selectCatalogProduct}
            />
          </section>
        ))}
        {renderSettingsModule('staff', `${data.staffMembers.length} 人 · 邀请 / 权限`, (
          <section className="vkpi-settings-two-column">
            <StaffInviteCard
              email={email}
              name={name}
              role={role}
              permission={permission}
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
              onCopyActivationLink={() => void copyActivationLink()}
              onSubmit={submitInvite}
            />
            <section className="vkpi-card vkpi-table-card">
              <div className="vkpi-table-card__header"><div><h2>授权账号</h2><span>{data.staffMembers.length} 人</span></div></div>
              <StaffTable members={data.staffMembers} onSelectStaff={openStaffPermissionDrawer} />
            </section>
          </section>
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
          <section className="vkpi-settings-rules">
            <div className="vkpi-settings-tabs">
              {[
                ['core', '核心功能'],
                ['platform', '平台抓取'],
                ['alerts', '告警规则'],
                ['sync', '同步策略'],
              ].map(([key, label]) => (
                <button className={rulesTab === key ? 'is-active' : ''} type="button" key={key} onClick={() => setRulesTab(key as typeof rulesTab)}>{label}</button>
              ))}
            </div>
            {rulesTab === 'core' ? (
              <FeatureFlagsPanel
                featureFlags={featureFlags}
                busy={busy}
                apiToken={apiToken}
                rowEnabled={rowEnabled}
                onRunMorningSync={() => void runMorningSync()}
                onToggleFeatureFlag={(row) => void toggleFeatureFlag(row)}
              />
            ) : null}
            {rulesTab === 'platform' ? (
              <PlatformCrawlPanel
                platformCrawl={platformCrawl}
                busy={busy}
                rowEnabled={rowEnabled}
                onTogglePlatformCrawl={(row) => void togglePlatformCrawl(row)}
              />
            ) : null}
            {rulesTab === 'alerts' ? (
              <CommentAlertThresholdCard
                key={JSON.stringify(commentAlertSettings)}
                settings={commentAlertSettings}
                busy={busy}
                onSave={(event) => void saveCommentAlertSettings(event)}
              />
            ) : null}
            {rulesTab === 'sync' ? (
              <section className="vkpi-card vkpi-action-card">
                <CardHeader title="同步策略" />
                <InfoBlock label="每日同步" value={`${syncTime} ${String(syncPolicy.timezone || 'Asia/Shanghai')}`} />
                <InfoBlock label="每人候选" value={`${String(syncPolicy.candidate_limit_per_staff || 100)} 条`} />
                <button className="vkpi-button vkpi-button--primary" type="button" disabled={busy || !apiToken} onClick={() => void runMorningSync()}>手动同步</button>
              </section>
            ) : null}
          </section>
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
