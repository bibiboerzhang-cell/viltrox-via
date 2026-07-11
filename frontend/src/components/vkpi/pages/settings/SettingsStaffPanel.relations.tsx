import React from 'react';
import { ShieldCheck } from 'lucide-react';
import type { VkpiStaffMember } from '../../vkpiTypes';
import { Avatar } from '../../shared/Avatar';
import { staffPendingActivation, staffStatusLabel } from './staffPermissionTemplates';

// ── 授权页 V1 · 关系视图(2026-07-11,对齐板块页范式 demo「成员与授权」页)──
// 替代扁平 6 列 StaffTable 的主视图:Owner 大卡 → 管理层 / 运营 / 只读 三组成员卡 grid。
// 卡片 = 头像字母 + 名 + 角色 + 在线点(staff-directory 的 user_online 真字段,5min 窗)
// + 待激活徽;点卡 = 选中(高亮)→ 打开右侧权限面板(StaffPermissionDrawer)。
// 原表格的 邮箱 / V-KPI / 状态 收进卡 hover title 与右侧面板,不丢字段;
// 「操作(授权)」= 点卡本身。状态口径统一走 staffStatusLabel(唯一真源)。

const MANAGER_ROLES = new Set(['manager', 'lead', 'marketing_lead', 'marketing_manager', 'admin']);
const READONLY_ROLES = new Set(['readonly', 'viewer']);

export interface StaffRelationGroups {
  owners: VkpiStaffMember[];
  managers: VkpiStaffMember[];
  ops: VkpiStaffMember[];
  readonly: VkpiStaffMember[];
}

export function staffRelationGroups(members: VkpiStaffMember[]): StaffRelationGroups {
  const groups: StaffRelationGroups = { owners: [], managers: [], ops: [], readonly: [] };
  for (const member of members) {
    const role = String(member.role || '').toLowerCase().trim();
    // Owner 判据 = 真源 staff.is_owner(owner 的 role 往往是 admin,不能只看 role)。
    if (member.isOwner || role === 'owner') groups.owners.push(member);
    else if (MANAGER_ROLES.has(role)) groups.managers.push(member);
    else if (READONLY_ROLES.has(role)) groups.readonly.push(member);
    else groups.ops.push(member);
  }
  return groups;
}

// 原 StaffTable 6 列信息(成员/邮箱/角色/V-KPI/状态)一字不丢,收进 hover title。
export function memberHoverTitle(member: VkpiStaffMember): string {
  return [
    member.name,
    member.email || '-',
    member.role,
    `V-KPI ${member.vkpiPermission || 'none'}`,
    staffStatusLabel(member),
    member.online ? '在线' : '',
  ].filter(Boolean).join(' · ');
}

function MemberCard({
  member,
  selected,
  onSelectStaff,
}: {
  member: VkpiStaffMember;
  selected: boolean;
  onSelectStaff: (staffId: string, fallback?: Partial<VkpiStaffMember>) => void;
}) {
  return (
    <button
      type="button"
      className={`vkpi-staff-member-card ${selected ? 'is-selected' : ''}`}
      title={memberHoverTitle(member)}
      aria-pressed={selected}
      onClick={() => onSelectStaff(member.id, member)}
    >
      <span className={`vkpi-staff-member-card__avatar ${member.online ? 'is-online' : ''}`}>
        <Avatar name={member.name} src={member.avatarUrl} size="xs" />
      </span>
      <span className="vkpi-staff-member-card__meta">
        <strong>{member.name}</strong>
        <span>
          {member.role}
          {member.online ? <em className="vkpi-staff-online-text"> · 在线</em> : null}
        </span>
      </span>
      {staffPendingActivation(member) ? (
        <span className="vkpi-staff-pending-badge">{staffStatusLabel(member)}</span>
      ) : null}
    </button>
  );
}

function GroupCaption({ title, count, hint }: { title: string; count: number; hint: string }) {
  return (
    <div className="vkpi-staff-group-cap">
      <span>{title}</span>
      <strong>{count}</strong>
      <i aria-hidden="true" />
      <em>{hint}</em>
    </div>
  );
}

export function StaffRelationView({
  members,
  selectedStaffId,
  onSelectStaff,
}: {
  members: VkpiStaffMember[];
  selectedStaffId?: string | null;
  onSelectStaff: (staffId: string, fallback?: Partial<VkpiStaffMember>) => void;
}) {
  const groups = staffRelationGroups(members);
  const onlineCount = members.filter((member) => member.online).length;
  const pendingCount = members.filter((member) => staffPendingActivation(member)).length;

  if (!members.length) {
    return <div className="vkpi-empty-panel">暂无成员访问记录，或当前账号没有读取成员列表权限。</div>;
  }

  return (
    <div className="vkpi-staff-relations">
      <div className="vkpi-staff-relations__stats">
        <span>成员总数 <strong>{members.length}</strong></span>
        <span>当前在线 <strong className="is-good">{onlineCount}</strong></span>
        <span>待激活 <strong className="is-warn">{pendingCount}</strong></span>
      </div>
      {groups.owners.map((owner) => (
        <button
          type="button"
          key={owner.id}
          className={`vkpi-staff-owner-card ${selectedStaffId === owner.id ? 'is-selected' : ''}`}
          title={memberHoverTitle(owner)}
          aria-pressed={selectedStaffId === owner.id}
          onClick={() => onSelectStaff(owner.id, owner)}
        >
          <span className={`vkpi-staff-member-card__avatar ${owner.online ? 'is-online' : ''}`}>
            <Avatar name={owner.name} src={owner.avatarUrl} size="sm" />
          </span>
          <span className="vkpi-staff-member-card__meta">
            <strong>{owner.name}</strong>
            <span>创始人 · 全部权限 · 敏感项唯一审批人</span>
          </span>
          <span className="vkpi-staff-owner-card__badge"><ShieldCheck size={11} />OWNER</span>
        </button>
      ))}
      {groups.managers.length ? (
        <>
          <GroupCaption title="管理层" count={groups.managers.length} hint="可管理组内成员" />
          <div className="vkpi-staff-member-grid">
            {groups.managers.map((member) => (
              <MemberCard key={member.id} member={member} selected={selectedStaffId === member.id} onSelectStaff={onSelectStaff} />
            ))}
          </div>
        </>
      ) : null}
      {groups.ops.length ? (
        <>
          <GroupCaption title="运营" count={groups.ops.length} hint="业务全量 · 数据按自己过滤" />
          <div className="vkpi-staff-member-grid">
            {groups.ops.map((member) => (
              <MemberCard key={member.id} member={member} selected={selectedStaffId === member.id} onSelectStaff={onSelectStaff} />
            ))}
          </div>
        </>
      ) : null}
      {groups.readonly.length ? (
        <>
          <GroupCaption title="只读 / 外部" count={groups.readonly.length} hint="仅查看" />
          <div className="vkpi-staff-member-grid">
            {groups.readonly.map((member) => (
              <MemberCard key={member.id} member={member} selected={selectedStaffId === member.id} onSelectStaff={onSelectStaff} />
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
}
