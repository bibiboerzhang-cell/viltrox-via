import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import {
  apiFetch,
  API_BASE,
  buildApiUrl,
  jsonBody,
  uploadVideo,
  type AuthUser,
  type AuditResponse,
  type CreatorAddress,
  type CreatorProgramResponse,
  type CreatorSocialAccount,
  type CreatorSubmission,
  type RedemptionRecord,
  type LeaderboardEntry,
  type RewardItem,
} from "../lib/api";
import {
  isUploadErrorState,
  type SubmissionProgressSnapshot,
} from "../lib/submissionProgress";
import { useAuth } from "../hooks/useAuth";
import type { SurfaceKey } from "../lib/contracts.generated";
import { useViaStore } from "../stores/useViaStore";
import { EmptyState, MetricStrip, Panel, StatusPill } from "./ui";

export type { SubmissionProgressSnapshot } from "../lib/submissionProgress";

function statusTone(status: string): "neutral" | "success" | "warning" | "danger" {
  const normalized = status.toLowerCase();
  if (normalized.includes("confirm") || normalized.includes("approved")) {
    return "success";
  }
  if (normalized.includes("reject") || normalized.includes("fail")) {
    return "danger";
  }
  if (normalized.includes("review") || normalized.includes("queue") || normalized.includes("run")) {
    return "warning";
  }
  return "neutral";
}

export function SubmissionComposer({
  signedIn,
  token,
  onRequireAuth,
  onSubmissionQueued,
  onProgressChange,
}: {
  signedIn: boolean;
  token: string;
  onRequireAuth: () => void;
  onSubmissionQueued: () => void;
  onProgressChange?: (snapshot: SubmissionProgressSnapshot) => void;
}) {
  const { t } = useTranslation();
  const { setProgressSnapshot } = useViaStore();
  const [videoUrl, setVideoUrl] = useState("");
  const [handle, setHandle] = useState("");
  const [platform, setPlatform] = useState("");
  const [permission, setPermission] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [statusLine, setStatusLine] = useState(t("creator.submit.ready"));
  const [jobId, setJobId] = useState("");
  const [step, setStep] = useState(1);
  const [submitting, setSubmitting] = useState(false);
  const [dropActive, setDropActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  function resetUploadProgressState() {
    setJobId("");
    setStep(1);
    setStatusLine(t("creator.submit.ready"));
    setProgressSnapshot(null);
  }

  useEffect(() => {
    const normalized = videoUrl.toLowerCase();
    if (normalized.includes("tiktok.com")) setPlatform("TikTok");
    else if (normalized.includes("instagram.com")) setPlatform("Instagram");
    else if (normalized.includes("youtube.com") || normalized.includes("youtu.be")) setPlatform("YouTube");
    else if (normalized.includes("facebook.com") || normalized.includes("fb.watch")) setPlatform("Facebook");
    else if (normalized.includes("reddit.com")) setPlatform("Reddit");
    else setPlatform("");
  }, [videoUrl]);

  useEffect(() => {
    const sourceLabel = file?.name || videoUrl.trim() || undefined;
    const hasPersistableProgress = Boolean(jobId || step > 1 || isUploadErrorState(statusLine));
    const nextSnapshot = hasPersistableProgress
      ? {
          surface: "upload" as const,
          step,
          statusLine,
          jobId: jobId || undefined,
          sourceLabel,
          sourceKind: file ? "file" as const : videoUrl.trim() ? "link" as const : undefined,
          updatedAt: Date.now(),
          isActive: Boolean(jobId && step < 5 && !isUploadErrorState(statusLine)),
        }
      : null;

    onProgressChange?.({
      step,
      statusLine,
      jobId: jobId || undefined,
      sourceLabel,
      sourceKind: file ? "file" : videoUrl.trim() ? "link" : undefined,
    });
    setProgressSnapshot(nextSnapshot);
  }, [file, jobId, onProgressChange, setProgressSnapshot, statusLine, step, videoUrl]);

  async function handleSubmit() {
    if (!signedIn) {
      onRequireAuth();
      return;
    }
    if (!videoUrl && !file) {
      setStatusLine(t("creator.submit.errors.addUrlOrFile"));
      return;
    }
    if (!permission) {
      setStatusLine(t("creator.submit.errors.confirmPermission"));
      return;
    }

    setSubmitting(true);
    setStatusLine(t("creator.submit.status.preparing"));
    setStep(1);

    try {
      let uploadedVideo: Record<string, string | number> | undefined;
      if (file) {
        setStatusLine(t("creator.submit.status.uploading"));
        const uploaded = await uploadVideo(file, token);
        uploadedVideo = {
          asset_id: uploaded.asset_id ?? 0,
          video_id: uploaded.video_id!,
          r2_key: uploaded.r2_key ?? "",
          size_mb: uploaded.size_mb ?? 0,
          filename: uploaded.filename ?? file.name,
          mime_type: uploaded.mime_type ?? file.type,
        };
      }

      setStatusLine(t("creator.submit.status.queueing"));
      setStep(2);

      const response = await apiFetch<AuditResponse>(
        "/api/audit/v2",
        {
          method: "POST",
          body: jsonBody({
            url: videoUrl,
            user_handle: handle,
            linked_handles: {},
            title: "",
            caption: "",
            raw_text: "",
            metrics: { views: 0, likes: 0, comments: 0, shares: 0, favorites: 0 },
            hints: { logo: false, product: false, voice: false, review: false },
            ...(uploadedVideo ? { uploaded_video: uploadedVideo } : {}),
          }),
        },
        token,
      );

      if (response.status === "queued" && (response.job_id || response.analysis_task_id)) {
        const nextJobId = response.job_id ?? response.analysis_task_id ?? "";
        setJobId(nextJobId);
        setStatusLine(t("creator.submit.status.jobAccepted"));
        setStep(2);
        onSubmissionQueued();
        return;
      }

      if (response.rejection_code || response.status === "rejected") {
        setStatusLine(response.rejection_reason ?? response.rejection_code ?? t("creator.submit.errors.rejected"));
        return;
      }

      setStatusLine(response.status || t("creator.submit.status.accepted"));
    } catch (error) {
      setJobId("");
      setStep(1);
      setStatusLine(error instanceof Error ? error.message : t("creator.submit.errors.failed"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Panel
      title={t("creator.submit.panelTitle")}
      kicker={t("creator.submit.panelKicker")}
      aside={<StatusPill label={signedIn ? t("creator.submit.signedIn") : t("creator.submit.pleaseSignIn")} tone={signedIn ? "success" : "warning"} />}
    >
      <div className="panel-stack">
        <div className="helper-banner">
          <strong>{signedIn ? t("creator.submit.sessionReady") : t("creator.submit.pleaseSignIn")}</strong>
          <span>
            {signedIn
              ? t("creator.submit.sessionReadyBody")
              : t("creator.submit.signInBody")}
          </span>
        </div>

        <div className="form-grid">
          <div className="field field--full field--dropzone">
            <span>{t("creator.submit.dropzoneLabel")}</span>
            <input
              ref={fileInputRef}
              className="sr-only"
              type="file"
              accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm,.avi"
              onChange={(event) => {
                setFile(event.target.files?.[0] ?? null);
                resetUploadProgressState();
              }}
            />
            <button
              className={`upload-zone${dropActive ? " is-active" : ""}`}
              type="button"
              onClick={() => fileInputRef.current?.click()}
              onDragOver={(event) => {
                event.preventDefault();
                setDropActive(true);
              }}
              onDragLeave={() => setDropActive(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDropActive(false);
                const dropped = event.dataTransfer.files?.[0];
                if (dropped) {
                  setFile(dropped);
                  resetUploadProgressState();
                }
              }}
            >
              <span className="upload-zone__icon">⇪</span>
              <strong>{file ? file.name : t("creator.submit.dropzoneLabel")}</strong>
              <small>{file ? t("creator.submit.fileSelected", { size: Math.round(file.size / 1024 / 1024) }) : t("creator.submit.fileHint")}</small>
            </button>
          </div>

          <div className="or-separator field--full" aria-hidden="true">
            <span>{t("creator.submit.or")}</span>
          </div>

          <label className="field field--full">
            <span>{t("creator.submit.optionB")}</span>
            <input
              value={videoUrl}
              onChange={(event) => {
                setVideoUrl(event.target.value);
                resetUploadProgressState();
              }}
              placeholder={t("creator.submit.urlPlaceholder")}
            />
          </label>
          <label className="field">
            <span>{t("creator.submit.platformAuto")}</span>
            <input value={platform} readOnly placeholder={t("creator.submit.autoDetected")} />
          </label>
          <label className="field">
            <span>{t("creator.submit.handleOptional")}</span>
            <input value={handle} onChange={(event) => setHandle(event.target.value)} placeholder={t("creator.submit.handlePlaceholder")} />
          </label>
        </div>

        <div className="toolbar toolbar--subtle">
          <button
            className="ghost-button ghost-button--small"
            type="button"
            onClick={() => {
              setVideoUrl("");
              setHandle("");
              setPlatform("");
              setPermission(false);
              setFile(null);
              setJobId("");
              setStep(1);
              setStatusLine(t("creator.submit.ready"));
              setProgressSnapshot(null);
            }}
          >
            {t("creator.submit.clear")}
          </button>
          <span className="toolbar-note">{t("creator.submit.clearNote")}</span>
        </div>

        <label className="consent-row">
          <input type="checkbox" checked={permission} onChange={(event) => setPermission(event.target.checked)} />
          <span>{t("creator.submit.permission")}</span>
        </label>

        <div className="action-row">
          <button className="primary-button" type="button" onClick={handleSubmit} disabled={submitting}>
            {submitting ? t("creator.submit.submitting") : t("creator.submit.action")}
          </button>
        </div>

        <div className="submit-status-line">
          <strong>{statusLine}</strong>
          <small>{jobId ? t("creator.submit.jobId", { id: jobId }) : t("creator.submit.ready")}</small>
        </div>
      </div>
    </Panel>
  );
}

export function MonoUploadComposer({
  signedIn,
  token,
  onRequireAuth,
  onSubmissionQueued,
  onProgressChange,
}: {
  signedIn: boolean;
  token: string;
  onRequireAuth: () => void;
  onSubmissionQueued: () => void;
  onProgressChange?: (snapshot: SubmissionProgressSnapshot) => void;
}) {
  const { t } = useTranslation();
  const { progressSnapshot, setProgressSnapshot } = useViaStore();
  const [videoUrl, setVideoUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [statusLine, setStatusLine] = useState(t("creator.submit.ready"));
  const [jobId, setJobId] = useState("");
  const [step, setStep] = useState(1);
  const [submitting, setSubmitting] = useState(false);
  const [dropActive, setDropActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const hasSelection = Boolean(videoUrl.trim() || file);
  const readyLabel = t("creator.submit.ready");
  const persistedSourceLabel = progressSnapshot?.surface === "upload" ? progressSnapshot.sourceLabel : undefined;
  const persistedSourceKind = progressSnapshot?.surface === "upload" ? progressSnapshot.sourceKind : undefined;
  const persistedProgressIsMeaningful = Boolean(
    signedIn
    &&
    progressSnapshot?.surface === "upload"
    && (
      progressSnapshot.jobId
      || progressSnapshot.step > 1
      || isUploadErrorState(progressSnapshot.statusLine)
    ),
  );

  useEffect(() => {
    if (!signedIn) {
      setVideoUrl("");
      setFile(null);
      setJobId("");
      setStep(1);
      setStatusLine(readyLabel);
      setProgressSnapshot(null);
      return;
    }
    if (!progressSnapshot || progressSnapshot.surface !== "upload") {
      return;
    }
    if (!persistedProgressIsMeaningful) {
      setProgressSnapshot(null);
      return;
    }
    setStatusLine(progressSnapshot.statusLine);
    setStep(progressSnapshot.step);
    setJobId(progressSnapshot.jobId ?? "");
  }, [persistedProgressIsMeaningful, progressSnapshot, readyLabel, setProgressSnapshot, signedIn]);

  useEffect(() => {
    const sourceLabel = file?.name || videoUrl.trim() || persistedSourceLabel || undefined;
    const hasPersistableProgress = Boolean(jobId || step > 1 || isUploadErrorState(statusLine));
    const nextSnapshot = hasPersistableProgress
      ? {
          surface: "upload" as const,
          step,
          statusLine,
          jobId: jobId || undefined,
          sourceLabel,
          sourceKind: file ? "file" as const : videoUrl.trim() ? "link" as const : persistedSourceKind,
          updatedAt: Date.now(),
          isActive: Boolean(jobId && step < 5 && !isUploadErrorState(statusLine)),
        }
      : null;

    onProgressChange?.({
      step,
      statusLine,
      jobId: jobId || undefined,
      sourceLabel,
      sourceKind: file ? "file" : videoUrl.trim() ? "link" : persistedSourceKind,
    });
    setProgressSnapshot(nextSnapshot);
  }, [file, jobId, onProgressChange, persistedSourceKind, persistedSourceLabel, setProgressSnapshot, statusLine, step, videoUrl]);

  async function handleSubmit() {
    if (!signedIn) {
      onRequireAuth();
      return;
    }
    if (!videoUrl && !file) {
      setStatusLine(t("creator.submit.errors.addUrlOrFile"));
      return;
    }
    setJobId("");
    setSubmitting(true);
    setStatusLine(t("creator.submit.status.preparing"));
    setStep(1);

    try {
      let uploadedVideo: Record<string, string | number> | undefined;
      if (file) {
        setStatusLine(t("creator.submit.status.uploading"));
        const uploaded = await uploadVideo(file, token);
        uploadedVideo = {
          asset_id: uploaded.asset_id ?? 0,
          video_id: uploaded.video_id!,
          r2_key: uploaded.r2_key ?? "",
          size_mb: uploaded.size_mb ?? 0,
          filename: uploaded.filename ?? file.name,
          mime_type: uploaded.mime_type ?? file.type,
        };
      }

      setStatusLine(t("creator.submit.status.queueing"));
      setStep(2);

      const response = await apiFetch<AuditResponse>(
        "/api/audit/v2",
        {
          method: "POST",
          body: jsonBody({
            url: videoUrl,
            user_handle: "",
            linked_handles: {},
            title: "",
            caption: "",
            raw_text: "",
            metrics: { views: 0, likes: 0, comments: 0, shares: 0, favorites: 0 },
            hints: { logo: false, product: false, voice: false, review: false },
            ...(uploadedVideo ? { uploaded_video: uploadedVideo } : {}),
          }),
        },
        token,
      );

      if (response.status === "queued" && (response.job_id || response.analysis_task_id)) {
        const nextJobId = response.job_id ?? response.analysis_task_id ?? "";
        setJobId(nextJobId);
        setStatusLine(t("creator.submit.status.jobAccepted"));
        setStep(2);
        onSubmissionQueued();
        return;
      }

      if (response.rejection_code || response.status === "rejected") {
        setStatusLine(response.rejection_reason ?? response.rejection_code ?? t("creator.submit.errors.rejected"));
        return;
      }

      setStep(5);
      setStatusLine(response.status || t("creator.submit.status.accepted"));
    } catch (error) {
      setJobId("");
      setStep(1);
      setStatusLine(error instanceof Error ? error.message : t("creator.submit.errors.failed"));
    } finally {
      setSubmitting(false);
    }
  }

  const statusKind = isUploadErrorState(statusLine)
    ? "error"
    : step >= 5
      ? "success"
      : jobId || step >= 2
        ? "progress"
        : submitting
          ? "working"
          : hasSelection
            ? "selected"
            : "idle";
  const statusBadge =
    statusKind === "error"
      ? t("creator.submit.stateCard.badges.error")
      : statusKind === "success"
        ? t("creator.submit.stateCard.badges.success")
        : statusKind === "progress"
          ? t("creator.submit.stateCard.badges.progress")
          : statusKind === "working"
            ? t("creator.submit.stateCard.badges.working")
            : statusKind === "selected"
              ? t("creator.submit.stateCard.badges.selected")
              : t("creator.submit.stateCard.badges.idle");
  const statusTitle =
    statusKind === "error"
      ? t("creator.submit.stateCard.title.error")
      : statusKind === "success"
        ? t("creator.submit.stateCard.title.success")
        : statusKind === "progress"
          ? t("creator.submit.stateCard.title.progress")
          : statusKind === "working"
            ? t("creator.submit.stateCard.title.working")
            : statusKind === "selected"
              ? t("creator.submit.stateCard.title.selected")
              : t("creator.submit.stateCard.title.idle");
  const statusBody =
    statusKind === "error"
      ? t("creator.submit.stateCard.body.error")
      : statusKind === "success"
        ? t("creator.submit.stateCard.body.success")
        : statusKind === "progress"
          ? t("creator.submit.stateCard.body.progress")
          : statusKind === "working"
            ? t("creator.submit.stateCard.body.working")
            : statusKind === "selected"
              ? t("creator.submit.stateCard.body.selected")
              : t("creator.submit.stateCard.body.idle");
  const primaryMetaLabel = jobId ? t("creator.submit.stateCard.meta.job") : t("creator.submit.stateCard.meta.source");
  const primaryMetaValue = jobId || file?.name || videoUrl || persistedSourceLabel || t("creator.submit.stateCard.meta.sourceFallback");
  const secondaryMetaValue = statusLine || readyLabel;

  return (
    <div className="bw-upload-stack">
      <div className={`bw-upload-zone${dropActive ? " is-active" : ""}`}>
        <label className="bw-upload-half">
          <input
            ref={fileInputRef}
            className="sr-only"
            type="file"
            accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm,.avi"
            onChange={(event) => {
              setVideoUrl("");
              setFile(event.target.files?.[0] ?? null);
              setProgressSnapshot(null);
              setJobId("");
              setStep(1);
              setStatusLine(readyLabel);
            }}
          />
          <button
            className="bw-upload-trigger"
            type="button"
            onClick={() => fileInputRef.current?.click()}
            onDragOver={(event) => {
              event.preventDefault();
              setDropActive(true);
            }}
            onDragLeave={() => setDropActive(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDropActive(false);
              const dropped = event.dataTransfer.files?.[0];
              if (dropped) {
                setVideoUrl("");
                setFile(dropped);
                setProgressSnapshot(null);
                setJobId("");
                setStep(1);
                setStatusLine(readyLabel);
              }
            }}
          >
            <span className="bw-upload-icon">⇪</span>
            <span className="bw-upload-copy">
              <strong>{file ? file.name : t("creator.submit.dropzoneLabel")}</strong>
              <small>{file ? t("creator.submit.fileSelected", { size: Math.round(file.size / 1024 / 1024) }) : t("creator.submit.dropHint")}</small>
            </span>
          </button>
        </label>

        <div className="bw-upload-divider" />

        <div className="bw-upload-half bw-upload-half--url">
          <span className="bw-upload-icon">⤴</span>
          <div className="bw-upload-copy">
            <strong>{t("creator.submit.optionB")}</strong>
          </div>
          <div className="bw-upload-url-row">
            <input
              className="bw-upload-input"
              value={videoUrl}
              onChange={(event) => {
                setFile(null);
                setVideoUrl(event.target.value);
                setProgressSnapshot(null);
                setJobId("");
                setStep(1);
                setStatusLine(readyLabel);
              }}
              placeholder={t("creator.submit.urlPlaceholder")}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  void handleSubmit();
                }
              }}
            />
          </div>
        </div>
      </div>

      <div className="bw-upload-actions">
        <button className="bw-upload-submit" type="button" disabled={submitting || !hasSelection} onClick={() => void handleSubmit()}>
          {submitting ? t("creator.submit.submitting") : "Go"}
        </button>
      </div>

      <div className="bw-upload-meta">
        <p>{t("creator.submit.fileHint")}</p>
      </div>

      {(submitting || jobId || step > 1 || isUploadErrorState(statusLine) || persistedProgressIsMeaningful) && (
        <div className={`bw-upload-state bw-upload-state--${statusKind}`}>
          <div className="bw-upload-state__head">
            <span className="bw-upload-state__eyebrow">{t("creator.submit.stateCard.eyebrow")}</span>
            <span className={`bw-upload-state__badge is-${statusKind}`}>{statusBadge}</span>
          </div>
          <strong>{statusTitle}</strong>
          <p>{statusBody}</p>
          <div className="bw-upload-state__meta">
            <div>
              <small>{primaryMetaLabel}</small>
              <span>{primaryMetaValue}</span>
            </div>
            <div>
              <small>{t("creator.submit.stateCard.meta.stage")}</small>
              <span>{secondaryMetaValue}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export function MonoProgressCard({
  step,
  statusLine,
}: SubmissionProgressSnapshot) {
  const { t } = useTranslation();
  const progressMap: Record<number, number> = {
    1: 0,
    2: 25,
    3: 55,
    4: 78,
    5: 100,
  };
  const steps = [
    t("creator.progress.steps.submit"),
    t("creator.progress.steps.queued"),
    t("creator.progress.steps.analyze"),
    t("creator.progress.steps.review"),
    t("creator.progress.steps.points"),
  ];
  const progress = progressMap[step] ?? 0;

  return (
    <div className="bw-progress-card">
      <div className="bw-progress-thumb" />
      <div className="bw-progress-main">
        <p>{statusLine}</p>
        <div className="bw-progress-steps">
          {steps.map((label, index) => {
            const currentStep = index + 1;
            const lineDone = currentStep < step;
            return (
              <div key={label} className="bw-progress-stepwrap">
                <div className="bw-progress-step">
                  <span className={`bw-progress-dot${currentStep < step ? " is-done" : ""}${currentStep === step ? " is-active" : ""}`}>
                    {currentStep < step ? "✓" : ""}
                  </span>
                  <small>{label}</small>
                </div>
                {index < steps.length - 1 ? <i className={`bw-progress-line${lineDone ? " is-done" : ""}`} /> : null}
              </div>
            );
          })}
        </div>
      </div>
      <div className="bw-progress-score">
        <strong>{progress === 100 ? "OK" : `${progress}%`}</strong>
        <span>{progress === 100 ? t("creator.progress.pointsReady") : "progress"}</span>
      </div>
    </div>
  );
}

export function SystemProgressCard({
  step,
  statusLine,
  surface = "upload",
}: SubmissionProgressSnapshot & { surface?: SurfaceKey }) {
  const { t } = useTranslation();
  const { setProgressSnapshot } = useViaStore();
  const progressMap: Record<number, number> = {
    1: 0,
    2: 20,
    3: 54,
    4: 78,
    5: 100,
  };
  const headline =
    step === 1
      ? t("creator.progress.awaitingSubmission")
      : step === 2
        ? t("creator.progress.queued")
        : step === 3
          ? t("creator.progress.calculating")
          : step === 4
        ? t("creator.progress.review")
        : t("creator.progress.pointsReady");
  const progressBadge = `${progressMap[step] ?? 0}%`;

  useEffect(() => {
    setProgressSnapshot((current) => ({
      surface,
      step,
      statusLine,
      jobId: current?.jobId,
      sourceLabel: current?.sourceLabel,
      sourceKind: current?.sourceKind,
      updatedAt: Date.now(),
      isActive: Boolean(current?.jobId && step < 5 && !isUploadErrorState(statusLine)),
    }));
  }, [setProgressSnapshot, statusLine, step, surface]);

  return (
    <Panel
      title={headline}
      kicker={t("creator.submit.panelKicker")}
    >
      <div className="system-progress-card system-progress-card--bw">
        <div className="system-progress-top">
          <div className="system-progress-badge">{progressBadge}</div>
          <div className="system-progress-meta">
            <strong>{headline}</strong>
            <p>{statusLine}</p>
          </div>
        </div>
        <div className="system-progress-bar">
          <div style={{ width: progressBadge }} />
        </div>
        <div className="system-progress-rail">
          {[
            t("creator.progress.steps.submit"),
            t("creator.progress.steps.queued"),
            t("creator.progress.steps.analyze"),
            t("creator.progress.steps.review"),
            t("creator.progress.steps.points"),
          ].map((label, index) => {
            const currentStep = index + 1;
            return (
            <div
              key={currentStep}
              className={`system-progress-step${currentStep < step ? " is-done" : ""}${currentStep === step ? " is-active" : ""}`}
            >
              <span>{currentStep}</span>
              <small>{label}</small>
            </div>
            );
          })}
        </div>
      </div>
    </Panel>
  );
}

export function RewardsPanel({ items, loading }: { items: RewardItem[]; loading: boolean }) {
  const { t } = useTranslation();
  return (
    <Panel title={t("creator.rewards.title")} kicker={t("creator.rewards.kicker")}>
      {loading ? (
        <div className="muted-block">{t("creator.rewards.loading")}</div>
      ) : !items.length ? (
        <EmptyState title={t("creator.rewards.emptyTitle")} body={t("creator.rewards.emptyBody")} />
      ) : (
        <div className="reward-grid">
          {items.slice(0, 6).map((item) => (
            <article key={item.id} className="reward-card">
              <div className="reward-card__thumb">{item.category}</div>
                <div className="reward-card__content">
                  <strong>{item.title}</strong>
                  <p>{item.description || t("creator.rewards.fallbackDescription")}</p>
                  <div className="reward-card__footer">
                    <span>{t("creator.rewards.points", { points: Number(item.points_cost || 0).toLocaleString() })}</span>
                    <Link className="inline-link" to="/account">
                      {t("creator.rewards.redeem")}
                    </Link>
                  </div>
                </div>
            </article>
          ))}
        </div>
      )}
    </Panel>
  );
}

export function LeaderboardPanel({ items, loading }: { items: LeaderboardEntry[]; loading: boolean }) {
  const { t } = useTranslation();
  const ranked = items
    .slice()
    .sort(
      (left, right) =>
        Number(right.total_score ?? right.total_campaign_score ?? right.points ?? 0) -
        Number(left.total_score ?? left.total_campaign_score ?? left.points ?? 0),
    )
    .slice(0, 8);

  return (
    <Panel title={t("creator.leaderboard.title")} kicker={t("creator.leaderboard.kicker")}>
      {loading ? (
        <div className="muted-block">{t("creator.leaderboard.loading")}</div>
      ) : !ranked.length ? (
        <EmptyState title={t("creator.leaderboard.emptyTitle")} body={t("creator.leaderboard.emptyBody")} />
      ) : (
        <div className="leaderboard-list">
          {ranked.map((entry, index) => (
            <article key={`${entry.creator_code ?? entry.handle ?? index}`} className="leaderboard-row">
              <div className="leaderboard-row__rank">{String(index + 1).padStart(2, "0")}</div>
              <div className="leaderboard-row__body">
                <strong>{entry.display_name || entry.name || entry.creator_code || entry.handle || t("creator.leaderboard.creatorFallback")}</strong>
                <small>{entry.creator_code || entry.handle || t("creator.leaderboard.unassignedId")}</small>
              </div>
              <div className="leaderboard-row__score">
                {Number(entry.total_score ?? entry.total_campaign_score ?? entry.points ?? 0).toLocaleString()}
              </div>
            </article>
          ))}
        </div>
      )}
    </Panel>
  );
}

export function AccountHub({
  user,
  token,
  addresses,
  socialAccounts,
  submissions,
  redemptions,
  onSaved,
}: {
  user: AuthUser;
  token: string;
  addresses: CreatorAddress[];
  socialAccounts: CreatorSocialAccount[];
  submissions: CreatorSubmission[];
  redemptions: RedemptionRecord[];
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState(user.name ?? "");
  const [avatarUrl, setAvatarUrl] = useState(user.avatar_url ?? "");
  const [bio, setBio] = useState(user.bio ?? "");
  const [saving, setSaving] = useState(false);
  const defaultAddress = addresses.find((item) => item.is_default) ?? addresses[0];

  useEffect(() => {
    setName(user.name ?? "");
    setAvatarUrl(user.avatar_url ?? "");
    setBio(user.bio ?? "");
  }, [user]);

  async function saveProfile() {
    setSaving(true);
    try {
      await apiFetch(
        "/api/creator/profile",
        {
          method: "PATCH",
          body: jsonBody({ name, avatar_url: avatarUrl, bio }),
        },
        token,
      );
      await onSaved();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="page-stack">
      <MetricStrip
        items={[
          { label: t("creator.accountSurface.metrics.creatorId.label"), value: user.creator_code || t("creator.accountSurface.pending"), note: t("creator.accountSurface.metrics.creatorId.note") },
          { label: t("creator.accountSurface.metrics.points.label"), value: Number(user.points_balance || 0).toLocaleString(), note: t("creator.accountSurface.metrics.points.note") },
          { label: t("creator.accountSurface.metrics.linked.label"), value: String(socialAccounts.length), note: t("creator.accountSurface.metrics.linked.note") },
          { label: t("creator.accountSurface.metrics.submissions.label"), value: String(submissions.length), note: t("creator.accountSurface.metrics.submissions.note") },
        ]}
      />

      <div className="dashboard-grid">
        <Panel title={t("creator.accountSurface.profileSettings.title")} kicker={t("creator.accountSurface.profileSettings.kicker")}>
          <div className="form-grid">
            <label className="field">
              <span>{t("creator.accountSurface.fields.name")}</span>
              <input value={name} onChange={(event) => setName(event.target.value)} />
            </label>
            <label className="field">
              <span>{t("creator.accountSurface.fields.avatarUrl")}</span>
              <input value={avatarUrl} onChange={(event) => setAvatarUrl(event.target.value)} />
            </label>
            <label className="field field--full">
              <span>{t("creator.accountSurface.fields.bio")}</span>
              <textarea value={bio} onChange={(event) => setBio(event.target.value)} rows={4} />
            </label>
          </div>
          <div className="action-row">
            <button className="primary-button" type="button" onClick={saveProfile} disabled={saving}>
              {saving ? t("creator.accountSurface.saving") : t("creator.accountSurface.saveProfile")}
            </button>
          </div>
        </Panel>

        <Panel title={t("creator.accountSurface.shipping.title")} kicker={t("creator.accountSurface.shipping.kicker")}>
          {defaultAddress ? (
            <div className="info-list">
              <div>
                <strong>{defaultAddress.name || t("creator.accountSurface.shipping.primaryRecipient")}</strong>
                <span>{defaultAddress.address1}</span>
              </div>
              <div>
                <strong>{t("creator.accountSurface.shipping.location")}</strong>
                <span>
                  {[defaultAddress.city, defaultAddress.state, defaultAddress.postal_code]
                    .filter(Boolean)
                    .join(", ")}
                </span>
              </div>
              <div>
                <strong>{t("creator.accountSurface.shipping.country")}</strong>
                <span>{defaultAddress.country || t("creator.accountSurface.shipping.countryFallback")}</span>
              </div>
            </div>
          ) : (
            <EmptyState title={t("creator.accountSurface.shipping.emptyTitle")} body={t("creator.accountSurface.shipping.emptyBody")} />
          )}
        </Panel>

        <Panel title={t("creator.accountSurface.social.title")} kicker={t("creator.accountSurface.social.kicker")}>
          {!socialAccounts.length ? (
            <EmptyState title={t("creator.accountSurface.social.emptyTitle")} body={t("creator.accountSurface.social.emptyBody")} />
          ) : (
            <div className="stack-list">
              {socialAccounts.map((account) => (
                <div key={account.id} className="list-row">
                  <div>
                    <strong>{account.platform}</strong>
                    <small>{account.handle}</small>
                  </div>
                  <StatusPill label={account.verified ? t("creator.accountSurface.social.verified") : t("creator.accountSurface.pending")} tone={account.verified ? "success" : "warning"} />
                </div>
              ))}
            </div>
          )}
        </Panel>

        <Panel title={t("creator.accountSurface.submissions.title")} kicker={t("creator.accountSurface.submissions.kicker")}>
          {!submissions.length ? (
            <EmptyState title={t("creator.accountSurface.submissions.emptyTitle")} body={t("creator.accountSurface.submissions.emptyBody")} />
          ) : (
            <div className="stack-list">
              {submissions.slice(0, 8).map((submission) => (
                <div key={submission.id} className="list-row">
                  <div>
                    <strong>{submission.title || t("creator.accountSurface.submissions.fallbackTitle", { id: submission.id })}</strong>
                    <small>
                      {submission.platform || t("creator.accountSurface.unknown")} · {submission.created_at || t("creator.accountSurface.submissions.noDate")}
                    </small>
                  </div>
                  <div className="list-row__aside">
                    <StatusPill
                      label={submission.detection_status || t("creator.accountSurface.pending")}
                      tone={statusTone(submission.detection_status || "")}
                    />
                    <small>{t("creator.accountSurface.pointsLabel", { points: Number(submission.points_awarded || 0).toLocaleString() })}</small>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Panel>

        <Panel title={t("creator.accountSurface.redemptions.title")} kicker={t("creator.accountSurface.redemptions.kicker")}>
          {!redemptions.length ? (
            <EmptyState title={t("creator.accountSurface.redemptions.emptyTitle")} body={t("creator.accountSurface.redemptions.emptyBody")} />
          ) : (
            <div className="stack-list">
              {redemptions.slice(0, 8).map((record) => (
                <div key={record.id} className="list-row">
                  <div>
                    <strong>{record.item_name || t("creator.accountSurface.redemptions.fallbackTitle", { id: record.id })}</strong>
                    <small>{record.created_at || t("creator.accountSurface.redemptions.pendingDate")}</small>
                  </div>
                  <div className="list-row__aside">
                    <StatusPill label={record.status || t("creator.accountSurface.pending")} tone={statusTone(record.status || "")} />
                    <small>{t("creator.accountSurface.redemptions.costLabel", { points: Number(record.points_cost || 0).toLocaleString() })}</small>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}

export function CreatorWelcome({
  user,
  program,
}: {
  user: AuthUser | null;
  program?: CreatorProgramResponse | null;
}) {
  const { t } = useTranslation();
  const vip = program?.vip;
  const affiliate = program?.affiliate;
  return (
    <section className="side-card">
      <h3 className="side-title">{t("creator.welcome.title")}</h3>
      {user ? (
        <div className="account-empty account-empty--signed">
          <div className="icon">◌</div>
          <h3>{user.name}</h3>
          <p>{t("creator.welcome.pointsBalance", { email: user.email, points: Number(user.points_balance || 0).toLocaleString() })}</p>
          <div className="account-empty__stats">
            <div>
              <small>{t("creator.welcome.creatorId")}</small>
              <strong>{user.creator_code || t("creator.accountSurface.pending")}</strong>
            </div>
            <div>
              <small>{t("creator.welcome.vip")}</small>
              <strong>{vip?.tier_label || t("creator.accountSurface.pending")}</strong>
            </div>
            <div>
              <small>{t("creator.welcome.affiliate")}</small>
              <strong>{t("creator.welcome.orders", { count: Number(affiliate?.orders_count || 0).toLocaleString() })}</strong>
            </div>
          </div>
          <div className="account-empty__quick-links">
            <Link className="ghost-button ghost-button--small" to="/redeem">
              {t("creator.welcome.viewRewards")}
            </Link>
            <Link className="ghost-button ghost-button--small" to="/account">
              {t("creator.welcome.openProfile")}
            </Link>
          </div>
          <Link className="black-btn account-empty__action" to="/account">
            {t("creator.welcome.openAccount")}
          </Link>
        </div>
      ) : (
        <div className="account-empty">
          <div className="icon">◌</div>
          <h3>{t("creator.welcome.notSignedIn")}</h3>
          <p>{t("creator.welcome.notSignedInBody")}</p>
          <Link className="black-btn account-empty__action" to="/login">
            {t("creator.welcome.signIn")}
          </Link>
        </div>
      )}
    </section>
  );
}

export function PointsRulesCard() {
  const { t } = useTranslation();
  const rules: Array<[string, string]> = [
    [t("creator.pointsRules.cap"), t("creator.pointsRules.capValue")],
    [t("creator.pointsRules.approved"), t("creator.pointsRules.approvedValue")],
    [t("creator.pointsRules.detected"), t("creator.pointsRules.detectedValue")],
    [t("creator.pointsRules.growth"), t("creator.pointsRules.growthValue")],
    [t("creator.pointsRules.featured"), t("creator.pointsRules.featuredValue")],
  ];
  return (
    <Panel title={t("creator.pointsRules.title")}>
      <div className="stack-list compact-rule-list">
        {rules.map(([label, value]) => (
          <div key={label} className="list-row">
            <div>
              <strong>{label}</strong>
            </div>
            <div className="list-row__aside">
              <small>{value}</small>
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}

export function VideoSourceLink({ submission }: { submission: CreatorSubmission }) {
  const { t } = useTranslation();
  const { token } = useAuth();
  const href =
    submission.video_url ||
    (submission.has_video ? buildApiUrl(`/api/submissions/${submission.id}/video`) : "") ||
    submission.url;
  if (!href) {
    return null;
  }
  const apiOrigin = API_BASE || (typeof window !== "undefined" ? window.location.origin : "");
  const isProtectedSubmissionMedia =
    href.startsWith("/api/submissions/") || (apiOrigin && href.startsWith(`${apiOrigin}/api/submissions/`));
  return (
    <a
      className="inline-link"
      href={href}
      target="_blank"
      rel="noreferrer"
      onClick={async (event) => {
        if (!isProtectedSubmissionMedia || !token) {
          return;
        }
        event.preventDefault();
        const response = await fetch(href, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!response.ok) {
          throw new Error(t("creator.media.openProtectedFailed"));
        }
        const blob = await response.blob();
        const blobUrl = window.URL.createObjectURL(blob);
        window.open(blobUrl, "_blank", "noopener,noreferrer");
        window.setTimeout(() => window.URL.revokeObjectURL(blobUrl), 60_000);
      }}
    >
      {t("creator.media.open")}
    </a>
  );
}
