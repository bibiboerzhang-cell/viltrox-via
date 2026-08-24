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
import { CostLedgerCard, HealthSentinelCard } from './settings/SettingsOperationalCards';
import { computeSettingsDerived, resolveDrawerMember } from './settings/SettingsPage.helpers';
import { createSettingsActions } from './settings/SettingsPage.actions';
import {
  boolValue,
  currentFrontendAsset,
  percentLabel,
  rowEnabled,
} from '../../../domains/settings';
import type { BackendBuildInfo } from '../../../domains/settings';
import { humanizeLlmReason } from '../cockpit/llmReasonCopy';

// 生产就绪只读视图：只消费后端已经过密钥脱敏的 readiness audit，
// 不做探针、不调用 provider，也不把“已注册 / 已配置”冒充为可用。
interface LlmReadinessEvidenceSource {
  source?: string;
  parsed?: boolean;
  error?: string | null;
  binding_count?: number;
  secret_values_exposed?: boolean;
}

interface LlmReadinessAudit {
  candidate_count?: number;
  configured_count?: number;
  probed_count?: number;
  evaluated_count?: number;
  production_ready_count?: number;
  blocked_count?: number;
  blocked_count_semantics?: string;
  signed_evidence_blocked_count?: number;
  runtime_model_gate_blocked_count?: number;
  operator_acknowledged_count?: number;
  model_readiness_authorized_count?: number;
  active_scope?: {
    binding_count?: number;
    bindings?: string[];
    task_assignment_count?: number;
    production_ready_count?: number;
    signed_evidence_blocked_count?: number;
    runtime_authorized_count?: number;
    runtime_blocked_count?: number;
    runtime_blocked_bindings?: string[];
    task_production_ready_count?: number;
    task_runtime_authorized_count?: number;
    claim_status?: string;
  };
  evidence_source?: LlmReadinessEvidenceSource;
  attestation_trust_roots?: {
    exact_probe?: { configured?: boolean; declared_key_count?: number; valid_key_count?: number };
    evaluation?: { configured?: boolean; declared_key_count?: number; valid_key_count?: number };
    distinct_key_ids?: boolean;
    distinct_public_keys?: boolean;
    ready_to_verify_signed_evidence?: boolean;
    runtime_can_extend_trust_roots?: boolean;
    release_review_required?: boolean;
    failure_reasons?: string[];
  };
}

interface LlmRuntimeGate {
  code?: string;
  category?: string;
  failure_reasons?: string[];
}

interface LlmTaskReadiness {
  binding?: string;
  state?: string;
  configured?: boolean;
  probed?: boolean;
  evaluated?: boolean;
  production_ready?: boolean;
  failure_reasons?: string[];
  runtime_gate?: LlmRuntimeGate;
  runtime_authorization?: {
    allowed_by_model_readiness?: boolean;
    source?: 'signed_evidence' | 'operator_ack' | 'blocked';
    operator_acknowledged?: boolean;
    temporary?: boolean;
    budget_and_feature_gates_still_apply?: boolean;
    claim_status?: string;
  };
  probe?: {
    attestation_verified?: boolean;
    as_of?: string | null;
  };
  evaluation?: {
    attestation_verified?: boolean;
    sample_count?: number;
    as_of?: string | null;
    success_rate?: number | null;
    structured_valid_rate?: number | null;
    factual_valid_rate?: number | null;
    source_valid_rate?: number | null;
    safety_valid_rate?: number | null;
    latency_ms?: { p95?: number | null };
  };
  thresholds?: {
    minimum_eval_samples?: number;
    maximum_p95_latency_ms?: number;
  };
}

interface LlmSystemModelsResponse {
  status?: string;
  claim_status?: string;
  available_models_semantics?: string;
  readiness_audit?: LlmReadinessAudit;
  task_model_readiness?: Record<string, LlmTaskReadiness>;
}

const LLM_TASK_LABELS: Record<string, string> = {
  audit_pre_filter: '提报预筛',
  audit_video_analysis: '视频 AI 分析',
  audit_vision_fallback: '视觉回退分析',
  audit_deep_score: '深度评分',
  deepsight_strategy: '策略洞察',
  deepsight_market_empath: '市场共情',
  deepsight_opportunity: '机会洞察',
  via_chat: 'AI 顾问对话',
  via_persona_summary: '用户记忆摘要',
  kol_audience_analysis: 'KOL 受众分析',
  kol_content_fit_analysis: 'KOL 内容契合',
  kol_product_fit_reason: 'KOL 产品推荐理由',
  kol_outreach_pack: 'KOL 外联包',
};

function llmTaskState(row: LlmTaskReadiness): string {
  if (row.production_ready === true) return '生产就绪';
  if (row.runtime_authorization?.source === 'operator_ack') return '临时精确授权 · 证据待补';
  if (row.configured === true || row.state === 'configured') return '已配置 · 未就绪';
  return '未配置 · 未就绪';
}

function llmPercent(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(Number(value))) return '—';
  return `${(Number(value) * 100).toFixed(0)}%`;
}

export function LlmProductionReadinessCard({ apiToken }: { apiToken?: string }) {
  const [result, setResult] = useState<LlmSystemModelsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!apiToken) {
      setResult(null);
      setLoading(false);
      setError('');
      return;
    }
    const controller = new AbortController();
    let alive = true;
    setLoading(true);
    setError('');
    apiFetch<LlmSystemModelsResponse>(
      '/api/admin/system/models',
      { timeoutMs: 15000, signal: controller.signal },
      apiToken,
    )
      .then((response) => {
        if (alive) setResult(response || null);
      })
      .catch((cause) => {
        if (!alive) return;
        setResult(null);
        setError(cause instanceof Error ? cause.message : 'LLM 生产就绪证据读取失败');
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
      controller.abort();
    };
  }, [apiToken]);

  const audit = result?.readiness_audit;
  const taskEntries = Object.entries(result?.task_model_readiness || {});
  const taskRows = taskEntries.map(([, row]) => row);
  const taskSignedReadyCount = taskRows.filter((row) => row?.production_ready === true).length;
  const taskTemporaryAuthorizationCount = taskRows.filter(
    (row) => row?.runtime_authorization?.source === 'operator_ack',
  ).length;
  const derivedActiveBindings = new Set(
    taskRows.map((row) => String(row?.binding || '')).filter(Boolean),
  );
  const candidateCount = Number(audit?.candidate_count ?? 0);
  const configuredCount = Number(audit?.configured_count ?? 0);
  const probedCount = Number(audit?.probed_count ?? 0);
  const evaluatedCount = Number(audit?.evaluated_count ?? 0);
  const readyCount = Number(audit?.production_ready_count ?? 0);
  const signedEvidenceBlockedCount = Number(
    audit?.signed_evidence_blocked_count
      ?? audit?.blocked_count
      ?? Math.max(candidateCount - readyCount, 0),
  );
  const operatorAckCount = Number(audit?.operator_acknowledged_count ?? 0);
  const modelAuthorizedCount = Number(audit?.model_readiness_authorized_count ?? readyCount);
  const activeScope = audit?.active_scope;
  const activeBindingCount = Number(activeScope?.binding_count ?? derivedActiveBindings.size);
  const activeSignedReadyCount = Number(
    activeScope?.production_ready_count
      ?? new Set(taskRows.filter((row) => row?.production_ready === true).map((row) => row.binding)).size,
  );
  const activeRuntimeAuthorizedCount = Number(
    activeScope?.runtime_authorized_count
      ?? new Set(taskRows.filter(
        (row) => row?.production_ready === true
          || row?.runtime_authorization?.allowed_by_model_readiness === true,
      ).map((row) => row.binding)).size,
  );
  const activeRuntimeBlockedCount = Number(
    activeScope?.runtime_blocked_count
      ?? Math.max(activeBindingCount - activeRuntimeAuthorizedCount, 0),
  );
  const taskAssignmentCount = Number(activeScope?.task_assignment_count ?? taskRows.length);
  const evidence = audit?.evidence_source;
  const hasAudit = Boolean(audit);
  const evidenceSource = String(evidence?.source || 'not_configured');
  const evidenceStatus = evidence?.parsed === true
    ? `已解析 ${Number(evidence?.binding_count ?? 0)} 个绑定`
    : '未解析';
  const trustRoots = audit?.attestation_trust_roots;
  const trustRootFailures = trustRoots?.failure_reasons || [];
  const stats = [
    { label: '实际精确绑定', value: activeBindingCount },
    { label: '实际绑定·正式签名', value: `${activeSignedReadyCount}/${activeBindingCount}` },
    { label: '实际绑定·运行放行', value: `${activeRuntimeAuthorizedCount}/${activeBindingCount}` },
    { label: '实际绑定·运行阻断', value: activeRuntimeBlockedCount },
    { label: '任务分配', value: taskAssignmentCount },
    { label: '候选目录（次级）', value: candidateCount },
    { label: '凭据键存在（非探针）', value: `${configuredCount}/${candidateCount}` },
    { label: '目录精确探针', value: `${probedCount}/${candidateCount}` },
    { label: '目录真实评测', value: `${evaluatedCount}/${candidateCount}` },
    { label: '目录正式签名', value: `${readyCount}/${candidateCount}` },
    { label: '目录待补签名', value: signedEvidenceBlockedCount },
    { label: '目录临时授权', value: operatorAckCount },
    { label: '目录模型门放行', value: modelAuthorizedCount },
  ];

  return (
    <section className="vkpi-card" style={{ marginBottom: 16, padding: '14px 16px' }} aria-labelledby="llm-readiness-title">
      <div className="flex items-center justify-between" style={{ gap: 12, flexWrap: 'wrap' }}>
        <div>
          <strong id="llm-readiness-title" style={{ fontSize: 14 }}>LLM 生产就绪</strong>
          <span className="text-muted" style={{ marginLeft: 10, fontSize: 'var(--ds-fs-12)' }}>
            {loading
              ? '读取生产证据中…'
              : hasAudit
                ? activeBindingCount === 0
                  ? '尚无可核验的实际任务绑定'
                  : activeSignedReadyCount === activeBindingCount
                  ? `${activeBindingCount} 个实际绑定已全部通过正式签名证据闸门`
                  : activeRuntimeAuthorizedCount > 0
                    ? `${activeRuntimeAuthorizedCount}/${activeBindingCount} 个实际绑定模型门已放行；正式签名 ${activeSignedReadyCount}/${activeBindingCount}`
                    : `${activeRuntimeBlockedCount}/${activeBindingCount} 个实际绑定被模型门阻断`
                : '不可核验'}
          </span>
        </div>
      </div>

      {!apiToken ? (
        <div className="vkpi-inline-message" style={{ marginTop: 8 }}>不可核验：缺少管理员会话。</div>
      ) : null}
      {error ? (
        <div className="vkpi-inline-message is-error" style={{ marginTop: 8 }}>不可核验：{error}</div>
      ) : null}

      {hasAudit ? (
        <>
          <div style={{ marginTop: 10, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 8 }}>
            {stats.map((item) => (
              <div key={item.label} style={{ padding: '8px 10px', borderRadius: 8, background: 'color-mix(in srgb, var(--ds-text) 6%, transparent)' }}>
                <div className="text-muted" style={{ fontSize: 'var(--ds-fs-11)' }}>{item.label}</div>
                <div style={{ fontSize: 16, fontWeight: 600 }}>{item.value}</div>
              </div>
            ))}
          </div>
          <div className="text-muted" style={{ marginTop: 10, fontSize: 'var(--ds-fs-11)', lineHeight: 1.6 }}>
            证据来源：{evidenceSource} · {evidenceStatus}
            {evidence?.error ? ` · 证据错误：${evidence.error}` : ''}
          </div>
          {trustRoots ? (
            <div
              className={`vkpi-inline-message${trustRoots.ready_to_verify_signed_evidence ? '' : ' is-error'}`}
              data-testid="llm-trust-root-status"
              style={{ marginTop: 8, fontSize: 'var(--ds-fs-11)', lineHeight: 1.6 }}
            >
              独立签名信任根：精确探针 {Number(trustRoots.exact_probe?.valid_key_count ?? 0)} 个 · 真实评测 {Number(trustRoots.evaluation?.valid_key_count ?? 0)} 个。
              {trustRoots.ready_to_verify_signed_evidence
                ? '已具备校验独立签名证据的基础条件。'
                : '尚不具备校验条件；必须由发布审核提供两套不同公钥，运行时不能自行添加。'}
              {trustRootFailures.length ? (
                <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
                  {trustRootFailures.map((reason) => {
                    const copy = humanizeLlmReason(reason, '签名信任根未通过发布审核。');
                    return <li key={reason}>{copy.message} <code>{reason}</code></li>;
                  })}
                </ul>
              ) : null}
            </div>
          ) : null}
          <details style={{ marginTop: 10 }}>
            <summary style={{ cursor: 'pointer', fontSize: 'var(--ds-fs-12)', fontWeight: 600 }}>
              逐任务真实状态（签名 {taskSignedReadyCount}/{taskRows.length} · 临时授权 {taskTemporaryAuthorizationCount}/{taskRows.length}）
            </summary>
            <div style={{ marginTop: 8, display: 'grid', gap: 8 }}>
              {taskEntries.map(([task, row]) => {
                const reasons = row.runtime_gate?.failure_reasons || row.failure_reasons || [];
                const evaluation = row.evaluation || {};
                const minimumSamples = Number(row.thresholds?.minimum_eval_samples ?? 30);
                const rawP95 = evaluation.latency_ms?.p95;
                const p95 = rawP95 == null ? null : Number(rawP95);
                const maximumP95 = Number(row.thresholds?.maximum_p95_latency_ms ?? 15000);
                return (
                  <article
                    key={task}
                    data-testid={`llm-task-${task}`}
                    style={{ padding: '9px 10px', borderRadius: 8, border: '1px solid color-mix(in srgb, var(--ds-text) 12%, transparent)' }}
                  >
                    <div className="flex items-center justify-between" style={{ gap: 10, flexWrap: 'wrap' }}>
                      <strong style={{ fontSize: 'var(--ds-fs-12)' }}>{LLM_TASK_LABELS[task] || task}</strong>
                      <span style={{ fontSize: 'var(--ds-fs-11)' }}>{llmTaskState(row)}</span>
                    </div>
                    <div className="text-muted" style={{ marginTop: 4, fontSize: 'var(--ds-fs-10)', overflowWrap: 'anywhere' }}>
                      {task} · {row.binding || '未绑定'}
                    </div>
                    <div className="text-muted" style={{ marginTop: 5, fontSize: 'var(--ds-fs-10)', lineHeight: 1.55 }}>
                      精确探针：{row.probed ? '通过' : '未通过'}（签名 {row.probe?.attestation_verified ? '已核验' : '未核验'}）
                      {' · '}真实评测：{row.evaluated ? '通过' : '未通过'}（{Number(evaluation.sample_count ?? 0)}/{minimumSamples} 条）
                      {' · '}P95：{p95 != null && Number.isFinite(p95) ? `${Math.round(p95)}ms` : '—'} / ≤{Math.round(maximumP95)}ms
                      {' · '}五项率：{[
                        evaluation.success_rate,
                        evaluation.structured_valid_rate,
                        evaluation.factual_valid_rate,
                        evaluation.source_valid_rate,
                        evaluation.safety_valid_rate,
                      ].map(llmPercent).join(' / ')}
                    </div>
                    {row.runtime_authorization?.source === 'operator_ack' ? (
                      <div className="vkpi-inline-message" style={{ marginTop: 5, fontSize: 'var(--ds-fs-10)' }}>
                        当前仅凭操作员对该精确模型的临时授权通过模型门；预算、功能开关和每次用户确认仍会独立校验。
                      </div>
                    ) : null}
                    {reasons.length ? (
                      <ul style={{ margin: '5px 0 0', paddingLeft: 18, fontSize: 'var(--ds-fs-10)', lineHeight: 1.55 }}>
                        {reasons.map((reason) => {
                          const copy = humanizeLlmReason(reason, '该精确模型尚未通过生产证据闸门。');
                          return <li key={reason}>{copy.message} <code>{reason}</code></li>;
                        })}
                      </ul>
                    ) : null}
                  </article>
                );
              })}
            </div>
          </details>
        </>
      ) : null}

      <div className="text-muted" style={{ marginTop: 10, fontSize: 'var(--ds-fs-11)', lineHeight: 1.6 }}>
        实际绑定是当前任务真正使用的唯一模型集；候选目录只是注册清单，不应当作运行分母。
        凭据键存在只表示环境检测到 provider 凭据，不表示账号 entitlement 、精确模型探针或真实可调用。
        仅 production_ready 表示正式双签证据已通过。
        操作员临时授权只放行精确模型就绪门，不会改写 production_ready，也不绕过预算、功能开关和逐次用户确认。
        本卡片只读，不会调用外部模型；AI 未就绪或关闭时，基础数据流程继续可用。
        {result?.available_models_semantics === 'registered_candidates_only_not_verified_availability'
          ? ' 当前模型清单仅代表候选注册，不代表供应商已授权或模型真实可调用。'
          : ''}
      </div>
    </section>
  );
}

interface SettingsPageProps {
  data: VkpiDashboardData;
  viewMode: 'manager' | 'employee';
  apiToken?: string;
  // 授权页 V1:头像菜单「成员与授权」直达设置页 staff 区(默认仍落 status)。
  initialSection?: SettingsModuleKey;
  onOpenBusinessArea?: (area: 'shopify' | 'dealers' | 'events' | 'gtmCommand') => void;
  onInviteStaff?: (payload: { email: string; name?: string; role: string; vkpiPermission: 'none' | 'read' | 'write'; permissions?: StaffPermissionMap; permissionTemplate?: string }) => Promise<void>;
  onUpdateStaffPermission?: (staffId: string, permission: 'none' | 'read' | 'write') => Promise<void>;
  onUpsertProductCost?: (payload: { productSku: string; productName?: string; unitCostUsd: number; note?: string; active?: boolean }) => Promise<void>;
  onOpenStaffProfile?: (staffId: string, fallback?: Partial<VkpiStaffMember>) => void | Promise<void>;
  onRefreshData?: () => void | Promise<void>;
}

export function SettingsPage({ data, viewMode, apiToken, initialSection, onOpenBusinessArea, onInviteStaff, onUpsertProductCost, onRefreshData }: SettingsPageProps) {
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
  const [expandedSection, setExpandedSection] = useState<SettingsModuleKey | null>(initialSection ?? 'status');

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

  const reloadVersionStatus = async (signal?: AbortSignal) => {
    setFrontendAsset(currentFrontendAsset());
    setVersionCheckedAt(new Date().toISOString());
    try {
      const response = await fetch(buildApiUrl(`/health?client_build=${encodeURIComponent(frontendBuildInfo.gitSha)}`), {
        credentials: 'same-origin',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        signal,
      });
      const payload = response.ok ? await response.json() : null;
      if (signal?.aborted) return;
      setBackendBuild(payload?.build || null);
    } catch {
      if (signal?.aborted) return;
      setBackendBuild(null);
    }
  };

  useEffect(() => {
    const controller = new AbortController();
    void reloadVersionStatus(controller.signal);
    return () => controller.abort();
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
      <LlmProductionReadinessCard apiToken={apiToken} />
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
          onOpenBusinessArea={onOpenBusinessArea}
          onOpenCostModule={() => setExpandedSection('sku')}
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
