import React, { useEffect, useState } from 'react';
import {
  getCommentAlertSettings,
  getControlStatus,
  getSyncOverview,
  getNotificationSettings,
  getUserPreferences,
  listBudgetSettings,
  listFeatureFlags,
  listNotificationSettings,
  listPlatformCrawlSettings,
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
import type { VkpiDashboardData, VkpiStaffMember } from '../vkpiTypes';
import { CardHeader } from '../shared/CardHeader';
import { InfoBlock } from '../shared/InfoBlock';
import { ProductCostTable } from '../tables/ProductCostTable';
import { StaffTable } from '../tables/StaffTable';
import { SyncStatusPanel } from '../panels/SyncStatusPanel';
import { PageShell } from './PageShell';
import {
  ProductCostFormCard,
  ProviderHealthCard,
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
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [providers, setProviders] = useState<Array<Record<string, unknown>>>([]);
  const [providerBusy, setProviderBusy] = useState('');
  const [providerError, setProviderError] = useState('');
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
        const [providerResponse, flagsResponse, crawlResponse, budgetResponse, controlResponse, commentAlertResponse, preferenceListResponse, notificationListResponse] = await Promise.all([
          listProviderStatuses(apiToken),
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
    const status = String(row.last_test_status || 'not_configured');
    if (['not_configured', 'failed', 'error'].includes(status)) return `API 状态为 ${status}，需要先配置并测试通过。`;
    return '平台侧已具备抓取条件；单个账号仍需在数据分析页开启监控。';
  };
  const controlSummary = (controlStatus.summary || {}) as Record<string, unknown>;
  const syncPolicy = (controlStatus.sync_policy || {}) as Record<string, unknown>;
  const youtubeKpi = (controlStatus.youtube_kpi || {}) as Record<string, unknown>;
  const claudeProvider = providers.find((row) => ['anthropic', 'claude'].includes(String(row.provider || '').toLowerCase())) || {};
  const claudeConfigured = boolValue(claudeProvider.configured, false);
  const claudeStatus = claudeConfigured ? String(claudeProvider.latest_status || claudeProvider.status || 'unknown') : 'not_configured';

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
    setBusy(true);
    try {
      await updatePlatformCrawlSettings(apiToken, [{
        platform: row.platform,
        crawl_enabled: !rowEnabled(row, 'crawl_enabled'),
        daily_account_limit: row.daily_account_limit,
        posts_per_account: row.posts_per_account,
        crawl_evening: row.crawl_evening,
        crawl_followers: row.crawl_followers,
        monthly_budget_usd: row.monthly_budget_usd,
      }]);
      await reloadSystemSettings();
      setMessage('平台抓取开关已更新。');
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
    setBusy(true);
    try {
      await updatePlatformCrawlSettings(apiToken, [{
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
      }]);
      await reloadSystemSettings();
      setMessage('平台抓取限制已保存。');
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
    setBusy(true);
    try {
      await updateBudgetSettings(apiToken, [{
        budget_key: row.budget_key,
        enabled: form.get('enabled') === 'on',
        monthly_limit_usd: formNumber(form, 'monthly_limit_usd', numberValue(row.monthly_limit_usd)),
        current_month_spent: numberValue(row.current_month_spent),
        alert_threshold_pct: formNumber(form, 'alert_threshold_pct', numberValue(row.alert_threshold_pct, 80)),
      }]);
      await reloadSystemSettings();
      setMessage('预算控制已保存。');
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
    <PageShell title="系统设置" description="API 状态、授权账户、SKU 录入、员工授权列表。">
      <section className="vkpi-card-grid vkpi-card-grid--forms">
        <ProviderHealthCard
          providers={providers}
          providerBusy={providerBusy}
          providerError={providerError}
          onReload={() => void reloadProviders()}
          onProbe={(provider) => void runProviderProbe(provider)}
        />
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
        <ProductCostFormCard
          costSku={costSku}
          costProductName={costProductName}
          unitCostUsd={unitCostUsd}
          costNote={costNote}
          busy={busy}
          canUpsert={Boolean(onUpsertProductCost)}
          onCostSkuChange={setCostSku}
          onCostProductNameChange={setCostProductName}
          onUnitCostUsdChange={setUnitCostUsd}
          onCostNoteChange={setCostNote}
          onSubmit={submitProductCost}
        />
        <CommentAlertThresholdCard
          key={JSON.stringify(commentAlertSettings)}
          settings={commentAlertSettings}
          busy={busy}
          onSave={(event) => void saveCommentAlertSettings(event)}
        />
        {preferenceCard}
        {notificationCard}
      </section>
      {message ? <div className="vkpi-inline-message">{message}</div> : null}
      {settingsError ? <div className="vkpi-inline-message">{settingsError}</div> : null}
      <section className="vkpi-card-grid vkpi-card-grid--forms">
        <SyncStatusPanel
          apiToken={apiToken || ''}
          isAdmin={isManager}
          onLoadOverview={() => getSyncOverview(apiToken || '')}
          onTriggerSync={async (jobName: string) => { await triggerSync(apiToken || '', jobName); }}
        />
        <SystemSummaryCards
          controlSummary={controlSummary}
          syncPolicy={syncPolicy}
          youtubeKpi={youtubeKpi}
          claudeConfigured={claudeConfigured}
          claudeStatus={claudeStatus}
        />
      </section>
      <SettingsFeedbackPanel apiToken={apiToken} />
      <section className="vkpi-card-grid vkpi-card-grid--forms">
        <FeatureFlagsPanel
          featureFlags={featureFlags}
          busy={busy}
          apiToken={apiToken}
          rowEnabled={rowEnabled}
          onRunMorningSync={() => void runMorningSync()}
          onToggleFeatureFlag={(row) => void toggleFeatureFlag(row)}
        />
        <PlatformCrawlPanel
          platformCrawl={platformCrawl}
          busy={busy}
          rowEnabled={rowEnabled}
          platformBlockedReason={platformBlockedReason}
          onSavePlatformCrawl={(event, row) => void savePlatformCrawl(event, row)}
          onTogglePlatformCrawl={(row) => void togglePlatformCrawl(row)}
        />
      </section>
      <BudgetSettingsTable
        budgetSettings={budgetSettings}
        busy={busy}
        rowEnabled={rowEnabled}
        onSaveBudgetSetting={(event, row) => void saveBudgetSetting(event, row)}
      />
      <section className="vkpi-card vkpi-table-card">
        <div className="vkpi-table-card__header"><div><h2>员工授权列表</h2><span>{data.staffMembers.length} 人</span></div></div>
        <StaffTable members={data.staffMembers} onSelectStaff={onOpenStaffProfile} />
      </section>
      <section className="vkpi-card vkpi-table-card">
        <div className="vkpi-table-card__header">
          <div><h2>已录入 SKU</h2><span>{data.productCosts.length} 个 SKU</span></div>
        </div>
        <ProductCostTable rows={data.productCosts} />
      </section>
      {teamPreferenceTable}
      {teamNotificationTable}
    </PageShell>
  );
}
