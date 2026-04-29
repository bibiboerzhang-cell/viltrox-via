import { buildApiUrl, type CreatorSubmission, type ExternalLink, type LeaderboardEntry } from "./api";
import i18n from "../i18n";

export interface ViewerStat {
  label: string;
  value: string;
  valueClassName?: string;
}

export interface LegacyVideoViewerData {
  mode: "rank" | "submission";
  badge: string;
  title: string;
  subtitle: string;
  stats: ViewerStat[];
  externalLinks: ExternalLink[];
  uploadedVideoUrl?: string;
  posterUrl?: string;
  extraTitle?: string;
  extraBody?: string;
}

type ScoreMap = Record<string, number>;

function toNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value <= 1 ? value * 100 : value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) {
      return parsed <= 1 ? parsed * 100 : parsed;
    }
  }
  return null;
}

function average(values: Array<number | null>): number | null {
  const filtered = values.filter((value): value is number => Number.isFinite(value));
  if (!filtered.length) {
    return null;
  }
  return filtered.reduce((sum, value) => sum + value, 0) / filtered.length;
}

function clampScore(value: number | null): number | null {
  if (value == null || !Number.isFinite(value)) {
    return null;
  }
  return Math.max(0, Math.min(100, Math.round(value)));
}

function formatScore(value: number | null): string {
  return value == null ? "—" : `${value}`;
}

function formatDateLite(iso?: string): string {
  if (!iso) {
    return i18n.t("viewer.emptyValue");
  }
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? iso
    : date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function normalizeStatus(status?: string): string {
  const lower = String(status || "").trim().toLowerCase();
  if (lower === "confirmed" || lower === "approved") {
    return i18n.t("viewer.status.approved");
  }
  if (lower === "suspected" || lower === "review") {
    return i18n.t("viewer.status.inReview");
  }
  if (lower === "not_detected" || lower === "rejected") {
    return i18n.t("viewer.status.rejected");
  }
  if (lower === "queued" || lower === "running" || lower === "processing") {
    return i18n.t("viewer.status.processing");
  }
  return status || i18n.t("viewer.status.pending");
}

function statusTone(status?: string): string {
  const normalized = normalizeStatus(status).toLowerCase();
  if (normalized === "approved") {
    return "is-status";
  }
  if (normalized === "rejected") {
    return "is-danger";
  }
  if (normalized === "processing" || normalized === "in review") {
    return "is-warn";
  }
  return "is-muted";
}

function getAnalysis(submission: CreatorSubmission): Record<string, unknown> {
  const analysis = submission.video_analysis;
  return analysis && typeof analysis === "object" ? analysis : {};
}

function getQualityScores(submission: CreatorSubmission): ScoreMap {
  const analysis = getAnalysis(submission);
  const raw = analysis.quality_scores;
  if (!raw || typeof raw !== "object") {
    return {};
  }
  const scores: ScoreMap = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    const numeric = toNumber(value);
    if (numeric != null) {
      scores[key] = numeric;
    }
  }
  return scores;
}

export function submissionCleanliness(submission: CreatorSubmission): number | null {
  const scores = getQualityScores(submission);
  return clampScore(
    average([
      scores.focus ?? null,
      scores.composition ?? null,
      scores.lighting ?? null,
      scores.color_grade ?? null,
      scores.exposure ?? null,
    ]),
  );
}

export function submissionSpeed(submission: CreatorSubmission): number | null {
  const scores = getQualityScores(submission);
  return clampScore(
    average([
      scores.editing ?? null,
      scores.hook ?? null,
      scores.storytelling ?? null,
    ]),
  );
}

export function submissionQuality(submission: CreatorSubmission): number | null {
  const analysis = getAnalysis(submission);
  return (
    clampScore(toNumber(analysis.quality_overall)) ??
    clampScore(average(Object.values(getQualityScores(submission)).map((value) => value ?? null)))
  );
}

export function submissionGear(submission: CreatorSubmission): string {
  const analysis = getAnalysis(submission);
  const gear = String(analysis.gear_combo || analysis.viltrox_lens || submission.product_label || "").trim();
  return gear || i18n.t("viewer.emptyValue");
}

function normalizeLinks(links?: ExternalLink[]): ExternalLink[] {
  return Array.isArray(links)
    ? links.filter((link) => link?.url).map((link) => ({ label: link.label || i18n.t("viewer.openLink"), url: link.url }))
    : [];
}

function resolveMediaUrl(path?: string): string {
  const value = String(path || "").trim();
  return value ? buildApiUrl(value) : "";
}

export function buildLeaderboardViewerData(entry: LeaderboardEntry, rank: number): LegacyVideoViewerData {
  const score = Number(entry.total_score ?? entry.total_campaign_score ?? entry.top_score ?? 0);
  const points = Number(entry.points ?? entry.total_points_earned ?? entry.estimated_points ?? 0);
  const subtitle = entry.creator_code ? i18n.t("viewer.creatorId", { id: entry.creator_code }) : i18n.t("viewer.noCreatorId");
  return {
    mode: "rank",
    badge: `#${rank}`,
    title: entry.display_name || entry.name || entry.handle || i18n.t("viewer.creatorFallback", { rank }),
    subtitle,
    stats: [
      { label: i18n.t("viewer.stats.score"), value: score.toLocaleString() },
      { label: i18n.t("viewer.stats.points"), value: points.toLocaleString() },
      { label: i18n.t("viewer.stats.submissions"), value: String(entry.submission_count ?? entry.submissions ?? 0) },
      { label: i18n.t("viewer.stats.gear"), value: entry.gear_tag || i18n.t("viewer.emptyValue"), valueClassName: entry.gear_tag ? "" : "is-muted" },
    ],
    externalLinks: normalizeLinks(entry.external_links),
    uploadedVideoUrl: resolveMediaUrl(entry.uploaded_video_url),
    posterUrl: resolveMediaUrl(entry.poster_url),
  };
}

export function buildSubmissionViewerData(submission: CreatorSubmission, creatorCode = ""): LegacyVideoViewerData {
  const recommendation = String(submission.recommendation || "").trim();
  const qualitySummary = String(getAnalysis(submission).quality_summary || getAnalysis(submission).content_summary || "").trim();
  const sourceLinks = submission.url ? [{ label: submission.platform || "Source", url: submission.url }] : [];
  return {
    mode: "submission",
    badge: "▶",
    title: submission.title || i18n.t("viewer.submissionFallback", { id: submission.id }),
    subtitle: creatorCode ? i18n.t("viewer.creatorId", { id: creatorCode }) : submission.platform || i18n.t("viewer.submissionLabel"),
    stats: [
      { label: i18n.t("viewer.stats.platform"), value: submission.platform || i18n.t("viewer.unknown"), valueClassName: submission.platform ? "" : "is-muted" },
      { label: i18n.t("viewer.stats.submitted"), value: formatDateLite(submission.created_at), valueClassName: submission.created_at ? "" : "is-muted" },
      { label: i18n.t("viewer.stats.status"), value: normalizeStatus(submission.detection_status), valueClassName: statusTone(submission.detection_status) },
      { label: i18n.t("viewer.stats.score"), value: String(Math.round(Number(submission.overall_score ?? submission.final_score ?? 0))) },
      { label: i18n.t("viewer.stats.points"), value: String(Math.round(Number(submission.points_awarded ?? 0))) },
      { label: i18n.t("viewer.stats.cleanliness"), value: formatScore(submissionCleanliness(submission)), valueClassName: submissionCleanliness(submission) == null ? "is-muted" : "" },
      { label: i18n.t("viewer.stats.speed"), value: formatScore(submissionSpeed(submission)), valueClassName: submissionSpeed(submission) == null ? "is-muted" : "" },
      { label: i18n.t("viewer.stats.gear"), value: submissionGear(submission), valueClassName: submissionGear(submission) === i18n.t("viewer.emptyValue") ? "is-muted" : "" },
      { label: i18n.t("viewer.stats.quality"), value: formatScore(submissionQuality(submission)), valueClassName: submissionQuality(submission) == null ? "is-muted" : "" },
    ],
    externalLinks: sourceLinks,
    uploadedVideoUrl: resolveMediaUrl(submission.video_url || submission.video_path),
    posterUrl: resolveMediaUrl(submission.best_frame_url || submission.poster_url),
    extraTitle: recommendation ? i18n.t("viewer.recommendation") : qualitySummary ? i18n.t("viewer.summary") : undefined,
    extraBody: recommendation || qualitySummary || undefined,
  };
}

export function submissionStatusTone(status?: string): string {
  const normalized = normalizeStatus(status).toLowerCase();
  if (normalized === "approved") {
    return "approved";
  }
  if (normalized === "rejected") {
    return "rejected";
  }
  if (normalized === "processing" || normalized === "in review") {
    return "processing";
  }
  return "pending";
}

export function submissionStatusLabel(status?: string): string {
  return normalizeStatus(status);
}

export function submissionDateLabel(createdAt?: string): string {
  return formatDateLite(createdAt);
}
