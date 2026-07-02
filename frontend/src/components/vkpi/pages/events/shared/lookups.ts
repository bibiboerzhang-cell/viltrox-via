import { KOL_POOL } from "../data/kol-pool";
import { PROJECTS } from "../data/projects";
import { TEAM } from "../data/team";
import type { KolPoolEntry, MemberLike, MemberVm, ProjectVm, UiStaff } from "./types";

// 真实 staff 模块缓存(EventsPage 每次渲染同步喂入,与 setRealProjects 同款模式)。
// 让 ownerById/ownerByInitial 在没有 staff prop 的深层组件(物料/产品面板等)也能解析真人。
let _realStaff: UiStaff[] = [];
export function setRealStaff(list: UiStaff[]): void {
  _realStaff = Array.isArray(list) ? list : [];
}
function staffToMember(s: UiStaff): MemberVm {
  return { id: s.id, name: s.name, color: s.color || "#94a3b8", initial: s.avatar || String(s.name || "?").slice(0, 1) };
}
// 真人优先(按 staff id 匹配)→ 旧 mock TEAM(兼容老数据字母 id)→ 诚实兜底(不再冒充 TEAM[0])。
export function ownerById(id: string | number): MemberVm {
  const real = _realStaff.find(s => String(s.id) === String(id));
  if (real) return staffToMember(real);
  const legacy = TEAM.find(t => String(t.id) === String(id));
  if (legacy) return legacy;
  return { id, name: String(id ?? ""), color: "#94a3b8", initial: String(id ?? "?").slice(0, 1).toUpperCase() };
}
// key 可能是:真 staff id(新数据)/ 真人姓名(费用 paidBy)/ 旧 mock initial(老数据,原样显示不崩)。
export function ownerByInitial(i: string): MemberLike {
  const key = String(i ?? "");
  const real = _realStaff.find(s => String(s.id) === key) || _realStaff.find(s => String(s.name) === key);
  if (real) return staffToMember(real);
  return TEAM.find(t => t.initial === key) || { initial: key, color: "#94a3b8" };
}
// 真成员优先解析(teamUserIds 现在是真 staff id);命不中再回退旧 mock TEAM(兼容老 event 字母 id)。
// staff 为 UiStaff[]({id,name,color,avatar});统一返回 {id,name,color,initial} 供卡片/详情渲染。
export function memberFromStaff(id: string | number, staff: UiStaff[]): MemberVm {
  const list = Array.isArray(staff) ? staff : [];
  const real = list.find(s => String(s.id) === String(id));
  if (real) return { id: real.id, name: real.name, color: real.color || "#94a3b8", initial: real.avatar || String(real.name || "?").slice(0, 1) };
  const legacy = TEAM.find(t => String(t.id) === String(id));
  if (legacy) return legacy;
  return { id, name: String(id), color: "#94a3b8", initial: String(id).slice(0, 1).toUpperCase() };
}
export function kolById(id: string): KolPoolEntry | undefined { return KOL_POOL.find(k => k.id === id); }
// #7 真项目优先解析:EventsPage 拉到真 /projects 后 setRealProjects 喂模块缓存,projectById 命中真项目即返回
// (新建活动关联真项目后,卡片/详情正确显名);命不中回退 mock(兼容老活动的字母 id)。统一 {id,title}。
let _realProjects: ProjectVm[] = [];
export function setRealProjects(list: Array<Record<string, unknown>>): void {
  _realProjects = (Array.isArray(list) ? list : [])
    .map(p => ({ id: String(p.id ?? p.project_uid ?? ""), title: String(p.project_name || p.title || p.name || (p.id ?? "")) }))
    .filter(p => p.id);
}
export function projectById(id: string | number): ProjectVm | undefined {
  const sid = String(id);
  const real = _realProjects.find(p => p.id === sid);
  if (real) return real;
  return PROJECTS.find(p => String(p.id) === sid);
}
