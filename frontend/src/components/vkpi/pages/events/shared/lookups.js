import { KOL_POOL } from "../data/kol-pool.js";
import { PROJECTS } from "../data/projects.js";
import { TEAM } from "../data/team.js";
export function ownerById(id) { return TEAM.find(t => t.id === id) || TEAM[0]; }
export function ownerByInitial(i) { return TEAM.find(t => t.initial === i) || { initial: i, color: "#94a3b8" }; }
export function kolById(id) { return KOL_POOL.find(k => k.id === id); }
export function projectById(id) { return PROJECTS.find(p => p.id === id); }
