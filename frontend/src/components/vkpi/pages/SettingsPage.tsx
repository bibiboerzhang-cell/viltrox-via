import React, { useEffect, useState } from 'react';
import { buildApiUrl } from '../../../lib/api';
import { frontendBuildInfo } from '../../../lib/buildInfo';
import {
  getCommentAlertSettings,
  getControlStatus,
  getRbacStatus,
  getPreferenceSettings,
  listPreferenceSettings,
  getNotificationSettings,
  listNotificationSettings,
  listBudgetSettings,
  listFeatureFlags,
  listPlatformCrawlSettings,
  listProviderStatuses,
  listSchedulerTasks,
  listApiKeyPool,
} from '../../../domains/settings';
import { buildStaffMembers, getStaffInviteCapabilities } from '../../../domains/settings';
import type { VkpiStaffActivationLinkResponse, VkpiStaffInviteCapabilities } from '../../../domains/settings';
import { listProductCatalog } from '../../../domains/products';
import { getSyncOverview, type VkpiSyncOverview } from '../../../domains/settings';
import type {
  VkpiDashboardData,
  VkpiProductCatalogItem,
  VkpiStaffMember,
} from '../vkpiTypes';
import { PageShell } from './PageShell';
import { StaffPermissionDrawer } from './settings/StaffPermissionDrawer';
import { apiFetch } from '../../../services/http';
import { vkpiPermissionFromTemplate, type StaffPermissionMap } from './settings/staffPermissionTemplates';
import { type SettingsRulesTab } from './settings/SettingsRulesPanel';
import {
  EmployeeSettingsView,
  SettingsLoadingStrip,
  SettingsModule,
  type SettingsModuleKey,
} from './settings/SettingsPage.fragments';
import { SettingsCompanyZone, SettingsPersonalZone } from './settings/SettingsPage.Zones';
import { computeSettingsDerived, resolveDrawerMember } from './settings/SettingsPage.helpers';
import { createSettingsActions } from './settings/SettingsPage.actions';
import {
  boolValue,
  currentFrontendAsset,
  percentLabel,
  rowEnabled,
} from '../../../domains/settings';
import type { BackendBuildInfo } from '../../../domains/settings';
import { formatLocal } from '../lib/timeLocal';

// ── C1 数据健康哨兵 ──────────────────────────────────────────
// 10 项黄金链路日检:绿/黄/红点 + label + detail + 检查时间 + 手动运行。
// 只读展示 + 手动触发;后端 /ops/health-sentinel 落 persistent_cache,调度每日 09:30 自动跑。
interface SentinelCheck {
  key: string;
  label: string;
  status: 'ok' | 'warn' | 'fail' | string;
  detail: string;
  checked_at?: string;
}

interface SentinelResult {
  available?: boolean;
  reason?: string;
  ran_at?: string;
  trigger?: string;
  summary?: { ok?: number; warn?: number; fail?: number; total?: number };
  checks?: SentinelCheck[];
}

const SENTINEL_DOT_COLOR: Record<string, string> = {
  ok: '#22c55e',
  warn: '#eab308',
  fail: '#ef4444',
};

function HealthSentinelCard({ apiToken }: { apiToken?: string }) {
  const [result, setResult] = useState<SentinelResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setLoading(true);
    apiFetch<SentinelResult>('/api/admin/vkpi/ops/health-sentinel', { timeoutMs: 15000 }, apiToken)
      .then((res) => {
        if (alive) setResult(res || null);
      })
      .catch((err) => {
        if (alive) setError(err instanceof Error ? err.message : '哨兵结果读取失败');
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => { alive = false; };
  }, [apiToken]);

  const runNow = async () => {
    if (!apiToken || running) return;
    setRunning(true);
    setError('');
    try {
      // 手动跑一轮(10 项纯 SELECT 检查,通常几秒;留足超时)。
      const res = await apiFetch<SentinelResult>(
        '/api/admin/vkpi/ops/health-sentinel/run',
        { method: 'POST', timeoutMs: 60000 },
        apiToken,
      );
      setResult({ available: true, ...res });
    } catch (err) {
      setError(err instanceof Error ? err.message : '哨兵运行失败');
    } finally {
      setRunning(false);
    }
  };

  const checks = (result?.checks || []) as SentinelCheck[];
  const summary = result?.summary || {};
  const hasData = Boolean(result?.available !== false && checks.length);

  return (
    <section className="vkpi-card" style={{ marginBottom: 16, padding: '14px 16px' }}>
      <div className="flex items-center justify-between" style={{ gap: 12, flexWrap: 'wrap' }}>
        <div>
          <strong style={{ fontSize: 14 }}>数据健康哨兵</strong>
          <span className="text-slate-400" style={{ marginLeft: 10, fontSize: 12 }}>
            {hasData
              ? `10 项黄金链路 · 正常 ${summary.ok ?? 0} / 留意 ${summary.warn ?? 0} / 失败 ${summary.fail ?? 0} · 上次运行 ${formatLocal(result?.ran_at)}`
              : loading
                ? '读取中…'
                : '尚未运行过(每日 09:30 自动跑,或手动运行一次)'}
          </span>
        </div>
        <button type="button" className="vkpi-btn" disabled={running || !apiToken} onClick={() => void runNow()}>
          {running ? '检查中…' : '手动运行'}
        </button>
      </div>
      {error ? <div className="vkpi-inline-message is-error" style={{ marginTop: 8 }}>{error}</div> : null}
      {hasData ? (
        <div style={{ marginTop: 10, display: 'grid', gap: 4 }}>
          {checks.map((check) => (
            <div key={check.key} className="flex items-center" style={{ gap: 8, fontSize: 12, lineHeight: 1.5 }}>
              <span
                aria-label={check.status}
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  flex: '0 0 auto',
                  background: SENTINEL_DOT_COLOR[check.status] || '#94a3b8',
                }}
              />
              <span style={{ minWidth: 170, fontWeight: 500 }}>{check.label}</span>
              <span className="text-slate-400" style={{ flex: 1 }}>{check.detail}</span>
              <span className="text-slate-500" style={{ flex: '0 0 auto', fontSize: 11 }}>
                {formatLocal(check.checked_at)}
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

// ── C5 成本记账(内部口径)──────────────────────────────────
// 今日/本月 Apify+LLM 消耗 + top actor + 记账覆盖盲区提示。
// 后端 /ops/cost-ledger 只读聚合 vkpi_ai_cost_ledger;精确=usageTotalUsd(平台用量),
// 付费 actor 费按内部价目表估算(estimated);权威账单以 Apify console 为准。
interface CostBucket {
  apify_usd?: number;
  apify_calls?: number;
  llm_usd?: number;
  llm_calls?: number;
  total_usd?: number;
}

interface CostActorRow {
  actor_id?: string;
  runs?: number;
  cost_usd?: number;
}

interface CostCoverage {
  apify_runs?: number;
  unified_entries?: number;
  estimated_entries?: number;
  zero_cost_entries?: number;
  unified_ratio?: number | null;
}

interface CostOverview {
  generated_at?: string;
  today?: CostBucket;
  month?: CostBucket;
  top_actors_today?: CostActorRow[];
  top_actors_month?: CostActorRow[];
  coverage?: CostCoverage;
  note?: string;
}

const usd = (value?: number) => `$${Number(value ?? 0).toFixed(2)}`;

function CostLedgerCard({ apiToken }: { apiToken?: string }) {
  const [overview, setOverview] = useState<CostOverview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setLoading(true);
    apiFetch<CostOverview>('/api/admin/vkpi/ops/cost-ledger', { timeoutMs: 15000 }, apiToken)
      .then((res) => {
        if (alive) setOverview(res || null);
      })
      .catch((err) => {
        if (alive) setError(err instanceof Error ? err.message : '成本记账读取失败');
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => { alive = false; };
  }, [apiToken]);

  const today = overview?.today || {};
  const month = overview?.month || {};
  const coverage = overview?.coverage || {};
  const topActors = (overview?.top_actors_month || []).slice(0, 5);
  const apifyRuns = coverage.apify_runs ?? 0;
  const unified = coverage.unified_entries ?? 0;
  const stats: Array<{ label: string; value: string; sub: string }> = [
    { label: '今日 Apify', value: usd(today.apify_usd), sub: `${today.apify_calls ?? 0} 次调用` },
    { label: '今日 LLM', value: usd(today.llm_usd), sub: `${today.llm_calls ?? 0} 次调用` },
    { label: '本月 Apify', value: usd(month.apify_usd), sub: `${month.apify_calls ?? 0} 次调用` },
    { label: '本月 LLM', value: usd(month.llm_usd), sub: `${month.llm_calls ?? 0} 次调用` },
  ];

  return (
    <section className="vkpi-card" style={{ marginBottom: 16, padding: '14px 16px' }}>
      <div className="flex items-center justify-between" style={{ gap: 12, flexWrap: 'wrap' }}>
        <div>
          <strong style={{ fontSize: 14 }}>成本记账(内部口径)</strong>
          <span className="text-slate-400" style={{ marginLeft: 10, fontSize: 12 }}>
            {overview
              ? `本月合计 ${usd(month.total_usd)} · 统计时间 ${formatLocal(overview.generated_at)}`
              : loading
                ? '读取中…'
                : '暂无记账数据'}
          </span>
        </div>
      </div>
      {error ? <div className="vkpi-inline-message is-error" style={{ marginTop: 8 }}>{error}</div> : null}
      {overview ? (
        <>
          <div style={{ marginTop: 10, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 8 }}>
            {stats.map((item) => (
              <div key={item.label} style={{ padding: '8px 10px', borderRadius: 8, background: 'rgba(148,163,184,0.08)' }}>
                <div className="text-slate-400" style={{ fontSize: 11 }}>{item.label}</div>
                <div style={{ fontSize: 16, fontWeight: 600 }}>{item.value}</div>
                <div className="text-slate-500" style={{ fontSize: 11 }}>{item.sub}</div>
              </div>
            ))}
          </div>
          {topActors.length ? (
            <div style={{ marginTop: 10, display: 'grid', gap: 4 }}>
              <div className="text-slate-400" style={{ fontSize: 11, fontWeight: 500 }}>本月 Top Actor</div>
              {topActors.map((actor) => (
                <div key={actor.actor_id} className="flex items-center" style={{ gap: 8, fontSize: 12, lineHeight: 1.5 }}>
                  <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {actor.actor_id}
                  </span>
                  <span className="text-slate-400" style={{ flex: '0 0 auto' }}>{actor.runs ?? 0} 次</span>
                  <span style={{ flex: '0 0 auto', fontWeight: 500 }}>{usd(actor.cost_usd)}</span>
                </div>
              ))}
            </div>
          ) : null}
          <div className="text-slate-500" style={{ marginTop: 10, fontSize: 11, lineHeight: 1.6 }}>
            记账收口覆盖(本月):{unified}/{apifyRuns} 笔 Apify run 走统一记账口
            {coverage.estimated_entries ? ` · 估算 ${coverage.estimated_entries} 笔` : ''}
            {coverage.zero_cost_entries ? ` · 零成本盲区 ${coverage.zero_cost_entries} 笔` : ''}
            。{overview.note || '内部口径,权威账单以 Apify console 为准。'}
          </div>
        </>
      ) : null}
    </section>
  );
}

interface SettingsPageProps {
  data: VkpiDashboardData;
  viewMode: 'manager' | 'employee';
  apiToken?: string;
  // 授权页 V1:头像菜单「成员与授权」直达设置页 staff 区(默认仍落 status)。
  initialSection?: 'status' | 'sku' | 'staff' | 'funds' | 'rules' | 'scheduler' | 'apikeys' | 'goaffpro' | 'preference' | 'notification';
  onInviteStaff?: (payload: { email: string; name?: string; role: string; vkpiPermission: 'none' | 'read' | 'write'; permissions?: StaffPermissionMap; permissionTemplate?: string }) => Promise<void>;
  onUpdateStaffPermission?: (staffId: string, permission: 'none' | 'read' | 'write') => Promise<void>;
  onUpsertProductCost?: (payload: { productSku: string; productName?: string; unitCostUsd: number; note?: string; active?: boolean }) => Promise<void>;
  onOpenStaffProfile?: (staffId: string, fallback?: Partial<VkpiStaffMember>) => void | Promise<void>;
  onRefreshData?: () => void | Promise<void>;
}

export function SettingsPage({ data, viewMode, apiToken, initialSection, onInviteStaff, onUpsertProductCost, onRefreshData }: SettingsPageProps) {
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
  const [expandedSection, setExpandedSection] = useState<'status' | 'sku' | 'staff' | 'funds' | 'rules' | 'scheduler' | 'apikeys' | 'goaffpro' | 'preference' | 'notification' | null>(initialSection ?? 'status');

  // 2026-07-03 账号授权空白根治:成员名单此前只搭启动大批量(5s 静默超时+缓存投毒)。
  // 现打开设置页即直拉一次,拉到就覆盖 props 里的名单;失败保留 props(不降级)。
  // 2026-07-11 授权页 V1:优先打 /api/admin/staff(list_members 富行:user_online 在线
  // 5min 窗 / verification_status / delivery_method / is_owner / 归一化 permissions 含
  // board.*),关系视图的在线点与待激活徽吃真字段;403/失败回退旧 staff-directory,
  // 再失败保留 props —— 权限面从未放宽(后端 require_tab(system.read) 硬闸)。
  const [staffDirect, setStaffDirect] = useState<any[] | null>(null);
  useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    apiFetch<{ staff?: any[]; members?: any[] }>("/api/admin/staff", { timeoutMs: 10000 }, apiToken)
      .catch(() => apiFetch<{ staff?: any[]; members?: any[] }>("/api/admin/vkpi/staff-directory", { timeoutMs: 10000 }, apiToken))
      .then((res) => {
        if (!alive) return;
        const list = (res?.staff || res?.members || []) as any[];
        // 2026-07-03 整页炸根治:直拉端点回的是原始行(staff_name/permissions_json 等
        // snake_case),必须过 buildStaffMembers 归一化成 VkpiStaffMember(name/avatarUrl/
        // vkpiPermission…),否则 StaffTable 的 Avatar 对 undefined name 做字符串操作直接
        // 抛错 → 整页落 RouteErrorBoundary「页面加载失败」。归一化失败也兜底不炸。
        if (list.length) {
          try {
            const normalized = buildStaffMembers(list as never[]);
            if (normalized.length) setStaffDirect(normalized as unknown as any[]);
          } catch {
            /* 归一化异常:保留 props 名单兜底,绝不让设置页整页炸 */
          }
        }
      })
      .catch(() => { /* 静默:props 名单兜底 */ });
    return () => { alive = false; };
    // data.staffMembers 变化(onRefreshData 后)时重拉一次,富名单不落后于 props 名单。
  }, [apiToken, data.staffMembers]);
  // 面板与抽屉吃同一份名单(富名单优先),状态/在线/Owner 口径不再两处打架。
  const staffList = (staffDirect && staffDirect.length ? staffDirect : data.staffMembers) as VkpiStaffMember[];
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

  const syncPolicy = (controlStatus.sync_policy || {}) as Record<string, unknown>;
  const kolRefresh = (controlStatus.kol_refresh || {}) as Record<string, unknown>;
  const kolRefreshBatchPlan = (kolRefresh.apify_batch_plan || {}) as Record<string, unknown>;

  const {
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
  } = createSettingsActions({
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
  });

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
      <HealthSentinelCard apiToken={apiToken} />
      <CostLedgerCard apiToken={apiToken} />
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
          members={staffList as any}
          selectedStaffId={selectedStaffForPermissions?.id ?? null}
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
          onCopyActivationLink={() => void copyActivationLink(activationLink)}
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
          member={resolveDrawerMember(staffList, selectedStaffForPermissions)}
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
