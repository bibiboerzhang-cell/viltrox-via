import { useEffect, useMemo } from "react";
import { useTranslation } from "react-i18next";

import { buildEventStreamUrl } from "../../lib/api";
import { buildViaProgressCopy, isUploadErrorState, resolveSubmissionStatus } from "../../lib/submissionProgress";
import { useSSE } from "../../hooks/useSSE";
import { useViaStore } from "../../stores/useViaStore";

interface UploadStatusEvent {
  status?: string;
}

const INACTIVE_PROGRESS_VISIBLE_MS = 10 * 60 * 1000;
const ERROR_PROGRESS_VISIBLE_MS = 5 * 60 * 1000;

export function GlobalUploadProgressBridge() {
  const { t } = useTranslation();
  const { progressSnapshot, setProgressSnapshot, appendMessage } = useViaStore();
  const activeJobId = progressSnapshot?.isActive ? progressSnapshot.jobId || "" : "";
  const streamUrl = useMemo(() => (activeJobId ? buildEventStreamUrl(activeJobId) : null), [activeJobId]);

  const streamHandlers = useMemo(
    () => ({
      status_update: (payload: UploadStatusEvent) => {
        if (!activeJobId) {
          return;
        }
        const next = resolveSubmissionStatus(payload.status, t);
        setProgressSnapshot((current) => {
          if (!current || current.jobId !== activeJobId) {
            return current;
          }
          return {
            ...current,
            step: next.step,
            statusLine: next.statusLine,
            updatedAt: Date.now(),
            isActive: next.step < 5 && !isUploadErrorState(next.statusLine),
          };
        });
      },
      final_result: () => {
        if (!activeJobId) {
          return;
        }
        setProgressSnapshot((current) => {
          if (!current || current.jobId !== activeJobId) {
            return current;
          }
          return {
            ...current,
            step: 5,
            statusLine: t("creator.submit.status.finished"),
            updatedAt: Date.now(),
            isActive: false,
          };
        });
      },
      error_event: () => {
        if (!activeJobId) {
          return;
        }
        setProgressSnapshot((current) => {
          if (!current || current.jobId !== activeJobId) {
            return current;
          }
          return {
            ...current,
            statusLine: t("creator.submit.status.failed"),
            updatedAt: Date.now(),
            isActive: false,
          };
        });
      },
    }),
    [activeJobId, setProgressSnapshot, t],
  );

  useSSE<UploadStatusEvent>(streamUrl, streamHandlers);

  useEffect(() => {
    if (!progressSnapshot?.jobId || progressSnapshot.isActive) {
      return;
    }
    const suffix = isUploadErrorState(progressSnapshot.statusLine) ? "error" : "done";
    appendMessage({
      id: `via-upload-${progressSnapshot.jobId}-${suffix}`,
      role: "via",
      title: t("catographer.companion.conversation.viaTitle"),
      text: buildViaProgressCopy(progressSnapshot, t),
    });
  }, [appendMessage, progressSnapshot, t]);

  useEffect(() => {
    if (!progressSnapshot?.jobId || progressSnapshot.isActive) {
      return;
    }
    const ageMs = Date.now() - progressSnapshot.updatedAt;
    const visibleMs = isUploadErrorState(progressSnapshot.statusLine)
      ? ERROR_PROGRESS_VISIBLE_MS
      : INACTIVE_PROGRESS_VISIBLE_MS;
    const timeoutMs = Math.max(0, visibleMs - ageMs);
    const timeoutId = globalThis.setTimeout(() => {
      setProgressSnapshot((current) => {
        if (!current || current.isActive || current.jobId !== progressSnapshot.jobId) {
          return current;
        }
        return null;
      });
    }, timeoutMs);
    return () => globalThis.clearTimeout(timeoutId);
  }, [progressSnapshot, setProgressSnapshot]);

  return null;
}
