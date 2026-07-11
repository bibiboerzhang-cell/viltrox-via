import React, { useEffect, useState } from 'react';
import { X } from 'lucide-react';
import { apiFetch } from '../../../../services/http';
import { buildStaffMembers, getStaffInviteCapabilities } from '../../../../domains/settings';
import type {
  VkpiStaffActivationLinkResponse,
  VkpiStaffInviteCapabilities,
} from '../../../../domains/settings';
import type { VkpiDashboardData, VkpiStaffMember } from '../../vkpiTypes';
import { PageShell } from '../PageShell';
import { SettingsStaffPanel } from './SettingsStaffPanel';
import { StaffPermissionDrawer } from './StaffPermissionDrawer';
import { createSettingsActions } from './SettingsPage.actions';
import { resolveDrawerMember } from './SettingsPage.helpers';
import { vkpiPermissionFromTemplate } from './staffPermissionTemplates';

// 授权页 V1(2026-07-11)· 独立浮层容器:头像菜单「成员与授权」不再借道整页系统设置,
// 而是直达这张 demo 第二页同款的专属页 —— 页头(标题+OWNER 徽+成员数药丸+说明)+
// SettingsStaffPanel(邀请表单 / Owner→分组关系视图 / 表格备选)+ StaffPermissionDrawer。
// 零件全部复用 8daca301e 已有实现,本文件只是「容器 + 状态托管」:
// · 容器机制照抄系统设置浮层(cockpit-settings-dark 全屏层),自动吃到 global.css 里
//   该作用域的按钮重置 + color-scheme(暗色无白边),以及 vkpi-settings-dark.css 全套 token 换肤;
// · staff 状态/动作与 SettingsPage 同源:动作直接复用 createSettingsActions(零逻辑重造),
//   名单直拉与 invite 能力探测按 SettingsPage 同款口径。
// 权限:入口仅 Owner 可见(usePermissions().isOwner() 由 CockpitApp 判定);
// 后端 require_tab(system.members / system.read) 硬闸不因前端入口改变。

interface AuthorizationOverlayProps {
  data: VkpiDashboardData;
  apiToken?: string;
  onClose: () => void;
  onRefreshData?: () => void | Promise<void>;
  t?: (text: string) => string;
}

const noop = () => {};

export function AuthorizationOverlay({ data, apiToken, onClose, onRefreshData, t = (s) => s }: AuthorizationOverlayProps) {
  // —— 邀请表单 + 抽屉状态:与 SettingsPage staff 分区同名同默认值(行为对齐)——
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [role, setRole] = useState('employee');
  const [permission, setPermission] = useState<'none' | 'read' | 'write'>('write');
  const [invitePermissionTemplate, setInvitePermissionTemplate] = useState('employee_workspace');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [inviteCapabilities, setInviteCapabilities] = useState<VkpiStaffInviteCapabilities | null>(null);
  const [inviteCapabilitiesError, setInviteCapabilitiesError] = useState('');
  const [activationLink, setActivationLink] = useState<VkpiStaffActivationLinkResponse | null>(null);
  const [activationCopied, setActivationCopied] = useState(false);
  const [selectedStaffForPermissions, setSelectedStaffForPermissions] = useState<VkpiStaffMember | null>(null);

  // 富名单直拉:与 SettingsPage 同口径 —— 优先 /api/admin/staff(user_online / verification_status /
  // is_owner / board.* 归一化权限),403/失败回退旧 staff-directory,再失败保留 props 名单兜底。
  // 原始行必须过 buildStaffMembers 归一化,归一化异常也绝不让浮层整页炸。
  const [staffDirect, setStaffDirect] = useState<VkpiStaffMember[] | null>(null);
  useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    apiFetch<{ staff?: unknown[]; members?: unknown[] }>('/api/admin/staff', { timeoutMs: 10000 }, apiToken)
      .catch(() => apiFetch<{ staff?: unknown[]; members?: unknown[] }>('/api/admin/vkpi/staff-directory', { timeoutMs: 10000 }, apiToken))
      .then((res) => {
        if (!alive) return;
        const list = (res?.staff || res?.members || []) as never[];
        if (list.length) {
          try {
            const normalized = buildStaffMembers(list);
            if (normalized.length) setStaffDirect(normalized as unknown as VkpiStaffMember[]);
          } catch {
            /* 归一化异常:保留 props 名单兜底 */
          }
        }
      })
      .catch(() => { /* 静默:props 名单兜底 */ });
    return () => { alive = false; };
  }, [apiToken, data.staffMembers]);
  const staffList = (staffDirect && staffDirect.length ? staffDirect : data.staffMembers) as VkpiStaffMember[];

  useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    getStaffInviteCapabilities(apiToken)
      .then((res) => { if (alive) setInviteCapabilities(res); })
      .catch((error) => {
        if (alive) setInviteCapabilitiesError(error instanceof Error ? error.message : '邀请能力读取失败');
      });
    return () => { alive = false; };
  }, [apiToken]);

  // 动作全部复用 SettingsPage 的 createSettingsActions(submitInvite 邮件→手动链接降级、
  // 权限矩阵保存后就地保住选中成员等既有行为,零逻辑重造)。本浮层只渲染 staff 分区,
  // 非 staff 分区的必填 deps 以空值 / noop 打底 —— 对应动作在这里永不被调用。
  const {
    submitInvite,
    saveStaffPermissionMatrix,
    createSelectedStaffActivationLink,
    createSelectedStaffPasswordResetLink,
    copyActivationLink,
    openStaffPermissionDrawer,
  } = createSettingsActions({
    apiToken, data, onRefreshData,
    email, name, role, permission, invitePermissionTemplate, inviteCapabilities,
    setBusy, setMessage, setActivationLink, setActivationCopied, setEmail, setName,
    setSelectedStaffForPermissions,
    // —— 以下均为非 staff 分区 deps(空值/noop 打底)——
    costSku: '', costProductName: '', unitCostUsd: '', costNote: '',
    commentAlertSettings: {}, schedulerTasks: [], schedulerStatus: {}, apiKeyPool: [],
    keyDraft: { account_name: '', provider: 'gemini', key: '', daily_quota: '', enabled: true },
    landingPage: '', dateRangeDefault: '', tableDensity: '', rowsPerPage: '',
    compactMode: false, rightPanelOpen: false,
    emailEnabled: false, inAppEnabled: false, dailyDigestEnabled: false, weeklySummaryEnabled: false,
    stalledProjectEnabled: false, claimActivityEnabled: false, attributionAlertEnabled: false,
    costAlertEnabled: false, systemAlertEnabled: false, quietHoursStart: '', quietHoursEnd: '',
    hydratePreference: noop, hydrateNotification: noop,
    setSettingsError: noop, setFeatureFlags: noop, setPlatformCrawl: noop, setBudgetSettings: noop,
    setControlStatus: noop, setCommentAlertSettings: noop, setSyncOverview: noop,
    setSchedulerTasks: noop, setSchedulerStatus: noop, setApiKeyPool: noop, setKeyDraft: noop,
    setPreferenceList: noop, setNotificationList: noop, setCostSku: noop, setCostProductName: noop,
    setUnitCostUsd: noop, setCostNote: noop, setSelectedCatalogProduct: noop,
  });

  // 邀请口径与 computeSettingsDerived 完全同款:cockpit 浮层从不传 onInviteStaff(设置浮层同款),
  // email 模式诚实不可用;手动链接模式看 token + 后端能力位。
  const inviteMode: 'email' | 'manual_link' = inviteCapabilities?.email_available ? 'email' : 'manual_link';
  const canInvite = inviteMode === 'email'
    ? false
    : Boolean(apiToken && (inviteCapabilities?.manual_activation_link_available ?? true));

  const ownerCount = staffList.filter((member) => member.isOwner).length;
  // 页头药丸徽:OWNER 徽与头像菜单同源(warn token),成员数药丸 accent token,全 token 无写死色。
  const headingPills = (
    <div className="flex flex-wrap items-center gap-2 md:justify-end">
      <span className="rounded-full bg-warn-soft px-2 py-0.5 text-[9px] font-bold tracking-widest text-warn">OWNER</span>
      <span className="rounded-full bg-accent-soft px-2.5 py-0.5 text-[10px] font-semibold text-accent">
        {staffList.length} 成员{ownerCount ? ` · ${ownerCount} Owner` : ''}
      </span>
    </div>
  );

  return (
    <div className="cockpit-settings-dark vkpi-settings-surface fixed inset-0 z-[1000] overflow-auto">
      <button
        type="button"
        onClick={onClose}
        className="vkpi-settings-surface__close fixed right-5 top-4 z-[210] grid h-9 w-9 place-items-center rounded-lg border border-line bg-card text-muted shadow-sm backdrop-blur hover:bg-accent-soft hover:text-ink"
        title={t('关闭')}
        aria-label={t('关闭成员与授权')}
      >
        <X size={18} />
      </button>
      <PageShell
        title="成员与授权"
        description="Owner 专属:邀请成员、查看 Owner→分组关系,并按成员逐板块授权。系统设置内的同名分区仍保留(双入口)。"
        headingExtra={headingPills}
      >
        {message ? <div className="vkpi-inline-message">{message}</div> : null}
        <SettingsStaffPanel
          members={staffList}
          selectedStaffId={selectedStaffForPermissions?.id ?? null}
          email={email}
          name={name}
          role={role}
          permission={permission}
          permissionTemplate={invitePermissionTemplate}
          busy={busy}
          canInvite={canInvite}
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
        />
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
    </div>
  );
}
