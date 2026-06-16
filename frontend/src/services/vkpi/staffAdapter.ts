import type { VkpiStaffMember } from "../../components/vkpi/vkpiTypes";

// 把后端真实 17 名成员(VkpiStaffMember)适配成 TeamModal/EditGroupModal/Events 选人
// 所需的 UI 形态。纯函数,除类型外无依赖。
//   isAdmin = role admin/owner;avatar = 姓名首字(CJK 取首字,latin 取前两词首字母大写);
//   color = id 稳定哈希落入 8 色板;title = isAdmin ? '管理层' : (role || '成员');focus = ''。

export interface UiStaff {
  id: string;
  name: string;
  email: string;
  role: string;
  isAdmin: boolean;
  avatar: string;
  color: string;
  title: string;
  focus: string;
  online: boolean;
}

const PALETTE = [
  "#a855f7",
  "#ec4899",
  "#fbbf24",
  "#06b6d4",
  "#10b981",
  "#f97316",
  "#3b82f6",
  "#ef4444",
] as const;

/** id 稳定哈希 → 8 色板某一格(同一 id 永远同色,与渲染顺序无关)。 */
export function colorFromId(id: string): string {
  const key = String(id ?? "");
  let hash = 0;
  for (let i = 0; i < key.length; i += 1) {
    hash = (hash * 31 + key.charCodeAt(i)) | 0;
  }
  const idx = Math.abs(hash) % PALETTE.length;
  return PALETTE[idx];
}

/** 是否含 CJK(中日韩)字符。 */
function hasCjk(text: string): boolean {
  return /[㐀-鿿豈-﫿぀-ヿ가-힯]/.test(text);
}

/**
 * 姓名 → 头像缩写。
 *   CJK:取第一个字符(如「张三」→「张」)。
 *   latin:取前两个词的首字母并大写(如「John Doe」→「JD」,「alice」→「A」)。
 */
export function initials(name: string): string {
  const trimmed = String(name ?? "").trim();
  if (!trimmed) return "?";
  if (hasCjk(trimmed)) {
    return Array.from(trimmed)[0] ?? "?";
  }
  const words = trimmed.split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) {
    return words[0].slice(0, 1).toUpperCase();
  }
  return (words[0].slice(0, 1) + words[1].slice(0, 1)).toUpperCase();
}

/** role → 中文 title(管理层 / role 原文 / 兜底「成员」)。 */
export function titleFromRole(role: string, isAdmin: boolean): string {
  if (isAdmin) return "管理层";
  return String(role || "").trim() || "成员";
}

/** 单个真实成员 → UI 选人形态。 */
export function toUiStaff(member: VkpiStaffMember): UiStaff {
  const role = String(member.role ?? "");
  const isAdmin = role === "admin" || role === "owner";
  return {
    id: String(member.id),
    name: member.name ?? "",
    email: member.email ?? "",
    role,
    isAdmin,
    avatar: initials(member.name ?? ""),
    color: colorFromId(String(member.id)),
    title: titleFromRole(role, isAdmin),
    focus: "",
    online: Boolean(member.online),
  };
}

/** 批量适配。 */
export function toUiStaffList(members: VkpiStaffMember[]): UiStaff[] {
  return (Array.isArray(members) ? members : []).map(toUiStaff);
}
