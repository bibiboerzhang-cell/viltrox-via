import React, { useEffect, useMemo, useState } from "react";
import { Copy, KeyRound, Link2, Lock, Save, ShieldCheck, X } from "lucide-react";
import type { VkpiStaffActivationLinkResponse, VkpiStaffPasswordResetLinkResponse, VkpiPermissionLevel } from "../../../../domains/settings";
import type { VkpiStaffMember } from "../../vkpiTypes";
import { Avatar } from "../../shared/Avatar";
import { InfoBlock } from "../../shared/InfoBlock";
import {
  BOARD_PERMISSION_KEY_PREFIX,
  BOARD_PERMISSION_MODULES,
  DEFAULT_VISIBLE_MODULE_KEYS,
  STAFF_ASSIGNABLE_PERMISSION_TEMPLATES,
  STAFF_PERMISSION_MODULES,
  STAFF_PERMISSION_TEMPLATES,
  boardLevelFor,
  staffStatusLabel,
  type StaffPermissionMap,
} from "./staffPermissionTemplates";

// C7「两态·默认显示」(2026-06-16 用户拍板):去掉「无」,业务模块只在 显示/可使用 两态间选。
// 显示=read(能看见、进得了系统),可使用=write(能操作)。后端 require_tab(tab,read/write) 真 enforce,
// 且对非 owner 把这些模块兜底到至少 read(permissions.py DEFAULT_VISIBLE_KEYS),所以默认人人显示全部模块。
// 敏感模块(ownerOnly:api_keys/models/members/restart)仍保留「无」可显式收回,且写级由后端降级兜底。
const ASSIGN_LEVELS: Array<{ key: VkpiPermissionLevel; label: string }> = [
  { key: "read", label: "显示" },
  { key: "write", label: "可使用" },
];
const OWNER_LEVELS: Array<{ key: VkpiPermissionLevel; label: string }> = [
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
  // 两态默认显示:仅「默认显示」业务模块 缺省/无 → 显示(read),与后端 floor 对齐;
  // 系统/用量/运行诊断 + 敏感模块保留真实值(可为无,owner 按需授权)。
  const base = Object.fromEntries(STAFF_PERMISSION_MODULES.map((module) => {
    const lvl = normalizeLevel(current[module.key]);
    return [module.key, (DEFAULT_VISIBLE_MODULE_KEYS.has(module.key) && lvl === "none") ? "read" : lvl];
  })) as StaffPermissionMap;
  if (member.vkpiPermission && base.vkpi === "none") base.vkpi = normalizeLevel(member.vkpiPermission);
  // 板块可见(board.<navKey>)已显式存过的值必须随草稿一起保存回去 ——
  // 保存走「整包 permissions」通道,不带上会把历史板块设置洗掉。
  for (const [key, value] of Object.entries(current)) {
    if (key.startsWith(BOARD_PERMISSION_KEY_PREFIX)) base[key] = normalizeLevel(value);
  }
  return base;
}

// 状态口径统一走 staffPermissionTemplates.staffStatusLabel(与 StaffTable / 关系视图同源)。
const statusLabel = staffStatusLabel;

// 当前草稿匹配哪个模板(与 applyTemplate 同一套 floor 口径逐 key 对比);
// 全不匹配 → null(UI 显示「自定义」高亮)。
function detectTemplate(draft: StaffPermissionMap): string | null {
  for (const template of STAFF_ASSIGNABLE_PERMISSION_TEMPLATES) {
    const matches = STAFF_PERMISSION_MODULES.every((module) => {
      let expected = normalizeLevel(template.permissions[module.key]);
      if (DEFAULT_VISIBLE_MODULE_KEYS.has(module.key) && expected === "none") expected = "read";
      return normalizeLevel(draft[module.key]) === expected;
    });
    if (matches) return template.key;
  }
  return null;
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
  // 授权页 V1:模板选中态。套模板记 key;逐项微调后置 null(UI 高亮「自定义」);
  // 初始按当前权限反推(与 demo「当前高亮=生效模板」一致)。
  const [activeTemplate, setActiveTemplate] = useState<string | null>(() => detectTemplate(initialPermissions(member)));
  // 板块可见选择器(17 板块 chips)展开态。
  const [boardPickOpen, setBoardPickOpen] = useState(false);
  const [link, setLink] = useState<{ label: string; url: string; expires: number; sent?: boolean } | null>(null);
  const grouped = useMemo(() => {
    return STAFF_PERMISSION_MODULES.reduce<Record<string, typeof STAFF_PERMISSION_MODULES>>((acc, module) => {
      acc[module.group] = [...(acc[module.group] || []), module];
      return acc;
    }, {});
  }, []);

  // 线上修(2026-07-10):依赖改 member.id —— 只在「换了一个人」时重置本地状态。
  // 原依赖 [member](对象引用):生成激活链接的动作里 onRefreshData 刷新 staffMembers 后,
  // resolveDrawerMember 每次渲染返回新对象 → 本 effect 触发 setLink(null),把刚生成的
  // 链接立刻清掉(接口 200 但界面永远不显示)。重置密码不走刷新所以没中招。
  useEffect(() => {
    const next = initialPermissions(member);
    setDraft(next);
    setActiveTemplate(detectTemplate(next));
    setBoardPickOpen(false);
    setLocalMessage("");
    setLink(null);
    setDirty(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [member.id]);

  const updateLevel = (key: string, value: VkpiPermissionLevel) => {
    setDraft((current) => ({ ...current, [key]: value }));
    // 微调后模板不再整套生效 → 选中态落到「自定义」(demo 口径)。
    setActiveTemplate(null);
    setDirty(true);
    setLocalMessage("");
  };

  const applyTemplate = (key: string) => {
    const template = STAFF_PERMISSION_TEMPLATES.find((item) => item.key === key);
    if (!template) return;
    // 套模板后,「默认显示」业务模块仍兜底到至少「显示」(与两态默认显示一致)。
    const next: StaffPermissionMap = { ...template.permissions };
    for (const module of STAFF_PERMISSION_MODULES) {
      if (DEFAULT_VISIBLE_MODULE_KEYS.has(module.key) && normalizeLevel(next[module.key]) === "none") next[module.key] = "read";
    }
    // 板块可见设置独立于模块模板,套模板不清洗已有 board.* 草稿。
    setDraft((current) => {
      for (const [k, v] of Object.entries(current)) {
        if (k.startsWith(BOARD_PERMISSION_KEY_PREFIX)) next[k] = v;
      }
      return next;
    });
    // 痛点①:此前 applyTemplate 不记 activeTemplate → 点完模板毫无选中反馈。
    setActiveTemplate(key);
    setDirty(true);
    setLocalMessage("");
  };

  // 板块可见开关:写 board.<navKey> 进草稿(勾=read 可见,取消=none 隐藏),
  // 保存仍走同一个 onSavePermissions 整包通道。不动模板选中态(板块层独立于模块模板)。
  const toggleBoard = (navKey: string) => {
    const key = `${BOARD_PERMISSION_KEY_PREFIX}${navKey}`;
    setDraft((current) => ({
      ...current,
      [key]: boardLevelFor(current, navKey) === "none" ? "read" : "none",
    }));
    setDirty(true);
    setLocalMessage("");
  };

  const visibleBoardCount = BOARD_PERMISSION_MODULES.filter((board) => boardLevelFor(draft, board.navKey) !== "none").length;
  const memberIsOwner = Boolean(member.isOwner) || String(member.role || "").toLowerCase().trim() === "owner";

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

  // 与 save() 同款处置:失败/空结果必须可见,不许静默(用户点了没反馈会反复点)。
  const activation = async () => {
    setLocalMessage("");
    try {
      const response = await onCreateActivationLink(member);
      const url = String(response?.activation_url || "");
      if (url) setLink({ label: "激活链接", url, expires: Number(response?.expires_in_hours || 48), sent: false });
      else setLocalMessage("激活链接生成成功但未返回 URL，请查看后端日志。");
    } catch (err) {
      setLocalMessage(err instanceof Error ? err.message : "激活链接生成失败");
    }
  };

  const resetPassword = async () => {
    setLocalMessage("");
    try {
      const response = await onCreatePasswordResetLink(member.id);
      const url = String(response?.reset_url || "");
      if (url) setLink({ label: "密码重置链接", url, expires: Number(response?.expires_in_hours || 1), sent: Boolean(response?.email_sent) });
      else setLocalMessage("密码重置链接生成成功但未返回 URL，请查看后端日志。");
    } catch (err) {
      setLocalMessage(err instanceof Error ? err.message : "密码重置链接生成失败");
    }
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
        <button type="button" onClick={onClose} aria-label="关闭账号权限详情" title="关闭">
          <X size={17} />
        </button>
      </header>

      {/* 痛点③:draft→save 两步式保留,但改动一发生就在顶部亮黄条,不再只藏在底部按钮旁。 */}
      {dirty ? (
        <div className="vkpi-staff-permission-dirtybar" role="status">
          <strong>未保存</strong>
          <span>改动还在草稿里，点底部「保存权限」才会生效。</span>
        </div>
      ) : null}

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
          <span>先套模板再细调 · 当前高亮 = 生效模板，微调后落「自定义」</span>
        </div>
        <div className="vkpi-staff-template-row">
          {STAFF_ASSIGNABLE_PERMISSION_TEMPLATES.map((template) => (
            <button
              type="button"
              key={template.key}
              className={activeTemplate === template.key ? "is-active" : ""}
              aria-pressed={activeTemplate === template.key}
              title={template.detail}
              onClick={() => applyTemplate(template.key)}
            >
              <ShieldCheck size={13} />
              {template.label}
            </button>
          ))}
          <span
            className={`vkpi-staff-template-custom ${activeTemplate === null ? "is-active" : ""}`}
            title="不完全匹配任何模板：按下方逐项微调的结果生效"
          >
            自定义
          </span>
        </div>
      </section>

      <section className="vkpi-staff-permission-section">
        <div className="vkpi-staff-permission-section__head">
          <h3>深度授权</h3>
          <span>敏感项需要 owner 权限，后端会兜底拦截</span>
        </div>
        {/* 痛点②:两态/三态混排无图例 → 顶部一行图例讲清两种刻度。 */}
        <div className="vkpi-permission-legend">
          <span><i className="is-two" aria-hidden="true" />业务模块 · 两态（显示 / 可使用，默认人人显示）</span>
          <span><i className="is-three" aria-hidden="true" />系统与敏感 · 三态（无 / 显示 / 可使用，默认无）</span>
          <span><Lock size={10} aria-hidden="true" /> OWNER 锁 = 仅创始人，后端硬闸</span>
        </div>
        {Object.entries(grouped).map(([group, modules]) => (
          <div className="vkpi-staff-permission-group" key={group}>
            <h4>{group}</h4>
            {modules.map((module) => (
              <div className={`vkpi-staff-permission-row ${module.ownerOnly ? "is-owner-only" : ""}`} key={module.key}>
                <div>
                  <strong>{module.label}</strong>
                  <span>{module.key}</span>
                </div>
                {module.ownerOnly ? (
                  <span className="vkpi-owner-lock-badge" title="Owner 专属：非 owner 即使勾了也会被后端降级">
                    <Lock size={10} />OWNER
                  </span>
                ) : null}
                <div className="vkpi-permission-levels">
                  {(DEFAULT_VISIBLE_MODULE_KEYS.has(module.key) ? ASSIGN_LEVELS : OWNER_LEVELS).map((level) => (
                    <button
                      type="button"
                      className={draft[module.key] === level.key ? "is-active" : ""}
                      key={level.key}
                      onClick={() => updateLevel(module.key, level.key)}
                      aria-pressed={draft[module.key] === level.key}
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

      {/* 授权页 V1 · 板块可见选择器:激活 boardLevelFor 死代码,勾选写 board.<navKey>
          进草稿,保存走现有 onSavePermissions;侧栏 canViewBoard 读同一口径真实过滤。
          默认口径向后兼容:没存过 board 权限 = 全见(17/17),owner 永远全见。 */}
      <section className="vkpi-staff-permission-section">
        <div className="vkpi-staff-permission-section__head">
          <h3>板块可见 · 侧栏</h3>
          <span>{memberIsOwner ? "Owner 全见，此配置对其不生效" : "该成员侧栏能看到哪些板块 · 未设置默认全见"}</span>
        </div>
        <div className="vkpi-board-pickbar">
          <span>已开 <strong>{visibleBoardCount}</strong> / {BOARD_PERMISSION_MODULES.length}</span>
          <button
            type="button"
            className="vkpi-button"
            aria-expanded={boardPickOpen}
            onClick={() => setBoardPickOpen((open) => !open)}
          >
            {boardPickOpen ? "收起" : "选择板块"}
          </button>
        </div>
        {boardPickOpen ? (
          <div className="vkpi-board-chip-grid">
            {BOARD_PERMISSION_MODULES.map((board) => {
              const visible = boardLevelFor(draft, board.navKey) !== "none";
              return (
                <button
                  type="button"
                  key={board.key}
                  className={`vkpi-board-chip ${visible ? "is-on" : ""}`}
                  aria-pressed={visible}
                  title={`${board.group} · ${board.key}`}
                  onClick={() => toggleBoard(board.navKey)}
                >
                  <span>{board.label}</span>
                  <strong aria-hidden="true">{visible ? "✓" : "—"}</strong>
                </button>
              );
            })}
          </div>
        ) : null}
      </section>

      <section className="vkpi-staff-permission-section">
        <div className="vkpi-staff-permission-section__head">
          <h3>账号操作</h3>
          <span>激活 / 重置 / 保存一处收拢 · 不设临时密码，只发一次性链接</span>
        </div>
        <div className="vkpi-staff-action-row">
          <button className="vkpi-button" type="button" disabled={busy} onClick={() => void activation()}>
            <Link2 size={14} />
            生成激活链接
          </button>
          <button className="vkpi-button" type="button" disabled={busy} onClick={() => void resetPassword()}>
            <KeyRound size={14} />
            重置密码
          </button>
          <button className={`vkpi-button vkpi-button--primary ${dirty ? "is-dirty" : ""}`} type="button" disabled={busy} onClick={() => void save()}>
            <Save size={14} />
            {busy ? "保存中" : dirty ? "保存权限 · 未保存" : "保存权限"}
          </button>
        </div>
        {dirty ? <div className="vkpi-staff-permission-unsaved">权限已改但未保存，点“保存权限”后生效。</div> : null}
        {link ? (
          <div className="vkpi-activation-link-panel">
            <div>
              <strong>{link.label}</strong>
              <span>{link.sent ? "邮件已发送" : "手动复制"} · {link.expires} 小时内有效</span>
            </div>
            <input readOnly value={link.url} onFocus={(event) => event.currentTarget.select()} />
            <button className="vkpi-button" type="button" onClick={() => void copyLink()}>
              <Copy size={14} />
              复制链接
            </button>
          </div>
        ) : null}
        {localMessage ? <div className="vkpi-inline-message">{localMessage}</div> : null}
      </section>
    </aside>
  );
}
