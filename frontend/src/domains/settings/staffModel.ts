import type { VkpiStaffMember } from '../../components/vkpi/vkpiTypes';

type Row = Record<string, unknown>;

function parsePermissions(row: Row): Record<string, unknown> {
  const raw = row.permissions;
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) return raw as Record<string, unknown>;
  const rawJson = String(row.permissions_json || '').trim();
  if (!rawJson) return {};
  try {
    const parsed = JSON.parse(rawJson);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

export function buildStaffMembers(rows: Row[]): VkpiStaffMember[] {
  return rows.map((row) => {
    const permissions = parsePermissions(row);
    return {
      id: String(row.id || row.staff_id || row.staffId || ''),
      userId: row.user_id ? String(row.user_id) : undefined,
      name: String(row.user_name || row.staff_name || row.staffName || row.name || row.email || row.user_email || '未命名员工'),
      email: String(row.user_email || row.email || ''),
      role: String(row.role || 'readonly'),
      active: Number(row.active ?? 1) !== 0,
      avatarUrl: String(row.avatar_url || ''),
      employeeCode: String(row.user_handle || row.employee_code || ''),
      vkpiPermission: String(permissions.vkpi || row.vkpi_permission || 'none'),
      permissions: Object.fromEntries(Object.entries(permissions).map(([key, value]) => [key, String(value)])),
      verificationStatus: String(row.verification_status || ''),
      deliveryMethod: String(row.delivery_method || ''),
      inviteTokenActive: Boolean(row.invite_token_active),
      lastActiveAt: String(row.last_active_at || row.last_login || ''),
      invitedAt: String(row.invited_at || ''),
      acceptedAt: String(row.accepted_at || ''),
    };
  }).filter((row) => row.id || row.email);
}
