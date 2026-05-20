import React, { useEffect, useMemo, useState } from "react";
import type { VkpiStaffActivationLinkResponse, VkpiStaffPasswordResetLinkResponse, VkpiPermissionLevel } from "../../../../services/vkpi.ui-api";
import type { VkpiStaffMember } from "../../vkpiTypes";
import { Avatar } from "../../shared/Avatar";
import { InfoBlock } from "../../shared/InfoBlock";

type PermissionMap = Record<string, VkpiPermissionLevel>;

const LEVELS: Array<{ key: VkpiPermissionLevel; label: string }> = [
  { key: "none", label: "无" },
  { key: "read", label: "只读" },
  { key: "write", label: "可写" },
  { key: "admin", label: "管理" },
];

const MODULES: Array<{ key: string; label: string; group: string; ownerOnly?: boolean }> = [
  { key: "overview", label: "管理主控", group: "常用" },
  { key: "kol_ops", label: "KOL/账号管理", group: "常用" },
  { key: "vkpi", label: "V-KPI 工作台", group: "常用" },
  { key: "activities", label: "项目/活动", group: "常用" },
  { key: "analytics", label: "数据分析", group: "数据" },
  { key: "insights", label: "洞察报表", group: "数据" },
  { key: "products", label: "产品作战", group: "数据" },
  { key: "runtime", label: "运行诊断", group: "系统" },
  { key: "system", label: "系统设置", group: "系统" },
  { key: "system.usage", label: "用量/预算", group: "系统" },
  { key: "system.api_keys", label: "API Key", group: "敏感", ownerOnly: true },
  { key: "system.models", label: "模型配置", group: "敏感", ownerOnly: true },
  { key: "system.members", label: "成员管理", group: "敏感", ownerOnly: true },
  { key: "system.restart", label: "服务重启", group: "敏感", ownerOnly: true },
];

const TEMPLATES: Array<{ key: string; label: string; permissions: PermissionMap }> = [
  {
    key: "admin",
    label: "管理员",
    permissions: Object.fromEntries(MODULES.map((module) => [module.key, module.ownerOnly ? "read" : "admin"])) as PermissionMap,
  },
  {
    key: "kol_outreach",
    label: "KOL 外联",
    permissions: {
      overview: "read", kol_ops: "write", vkpi: "write", activities: "write",
      analytics: "read", insights: "read", products: "read", runtime: "none", system: "none",
      "system.usage": "none", "system.api_keys": "none", "system.models": "none", "system.members": "none", "system.restart": "none",
    },
  },
  {
    key: "content_ops",
    label: "内容运营",
    permissions: {
      overview: "read", kol_ops: "read", vkpi: "write", activities: "write",
      analytics: "write", insights: "read", products: "read", runtime: "none", system: "none",
      "system.usage": "none", "system.api_keys": "none", "system.models": "none", "system.members": "none", "system.restart": "none",
    },
  },
  {
    key: "finance",
    label: "财务",
    permissions: {
      overview: "read", kol_ops: "read", vkpi: "read", activities: "read",
      analytics: "read", insights: "write", products: "read", runtime: "none", system: "none",
      "system.usage": "read", "system.api_keys": "none", "system.models": "none", "system.members": "none", "system.restart": "none",
    },
  },
  {
    key: "viewer",
    label: "只读观察",
    permissions: Object.fromEntries(MODULES.map((module) => [module.key, module.ownerOnly ? "none" : "read"])) as PermissionMap,
  },
];

function normalizeLevel(value: unknown): VkpiPermissionLevel {
  const next = String(value || "none").toLowerCase();
  return next === "admin" || next === "write" || next === "read" ? next : "none";
}

function initialPermissions(member: VkpiStaffMember): PermissionMap {
  const current = member.permissions || {};
  const base = Object.fromEntries(MODULES.map((module) => [module.key, normalizeLevel(current[module.key])])) as PermissionMap;
  if (member.vkpiPermission && base.vkpi === "none") base.vkpi = normalizeLevel(member.vkpiPermission);
  return base;
}

function statusLabel(member: VkpiStaffMember) {
  if (!member.active) return "停用";
  if (member.verificationStatus === "verified") return "已验证";
  if (member.verificationStatus === "activated") return "已激活";
  if (member.verificationStatus === "pending") return "待激活";
  if (member.verificationStatus === "expired") return "邀请过期";
  return "启用";
}

export function StaffPermissionDrawer({
  member,
  busy,
  onClose,
  onSavePermissions,
  onCreateActivationLink,
  onCreatePasswordResetLink,
}: {
  member: VkpiStaffMember;
  busy: boolean;
  onClose: () => void;
  onSavePermissions: (staffId: string, permissions: PermissionMap) => Promise<void>;
  onCreateActivationLink: (member: VkpiStaffMember) => Promise<VkpiStaffActivationLinkResponse | null>;
  onCreatePasswordResetLink: (staffId: string) => Promise<VkpiStaffPasswordResetLinkResponse | null>;
}) {
  const [draft, setDraft] = useState<PermissionMap>(() => initialPermissions(member));
  const [localMessage, setLocalMessage] = useState("");
  const [link, setLink] = useState<{ label: string; url: string; expires: number; sent?: boolean } | null>(null);
  const grouped = useMemo(() => {
    return MODULES.reduce<Record<string, typeof MODULES>>((acc, module) => {
      acc[module.group] = [...(acc[module.group] || []), module];
      return acc;
    }, {});
  }, []);

  useEffect(() => {
    setDraft(initialPermissions(member));
    setLocalMessage("");
    setLink(null);
  }, [member]);

  const updateLevel = (key: string, value: VkpiPermissionLevel) => {
    setDraft((current) => ({ ...current, [key]: value }));
  };

  const applyTemplate = (key: string) => {
    const template = TEMPLATES.find((item) => item.key === key);
    if (template) setDraft({ ...template.permissions });
  };

  const save = async () => {
    setLocalMessage("");
    await onSavePermissions(member.id, draft);
    setLocalMessage("权限已保存，刷新后仍会保留。");
  };

  const activation = async () => {
    setLocalMessage("");
    const response = await onCreateActivationLink(member);
    const url = String(response?.activation_url || "");
    if (url) setLink({ label: "激活链接", url, expires: Number(response?.expires_in_hours || 48), sent: false });
  };

  const resetPassword = async () => {
    setLocalMessage("");
    const response = await onCreatePasswordResetLink(member.id);
    const url = String(response?.reset_url || "");
    if (url) setLink({ label: "密码重置链接", url, expires: Number(response?.expires_in_hours || 1), sent: Boolean(response?.email_sent) });
  };

  const copyLink = async () => {
    if (!link?.url) return;
    await navigator.clipboard.writeText(link.url);
    setLocalMessage("链接已复制。");
  };

  return (
    <aside className="vkpi-staff-permission-drawer" role="dialog" aria-label="账号权限详情">
      <header className="vkpi-staff-permission-drawer__header">
        <div>
          <span>账号权限详情</span>
          <h2>{member.name}</h2>
          <small>{member.email || "-"} · {member.role}</small>
        </div>
        <button type="button" onClick={onClose}>×</button>
      </header>

      <section className="vkpi-staff-permission-profile">
        <Avatar name={member.name} src={member.avatarUrl} size="lg" />
        <div>
          <h3>{member.name}</h3>
          <p>{statusLabel(member)} · {member.deliveryMethod || "unknown"}</p>
          <span>ID: {member.employeeCode || member.userId || member.id}</span>
        </div>
      </section>

      <div className="vkpi-result-grid vkpi-result-grid--compact">
        <InfoBlock label="角色" value={member.role} />
        <InfoBlock label="V-KPI" value={draft.vkpi || "none"} />
        <InfoBlock label="邀请" value={member.inviteTokenActive ? "有效 token" : (member.invitedAt || "-")} />
        <InfoBlock label="最近活跃" value={member.lastActiveAt || "-"} />
      </div>

      <section className="vkpi-staff-permission-section">
        <div className="vkpi-staff-permission-section__head">
          <h3>权限模板</h3>
          <span>先套模板，再细调模块权限</span>
        </div>
        <div className="vkpi-staff-template-row">
          {TEMPLATES.map((template) => (
            <button type="button" key={template.key} onClick={() => applyTemplate(template.key)}>{template.label}</button>
          ))}
        </div>
      </section>

      <section className="vkpi-staff-permission-section">
        <div className="vkpi-staff-permission-section__head">
          <h3>深度授权</h3>
          <span>敏感项需要 owner 权限，后端会兜底拦截</span>
        </div>
        {Object.entries(grouped).map(([group, modules]) => (
          <div className="vkpi-staff-permission-group" key={group}>
            <h4>{group}</h4>
            {modules.map((module) => (
              <div className={`vkpi-staff-permission-row ${module.ownerOnly ? "is-owner-only" : ""}`} key={module.key}>
                <div>
                  <strong>{module.label}</strong>
                  <span>{module.key}{module.ownerOnly ? " · Owner 专属" : ""}</span>
                </div>
                <div className="vkpi-permission-levels">
                  {LEVELS.map((level) => (
                    <button
                      type="button"
                      className={draft[module.key] === level.key ? "is-active" : ""}
                      key={level.key}
                      onClick={() => updateLevel(module.key, level.key)}
                    >
                      {level.label}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        ))}
      </section>

      <section className="vkpi-staff-permission-section">
        <div className="vkpi-staff-permission-section__head">
          <h3>账号操作</h3>
          <span>不设置临时密码，只发一次性链接</span>
        </div>
        <div className="vkpi-staff-action-row">
          <button className="vkpi-button" type="button" disabled={busy} onClick={() => void activation()}>生成激活链接</button>
          <button className="vkpi-button" type="button" disabled={busy} onClick={() => void resetPassword()}>重置密码</button>
          <button className="vkpi-button vkpi-button--primary" type="button" disabled={busy} onClick={() => void save()}>{busy ? "保存中" : "保存权限"}</button>
        </div>
        {link ? (
          <div className="vkpi-activation-link-panel">
            <div>
              <strong>{link.label}</strong>
              <span>{link.sent ? "邮件已发送" : "手动复制"} · {link.expires} 小时内有效</span>
            </div>
            <input readOnly value={link.url} onFocus={(event) => event.currentTarget.select()} />
            <button className="vkpi-button" type="button" onClick={() => void copyLink()}>复制链接</button>
          </div>
        ) : null}
        {localMessage ? <div className="vkpi-inline-message">{localMessage}</div> : null}
      </section>
    </aside>
  );
}
