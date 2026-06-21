import { KOL_POOL } from "../data/kol-pool.js";
import { PROJECTS } from "../data/projects.js";
import { TEAM } from "../data/team.js";
export function ownerById(id) { return TEAM.find(t => t.id === id) || TEAM[0]; }
export function ownerByInitial(i) { return TEAM.find(t => t.initial === i) || { initial: i, color: "#94a3b8" }; }
// 真成员优先解析(teamUserIds 现在是真 staff id);命不中再回退旧 mock TEAM(兼容老 event 字母 id)。
// staff 为 UiStaff[]({id,name,color,avatar});统一返回 {id,name,color,initial} 供卡片/详情渲染。
export function memberFromStaff(id, staff) {
  const list = Array.isArray(staff) ? staff : [];
  const real = list.find(s => String(s.id) === String(id));
  if (real) return { id: real.id, name: real.name, color: real.color || "#94a3b8", initial: real.avatar || String(real.name || "?").slice(0, 1) };
  const legacy = TEAM.find(t => String(t.id) === String(id));
  if (legacy) return legacy;
  return { id, name: String(id), color: "#94a3b8", initial: String(id).slice(0, 1).toUpperCase() };
}
export function kolById(id) { return KOL_POOL.find(k => k.id === id); }
// #7 真项目优先解析:EventsPage 拉到真 /projects 后 setRealProjects 喂模块缓存,projectById 命中真项目即返回
// (新建活动关联真项目后,卡片/详情正确显名);命不中回退 mock(兼容老活动的字母 id)。统一 {id,title}。
let _realProjects = [];
export function setRealProjects(list) {
  _realProjects = (Array.isArray(list) ? list : [])
    .map(p => ({ id: String(p.id ?? p.project_uid ?? ""), title: p.project_name || p.title || p.name || String(p.id ?? "") }))
    .filter(p => p.id);
}
export function projectById(id) {
  const sid = String(id);
  const real = _realProjects.find(p => p.id === sid);
  if (real) return real;
  return PROJECTS.find(p => String(p.id) === sid);
}
