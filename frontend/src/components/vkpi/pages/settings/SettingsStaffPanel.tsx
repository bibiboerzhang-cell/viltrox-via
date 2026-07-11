import React, { useState } from 'react';
import type {
  VkpiStaffActivationLinkResponse,
  VkpiStaffInviteCapabilities,
} from '../../../../domains/settings';
import type { VkpiStaffMember } from '../../vkpiTypes';
import { StaffTable } from '../../tables/StaffTable';
import { StaffInviteCard } from './SettingsAdminCards';
import { StaffRelationView } from './SettingsStaffPanel.relations';

interface SettingsStaffPanelProps {
  members: VkpiStaffMember[];
  selectedStaffId?: string | null;
  email: string;
  name: string;
  role: string;
  permission: 'none' | 'read' | 'write';
  permissionTemplate: string;
  busy: boolean;
  canInvite: boolean;
  inviteMode: 'email' | 'manual_link';
  inviteCapabilities: VkpiStaffInviteCapabilities | null;
  inviteCapabilitiesError: string;
  activationLink: VkpiStaffActivationLinkResponse | null;
  activationCopied: boolean;
  onEmailChange: (value: string) => void;
  onNameChange: (value: string) => void;
  onRoleChange: (value: string) => void;
  onPermissionChange: (value: 'none' | 'read' | 'write') => void;
  onPermissionTemplateChange: (value: string) => void;
  onCopyActivationLink: () => void;
  onSubmitInvite: React.FormEventHandler;
  onSelectStaff: (staffId: string, fallback?: Partial<VkpiStaffMember>) => void;
}

export function SettingsStaffPanel({
  members,
  selectedStaffId,
  email,
  name,
  role,
  permission,
  permissionTemplate,
  busy,
  canInvite,
  inviteMode,
  inviteCapabilities,
  inviteCapabilitiesError,
  activationLink,
  activationCopied,
  onEmailChange,
  onNameChange,
  onRoleChange,
  onPermissionChange,
  onPermissionTemplateChange,
  onCopyActivationLink,
  onSubmitInvite,
  onSelectStaff,
}: SettingsStaffPanelProps) {
  // 授权页 V1(2026-07-11):默认关系视图(Owner 大卡 → 三组成员卡),
  // 原 6 列扁平表保留为「表格」备选视图(信息与功能零删减)。
  const [view, setView] = useState<'relations' | 'table'>('relations');
  return (
    // --staff 修饰类:关系视图右卡天生高,本区左表单卡要贴内容(align-items:start,
    // 规则在 vkpi-settings-traffic.css),不吃 settings-dark 的全局 stretch。
    <section className="vkpi-settings-two-column vkpi-settings-two-column--staff">
      <StaffInviteCard
        email={email}
        name={name}
        role={role}
        permission={permission}
        permissionTemplate={permissionTemplate}
        busy={busy}
        canInvite={canInvite}
        inviteMode={inviteMode}
        inviteCapabilities={inviteCapabilities}
        inviteCapabilitiesError={inviteCapabilitiesError}
        activationLink={activationLink}
        activationCopied={activationCopied}
        onEmailChange={onEmailChange}
        onNameChange={onNameChange}
        onRoleChange={onRoleChange}
        onPermissionChange={onPermissionChange}
        onPermissionTemplateChange={onPermissionTemplateChange}
        onCopyActivationLink={onCopyActivationLink}
        onSubmit={onSubmitInvite}
      />
      <section className="vkpi-card vkpi-table-card vkpi-staff-directory-card">
        <div className="vkpi-table-card__header">
          <div><h2>授权账号</h2><span>{members.length} 人 · 点成员卡打开右侧权限面板</span></div>
          <div className="vkpi-staff-view-toggle" role="group" aria-label="成员视图切换">
            <button
              type="button"
              className={view === 'relations' ? 'is-active' : ''}
              aria-pressed={view === 'relations'}
              onClick={() => setView('relations')}
            >
              关系视图
            </button>
            <button
              type="button"
              className={view === 'table' ? 'is-active' : ''}
              aria-pressed={view === 'table'}
              onClick={() => setView('table')}
            >
              表格
            </button>
          </div>
        </div>
        {view === 'relations' ? (
          <StaffRelationView members={members} selectedStaffId={selectedStaffId} onSelectStaff={onSelectStaff} />
        ) : (
          <StaffTable members={members} onSelectStaff={onSelectStaff} />
        )}
      </section>
    </section>
  );
}
