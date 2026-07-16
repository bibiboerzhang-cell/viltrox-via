import { API_BASE, ApiResponseError, apiFetch, buildApiUrl, jsonBody } from "../http";

const ROOT = "/api/admin/vkpi/marketing-advisor";

export interface AdvisorReadiness {
  status: string;
  core_status?: "ready" | "blocked" | string;
  external_ai_status?: "ready" | "blocked" | string;
  ai_off_path_ready?: boolean;
  external_ai_ready?: boolean;
  provider_ready: boolean;
  provider_called?: boolean;
  reason?: string;
  persistence_ready: boolean;
  action_mode: "draft_only" | string;
  retryable?: boolean;
  knowledge_bridge_ready?: boolean;
  knowledge_bridge_reason?: string;
  blockers?: string[];
  capabilities?: Record<string, Record<string, unknown>>;
  provider_connectivity?: Record<string, unknown>;
  exact_model_evidence?: Record<string, unknown>;
}

export interface AdvisorThread {
  thread_uid: string;
  title: string;
  status: string;
  context_refs_json?: Array<Record<string, unknown>>;
  created_at?: string;
  updated_at?: string;
  last_message_at?: string | null;
}

export interface AdvisorMessage {
  message_uid: string;
  thread_uid: string;
  role: "user" | "assistant" | "system" | string;
  content_text: string;
  status: string;
  provider_status?: string;
  provider_reason?: string;
  metadata_json?: Record<string, unknown>;
  provenance_json?: Record<string, unknown>;
  created_at?: string;
}

export interface AdvisorMemorySettings {
  state: "active" | "paused" | string;
  retention_days: number;
  persisted?: boolean;
}

export interface AdvisorMemoryCandidate {
  candidate_uid: string;
  memory_kind: string;
  memory_key: string;
  summary: string;
  status: string;
  sensitivity?: string;
  created_at?: string;
}

export interface AdvisorMemoryFact {
  fact_uid: string;
  memory_kind: string;
  memory_key: string;
  summary: string;
  status: string;
  version?: number;
  updated_at?: string;
}

export interface AdvisorMemorySnapshot {
  settings: AdvisorMemorySettings;
  candidates: AdvisorMemoryCandidate[];
  facts: AdvisorMemoryFact[];
  retention_policy?: {
    mode: "read_window" | string;
    retention_days: number;
    cutoff_at?: string;
    candidate_clock?: string;
    fact_clock?: string;
    expired_rows_returned?: boolean;
    physical_delete_performed?: boolean;
  };
}

export interface AdvisorTurnResponse {
  status: string;
  reason?: string;
  claim_status?: string;
  provider?: AdvisorReadiness;
  messages: AdvisorMessage[];
  draft_actions?: Array<Record<string, unknown>>;
  idempotent_replay?: boolean;
  knowledge_bridge?: { status?: string; mode?: string; reason?: string };
}

export interface AdvisorStreamEvent {
  type: "accepted" | "final" | "error";
  payload: Record<string, unknown>;
}

export async function getAdvisorReadiness(token: string): Promise<AdvisorReadiness> {
  return apiFetch<AdvisorReadiness>(`${ROOT}/readiness`, { cache: "no-store" }, token);
}

export async function listAdvisorThreads(token: string, limit = 50): Promise<AdvisorThread[]> {
  const payload = await apiFetch<{ threads?: AdvisorThread[] }>(
    `${ROOT}/threads?limit=${Math.max(1, Math.min(limit, 200))}`,
    { cache: "no-store" },
    token,
  );
  return Array.isArray(payload.threads) ? payload.threads : [];
}

export async function createAdvisorThread(token: string, title: string): Promise<AdvisorThread> {
  const payload = await apiFetch<{ thread: AdvisorThread }>(
    `${ROOT}/threads`,
    { method: "POST", body: jsonBody({ title, context_refs: [] }) },
    token,
  );
  return payload.thread;
}

export async function listAdvisorMessages(
  token: string,
  threadUid: string,
  limit = 100,
): Promise<AdvisorMessage[]> {
  const payload = await apiFetch<{ messages?: AdvisorMessage[] }>(
    `${ROOT}/threads/${encodeURIComponent(threadUid)}/messages?limit=${Math.max(1, Math.min(limit, 500))}`,
    { cache: "no-store" },
    token,
  );
  return Array.isArray(payload.messages) ? payload.messages : [];
}

export async function postAdvisorMessage(
  token: string,
  threadUid: string,
  content: string,
  clientRequestId: string,
  allowExternalAi = false,
): Promise<AdvisorTurnResponse> {
  return apiFetch<AdvisorTurnResponse>(
    `${ROOT}/threads/${encodeURIComponent(threadUid)}/messages`,
    {
      method: "POST",
      body: jsonBody({
        content,
        client_request_id: clientRequestId,
        context_refs: [],
        requested_actions: [],
        allow_external_ai: allowExternalAi,
      }),
      // The server's provider deadline is 45 s.  Keep the client above that
      // boundary so a browser abort cannot race a paid call into unknown state.
      timeoutMs: 60_000,
    },
    token,
  );
}

function parsePayload(raw: string): unknown {
  if (!raw) return {};
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

/**
 * Real staged stream for the private advisor.
 *
 * The server emits an immediate durable `accepted` event followed by exactly
 * one persisted `final` (or bounded `error`) event.  Provider transports remain
 * buffered, so this deliberately does not invent token-level progress.  There
 * is no client timeout after acceptance: aborting a browser request while a
 * paid call is running would make the outcome ambiguous; idempotent retry stays
 * available through the unchanged client_request_id.
 */
export async function postAdvisorMessageStream(
  token: string,
  threadUid: string,
  content: string,
  clientRequestId: string,
  allowExternalAi = false,
  onEvent?: (event: AdvisorStreamEvent) => void,
): Promise<AdvisorTurnResponse> {
  const headers = new Headers({
    "Content-Type": "application/json",
    Accept: "text/event-stream",
    "X-Requested-With": "XMLHttpRequest",
  });
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(
    buildApiUrl(`${ROOT}/threads/${encodeURIComponent(threadUid)}/messages/stream`),
    {
      method: "POST",
      credentials: API_BASE ? "include" : "same-origin",
      headers,
      body: jsonBody({
        content,
        client_request_id: clientRequestId,
        context_refs: [],
        requested_actions: [],
        allow_external_ai: allowExternalAi,
      }),
    },
  );

  if (!response.ok) {
    const raw = await response.text();
    throw new ApiResponseError(response, parsePayload(raw));
  }
  if (!response.body) throw new Error("advisor_stream_unavailable");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalPayload: AdvisorTurnResponse | null = null;

  const consumeBlock = (rawBlock: string) => {
    const lines = rawBlock.split("\n");
    let eventName = "message";
    const dataLines: string[] = [];
    for (const line of lines) {
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
    }
    if (!dataLines.length || !["accepted", "final", "error"].includes(eventName)) return;
    const parsed = parsePayload(dataLines.join("\n"));
    const payload = parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {};
    const event = { type: eventName as AdvisorStreamEvent["type"], payload };
    onEvent?.(event);
    if (event.type === "final") finalPayload = payload as unknown as AdvisorTurnResponse;
    if (event.type === "error") throw new Error(String(payload.code || "advisor_stream_failed"));
  };

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    buffer = buffer.replace(/\r\n/g, "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      if (block.trim()) consumeBlock(block);
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
  if (buffer.trim()) consumeBlock(buffer.trim());
  if (!finalPayload) throw new Error("advisor_stream_missing_final");
  return finalPayload;
}

export async function getAdvisorMemory(token: string): Promise<AdvisorMemorySnapshot> {
  const payload = await apiFetch<AdvisorMemorySnapshot>(`${ROOT}/memory?limit=100`, { cache: "no-store" }, token);
  return {
    settings: payload.settings || { state: "active", retention_days: 180, persisted: false },
    candidates: Array.isArray(payload.candidates) ? payload.candidates : [],
    facts: Array.isArray(payload.facts) ? payload.facts : [],
    retention_policy: payload.retention_policy,
  };
}

export async function updateAdvisorMemorySettings(
  token: string,
  state: "active" | "paused",
  retentionDays?: number,
): Promise<AdvisorMemorySettings> {
  const payload = await apiFetch<{ settings: AdvisorMemorySettings }>(
    `${ROOT}/memory/settings`,
    { method: "PATCH", body: jsonBody({ state, retention_days: retentionDays }) },
    token,
  );
  return payload.settings;
}

export async function createAdvisorMemoryCandidate(
  token: string,
  summary: string,
): Promise<AdvisorMemoryCandidate> {
  const createdAt = new Date().toISOString();
  const key = `manual:${createdAt}:${Math.random().toString(36).slice(2, 10)}`;
  const payload = await apiFetch<{ candidate: AdvisorMemoryCandidate }>(
    `${ROOT}/memory/candidates`,
    {
      method: "POST",
      body: jsonBody({
        memory_kind: "preference",
        memory_key: key,
        summary,
        value: { text: summary },
        provenance: { source_ref: "explicit:user-memory-candidate", observed_at: createdAt },
        sensitivity: "normal",
      }),
    },
    token,
  );
  return payload.candidate;
}

export async function confirmAdvisorMemoryCandidate(token: string, candidateUid: string): Promise<AdvisorMemoryFact> {
  const payload = await apiFetch<{ fact: AdvisorMemoryFact }>(
    `${ROOT}/memory/candidates/${encodeURIComponent(candidateUid)}/confirm`,
    { method: "POST", body: jsonBody({}) },
    token,
  );
  return payload.fact;
}

export async function rejectAdvisorMemoryCandidate(token: string, candidateUid: string): Promise<void> {
  await apiFetch(
    `${ROOT}/memory/candidates/${encodeURIComponent(candidateUid)}/reject`,
    { method: "POST", body: jsonBody({}) },
    token,
  );
}

export async function updateAdvisorMemoryFact(
  token: string,
  factUid: string,
  status: "active" | "paused",
): Promise<AdvisorMemoryFact> {
  const payload = await apiFetch<{ fact: AdvisorMemoryFact }>(
    `${ROOT}/memory/facts/${encodeURIComponent(factUid)}`,
    { method: "PATCH", body: jsonBody({ status }) },
    token,
  );
  return payload.fact;
}
