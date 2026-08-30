import { useEffect } from "react";
import { getKolSearchSession, type VkpiKolSearchHistoryItem } from "../../../../domains/kol";
import { searchSessionProgress } from "./SmartKolInputPanel.Sections";
import { sessionPollStateAfterTimeout } from "./SmartKolInputPanel.searchState";

type SmartKolSessionPollingOptions = {
  apiToken: string;
  pollingSearchSessionId: number | null;
  applyPolledSession: (session: VkpiKolSearchHistoryItem) => void;
  refreshHistory: () => Promise<void>;
  setPollingSearchSessionId: (sessionId: number | null) => void;
  setPollPausedSessionId: (sessionId: number | null) => void;
  setSessionPollNotice: (notice: string) => void;
};

export function useSmartKolSessionPolling({
  apiToken,
  pollingSearchSessionId,
  applyPolledSession,
  refreshHistory,
  setPollingSearchSessionId,
  setPollPausedSessionId,
  setSessionPollNotice,
}: SmartKolSessionPollingOptions) {
  useEffect(() => {
    if (!apiToken || !pollingSearchSessionId || typeof window === "undefined") return undefined;
    let cancelled = false;
    let inFlight = false;
    let terminalSince: number | null = null;
    const startedAt = Date.now();
    const maxPollMs = 12 * 60 * 1000;
    const poll = async () => {
      if (cancelled || inFlight) return;
      inFlight = true;
      try {
        const session = await getKolSearchSession(apiToken, pollingSearchSessionId);
        if (cancelled) return;
        applyPolledSession(session);
        const progress = searchSessionProgress(session);
        const stageText = (label: string, stage: typeof progress.video) => {
          const suffix = [
            stage.active > 0 ? `进行 ${stage.active}` : "",
            stage.failed > 0 ? `失败 ${stage.failed}` : "",
            stage.notRequested > 0 ? `未请求 ${stage.notRequested}` : "",
          ].filter(Boolean).join("/");
          return `${label} ${stage.ready}/${progress.target}${suffix ? `（${suffix}）` : ""}`;
        };
        const progressNote = progress.target > 0
          ? progress.downstreamTracked
            ? `阶段：${progress.phaseLabel} · ①基础 ${progress.basicVisible}/${progress.target} · ②档案 ${progress.profileReady}/${progress.target} · ③${stageText("视频", progress.video)} · ④${stageText("评论", progress.comments)} / ${stageText("受众", progress.audience)}`
            : `阶段：${progress.phaseLabel} · 基础结果 ${progress.basicVisible}/${progress.target} · 档案补全 ${progress.profileReady}/${progress.target} · 完整分析 ${progress.deepReady}/${progress.target}${progress.deepPartial > 0 ? ` · 部分 ${progress.deepPartial}` : ""}`
          : `阶段：${progress.phaseLabel}`;
        setSessionPollNotice(progressNote);
        // Discovery arriving first does not mean the batch is complete. Keep receiving
        // trailing evidence until required tasks are terminal or the bounded poll pauses.
        const timedOut = Date.now() - startedAt > maxPollMs;
        if (progress.requiredTasksComplete) {
          if (terminalSince == null) terminalSince = Date.now();
          const graceUsedUp = Date.now() - terminalSince >= 30000;
          if (graceUsedUp || timedOut) {
            setPollingSearchSessionId(null);
            setPollPausedSessionId(null);
            setSessionPollNotice(`${progressNote} · 结果已更新`);
            void refreshHistory();
            return;
          }
        } else {
          terminalSince = null;
          if (timedOut) {
            const timeoutState = sessionPollStateAfterTimeout(pollingSearchSessionId, false);
            setPollingSearchSessionId(timeoutState.pollingSessionId);
            setPollPausedSessionId(timeoutState.pausedSessionId);
            setSessionPollNotice(`${progressNote} · 后台任务未确认结束，已暂停高频同步；“继续同步”只刷新状态，不会重复发起查找`);
            void refreshHistory();
          }
        }
      } catch (err) {
        if (cancelled) return;
        setSessionPollNotice(err instanceof Error ? err.message : "同步失败，稍后会自动重试");
      } finally {
        inFlight = false;
      }
    };
    void poll();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "hidden") return;
      void poll();
    }, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [
    apiToken,
    pollingSearchSessionId,
    applyPolledSession,
    refreshHistory,
    setPollingSearchSessionId,
    setPollPausedSessionId,
    setSessionPollNotice,
  ]);
}
