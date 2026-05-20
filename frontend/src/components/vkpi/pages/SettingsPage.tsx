import React, { useEffect, useState } from 'react';
import {
  getCommentAlertSettings,
  getControlStatus,
  getRbacStatus,
  getSyncOverview,
  getNotificationSettings,
  getUserPreferences,
  listBudgetSettings,
  listFeatureFlags,
  listNotificationSettings,
  listPlatformCrawlSettings,
  listProductCatalog,
  listProviderStatuses,
  listUserPreferences,
  probeProviderStatus,
  runVkpiAutomation,
  triggerSync,
  updateBudgetSettings,
  updateCommentAlertSettings,
  updateFeatureFlags,
  updateNotificationSettings,
  updatePlatformCrawlSettings,
  updateUserPreferences,
} from '../../../services/vkpi.ui-api';
import type { VkpiDashboardData, VkpiProductCatalogItem, VkpiStaffMember } from '../vkpiTypes';
import { CardHeader } from '../shared/CardHeader';
import { InfoBlock } from '../shared/InfoBlock';
import { ProductCostTable } from '../tables/ProductCostTable';
import { StaffTable } from '../tables/StaffTable';
import { SyncStatusPanel } from '../panels/SyncStatusPanel';
import { PageShell } from './PageShell';
import {
  ProductCostFormCard,
  ProductCatalogPreviewCard,
  ProviderHealthCard,
  RbacStatusCard,
  StaffInviteCard,
  SystemSummaryCards,
} from './settings/SettingsAdminCards';
import {
  BudgetSettingsTable,
  CommentAlertThresholdCard,
  FeatureFlagsPanel,
  PlatformCrawlPanel,
} from './settings/SettingsControlPanels';
import {
  NotificationSettingsCard,
  PreferenceSettingsCard,
  TeamNotificationTable,
  TeamPreferenceTable,
} from './settings/SettingsPreferencePanels';
import { SettingsFeedbackPanel } from './settings/SettingsFeedbackPanel';
import { SettingsOperatingReviewPanel } from './settings/SettingsOperatingReviewPanel';

interface SettingsPageProps {
  data: VkpiDashboardData;
  viewMode: 'manager' | 'employee';
  apiToken?: string;
  onInviteStaff?: (payload: { email: string; name?: string; role: string; vkpiPermission: 'none' | 'read' | 'write' }) => Promise<void>;
  onUpdateStaffPermission?: (staffId: string, permission: 'none' | 'read' | 'write') => Promise<void>;
  onUpsertProductCost?: (payload: { productSku: string; productName?: string; unitCostUsd: number; note?: string; active?: boolean }) => Promise<void>;
  onOpenStaffProfile?: (staffId: string, fallback?: Partial<VkpiStaffMember>) => void | Promise<void>;
}

export function SettingsPage({ data, viewMode, apiToken, onInviteStaff, onUpdateStaffPermission, onUpsertProductCost, onOpenStaffProfile }: SettingsPageProps) {
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [role, setRole] = useState('employee');
  const [permission, setPermission] = useState<'none' | 'read' | 'write'>('write');
  const [targetStaffId, setTargetStaffId] = useState('');
  const [targetPermission, setTargetPermission] = useState<'none' | 'read' | 'write'>('write');
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
  const [preferenceList, setPreferenceList] = useState<Array<Record<string, unknown>>>([]);
  const [notificationList, setNotificationList] = useState<Array<Record<string, unknown>>>([]);
  const [landingPage, setLandingPage] = useState('dashboard');
  const [dateRangeDefault, setDateRangeDefault] = useState('7d');
  const [tableDensity, setTableDensity] = useState('comfortable');
  const [rowsPerPage, setRowsPerPage] = useState('20');
  const [compactMode, setCompactMode] = useState(false);
  const [rightPanelOpen, setRightPanelOpen] = useState(true);
  const [emailEnabled, setEmailEnabled] = useState(false);
  const [inAppEnabled, setInAppEnabled] = useState(true);
  const [dailyDigestEnabled, setDailyDigestEnabled] = useState(true);
  const [weeklySummaryEnabled, setWeeklySummaryEnabled] = useState(true);
  const [stalledProjectEnabled, setStalledProjectEnabled] = useState(true);
  const [claimActivityEnabled, setClaimActivityEnabled] = useState(true);
  const [attributionAlertEnabled, setAttributionAlertEnabled] = useState(true);
  const [costAlertEnabled, setCostAlertEnabled] = useState(false);
  const [systemAlertEnabled, setSystemAlertEnabled] = useState(true);
  const [quietHoursStart, setQuietHoursStart] = useState('22:00');
  const [quietHoursEnd, setQuietHoursEnd] = useState('08:00');
  const [settingsError, setSettingsError] = useState('');
  const [expandedSection, setExpandedSection] = useState<'status' | 'sku' | 'staff' | 'funds' | 'rules'>('status');
  const [rulesTab, setRulesTab] = useState<'core' | 'platform' | 'alerts' | 'sync'>('platform');
  const [productSearch, setProductSearch] = useState('');
  const [selectedCatalogProduct, setSelectedCatalogProduct] = useState<VkpiProductCatalogItem | null>(null);
  const isManager = viewMode === 'manager';

  const boolValue = (value: unknown, fallback = false) => {
    if (value === undefined || value === null) return fallback;
    if (typeof value === 'string') return ['1', 'true', 'yes', 'on', 'enabled'].includes(value.toLowerCase());
    return Boolean(value);
  };

  const applyPreferenceResponse = (response: { preference?: Record<string, unknown> }) => {
    const prefs = (response.preference?.preferences || {}) as Record<string, unknown>;
    setLandingPage(String(prefs.landing_page || 'dashboard'));
    setDateRangeDefault(String(prefs.date_range_default || '7d'));
    setTableDensity(String(prefs.table_density || 'comfortable'));
    setRowsPerPage(String(prefs.rows_per_page || 20));
    setCompactMode(boolValue(prefs.compact_mode, false));
    setRightPanelOpen(boolValue(prefs.right_panel_open, true));
  };

  const applyNotificationResponse = (response: { notification_settings?: Record<string, unknown> }) => {
    const settings = (response.notification_settings?.settings || {}) as Record<string, unknown>;
    setEmailEnabled(boolValue(settings.email_enabled, false));
    setInAppEnabled(boolValue(settings.in_app_enabled, true));
    setDailyDigestEnabled(boolValue(settings.daily_digest_enabled, true));
    setWeeklySummaryEnabled(boolValue(settings.weekly_summary_enabled, true));
    setStalledProjectEnabled(boolValue(settings.stalled_project_enabled, true));
    setClaimActivityEnabled(boolValue(settings.claim_activity_enabled, true));
    setAttributionAlertEnabled(boolValue(settings.attribution_alert_enabled, true));
    setCostAlertEnabled(boolValue(settings.cost_alert_enabled, false));
    setSystemAlertEnabled(boolValue(settings.system_alert_enabled, true));
    setQuietHoursStart(String(settings.quiet_hours_start || '22:00'));
    setQuietHoursEnd(String(settings.quiet_hours_end || '08:00'));
  };

  useEffect(() => {
    if (!apiToken) return;
    let cancelled = false;
    const loadPersonalSettings = async () => {
      setSettingsError('');
      try {
        const [preferenceResponse, notificationResponse] = await Promise.all([
          getUserPreferences(apiToken),
          getNotificationSettings(apiToken),
        ]);
        if (!cancelled) {
          applyPreferenceResponse(preferenceResponse);
          applyNotificationResponse(notificationResponse);
        }
      } catch (error) {
        if (!cancelled) setSettingsError(error instanceof Error ? error.message : '个人设置读取失败');
      }
    };
    void loadPersonalSettings();
    return () => {
      cancelled = true;
    };
  }, [apiToken]);

  useEffect(() => {
    if (!isManager || !apiToken) return;
    let cancelled = false;
    const load = async () => {
      setProviderError('');
      setSettingsError('');
      try {
        const [providerResponse, rbacResponse, flagsResponse, crawlResponse, budgetResponse, controlResponse, commentAlertResponse, preferenceListResponse, notificationListResponse] = await Promise.all([
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
          listUserPreferences(apiToken),
          listNotificationSettings(apiToken),
        ]);
        if (!cancelled) {
          setProviders(providerResponse.providers || []);
          setRbacStatus(rbacResponse || {});
          setFeatureFlags(flagsResponse.flags || []);
          setPlatformCrawl(crawlResponse.platforms || []);
          setBudgetSettings(budgetResponse.budgets || []);
          setControlStatus(controlResponse || {});
          setCommentAlertSettings(commentAlertResponse.settings || {});
          setPreferenceList(preferenceListResponse.preferences || []);
          setNotificationList(notificationListResponse.notification_settings || []);
        }
      } catch (error) {
        if (!cancelled) setSettingsError(error instanceof Error ? error.message : '系统设置读取失败');
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
          categories: ['Lens', 'Cine Lens', 'Lighting/Flash', 'Adapter'],
          limit: 300,
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
  const formBool = (form: FormData, key: string, fallback: boolean) => {
    const raw = form.get(key);
    return raw === null ? fallback : raw === 'on';
  };
  const platformBlockedReason = (row: Record<string, unknown>) => {
    if (!rowEnabled(row, 'crawl_enabled')) return '平台抓取开关关闭。';
    if (numberValue(row.daily_account_limit) <= 0) return '每日账号为 0，开启后也不会实际抓取。';
    if (numberValue(row.posts_per_account) <= 0) return '每账号内容为 0，无法同步帖子/视频。';
    if (numberValue(row.monthly_budget_usd) <= 0) return '平台月预算为 0，预算闸门会阻止抓取。';
    return '平台侧已开启，API 按默认已配置处理。';
  };
  const boolLabel = (value: boolean) => (value ? '开启' : '关闭');
  const moneyLabel = (value: unknown) => `$${numberValue(value).toLocaleString('en-US')}`;
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
      `当前闸门: ${platformBlockedReason({ ...row, crawl_enabled: nextEnabled })}`,
      `每日账号: ${numberValue(row.daily_account_limit)}`,
      `每账号内容: ${numberValue(row.posts_per_account)}`,
      `月预算 USD: ${moneyLabel(row.monthly_budget_usd)}`,
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

  const savePlatformCrawl = async (event: React.FormEvent<HTMLFormElement>, row: Record<string, unknown>) => {
    event.preventDefault();
    if (!apiToken) return;
    const form = new FormData(event.currentTarget);
    setSettingsError('');
    const payload = {
      platform: row.platform,
      crawl_enabled: rowEnabled(row, 'crawl_enabled'),
      daily_account_limit: formNumber(form, 'daily_account_limit', numberValue(row.daily_account_limit)),
      posts_per_account: formNumber(form, 'posts_per_account', numberValue(row.posts_per_account)),
      monthly_budget_usd: formNumber(form, 'monthly_budget_usd', numberValue(row.monthly_budget_usd)),
      crawl_comments: formBool(form, 'crawl_comments', rowEnabled(row, 'crawl_comments')),
      crawl_followers: formBool(form, 'crawl_followers', rowEnabled(row, 'crawl_followers')),
      crawl_audience_graph: formBool(form, 'crawl_audience_graph', rowEnabled(row, 'crawl_audience_graph')),
      only_uncontacted_kols: formBool(form, 'only_uncontacted_kols', rowEnabled(row, 'only_uncontacted_kols')),
      include_company_accounts: formBool(form, 'include_company_accounts', rowEnabled(row, 'include_company_accounts')),
      include_competitor_accounts: formBool(form, 'include_competitor_accounts', rowEnabled(row, 'include_competitor_accounts')),
      include_candidate_kols: formBool(form, 'include_candidate_kols', rowEnabled(row, 'include_candidate_kols')),
      failure_threshold: formNumber(form, 'failure_threshold', numberValue(row.failure_threshold, 5)),
      last_test_status: row.last_test_status || 'not_configured',
    };
    const lines = [
      `平台: ${String(row.platform || '-')}`,
      settingChangeLine('每日账号', numberValue(row.daily_account_limit), payload.daily_account_limit),
      settingChangeLine('每账号内容', numberValue(row.posts_per_account), payload.posts_per_account),
      settingChangeLine('月预算 USD', moneyLabel(row.monthly_budget_usd), moneyLabel(payload.monthly_budget_usd)),
      settingChangeLine('失败阈值', numberValue(row.failure_threshold, 5), payload.failure_threshold),
      settingChangeLine('评论抓取', boolLabel(rowEnabled(row, 'crawl_comments')), boolLabel(payload.crawl_comments)),
      settingChangeLine('粉丝抓取', boolLabel(rowEnabled(row, 'crawl_followers')), boolLabel(payload.crawl_followers)),
      settingChangeLine('只推未联系', boolLabel(rowEnabled(row, 'only_uncontacted_kols')), boolLabel(payload.only_uncontacted_kols)),
    ];
    if (!confirmHighRiskSettingChange('确认保存平台抓取限制', lines)) return;
    setBusy(true);
    try {
      await updatePlatformCrawlSettings(apiToken, [payload]);
      await reloadSystemSettings();
      setMessage(summarizeSettingChange('平台抓取限制已保存', lines));
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : '平台抓取限制保存失败');
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

  const saveUserPreferenceSettings = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!apiToken) return;
    setBusy(true);
    setSettingsError('');
    try {
      const response = await updateUserPreferences(apiToken, {
        landing_page: landingPage,
        date_range_default: dateRangeDefault,
        table_density: tableDensity,
        rows_per_page: Number(rowsPerPage || 20),
        compact_mode: compactMode,
        right_panel_open: rightPanelOpen,
      });
      applyPreferenceResponse(response);
      if (isManager) {
        const list = await listUserPreferences(apiToken);
        setPreferenceList(list.preferences || []);
      }
      setMessage('个人偏好已保存。');
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : '个人偏好保存失败');
    } finally {
      setBusy(false);
    }
  };

  const saveNotificationSettings = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!apiToken) return;
    setBusy(true);
    setSettingsError('');
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
      applyNotificationResponse(response);
      if (isManager) {
        const list = await listNotificationSettings(apiToken);
        setNotificationList(list.notification_settings || []);
      }
      setMessage('通知配置已保存。当前仅保存设置，不会发送通知。');
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : '通知配置保存失败');
    } finally {
      setBusy(false);
    }
  };

  const submitInvite = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!onInviteStaff || !email.trim()) return;
    setBusy(true);
    try {
      await onInviteStaff({ email: email.trim(), name: name.trim() || undefined, role, vkpiPermission: permission });
      setEmail('');
      setName('');
      setMessage('授权账户已写入。');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '员工邀请失败');
    } finally {
      setBusy(false);
    }
  };

  const submitPermission = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!onUpdateStaffPermission || !targetStaffId) return;
    setBusy(true);
    try {
      await onUpdateStaffPermission(targetStaffId, targetPermission);
      setMessage('员工权限已更新。');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '权限更新失败');
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

  const preferenceCard = (
    <PreferenceSettingsCard
      busy={busy}
      apiToken={apiToken}
      landingPage={landingPage}
      dateRangeDefault={dateRangeDefault}
      tableDensity={tableDensity}
      rowsPerPage={rowsPerPage}
      compactMode={compactMode}
      rightPanelOpen={rightPanelOpen}
      onLandingPageChange={setLandingPage}
      onDateRangeDefaultChange={setDateRangeDefault}
      onTableDensityChange={setTableDensity}
      onRowsPerPageChange={setRowsPerPage}
      onCompactModeChange={setCompactMode}
      onRightPanelOpenChange={setRightPanelOpen}
      onSubmit={saveUserPreferenceSettings}
    />
  );

  const notificationCard = (
    <NotificationSettingsCard
      busy={busy}
      apiToken={apiToken}
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
      onSubmit={saveNotificationSettings}
    />
  );

  const teamPreferenceTable = <TeamPreferenceTable preferenceList={preferenceList} />;
  const teamNotificationTable = <TeamNotificationTable notificationList={notificationList} boolValue={boolValue} />;
  const platformEnabledCount = platformCrawl.filter((row) => rowEnabled(row, 'crawl_enabled')).length;
  const platformCount = platformCrawl.length;
  const totalBudgetUsd = budgetSettings.reduce((sum, row) => sum + numberValue(row.monthly_limit_usd), 0);
  const totalSpentUsd = budgetSettings.reduce((sum, row) => sum + numberValue(row.current_month_spent), 0);
  const skuCount = productCatalog.length || data.productCosts.length;
  const lensCount = productCatalog.filter((product) => ['Lens', 'Cine Lens'].includes(product.categoryMain)).length;
  const lightingCount = productCatalog.filter((product) => product.categoryMain === 'Lighting/Flash').length;
  const adapterCount = productCatalog.filter((product) => product.categoryMain === 'Adapter').length;
  const syncTime = String(syncPolicy.daily_sync_time || '08:00');
  const systemHealth = settingsError || providerError || rbacStatusError ? '需要处理' : 'healthy';
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
        <button className="vkpi-settings-module__head" type="button" onClick={() => setExpandedSection(open ? 'status' : key)}>
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
      <PageShell title="个人设置" description="员工视角只保留本人偏好和通知配置，授权、SKU、API 状态由管理层维护。">
        {message ? <div className="vkpi-inline-message">{message}</div> : null}
        {settingsError ? <div className="vkpi-inline-message is-error">{settingsError}</div> : null}
        <section className="vkpi-card-grid vkpi-card-grid--forms">
          <section className="vkpi-card vkpi-action-card">
            <CardHeader title="当前账号" />
            <InfoBlock label="界面" value="员工视角" />
            <InfoBlock label="数据范围" value="本人项目 / 本人短链 / 本人归因" />
            <InfoBlock label="头像" value="左下角上传真人头像" />
          </section>
          {preferenceCard}
          {notificationCard}
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
    <PageShell title="系统设置" description="只保留看状态、录 SKU、加人、管钱、调规则。">
      {message ? <div className="vkpi-inline-message">{message}</div> : null}
      {settingsError ? <div className="vkpi-inline-message">{settingsError}</div> : null}
      <div className="vkpi-settings-clean">
        {renderSettingsModule('status', `API 默认配置 · 同步 ${syncTime} · ${systemHealth}`, (
          <div className="vkpi-settings-status-grid">
            <InfoBlock label="API" value={`${platformCount || providers.length} / ${platformCount || providers.length || 0} 默认配置`} />
            <InfoBlock label="同步" value={`每日 ${syncTime}`} />
            <InfoBlock label="本月成本" value={`$${totalSpentUsd.toLocaleString('en-US')} / $${totalBudgetUsd.toLocaleString('en-US')}`} />
            <InfoBlock label="系统" value={systemHealth} />
          </div>
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
              canInvite={Boolean(onInviteStaff)}
              onEmailChange={setEmail}
              onNameChange={setName}
              onRoleChange={setRole}
              onPermissionChange={setPermission}
              onSubmit={submitInvite}
            />
            <section className="vkpi-card vkpi-table-card">
              <div className="vkpi-table-card__header"><div><h2>授权账号</h2><span>{data.staffMembers.length} 人</span></div></div>
              <StaffTable members={data.staffMembers} onSelectStaff={onOpenStaffProfile} />
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
                platformBlockedReason={platformBlockedReason}
                onSavePlatformCrawl={(event, row) => void savePlatformCrawl(event, row)}
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
      <section className="vkpi-settings-secondary">
        <details>
          <summary>个人偏好和通知</summary>
          <div className="vkpi-settings-two-column">
            {preferenceCard}
            {notificationCard}
          </div>
        </details>
        <details>
          <summary>团队偏好记录</summary>
          {teamPreferenceTable}
          {teamNotificationTable}
        </details>
      </section>
      <section className="vkpi-card vkpi-table-card vkpi-settings-sku-history">
        <div className="vkpi-table-card__header">
          <div><h2>已录入 SKU</h2><span>{data.productCosts.length} 个</span></div>
        </div>
        <ProductCostTable rows={data.productCosts} />
      </section>
    </PageShell>
  );
}
