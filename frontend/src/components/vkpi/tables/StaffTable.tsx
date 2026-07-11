import type { VkpiDashboardData, VkpiStaffMember } from '../vkpiTypes';
import { ShieldCheck } from 'lucide-react';
import { Avatar } from '../shared/Avatar';
import { staffStatusLabel } from '../pages/settings/staffPermissionTemplates';

export function StaffTable({ members, onSelectStaff }: { members: VkpiDashboardData['staffMembers']; onSelectStaff?: (staffId: string, fallback?: Partial<VkpiStaffMember>) => void | Promise<void> }) {
  // 状态口径统一(2026-07-11):此前这里自写一份(「已过期」且 pending 优先级不同),
  // 与抽屉 / 关系视图三处打架。现统一走 staffPermissionTemplates.staffStatusLabel。
  const statusLabel = staffStatusLabel;
  return (
    <div className="vkpi-table-wrap"><table className="vkpi-table"><thead><tr><th>成员</th><th>邮箱</th><th>角色</th><th>V-KPI</th><th>状态</th><th>操作</th></tr></thead><tbody>{members.length ? members.map((member) => <tr className={onSelectStaff ? 'is-clickable-row' : ''} key={member.id} onClick={() => {
      if (onSelectStaff) void onSelectStaff(member.id, member);
    }}><td><button className={`vkpi-owner-cell vkpi-owner-cell--button ${onSelectStaff ? 'is-clickable' : ''}`} type="button" onClick={(event) => {
      event.stopPropagation();
      if (onSelectStaff) void onSelectStaff(member.id, member);
    }}><Avatar name={member.name} src={member.avatarUrl} size="xs" />{member.name}</button></td><td>{member.email || '-'}</td><td>{member.role}</td><td><strong>{member.vkpiPermission}</strong></td><td>{statusLabel(member)}</td><td><button className="vkpi-mini-button" type="button" onClick={(event) => {
      event.stopPropagation();
      if (onSelectStaff) void onSelectStaff(member.id, member);
    }}><ShieldCheck size={13} />授权</button></td></tr>) : <tr><td className="vkpi-table-empty" colSpan={6}>暂无成员访问记录，或当前账号没有读取成员列表权限。</td></tr>}</tbody></table></div>
  );
}
