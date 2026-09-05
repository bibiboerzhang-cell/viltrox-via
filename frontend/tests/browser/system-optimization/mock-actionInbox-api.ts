// Vite aliases point here only when the dedicated fixture config is selected.
export type * from "../../../src/services/vkpi/actionInbox-api";
import type { ActionReconcileRequest } from "../../../src/services/vkpi/actionInbox-api";
import { deny, execute, listInbox, recentLedger, reconcile, transition } from "./fixture-state";

export async function listActionInbox(token: string, params = {}) { return listInbox(token, params); }
export async function approveAction(token: string, id: number) { return transition(token, id, "approved"); }
export async function dismissAction(token: string, id: number) { return transition(token, id, "dismissed"); }
export async function snoozeAction(token: string, id: number, minutes: number) {
  if (minutes !== 1440) return deny("unsupported snooze duration");
  return transition(token, id, "snoozed");
}
export async function executeAction(token: string, id: number) { return execute(token, id); }
export async function reconcileAction(token: string, id: number, payload: ActionReconcileRequest) { return reconcile(token, id, payload); }
export async function listRecentExecutionLedger(token: string, limit?: number) { return recentLedger(token, limit); }
export async function listActionExecutionLedger() { return deny("listActionExecutionLedger not in fixture contract"); }
export async function getActionReviewCandidate() { return deny("getActionReviewCandidate not in fixture contract"); }
export async function verifyActionResult() { return deny("verifyActionResult not in fixture contract"); }
