import React from "react";
import { listKolSearchHistory, type VkpiKolSearchHistoryItem } from "../../../../domains/kol";
import {
  archiveAllKolSearchHistory,
  archiveKolSearchHistorySession,
  restoreKolSearchHistorySession,
} from "../../../../services/vkpi/kolPool-api";
import { HistoryStrip, PENDING_SEARCH_SESSION_KEY, historySessionId } from "./SmartKolInputPanel.Sections";

export function KolSearchHistoryPanel({
  apiToken = "",
  onOpenBoard,
}: {
  apiToken?: string;
  onOpenBoard?: () => void;
}) {
  const [items, setItems] = React.useState<VkpiKolSearchHistoryItem[]>([]);
  const [archivedItems, setArchivedItems] = React.useState<VkpiKolSearchHistoryItem[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [actionBusy, setActionBusy] = React.useState("");
  const [notice, setNotice] = React.useState("");

  const refresh = React.useCallback(async () => {
    if (!apiToken) {
      setItems([]);
      setArchivedItems([]);
      return;
    }
    setLoading(true);
    setNotice("");
    try {
      const [active, archived] = await Promise.allSettled([
        listKolSearchHistory(apiToken, { limit: 50, itemLimit: 5, archived: false }),
        listKolSearchHistory(apiToken, { limit: 50, itemLimit: 0, archived: true }),
      ]);
      if (active.status === "fulfilled") setItems(Array.isArray(active.value.items) ? active.value.items : []);
      if (archived.status === "fulfilled") setArchivedItems(Array.isArray(archived.value.items) ? archived.value.items : []);
      if (active.status === "rejected" || archived.status === "rejected") {
        setNotice("部分历史记录暂时无法同步，可稍后刷新");
      }
    } finally {
      setLoading(false);
    }
  }, [apiToken]);

  React.useEffect(() => {
    void refresh();
  }, [refresh]);

  const open = React.useCallback((session: VkpiKolSearchHistoryItem) => {
    const sessionId = historySessionId(session);
    if (!sessionId || typeof window === "undefined") return;
    window.localStorage.setItem(PENDING_SEARCH_SESSION_KEY, String(sessionId));
    onOpenBoard?.();
  }, [onOpenBoard]);

  const archive = React.useCallback(async (session: VkpiKolSearchHistoryItem) => {
    const sessionId = historySessionId(session);
    if (!apiToken || !sessionId) return;
    setActionBusy(`active-${sessionId}`);
    setNotice("");
    try {
      await archiveKolSearchHistorySession(apiToken, sessionId);
      setNotice("已移除，可在“已移除”中恢复");
      await refresh();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "移除失败，请稍后重试");
    } finally {
      setActionBusy("");
    }
  }, [apiToken, refresh]);

  const restore = React.useCallback(async (session: VkpiKolSearchHistoryItem) => {
    const sessionId = historySessionId(session);
    if (!apiToken || !sessionId) return;
    setActionBusy(`archived-${sessionId}`);
    setNotice("");
    try {
      await restoreKolSearchHistorySession(apiToken, sessionId);
      setNotice("历史记录已恢复");
      await refresh();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "恢复失败，请稍后重试");
    } finally {
      setActionBusy("");
    }
  }, [apiToken, refresh]);

  const archiveAll = React.useCallback(async () => {
    if (!apiToken) return;
    setActionBusy("all");
    setNotice("");
    try {
      const response = await archiveAllKolSearchHistory(apiToken);
      const archivedCount = Math.max(0, Number(response.archived_count) || 0);
      const skippedCount = Math.max(0, Number(response.skipped_active_count) || 0);
      setNotice(`已移除 ${archivedCount} 条已完成记录${skippedCount ? `；${skippedCount} 条进行中任务已保留` : ""}`);
      await refresh();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "清理失败，请稍后重试");
    } finally {
      setActionBusy("");
    }
  }, [apiToken, refresh]);

  if (!apiToken) {
    return <div className="rounded-lg border border-dashed border-line p-4 text-center text-[11px] text-muted">登录后显示当前账号的搜索历史。</div>;
  }

  return (
    <HistoryStrip
      items={items}
      archivedItems={archivedItems}
      loading={loading}
      actionBusy={actionBusy}
      notice={notice}
      onOpen={open}
      onArchive={(session) => void archive(session)}
      onRestore={(session) => void restore(session)}
      onArchiveAll={() => void archiveAll()}
    />
  );
}
