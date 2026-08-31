import { useEffect } from "react";
import { getKolSearchSession, type VkpiKolSearchHistoryItem } from "../../../../domains/kol";
import { searchSessionProgress } from "./SmartKolInputPanel.Sections";
import { sessionPollStateAfterTimeout } from "./SmartKolInputPanel.searchState";
import { stableFingerprint } from "./SmartKolInputPanel.renderGuards";
import {
  publishSearchProgressNotice,
  resetSearchProgress,
} from "./SmartKolInputPanel.progressStore";
import type { SearchSessionProgress } from "./SmartKolInputPanel.derivers";

type SmartKolSessionPollingOptions = {
  apiToken: string;
  pollingSearchSessionId: number | null;
  applyPolledSession: (session: VkpiKolSearchHistoryItem) => void;
  refreshHistory: () => Promise<void>;
  setPollingSearchSessionId: (sessionId: number | null) => void;
  setPollPausedSessionId: (sessionId: number | null) => void;
  setSessionPollNotice: (notice: string) => void;
};

/**
 * 轮询节奏(M2「治卡」③):不再 2.5 秒定频空转。
 * 后端没有新数据时逐级退避,最后一项是稳态上限;一旦真有新数据(或标签页重新可见)立刻退回队首。
 * 12 分钟的总时限不变。
 */
export const SESSION_POLL_BACKOFF_MS = [2500, 2500, 5000, 5000, 10000] as const;

export function sessionPollDelayMs(idleSteps: number): number {
  const index = Math.min(Math.max(idleSteps, 0), SESSION_POLL_BACKOFF_MS.length - 1);
  return SESSION_POLL_BACKOFF_MS[index];
}

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
    const sessionId = pollingSearchSessionId;
    let cancelled = false;
    let stopped = false;
    let inFlight = false;
    let terminalSince: number | null = null;
    let timer: number | null = null;
    // 已应用过的快照指纹 + 它的派生值。后端这一拍一个字节都没变时,既不重新派生也不 setState。
    let appliedFingerprint: string | null = null;
    let appliedProgress: SearchSessionProgress | null = null;
    let idleSteps = 0;
    const startedAt = Date.now();
    const maxPollMs = 12 * 60 * 1000;

    // 换会话就复位实时快照,展示层先回落到容器 props 上的文案,不会串场到上一次搜索。
    resetSearchProgress(sessionId);

    const clearTimer = () => {
      if (timer != null) {
        window.clearTimeout(timer);
        timer = null;
      }
    };
    const schedule = () => {
      clearTimer();
      if (cancelled || stopped) return;
      timer = window.setTimeout(tick, sessionPollDelayMs(idleSteps));
    };
    const stageText = (label: string, stage: SearchSessionProgress["video"], target: number) => {
      const suffix = [
        stage.active > 0 ? `进行 ${stage.active}` : "",
        stage.failed > 0 ? `失败 ${stage.failed}` : "",
        stage.notRequested > 0 ? `未请求 ${stage.notRequested}` : "",
      ].filter(Boolean).join("/");
      return `${label} ${stage.ready}/${target}${suffix ? `（${suffix}）` : ""}`;
    };
    const progressNoteOf = (progress: SearchSessionProgress) => (
      progress.target > 0
        ? progress.downstreamTracked
          ? `阶段：${progress.phaseLabel} · ①基础 ${progress.basicVisible}/${progress.target} · ②档案 ${progress.profileReady}/${progress.target} · ③${stageText("视频", progress.video, progress.target)} · ④${stageText("评论", progress.comments, progress.target)} / ${stageText("受众", progress.audience, progress.target)}`
          : `阶段：${progress.phaseLabel} · 基础结果 ${progress.basicVisible}/${progress.target} · 档案补全 ${progress.profileReady}/${progress.target} · 完整分析 ${progress.deepReady}/${progress.target}${progress.deepPartial > 0 ? ` · 部分 ${progress.deepPartial}` : ""}`
        : `阶段：${progress.phaseLabel}`
    );
    const stopPolling = () => {
      stopped = true;
      clearTimer();
      resetSearchProgress(null);
    };

    const poll = async () => {
      if (cancelled || stopped || inFlight) return;
      inFlight = true;
      try {
        const session = await getKolSearchSession(apiToken, sessionId);
        if (cancelled || stopped) return;
        // 【M2 核心】后端这一拍与上一拍逐字节相同时,不调 applyPolledSession。
        // 原来无条件调用会让 mergeKol*Snapshots 每拍 mint 一个全新会话对象 → 容器 setState →
        // 68 props 的结果巨树整页重画。没有新数据就没有理由重画。
        const fingerprint = stableFingerprint(session);
        const changed = fingerprint == null || fingerprint !== appliedFingerprint;
        let progress: SearchSessionProgress;
        if (changed) {
          appliedFingerprint = fingerprint;
          idleSteps = 0;
          applyPolledSession(session);
          progress = searchSessionProgress(session);
          appliedProgress = progress;
        } else {
          idleSteps += 1;
          progress = appliedProgress ?? searchSessionProgress(session);
          appliedProgress = progress;
        }
        const progressNote = progressNoteOf(progress);
        // 进度文案走外部 store:只有订阅它的那一行重渲,不再 setState 打在容器上。
        // 口径不变——改造前这行文案本来就是按这一拍的原始快照算的。
        publishSearchProgressNotice(sessionId, progressNote);
        // Discovery arriving first does not mean the batch is complete. Keep receiving
        // trailing evidence until required tasks are terminal or the bounded poll pauses.
        const timedOut = Date.now() - startedAt > maxPollMs;
        if (progress.requiredTasksComplete) {
          if (terminalSince == null) terminalSince = Date.now();
          const graceUsedUp = Date.now() - terminalSince >= 30000;
          if (graceUsedUp || timedOut) {
            stopPolling();
            setPollingSearchSessionId(null);
            setPollPausedSessionId(null);
            setSessionPollNotice(`${progressNote} · 结果已更新`);
            void refreshHistory();
            return;
          }
        } else {
          terminalSince = null;
          if (timedOut) {
            const timeoutState = sessionPollStateAfterTimeout(sessionId, false);
            stopPolling();
            setPollingSearchSessionId(timeoutState.pollingSessionId);
            setPollPausedSessionId(timeoutState.pausedSessionId);
            setSessionPollNotice(`${progressNote} · 后台任务未确认结束，已暂停高频同步；“继续同步”只刷新状态，不会重复发起查找`);
            void refreshHistory();
          }
        }
      } catch (err) {
        if (cancelled || stopped) return;
        // 失败提示同样走 store:一次网络抖动不该重画整棵结果树。
        publishSearchProgressNotice(sessionId, err instanceof Error ? err.message : "同步失败，稍后会自动重试");
      } finally {
        inFlight = false;
      }
    };

    const tick = () => {
      timer = null;
      if (cancelled || stopped) return;
      // 页面不可见:不发请求也不空转,等 visibilitychange 唤醒(原来只是跳过一拍,定时器照转)。
      if (document.visibilityState === "hidden") return;
      void poll().then(schedule);
    };
    const onVisibilityChange = () => {
      if (cancelled || stopped) return;
      if (document.visibilityState === "hidden") {
        clearTimer();
        return;
      }
      // 回到前台:退避归零,立刻补一拍。
      idleSteps = 0;
      if (timer == null && !inFlight) tick();
    };

    document.addEventListener("visibilitychange", onVisibilityChange);
    tick();
    return () => {
      cancelled = true;
      clearTimer();
      document.removeEventListener("visibilitychange", onVisibilityChange);
      resetSearchProgress(null);
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
