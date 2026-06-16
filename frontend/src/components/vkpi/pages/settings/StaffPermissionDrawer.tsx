import React, { useEffect, useMemo, useState } from "react";
import type { VkpiStaffActivationLinkResponse, VkpiStaffPasswordResetLinkResponse, VkpiPermissionLevel } from "../../../../domains/settings";
import type { VkpiStaffMember } from "../../vkpiTypes";
import { Avatar } from "../../shared/Avatar";
import { InfoBlock } from "../../shared/InfoBlock";
import { BOARD_PERMISSION_MODULES, STAFF_ASSIGNABLE_PERMISSION_TEMPLATES, STAFF_PERMISSION_MODULES, STAFF_PERMISSION_TEMPLATES, boardLevelFor, type StaffPermissionMap } from "./staffPermissionTemplates";

// 项⑦ 简化(2026-06-16):4 档「无/只读/可写/管理」→ 3 态「无/显示/可使用」。
// 显示=read(能看,数据隔离已限自己),可使用=write(能操作)。后端 require_tab(tab,read/write)
// 真 enforce(非假)。管理(admin)= owner 专属,由 ownerOnly 模块 + system gate 兜底,不在此选。
const LEVELS: Array<{ key: VkpiPermissionLevel; label: string }> = [
  { key: "none", label: "无" },
  { key: "read", label: "显示" },
  { key: "write", label: "可使用" },
];

function normalizeLevel(value: unknown): VkpiPermissionLevel {
  const next = String(value || "none").toLowerCase();
  return next === "admin" || next === "write" || next === "read" ? next : "none";
}

function initialPermissions(member: VkpiStaffMember): StaffPermissionMap {
  const current = member.permissions || {};
  const base = Object.fromEntries(STAFF_PERMISSION_MODULES.map((module) => [module.key, normalizeLevel(current[module.key])])) as StaffPermissionMap;
  if (member.vkpiPermission && base.vkpi === "none") base.vkpi = normalizeLevel(member.vkpiPermission);
  // 导航板块:未显式设置 → 默认可见(read),避免现有成员升级后侧栏突然空白。
  for (const module of BOARD_PERMISSION_MODULES) base[module.key] = boardLevelFor(current, module.navKey);
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
  onSavePermissions: (staffId: string, permissions: StaffPermissionMap) => Promise<void>;
  onCreateActivationLink: (member: VkpiStaffMember) => Promise<VkpiStaffActivationLinkResponse | null>;
  onCreatePasswordResetLink: (staffId: string) => Promise<VkpiStaffPasswordResetLinkResponse | null>;
}) {
  const [draft, setDraft] = useState<StaffPermissionMap>(() => initialPermissions(member));
  const [localMessage, setLocalMessage] = useState("");
  // 项②:权限矩阵是「改草稿→点保存」两步式,用户点了级别按钮以为生效其实没落库 → 加「未保存」标记
  // + 保存失败 toast(原 save() 吞异常,非 owner 403 时无任何反馈)。
  const [dirty, setDirty] = useState(false);
  const [link, setLink] = useState<{ label: string; url: string; expires: number; sent?: boolean } | null>(null);
  const grouped = useMemo(() => {
    return STAFF_PERMISSION_MODULES.reduce<Record<string, typeof STAFF_PERMISSION_MODULES>>((acc, module) => {
      acc[module.group] = [...(acc[module.group] || []), module];
      return acc;
    }, {});
  }, []);
  const boardGrouped = useMemo(() => {
    return BOARD_PERMISSION_MODULES.reduce<Record<string, typeof BOARD_PERMISSION_MODULES>>((acc, module) => {
      acc[module.group] = [...(acc[module.group] || []), module];
      return acc;
    }, {});
  }, []);

  useEffect(() => {
    setDraft(initialPermissions(member));
    setLocalMessage("");
    setLink(null);
    setDirty(false);
  }, [member]);

  const updateLevel = (key: string, value: VkpiPermissionLevel) => {
    setDraft((current) => ({ ...current, [key]: value }));
    setDirty(true);
    setLocalMessage("");
  };

  const applyTemplate = (key: string) => {
    const template = STAFF_PERMISSION_TEMPLATES.find((item) => item.key === key);
    if (!template) return;
    // 模板只覆盖 14 个 tab;板块授权保留当前选择(不被模板清空)。
    setDraft((cur) => {
      const boards = Object.fromEntries(BOARD_PERMISSION_MODULES.map((m) => [m.key, boardLevelFor(cur, m.navKey)]));
      return { ...template.permissions, ...boards };
    });
  };

  const save = async () => {
    setLocalMessage("");
    try {
      await onSavePermissions(member.id, draft);
      setDirty(false);
      setLocalMessage("权限已保存，刷新后仍会保留。");
    } catch (err) {
      // 原本吞异常 → 非 owner 403 时用户以为「点了无效」。明确提示。
      setLocalMessage(err instanceof Error ? err.message : "保存失败：仅 owner 可修改权限");
    }
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
          {STAFF_ASSIGNABLE_PERMISSION_TEMPLATES.map((template) => (
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
          <h3>导航板块授权</h3>
          <span>控制该成员侧栏能看到哪些板块（无 = 隐藏）。默认全部可见。</span>
        </div>
        {Object.entries(boardGrouped).map(([group, modules]) => (
          <div className="vkpi-staff-permission-group" key={group}>
            <h4>{group}</h4>
            {modules.map((module) => (
              <div className="vkpi-staff-permission-row" key={module.key}>
                <div>
                  <strong>{module.label}</strong>
                  <span>{module.key}</span>
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
          <button className="vkpi-button vkpi-button--primary" type="button" disabled={busy} onClick={() => void save()}
            style={dirty ? { boxShadow: "0 0 0 2px #fbbf24", fontWeight: 700 } : undefined}>
            {busy ? "保存中" : dirty ? "● 保存权限(未保存)" : "保存权限"}
          </button>
        </div>
        {dirty ? <div style={{ marginTop: 6, fontSize: "11px", color: "#fbbf24" }}>权限已改但未保存 —— 点「保存权限」才生效</div> : null}
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
