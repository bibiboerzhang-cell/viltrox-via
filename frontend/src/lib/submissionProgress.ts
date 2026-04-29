import type { TFunction } from "i18next";

import type { SurfaceKey } from "./contracts.generated";

export interface SubmissionProgressSnapshot {
  step: number;
  statusLine: string;
  jobId?: string;
  sourceLabel?: string;
  sourceKind?: "file" | "link";
}

export interface ViaProgressSnapshot extends SubmissionProgressSnapshot {
  surface: SurfaceKey;
  updatedAt: number;
  isActive: boolean;
}

function normalizeSubmissionStatus(status: string | undefined): string {
  const normalized = String(status || "").trim().toLowerCase();
  if (!normalized) {
    return "";
  }
  if (normalized.includes("prefilter") || normalized.includes("rejected") || normalized.includes("拒绝")) {
    return "prefilter_rejected";
  }
  if (normalized.includes("retry") || normalized.includes("重试")) {
    return "retrying";
  }
  if (normalized.includes("partial")) {
    return "partial_done";
  }
  if (normalized === "running" || normalized === "in_progress" || normalized.includes("worker") || normalized.includes("分析")) {
    return "processing";
  }
  return normalized;
}

export function resolveSubmissionStatus(status: string | undefined, t: TFunction): SubmissionProgressSnapshot {
  const statusMap: Record<string, SubmissionProgressSnapshot> = {
    queued: { step: 2, statusLine: t("creator.submit.status.queued") },
    processing: { step: 3, statusLine: t("creator.submit.status.processing") },
    retrying: { step: 3, statusLine: t("creator.submit.status.retrying") },
    partial_done: { step: 4, statusLine: t("creator.submit.status.partialDone") },
    done: { step: 5, statusLine: t("creator.submit.status.done") },
    failed: { step: 3, statusLine: t("creator.submit.status.failed") },
    prefilter_rejected: { step: 2, statusLine: t("creator.submit.status.prefilterRejected") },
  };
  const key = normalizeSubmissionStatus(status);
  return statusMap[key] ?? { step: 2, statusLine: status ?? t("creator.submit.status.unknown") };
}

export function getSubmissionProgressPercent(step: number): number {
  const progressMap: Record<number, number> = {
    1: 0,
    2: 25,
    3: 55,
    4: 78,
    5: 100,
  };
  return progressMap[step] ?? 0;
}

export function isUploadProgressRecent(snapshot: ViaProgressSnapshot | null, maxAgeMs = 1000 * 60 * 10): boolean {
  if (!snapshot?.updatedAt) {
    return false;
  }
  return Date.now() - snapshot.updatedAt <= maxAgeMs;
}

export function buildViaProgressCopy(snapshot: SubmissionProgressSnapshot, t: TFunction): string {
  if (isUploadErrorState(snapshot.statusLine)) {
    return t("creator.progress.via.failedNotice");
  }
  if (snapshot.step >= 5) {
    return t("creator.progress.via.finishedNotice");
  }
  const stage =
    snapshot.step >= 4
      ? t("creator.progress.via.reviewTitle")
      : snapshot.step >= 3
        ? t("creator.progress.via.analysisTitle")
        : snapshot.step >= 2
          ? t("creator.progress.via.queueTitle")
          : t("creator.progress.via.idleTitle");
  return t("creator.progress.via.liveStatus", { stage });
}

export function isUploadErrorState(status: string): boolean {
  const normalized = status.toLowerCase();
  return (
    normalized.includes("reject")
    || normalized.includes("fail")
    || normalized.includes("error")
    || normalized.includes("denied")
    || normalized.includes("forbidden")
    || normalized.includes("invalid")
    || normalized.includes("bad request")
    || normalized.includes("timeout")
    || normalized.includes("not authenticated")
    || normalized.includes("sign in")
    || normalized.includes("not available")
    || normalized.includes("current tier")
    || normalized.includes("too many requests")
    || normalized.includes("rate limit")
    || /失败|拒绝|错误|禁止|无效|超时|登录|不可用|限流|频率/.test(status)
  );
}
