import type { VkpiDashboardData, VkpiStaffMember } from '../vkpiTypes';
import { Avatar } from '../shared/Avatar';

export function StaffTable({ members, onSelectStaff }: { members: VkpiDashboardData['staffMembers']; onSelectStaff?: (staffId: string, fallback?: Partial<VkpiStaffMember>) => void | Promise<void> }) {
  return (
    <div className="vkpi-table-wrap"><table className="vkpi-table"><thead><tr><th>员工</th><th>邮箱</th><th>员工 ID</th><th>角色</th><th>Marketing 权限</th><th>状态</th><th>邀请时间</th><th>最近活跃</th></tr></thead><tbody>{members.length ? members.map((member) => <tr key={member.id}><td><button className={`vkpi-owner-cell vkpi-owner-cell--button ${onSelectStaff ? 'is-clickable' : ''}`} type="button" onClick={() => {
      if (onSelectStaff) void onSelectStaff(member.id, member);
    }}><Avatar name={member.name} src={member.avatarUrl} size="xs" />{member.name}</button></td><td>{member.email || '-'}</td><td>{member.employeeCode || member.userId || '-'}</td><td>{member.role}</td><td><strong>{member.vkpiPermission}</strong></td><td>{member.active ? '启用' : '停用'}</td><td>{member.invitedAt || '-'}</td><td>{member.lastActiveAt || '-'}</td></tr>) : <tr><td className="vkpi-table-empty" colSpan={8}>暂无员工授权记录，或当前账号没有读取员工列表权限。</td></tr>}</tbody></table></div>
  );
}

