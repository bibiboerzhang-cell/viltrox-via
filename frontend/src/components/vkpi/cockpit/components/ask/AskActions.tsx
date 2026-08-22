// Ask ⌘K 动作执行:候选 → navigate / open_entity / ask。复用既有抽屉与导航事件管道,不改管道。
//   KOL     → localStorage vkpi:pending-kolpool-open-id + vkpi:open-kol-pool-item
//   项目    → vkpi:open-project-task {projectId}
//   活动    → 导航 events
//   SKU     → sessionStorage vkpi:sku360-sku + vkpi:open-sku360 + 导航 sku360
//   镜头家族 → sessionStorage vkpi:sku360-search + vkpi:open-sku360-search(预填搜索)+ 导航 sku360
//   找达人记录 → localStorage vkpi:pendingKolSearchSessionId + vkpi:open-kol-search-session
// 后端 IntelligentAction 的执行(navigate / suggest_query)也在这里;requires_approval 永不执行。

import type { IntelligentAction } from "../../../../../services/vkpi/intelligent-api";
import type { AskCandidate } from "./askGrammar";
import { PENDING_SEARCH_SESSION_KEY } from "./useAskCandidates";
import { pushAskRecent } from "./askRecent";

export interface AskActionContext {
  onNavigate?: (key: string) => void;
  onClose: () => void;
  ask: (question: string) => void;
  setQuery: (value: string) => void;
}

function setLocal(key: string, value: string) {
  try { window.localStorage.setItem(key, value); } catch { /* 隐私模式/配额:事件仍会派发 */ }
}

function setSession(key: string, value: string) {
  try { window.sessionStorage.setItem(key, value); } catch { /* 同上 */ }
}

function dispatch(name: string, detail: Record<string, unknown>) {
  window.dispatchEvent(new CustomEvent(name, { detail }));
}

export function openAskEntity(action: Extract<AskCandidate["action"], { type: "open_entity" }>, ctx: AskActionContext): void {
  const { entity } = action;
  switch (entity.type) {
    case "kol":
      setLocal("vkpi:pending-kolpool-open-id", String(entity.id));
      dispatch("vkpi:open-kol-pool-item", { kolPoolId: Number(entity.id) });
      break;
    case "project":
      dispatch("vkpi:open-project-task", { projectId: String(entity.id) });
      break;
    case "event":
      ctx.onNavigate?.(action.route || "events");
      break;
    case "sku":
      setSession("vkpi:sku360-sku", String(entity.id));
      dispatch("vkpi:open-sku360", { sku: String(entity.id) });
      ctx.onNavigate?.(action.route || "sku360");
      break;
    case "search_session":
      setLocal(PENDING_SEARCH_SESSION_KEY, String(entity.id));
      dispatch("vkpi:open-kol-search-session", { sessionId: Number(entity.id) });
      break;
    default:
      if (action.route) ctx.onNavigate?.(action.route);
  }
}

/** 候选主动作。navigate / open_entity 会关闭浮层并留痕;ask 留在浮层内发问。 */
export function runAskCandidate(candidate: AskCandidate, ctx: AskActionContext): void {
  const { action } = candidate;
  if (action.type === "ask") {
    pushAskRecent(candidate);
    ctx.setQuery(action.query);
    ctx.ask(action.query);
    return;
  }
  pushAskRecent(candidate);
  if (action.type === "navigate") {
    const search = action.params?.search?.trim();
    if (action.route === "sku360" && search) {
      setSession("vkpi:sku360-search", search);
      dispatch("vkpi:open-sku360-search", { query: search });
    }
    ctx.onNavigate?.(action.route);
  } else {
    openAskEntity(action, ctx);
  }
  ctx.onClose();
}

/** 后端答案里的动作:只执行无需审批的 navigate / suggest_query;其余一律不动。 */
export function runIntelligentAction(action: IntelligentAction, ctx: AskActionContext): void {
  if (action.requires_approval) return;
  const suggestedQuery = action.type === "suggest_query" && typeof action.params?.query === "string"
    ? action.params.query.trim()
    : "";
  if (suggestedQuery) {
    ctx.setQuery(suggestedQuery);
    ctx.ask(suggestedQuery);
    return;
  }
  if (action.type === "navigate" && action.route) {
    const pendingKolQuery = action.route === "kol-pool" && typeof action.params?.query === "string"
      ? action.params.query.trim()
      : "";
    if (pendingKolQuery) {
      setLocal("vkpi:pending-kolpool-search", pendingKolQuery);
      dispatch("vkpi:open-kol-pool-search", { query: pendingKolQuery });
    }
    ctx.onNavigate?.(action.route);
    ctx.onClose();
  }
}

export function isRunnableIntelligentAction(action: IntelligentAction): boolean {
  if (action.requires_approval) return false;
  if (action.type === "navigate") return Boolean(action.route);
  return action.type === "suggest_query" && typeof action.params?.query === "string";
}
