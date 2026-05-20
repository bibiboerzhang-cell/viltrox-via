import type { VkpiDashboardData, VkpiStaffMember } from '../vkpiTypes';
import { Avatar } from '../shared/Avatar';

export function StaffTable({ members, onSelectStaff }: { members: VkpiDashboardData['staffMembers']; onSelectStaff?: (staffId: string, fallback?: Partial<VkpiStaffMember>) => void | Promise<void> }) {
  const statusLabel = (member: VkpiStaffMember) => {
    if (!member.active) return '停用';
    if (member.verificationStatus === 'pending') return '待激活';
    if (member.verificationStatus === 'expired') return '已过期';
    if (member.verificationStatus === 'verified') return '已验证';
    if (member.verificationStatus === 'activated') return '已激活';
    return '启用';
  };
  return (
    <div className="vkpi-table-wrap"><table className="vkpi-table"><thead><tr><th>员工</th><th>邮箱</th><th>角色</th><th>V-KPI</th><th>状态</th><th>操作</th></tr></thead><tbody>{members.length ? members.map((member) => <tr className={onSelectStaff ? 'is-clickable-row' : ''} key={member.id} onClick={() => {
      if (onSelectStaff) void onSelectStaff(member.id, member);
    }}><td><button className={`vkpi-owner-cell vkpi-owner-cell--button ${onSelectStaff ? 'is-clickable' : ''}`} type="button" onClick={(event) => {
      event.stopPropagation();
      if (onSelectStaff) void onSelectStaff(member.id, member);
    }}><Avatar name={member.name} src={member.avatarUrl} size="xs" />{member.name}</button></td><td>{member.email || '-'}</td><td>{member.role}</td><td><strong>{member.vkpiPermission}</strong></td><td>{statusLabel(member)}</td><td><button className="vkpi-mini-button" type="button" onClick={(event) => {
      event.stopPropagation();
      if (onSelectStaff) void onSelectStaff(member.id, member);
    }}>授权</button></td></tr>) : <tr><td className="vkpi-table-empty" colSpan={6}>暂无员工授权记录，或当前账号没有读取员工列表权限。</td></tr>}</tbody></table></div>
  );
}
