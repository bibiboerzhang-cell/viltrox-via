import { useCallback, useState } from "react";
import { listKolSearchHistory, type VkpiKolSearchHistoryItem } from "../../../../domains/kol";
import {
  archiveAllKolSearchHistory,
  archiveKolSearchHistorySession,
  restoreKolSearchHistorySession,
} from "../../../../services/vkpi/kolPool-api";
import { historySessionId } from "./SmartKolInputPanel.Sections";

export function useSmartKolSearchHistory(apiToken: string) {
  const [historyItems, setHistoryItems] = useState<VkpiKolSearchHistoryItem[]>([]);
  const [archivedHistoryItems, setArchivedHistoryItems] = useState<VkpiKolSearchHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyActionBusy, setHistoryActionBusy] = useState("");
  const [historyNotice, setHistoryNotice] = useState("");

  const refreshHistory = useCallback(async () => {
    if (!apiToken) {
      setHistoryItems([]);
      setArchivedHistoryItems([]);
      return;
    }
    setHistoryLoading(true);
    try {
      const [active, archived] = await Promise.allSettled([
        listKolSearchHistory(apiToken, { limit: 50, itemLimit: 5, archived: false }),
        listKolSearchHistory(apiToken, { limit: 50, itemLimit: 0, archived: true }),
      ]);
      if (active.status === "fulfilled") {
        setHistoryItems(Array.isArray(active.value.items) ? active.value.items : []);
      }
      if (archived.status === "fulfilled") {
        setArchivedHistoryItems(Array.isArray(archived.value.items) ? archived.value.items : []);
      }
      if (active.status === "rejected" && archived.status === "rejected") {
        setHistoryNotice("历史记录暂时无法同步，主搜索功能不受影响");
      }
    } catch {
      setHistoryNotice("历史记录暂时无法同步，主搜索功能不受影响");
    } finally {
      setHistoryLoading(false);
    }
  }, [apiToken]);

  const archiveHistoryEntry = useCallback(async (session: VkpiKolSearchHistoryItem) => {
    const sessionId = historySessionId(session);
    if (!apiToken || !sessionId) return;
    setHistoryActionBusy(`active-${sessionId}`);
    setHistoryNotice("");
    try {
      await archiveKolSearchHistorySession(apiToken, sessionId);
      setHistoryNotice("已从最近历史移除；搜索结果和任务数据仍保留，可在“已移除”中恢复");
      await refreshHistory();
    } catch (err) {
      setHistoryNotice(err instanceof Error ? err.message : "移除失败，请稍后重试");
    } finally {
      setHistoryActionBusy("");
    }
  }, [apiToken, refreshHistory]);

  const restoreHistoryEntry = useCallback(async (session: VkpiKolSearchHistoryItem) => {
    const sessionId = historySessionId(session);
    if (!apiToken || !sessionId) return;
    setHistoryActionBusy(`archived-${sessionId}`);
    setHistoryNotice("");
    try {
      await restoreKolSearchHistorySession(apiToken, sessionId);
      setHistoryNotice("历史记录已恢复");
      await refreshHistory();
    } catch (err) {
      setHistoryNotice(err instanceof Error ? err.message : "恢复失败，请稍后重试");
    } finally {
      setHistoryActionBusy("");
    }
  }, [apiToken, refreshHistory]);

  const archiveCompletedHistory = useCallback(async () => {
    if (!apiToken) return;
    setHistoryActionBusy("all");
    setHistoryNotice("");
    try {
      const response = await archiveAllKolSearchHistory(apiToken);
      const archivedCount = Math.max(0, Number(response.archived_count) || 0);
      const skippedCount = Math.max(0, Number(response.skipped_active_count) || 0);
      setHistoryNotice(`已移除 ${archivedCount} 条已完成记录${skippedCount ? `；${skippedCount} 条进行中任务已保留` : ""}`);
      await refreshHistory();
    } catch (err) {
      setHistoryNotice(err instanceof Error ? err.message : "清理失败，请稍后重试");
    } finally {
      setHistoryActionBusy("");
    }
  }, [apiToken, refreshHistory]);

  return {
    historyItems,
    archivedHistoryItems,
    historyLoading,
    setHistoryLoading,
    historyActionBusy,
    historyNotice,
    refreshHistory,
    archiveHistoryEntry,
    restoreHistoryEntry,
    archiveCompletedHistory,
  };
}
