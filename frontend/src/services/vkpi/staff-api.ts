import { apiFetch, jsonBody } from "../http";
import type { VkpiStaffProfile } from "../../components/vkpi/vkpiTypes";

type Row = Record<string, unknown>;

export type VkpiPermissionLevel = "none" | "read" | "write" | "admin";

export interface VkpiInviteStaffPayload {
  email: string;
  name?: string;
  role: string;
  vkpiPermission: "none" | "read" | "write";
  permissions?: Record<string, VkpiPermissionLevel | string>;
  permissionTemplate?: string;
}

export interface VkpiStaffInviteCapabilities {
  email_available?: boolean;
  external_emails_allowed?: boolean;
  allowed_domains?: string[];
  token_ttl_hours?: number;
  manual_activation_link_available?: boolean;
  delivery_methods?: string[];
  site_url_configured?: boolean;
}

export interface VkpiStaffActivationLinkResponse {
  staff_id?: number;
  user_id?: number;
  email?: string;
  full_name?: string;
  role?: string;
  activation_url?: string;
  token_hint?: string;
  expires_at?: string;
  expires_in_hours?: number;
  delivery_method?: string;
}

export interface VkpiStaffPasswordResetLinkResponse {
  ok?: boolean;
  staff_id?: number;
  user_id?: number;
  email?: string;
  reset_url?: string;
  token_hint?: string;
  expires_at?: string;
  expires_in_hours?: number;
  email_sent?: boolean;
  delivery_method?: string;
}

export interface VkpiStaffInviteStatusResponse {
  valid?: boolean;
  state?: "missing" | "invalid" | "used" | "expired" | "active" | string;
  message?: string;
  email?: string;
  full_name?: string;
  expires_at?: string;
  used_at?: string;
  accepted_at?: string;
}

function staffInviteBody(payload: VkpiInviteStaffPayload): Row {
  return {
    email: payload.email,
    name: payload.name,
    full_name: payload.name,
    role: payload.role,
    permissions: payload.permissions || { vkpi: payload.vkpiPermission },
    permission_template: payload.permissionTemplate,
  };
}

export async function getStaffInviteCapabilities(token: string) {
  return apiFetch<VkpiStaffInviteCapabilities>("/api/admin/staff/invite/capabilities", {}, token);
}

export async function inviteMarketingStaff(token: string, payload: VkpiInviteStaffPayload) {
  return apiFetch<Row>(
    "/api/admin/staff/invite",
    { method: "POST", body: jsonBody(staffInviteBody(payload)) },
    token,
  );
}

export async function createStaffActivationLink(token: string, payload: VkpiInviteStaffPayload) {
  return apiFetch<VkpiStaffActivationLinkResponse>(
    "/api/admin/staff/invite/activation-link",
    { method: "POST", body: jsonBody(staffInviteBody(payload)) },
    token,
  );
}

export async function createExistingStaffActivationLink(token: string, staffId: string) {
  return apiFetch<VkpiStaffActivationLinkResponse>(
    `/api/admin/staff/${encodeURIComponent(staffId)}/activation-link`,
    { method: "POST", body: jsonBody({}) },
    token,
  );
}

export async function acceptStaffInvite(inviteToken: string, password: string) {
  return apiFetch<Row>(
    "/api/admin/staff/accept-invite",
    { method: "POST", body: jsonBody({ invite_token: inviteToken, password }) },
  );
}

export async function getStaffInviteStatus(inviteToken: string) {
  return apiFetch<VkpiStaffInviteStatusResponse>(
    `/api/admin/staff/invite/status?token=${encodeURIComponent(inviteToken)}`,
  );
}

export async function updateStaffMarketingPermission(
  token: string,
  staffId: string,
  permission: "none" | "read" | "write",
) {
  return apiFetch<Row>(
    `/api/admin/staff/${encodeURIComponent(staffId)}/permissions`,
    { method: "POST", body: jsonBody({ permissions: { vkpi: permission } }) },
    token,
  );
}

export async function updateStaffPermissions(
  token: string,
  staffId: string,
  permissions: Record<string, VkpiPermissionLevel | string>,
) {
  return apiFetch<Row>(
    `/api/admin/staff/${encodeURIComponent(staffId)}/permissions`,
    { method: "POST", body: jsonBody({ permissions }) },
    token,
  );
}

export async function createStaffPasswordResetLink(token: string, staffId: string) {
  return apiFetch<VkpiStaffPasswordResetLinkResponse>(
    `/api/admin/staff/${encodeURIComponent(staffId)}/reset-password-link`,
    { method: "POST", body: jsonBody({}) },
    token,
  );
}

export async function getStaffProfile(token: string, staffId: string, window = "month") {
  return apiFetch<VkpiStaffProfile>(
    `/api/marketing/staff/${encodeURIComponent(staffId)}/profile?window=${encodeURIComponent(window)}&limit=120`,
    {},
    token,
  );
}
