import React from 'react';
import type {
  VkpiStaffActivationLinkResponse,
  VkpiStaffInviteCapabilities,
} from '../../../../domains/settings';
import type { VkpiStaffMember } from '../../vkpiTypes';
import { StaffTable } from '../../tables/StaffTable';
import { StaffInviteCard } from './SettingsAdminCards';

interface SettingsStaffPanelProps {
  members: VkpiStaffMember[];
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
  return (
    <section className="vkpi-settings-two-column">
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
      <section className="vkpi-card vkpi-table-card">
        <div className="vkpi-table-card__header">
          <div><h2>授权账号</h2><span>{members.length} 人</span></div>
        </div>
        <StaffTable members={members} onSelectStaff={onSelectStaff} />
      </section>
    </section>
  );
}
