import React from "react";
import { ChevronLeft, ChevronRight, Clock3, Database, UserRound } from "lucide-react";
import {
  listKolSearchHistory,
  type VkpiKolSearchHistoryItem,
  type VkpiKolSearchHistoryResponse,
} from "../../../../services/vkpi/kolPool-api";
import { formatLocal } from "../../lib/timeLocal";
import { asArray, asRow, str, type Row } from "./KolProfileBoardPage.charts";

export type ProfileHistoryKind = "search" | "deep" | "cooperation";

export interface ProfileHistoryEvent {
  id: string;
  kind: ProfileHistoryKind;
  title: string;
  occurredAt: string;
  status: string;
  source: string;
  operator: string;
  summary: string;
  provenance: string[];
}

interface SearchHistoryBundle {
  active: VkpiKolSearchHistoryItem[];
  archived: VkpiKolSearchHistoryItem[];
  errors: string[];
}

interface HistoryRemoteState {
  status: "idle" | "loading" | "ready";
  data: SearchHistoryBundle;
}

interface SourceRemote {
  status: "idle" | "loading" | "ready" | "error";
  data: Row | null;
  error: string;
}

const EMPTY_SEARCH_BUNDLE: SearchHistoryBundle = { active: [], archived: [], errors: [] };
const PAGE_SIZE = 6;

function toPositiveId(value: unknown): number {
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : 0;
}

function containsExactKolId(value: unknown, kolId: number, seen = new Set<unknown>(), depth = 0): boolean {
  if (depth > 7 || value === null || value === undefined) return false;
  if (typeof value !== "object") return false;
  if (seen.has(value)) return false;
  seen.add(value);
  if (Array.isArray(value)) return value.some((item) => containsExactKolId(item, kolId, seen, depth + 1));
  for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
    if ((key === "kol_pool_id" || key === "matched_kol_pool_id") && toPositiveId(child) === kolId) return true;
    if (containsExactKolId(child, kolId, seen, depth + 1)) return true;
  }
  return false;
}

/** Only exact numeric references count; free-text handle/query similarity is never treated as provenance. */
export function searchSessionReferencesKol(session: VkpiKolSearchHistoryItem, kolId: number): boolean {
  const approved = Array.isArray(session.approved_kol_ids) ? session.approved_kol_ids : [];
  if (approved.some((value) => toPositiveId(value) === kolId)) return true;
  return containsExactKolId(
    {
      input_payload: session.input_payload,
      result_summary: session.result_summary,
      summary: session.summary,
      items_preview: session.items_preview,
      active_items: session.active_items,
      items: session.items,
    },
    kolId,
  );
}

function sourceLabel(value: unknown, fallback: string): string {
  const label = str(value).trim();
  return label || fallback;
}

function operatorLabel(value: unknown): string {
  const id = toPositiveId(value);
  return id > 0 ? `员工 #${id}` : "未知（源端点未返回）";
}

function searchEvents(session: VkpiKolSearchHistoryItem, archived: boolean): ProfileHistoryEvent[] {
  const sessionId = toPositiveId(session.id);
  const query = str(session.query_text).trim() || "未命名搜索";
  const source = sourceLabel(session.source, "KOL 搜索会话");
  const events: ProfileHistoryEvent[] = [
    {
      id: `search-${sessionId || query}-${session.created_at || "unknown"}`,
      kind: "search",
      title: `搜索：${query}`,
      occurredAt: str(session.created_at),
      status: sourceLabel(session.status, "状态未知"),
      source,
      operator: operatorLabel(session.created_by),
      summary: `会话 #${sessionId || "未知"} · ${sourceLabel(session.query_type, "类型未知")} · ${Number(session.item_count || 0)} 个候选`,
      provenance: [
        `kol-search-history 会话 #${sessionId || "未知"}`,
        "仅因返回体中出现精确 kol_pool_id 才归入本档案；不按用户名文本猜测",
      ],
    },
  ];
  const archiveReason = str(session.archive_reason).trim();
  if (archived && session.archived_at) {
    events.push({
      id: `archive-${sessionId || query}-${session.archived_at}`,
      kind: "search",
      title: "搜索会话已移除",
      occurredAt: str(session.archived_at),
      status: "已移除，可在 KOL Pool 恢复",
      source: "KOL 搜索历史归档状态",
      operator: operatorLabel(session.archived_by),
      summary: archiveReason ? `原因：${archiveReason}` : "归档原因未记录",
      provenance: [`kol-search-history archived_at · 会话 #${sessionId || "未知"}`],
    });
  } else if (!session.archived_at && archiveReason) {
    events.push({
      id: `restore-${sessionId || query}-${session.updated_at || "unknown"}`,
      kind: "search",
      title: "搜索会话已恢复到历史",
      occurredAt: str(session.updated_at),
      status: "当前可用",
      source: "KOL 搜索历史当前状态",
      operator: "未知（恢复接口未返回 restored_by）",
      summary: `曾移除原因：${archiveReason}`,
      provenance: [
        `会话 #${sessionId || "未知"} 当前 archived_at 为空且保留 archive_reason`,
        "恢复时间取会话 updated_at；后端尚无独立 restored_at，不能视为精确恢复时间",
      ],
    });
  }
  return events;
}

export function buildProfileHistoryEvents({
  kolId,
  activeSessions,
  archivedSessions,
  deepData,
  cooperationData,
}: {
  kolId: number;
  activeSessions: VkpiKolSearchHistoryItem[];
  archivedSessions: VkpiKolSearchHistoryItem[];
  deepData: Row | null;
  cooperationData: Row | null;
}): ProfileHistoryEvent[] {
  const events: ProfileHistoryEvent[] = [];
  for (const session of activeSessions) {
    if (searchSessionReferencesKol(session, kolId)) events.push(...searchEvents(session, false));
  }
  for (const session of archivedSessions) {
    if (searchSessionReferencesKol(session, kolId)) events.push(...searchEvents(session, true));
  }

  const deepItems = asArray(deepData?.items);
  const seenDeep = new Set<string>();
  for (const value of deepItems) {
    const item = asRow(value);
    if (!item) continue;
    const itemId = str(item.id) || `${str(item.created_at)}-${str(item.source_evidence_id)}`;
    if (!itemId || seenDeep.has(itemId)) continue;
    seenDeep.add(itemId);
    const provider = sourceLabel(item.provider, "提供方未记录");
    const method = sourceLabel(item.method, sourceLabel(item.analysis_kind, "深析方法未记录"));
    const sourceUrl = str(item.source_url).trim();
    const evidenceId = toPositiveId(item.source_evidence_id);
    const cacheId = toPositiveId(item.source_cache_id);
    events.push({
      id: `deep-${itemId}`,
      kind: "deep",
      title: `深析结果 · ${sourceLabel(item.analysis_kind, "类型未记录")}`,
      occurredAt: str(item.created_at),
      status: sourceLabel(item.status, "状态未知"),
      source: `${provider} · ${method}`,
      operator: "未知（深析结果未记录人工操作者）",
      summary: evidenceId > 0 ? `来源证据 #${evidenceId}` : "来源证据 ID 未记录",
      provenance: [
        `llm-deep-analysis 结果 #${str(item.id) || "未知"}`,
        ...(evidenceId > 0 ? [`source_evidence_id=${evidenceId}`] : []),
        ...(cacheId > 0 ? [`source_cache_id=${cacheId}`] : []),
        ...(sourceUrl ? [`source_url=${sourceUrl}`] : []),
      ],
    });
  }

  for (const value of asArray(cooperationData?.events)) {
    const item = asRow(value);
    if (!item) continue;
    const eventId = str(item.id) || `${str(item.created_at)}-${str(item.action_type)}`;
    events.push({
      id: `cooperation-${eventId}`,
      kind: "cooperation",
      title: `合作动作 · ${sourceLabel(item.action_label, sourceLabel(item.action_type, "动作未记录"))}`,
      occurredAt: str(item.created_at),
      status: sourceLabel(item.status_label, sourceLabel(item.status_after, "状态未记录")),
      source: "合作动作时间线",
      operator: operatorLabel(item.actor_staff_id),
      summary: str(item.note).trim() || "无备注",
      provenance: [`cooperation event #${str(item.id) || "未知"} · kol_pool_id=${kolId}`],
    });
  }

  return events.sort((left, right) => {
    const timeOrder = right.occurredAt.localeCompare(left.occurredAt);
    return timeOrder || right.id.localeCompare(left.id);
  });
}

function responseItems(response: VkpiKolSearchHistoryResponse | undefined): VkpiKolSearchHistoryItem[] {
  return Array.isArray(response?.items) ? response.items : [];
}

function useProfileSearchHistory(apiToken: string, kolId: number, reloadTick: number): HistoryRemoteState {
  const [state, setState] = React.useState<HistoryRemoteState>({ status: "idle", data: EMPTY_SEARCH_BUNDLE });
  React.useEffect(() => {
    if (!apiToken || kolId <= 0) {
      setState({ status: "idle", data: EMPTY_SEARCH_BUNDLE });
      return;
    }
    let alive = true;
    setState({ status: "loading", data: EMPTY_SEARCH_BUNDLE });
    void Promise.allSettled([
      listKolSearchHistory(apiToken, { limit: 50, itemLimit: 10, archived: false }),
      listKolSearchHistory(apiToken, { limit: 50, itemLimit: 10, archived: true }),
    ]).then(([activeResult, archivedResult]) => {
      if (!alive) return;
      const errors: string[] = [];
      if (activeResult.status === "rejected") errors.push("当前历史读取失败");
      if (archivedResult.status === "rejected") errors.push("已移除历史读取失败");
      setState({
        status: "ready",
        data: {
          active: activeResult.status === "fulfilled" ? responseItems(activeResult.value) : [],
          archived: archivedResult.status === "fulfilled" ? responseItems(archivedResult.value) : [],
          errors,
        },
      });
    });
    return () => {
      alive = false;
    };
  }, [apiToken, kolId, reloadTick]);
  return state;
}

const KIND_LABELS: Record<ProfileHistoryKind | "all", string> = {
  all: "全部",
  search: "搜索 / 归档",
  deep: "深析",
  cooperation: "合作",
};

const KIND_TONES: Record<ProfileHistoryKind, string> = {
  search: "border-info bg-info-soft text-info",
  deep: "border-accent bg-accent-soft text-accent",
  cooperation: "border-good bg-good-soft text-good",
};

export function KolProfileHistoryModule({
  apiToken,
  kolId,
  reloadTick,
  deep,
  cooperation,
}: {
  apiToken: string;
  kolId: number;
  reloadTick: number;
  deep: SourceRemote;
  cooperation: SourceRemote;
}) {
  const search = useProfileSearchHistory(apiToken, kolId, reloadTick);
  const [filter, setFilter] = React.useState<ProfileHistoryKind | "all">("all");
  const [page, setPage] = React.useState(1);
  const events = React.useMemo(
    () => buildProfileHistoryEvents({
      kolId,
      activeSessions: search.data.active,
      archivedSessions: search.data.archived,
      deepData: deep.data,
      cooperationData: cooperation.data,
    }),
    [cooperation.data, deep.data, kolId, search.data.active, search.data.archived],
  );
  const filtered = React.useMemo(() => (filter === "all" ? events : events.filter((event) => event.kind === filter)), [events, filter]);
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount);
  const visible = filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);
  React.useEffect(() => setPage(1), [filter, kolId]);
  React.useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

  const errors = [
    ...search.data.errors,
    ...(deep.status === "error" ? ["深析历史读取失败"] : []),
    ...(cooperation.status === "error" ? ["合作历史读取失败"] : []),
  ];
  const loading = search.status === "loading" || deep.status === "loading" || cooperation.status === "loading";
  const counts: Record<ProfileHistoryKind, number> = {
    search: events.filter((event) => event.kind === "search").length,
    deep: events.filter((event) => event.kind === "deep").length,
    cooperation: events.filter((event) => event.kind === "cooperation").length,
  };

  return (
    <div className="space-y-3" data-testid="kol-profile-unified-history">
      <div className="flex flex-wrap items-center gap-1.5">
        {(Object.keys(KIND_LABELS) as Array<ProfileHistoryKind | "all">).map((kind) => (
          <button
            key={kind}
            type="button"
            onClick={() => setFilter(kind)}
            aria-pressed={filter === kind}
            className={`rounded-md border px-2 py-1 text-[10px] transition-colors ${
              filter === kind ? "border-accent bg-accent-soft text-accent" : "border-line bg-card text-muted hover:text-ink"
            }`}
          >
            {KIND_LABELS[kind]} {kind === "all" ? events.length : counts[kind]}
          </button>
        ))}
        <span className="ml-auto text-[9.5px] text-muted">只读 · 当前账号搜索历史作用域</span>
      </div>

      {errors.length > 0 ? (
        <div role="status" className="rounded-lg border border-warn bg-card px-3 py-2 text-[10.5px] text-warn">
          部分来源读取失败：{errors.join("；")}。其余已返回事实仍可查看。
        </div>
      ) : null}

      {visible.length === 0 ? (
        <div className="flex min-h-[130px] flex-col items-center justify-center rounded-xl border border-dashed border-line px-4 text-center">
          {loading ? (
            <>
              <Clock3 size={18} className="mb-2 text-muted" />
              <span className="text-[11px] text-ink-2">统一历史读取中…</span>
            </>
          ) : (
            <>
              <Database size={18} className="mb-2 text-muted" />
              <span className="text-[11px] text-ink-2">该范围内没有可核验历史</span>
              <span className="mt-1 text-[9.5px] text-muted">不以用户名相似、静态文案或模拟记录补空。</span>
            </>
          )}
        </div>
      ) : (
        <ol className="space-y-2" aria-label="KOL 统一历史">
          {visible.map((event) => (
            <li key={event.id} className="rounded-xl border border-line bg-card px-3 py-2.5">
              <div className="flex flex-wrap items-start gap-2">
                <span className={`rounded-md border px-1.5 py-0.5 text-[9px] font-semibold ${KIND_TONES[event.kind]}`}>
                  {KIND_LABELS[event.kind]}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <b className="text-[11.5px] text-ink">{event.title}</b>
                    <span className="text-[9.5px] text-muted" title="UTC 存储，按浏览器时区显示">
                      {formatLocal(event.occurredAt || null)}
                    </span>
                    <span className="rounded border border-line px-1.5 py-px text-[9px] text-ink-2">{event.status}</span>
                  </div>
                  <p className="mt-1 text-[10.5px] leading-relaxed text-ink-2">{event.summary}</p>
                  <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[9.5px] text-muted">
                    <span className="inline-flex items-center gap-1"><Database size={10} />来源：{event.source}</span>
                    <span className="inline-flex items-center gap-1"><UserRound size={10} />操作者：{event.operator}</span>
                  </div>
                  <details className="mt-1.5 text-[9.5px] text-muted">
                    <summary className="cursor-pointer select-none text-accent">查看 provenance</summary>
                    <ul className="mt-1 space-y-0.5 break-all border-l border-line pl-2">
                      {event.provenance.map((line, index) => <li key={`${event.id}-prov-${index}`}>{line}</li>)}
                    </ul>
                  </details>
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}

      <div className="flex flex-wrap items-center gap-2 border-t border-line pt-2 text-[9.5px] text-muted">
        <span>显示 {filtered.length === 0 ? 0 : (safePage - 1) * PAGE_SIZE + 1}–{Math.min(safePage * PAGE_SIZE, filtered.length)} / {filtered.length}</span>
        <span>· 搜索覆盖上限：有效 50 + 已移除 50，每会话最多核验 10 个候选</span>
        <div className="ml-auto flex items-center gap-1">
          <button
            type="button"
            aria-label="上一页历史"
            disabled={safePage <= 1}
            onClick={() => setPage((value) => Math.max(1, value - 1))}
            className="rounded border border-line p-1 text-muted hover:text-ink disabled:cursor-default disabled:text-line-strong"
          >
            <ChevronLeft size={12} />
          </button>
          <span>{safePage} / {pageCount}</span>
          <button
            type="button"
            aria-label="下一页历史"
            disabled={safePage >= pageCount}
            onClick={() => setPage((value) => Math.min(pageCount, value + 1))}
            className="rounded border border-line p-1 text-muted hover:text-ink disabled:cursor-default disabled:text-line-strong"
          >
            <ChevronRight size={12} />
          </button>
        </div>
      </div>
    </div>
  );
}
