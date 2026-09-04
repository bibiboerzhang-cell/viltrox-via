import type {
  VkpiKolRecallItem,
  VkpiKolUrlDeepCrawlResponse,
} from "../../../../domains/kol";

import { asRecord, cleanText } from "./SmartKolInputPanel.helpers";

export type SmartKolInputPanelProps = {
  apiToken?: string;
  /**
   * Real authenticated account identity. The API token is deliberately not used
   * for browser-state isolation because cookie sessions all expose the same
   * `cookie-session` marker to the UI.
   *
   * `null` means the account has not been resolved yet and disables search-state
   * restoration for that render. Omitting the prop keeps the legacy unscoped key
   * for isolated/component callers that do not run inside CockpitApp.
   */
  accountId?: string | number | null;
  searchMode?: string;
  onSearchModeChange?: (mode: "balanced" | "precision" | "discovery") => void;
  onRecallItems?: (items: VkpiKolRecallItem[]) => void;
  onOpenRecallItem?: (item: VkpiKolRecallItem) => void;
  onOpenProfile?: (result: VkpiKolUrlDeepCrawlResponse) => void;
};

// URL 多行批量：提取、清理并去重，调用方负责按上限截断。
export const URL_BATCH_MAX = 10;

export function extractUrls(raw: string): string[] {
  const matched = String(raw || "").match(/https?:\/\/[^\s]+/g) || [];
  const cleaned = matched.map((url) => url.replace(/[),;'"\]]+$/, "")).filter(Boolean);
  return Array.from(new Set(cleaned));
}

// URL 执行按钮只由后端返回的状态和当前忙碌态决定；抽出后保持原判断顺序不变。
export function canExecuteUrlResult(
  apiToken: string,
  result: VkpiKolUrlDeepCrawlResponse | null,
  isBusy: boolean,
): boolean {
  const profileFlow = asRecord(result?.profile_flow);
  const videoFlow = asRecord(result?.video_flow);
  const videoCreator = asRecord(result?.creator_identity || videoFlow.creator_identity);
  const videoStatus = cleanText(profileFlow.status || videoFlow.status);
  const videoJobStatus = cleanText(videoFlow.job_status || videoStatus);
  const videoJobLastError = cleanText(videoFlow.job_last_error);
  const profileOperation = cleanText(profileFlow.operation);
  const rawVideoOperation = cleanText(videoFlow.operation);
  const videoOperation = ["existing_creator_video_analysis", "new_creator_video_analysis"].includes(profileOperation)
    ? profileOperation
    : rawVideoOperation || profileOperation;
  const videoCreatorResolved = Boolean(
    cleanText(videoFlow.creator_resolution_status) === "resolved" ||
    cleanText(videoCreator.handle || videoCreator.channel_id || videoCreator.profile_url || result?.handle || result?.channel_id),
  );

  return Boolean(
    apiToken &&
    result &&
    (!result.execute || Boolean(videoJobLastError)) &&
    !isBusy &&
    (
      (result.url_type === "profile" && cleanText(profileFlow.status) === "dry_run_ready") ||
      (result.url_type === "video" && Boolean(videoJobLastError) && ["failed", "blocked"].includes(videoJobStatus)) ||
      (
        result.url_type === "video" &&
        !result.execute &&
        ["provider_refresh_pending", "identified", ""].includes(cleanText(videoFlow.status))
      ) ||
      (
        result.url_type === "video" &&
        ["dry_run_ready", "ready_to_execute"].includes(videoStatus) &&
        videoCreatorResolved &&
        ["existing_creator_video_analysis", "new_creator_video_analysis"].includes(videoOperation)
      )
    )
  );
}
