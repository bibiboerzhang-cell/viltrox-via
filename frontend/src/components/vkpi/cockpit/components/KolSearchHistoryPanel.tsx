import React from "react";
import { listKolSearchHistory, type VkpiKolSearchHistoryItem } from "../../../../domains/kol";
import {
  archiveAllKolSearchHistory,
  archiveKolSearchHistorySession,
  restoreKolSearchHistorySession,
} from "../../../../services/vkpi/kolPool-api";
import { HistoryStrip, historySessionId, pendingSearchSessionStorageKey } from "./SmartKolInputPanel.Sections";

function historyAccountIdentity(accountId: string | number | null | undefined): {
  key: string;
  ready: boolean;
} {
  if (accountId === undefined) return { key: "legacy-unscoped", ready: true };
  const normalized = String(accountId ?? "").trim();
  return normalized
    ? { key: `account:${normalized}`, ready: true }
    : { key: "account:unresolved", ready: false };
}

export function KolSearchHistoryPanel({
  apiToken = "",
  accountId,
  onOpenBoard,
}: {
  apiToken?: string;
  accountId?: string | number | null;
  onOpenBoard?: () => void;
}) {
  const accountIdentity = historyAccountIdentity(accountId);
  const lifecycleRef = React.useRef({ key: accountIdentity.key, generation: 0 });
  if (lifecycleRef.current.key !== accountIdentity.key) {
    lifecycleRef.current = {
      key: accountIdentity.key,
      generation: lifecycleRef.current.generation + 1,
    };
  }
  const accountGeneration = lifecycleRef.current.generation;
  const isCurrentGeneration = React.useCallback(
    (generation: number) => lifecycleRef.current.generation === generation,
    [],
  );
  const [items, setItems] = React.useState<VkpiKolSearchHistoryItem[]>([]);
  const [archivedItems, setArchivedItems] = React.useState<VkpiKolSearchHistoryItem[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [actionBusy, setActionBusy] = React.useState("");
  const [notice, setNotice] = React.useState("");
  const [stateAccountKey, setStateAccountKey] = React.useState(accountIdentity.key);

  const refresh = React.useCallback(async () => {
    const requestGeneration = accountGeneration;
    if (!apiToken || !accountIdentity.ready || !isCurrentGeneration(requestGeneration)) return;
    setLoading(true);
    setNotice("");
    try {
      const [active, archived] = await Promise.allSettled([
        listKolSearchHistory(apiToken, { limit: 50, itemLimit: 5, archived: false }),
        listKolSearchHistory(apiToken, { limit: 50, itemLimit: 0, archived: true }),
      ]);
      if (!isCurrentGeneration(requestGeneration)) return;
      if (active.status === "fulfilled") setItems(Array.isArray(active.value.items) ? active.value.items : []);
      if (archived.status === "fulfilled") setArchivedItems(Array.isArray(archived.value.items) ? archived.value.items : []);
      if (active.status === "rejected" || archived.status === "rejected") {
        setNotice("部分历史记录暂时无法同步，可稍后刷新");
      }
    } finally {
      if (isCurrentGeneration(requestGeneration)) setLoading(false);
    }
  }, [accountGeneration, accountIdentity.ready, apiToken, isCurrentGeneration]);

  React.useEffect(() => {
    // Clear before starting the new account's request. The render below also
    // gates old rows by stateAccountKey, so account A cannot flash during the
    // commit where props already identify account B.
    setItems([]);
    setArchivedItems([]);
    setLoading(false);
    setActionBusy("");
    setNotice("");
    setStateAccountKey(accountIdentity.key);
    if (!accountIdentity.ready || !apiToken) return;
    void refresh();
  }, [accountIdentity.key, accountIdentity.ready, apiToken, refresh]);

  const open = React.useCallback((session: VkpiKolSearchHistoryItem) => {
    const sessionId = historySessionId(session);
    if (!sessionId || typeof window === "undefined") return;
    const storageKey = pendingSearchSessionStorageKey(accountId);
    if (!storageKey) return;
    window.localStorage.setItem(storageKey, String(sessionId));
    onOpenBoard?.();
  }, [accountId, onOpenBoard]);

  const archive = React.useCallback(async (session: VkpiKolSearchHistoryItem) => {
    const sessionId = historySessionId(session);
    const requestGeneration = accountGeneration;
    if (!apiToken || !accountIdentity.ready || !sessionId || !isCurrentGeneration(requestGeneration)) return;
    setActionBusy(`active-${sessionId}`);
    setNotice("");
    try {
      await archiveKolSearchHistorySession(apiToken, sessionId);
      if (!isCurrentGeneration(requestGeneration)) return;
      setNotice("已移除，可在“已移除”中恢复");
      await refresh();
    } catch (error) {
      if (isCurrentGeneration(requestGeneration)) {
        setNotice(error instanceof Error ? error.message : "移除失败，请稍后重试");
      }
    } finally {
      if (isCurrentGeneration(requestGeneration)) setActionBusy("");
    }
  }, [accountGeneration, accountIdentity.ready, apiToken, isCurrentGeneration, refresh]);

  const restore = React.useCallback(async (session: VkpiKolSearchHistoryItem) => {
    const sessionId = historySessionId(session);
    const requestGeneration = accountGeneration;
    if (!apiToken || !accountIdentity.ready || !sessionId || !isCurrentGeneration(requestGeneration)) return;
    setActionBusy(`archived-${sessionId}`);
    setNotice("");
    try {
      await restoreKolSearchHistorySession(apiToken, sessionId);
      if (!isCurrentGeneration(requestGeneration)) return;
      setNotice("历史记录已恢复");
      await refresh();
    } catch (error) {
      if (isCurrentGeneration(requestGeneration)) {
        setNotice(error instanceof Error ? error.message : "恢复失败，请稍后重试");
      }
    } finally {
      if (isCurrentGeneration(requestGeneration)) setActionBusy("");
    }
  }, [accountGeneration, accountIdentity.ready, apiToken, isCurrentGeneration, refresh]);

  const archiveAll = React.useCallback(async () => {
    const requestGeneration = accountGeneration;
    if (!apiToken || !accountIdentity.ready || !isCurrentGeneration(requestGeneration)) return;
    setActionBusy("all");
    setNotice("");
    try {
      const response = await archiveAllKolSearchHistory(apiToken);
      if (!isCurrentGeneration(requestGeneration)) return;
      const archivedCount = Math.max(0, Number(response.archived_count) || 0);
      const skippedCount = Math.max(0, Number(response.skipped_active_count) || 0);
      setNotice(`已移除 ${archivedCount} 条已完成记录${skippedCount ? `；${skippedCount} 条进行中任务已保留` : ""}`);
      await refresh();
    } catch (error) {
      if (isCurrentGeneration(requestGeneration)) {
        setNotice(error instanceof Error ? error.message : "清理失败，请稍后重试");
      }
    } finally {
      if (isCurrentGeneration(requestGeneration)) setActionBusy("");
    }
  }, [accountGeneration, accountIdentity.ready, apiToken, isCurrentGeneration, refresh]);

  if (!apiToken) {
    return <div className="rounded-lg border border-dashed border-line p-4 text-center text-[11px] text-muted">登录后显示当前账号的搜索历史。</div>;
  }

  return (
    <HistoryStrip
      items={stateAccountKey === accountIdentity.key ? items : []}
      archivedItems={stateAccountKey === accountIdentity.key ? archivedItems : []}
      loading={stateAccountKey === accountIdentity.key ? loading : false}
      actionBusy={stateAccountKey === accountIdentity.key ? actionBusy : ""}
      notice={stateAccountKey === accountIdentity.key ? notice : ""}
      onOpen={open}
      onArchive={(session) => void archive(session)}
      onRestore={(session) => void restore(session)}
      onArchiveAll={() => void archiveAll()}
    />
  );
}
