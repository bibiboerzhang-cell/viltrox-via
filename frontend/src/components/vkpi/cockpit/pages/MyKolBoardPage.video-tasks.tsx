import React from "react";

import {
  freshnessText,
  isTaskActive,
  TASK_CHIP_TONE_CLASS,
  taskChip,
  type VkpiVideoTasks,
} from "../../../../services/vkpi/myKolVideoTasks";
import type { VkpiKolPoolVideoRow } from "../../../../services/vkpi/myKolBoard-api";
import { DataWatchContext } from "./MyKolBoardPage.data-watch";
import { EtaHint, FailureGuidance } from "../lib/failureGuidance";
import { useT } from "../lib/i18n";

// MY KOL 视频任务状态 UI(员工反馈 #5:看不到单品播放追踪、不知道怎么加)。
//   两层:任务态 chip(排队/进行中/重试中/已阻断/失败/已完成)+ 数据新鲜度(实测于 xx 前 / 已过期 / 从未测);
//   旧结果被新任务 supersede 时显示「重测中 · 上次结果可见」。真值全部来自契约 my_kol_video_recovery_v1
//   (services/vkpi/myKolVideoTasks.ts),本件只排版;门面禁内部词。
//   「数据关注」= POST …/videos/{evidence_id}/data-watch(一键:自动认产品 + 登记追踪 + 排队刷新;
//     认不出产品时服务端返回候选,入口回落「关联 SKU」流程,绝不无 SKU 假登记);
//   「追踪播放」= POST /my-kol/{id}/videos(登记持久追踪 + 排队抓取,可带 SKU);
//   「刷新指标」= POST …/videos/{evidence_id}/refresh(一次性)。三者都只排队,不冒充实时完成。

const STATUS_CHIP = "inline-flex items-center gap-1 rounded-[5px] border px-1.5 py-px text-[9.5px] font-bold leading-4";

function TaskRow({
  kind,
  tasks,
  onRetryAnalysis,
  retryBusy = false,
}: {
  kind: "metric" | "analysis";
  tasks: VkpiVideoTasks;
  onRetryAnalysis?: () => void;
  retryBusy?: boolean;
}) {
  const { t } = useT();
  const state = kind === "metric" ? tasks.metric_refresh : tasks.final_v1;
  const chip = taskChip(kind, state);
  const fresh = freshnessText(kind, state);
  const active = isTaskActive(state);
  const failed = state.status === "failed" || state.status === "blocked";
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-1.5" data-vkpi-video-task={kind} data-vkpi-task-status={state.status}>
      <span className={`${STATUS_CHIP} ${TASK_CHIP_TONE_CLASS[chip.tone]}`} title={chip.title} role="status">
        {active ? <span aria-hidden="true" className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-current" /> : null}
        {t(chip.label)}
      </span>
      <span className="font-mono text-[10px] leading-4 text-muted" title={fresh.title}>{t(fresh.label)}</span>
      {/* F7 ETA 新口径:只认服务端 eta_seconds;缺失不显示,绝不假排队。 */}
      {active ? <EtaHint source={state} /> : null}
      {/* F3 失败可读:failure_reason_human + 按类别动作;authorization 在 MY KOL 内原地重发(收藏负责人)。 */}
      {failed && kind === "analysis" ? (
        <FailureGuidance source={state} onReissue={onRetryAnalysis} reissueBusy={retryBusy} className="basis-full" />
      ) : null}
    </div>
  );
}

/** 每条视频的两层状态(播放追踪 + 深析)。tasks 缺席(board-ext 聚合行)→ 诚实提示而非假「未发起」。 */
export function VideoTaskStatus({
  tasks,
  compact = false,
  onRetryAnalysis,
  retryBusy = false,
}: {
  tasks: VkpiVideoTasks | null | undefined;
  compact?: boolean;
  /** 深析失败(authorization 类)时「从 MY KOL 重新发起」;只读视图不传 = 不渲染按钮 */
  onRetryAnalysis?: () => void;
  retryBusy?: boolean;
}) {
  const { t } = useT();
  if (!tasks) {
    return compact ? null : (
      <div className="mt-1 text-[10px] leading-4 text-muted" title={t("聚合视图不带逐条任务态;选中单个 KOL 或打开 KOL 详情可见")}>
        {t("任务状态见单 KOL 视图")}
      </div>
    );
  }
  return (
    <div className="mt-1 flex flex-col gap-0.5">
      <TaskRow kind="metric" tasks={tasks} />
      {compact ? null : <TaskRow kind="analysis" tasks={tasks} onRetryAnalysis={onRetryAnalysis} retryBusy={retryBusy} />}
    </div>
  );
}

export const TRACK_BTN =
  "inline-flex min-h-8 items-center gap-1 rounded-lg border border-accent bg-accent-soft px-2 py-1 text-[10.5px] font-medium text-accent transition-colors hover:border-accent-hover disabled:cursor-default disabled:border-line disabled:bg-transparent disabled:text-muted";
export const SECONDARY_BTN =
  "inline-flex min-h-8 items-center rounded-lg border border-line px-2 py-1 text-[10.5px] text-muted transition-colors hover:border-accent hover:text-accent disabled:cursor-default";

/** 卡片级入口:数据关注(一键主入口)+ 追踪播放 + 关联 SKU(次)。禁用原因如实放 title。 */
export function VideoTrackActions({
  video,
  busy = false,
  readOnly = false,
  readOnlyHint = "",
  onTrack,
  onLinkSku,
  onDataWatch,
  dataWatchBusy = false,
}: {
  video: VkpiKolPoolVideoRow;
  busy?: boolean;
  readOnly?: boolean;
  readOnlyHint?: string;
  onTrack?: (video: VkpiKolPoolVideoRow) => void;
  onLinkSku?: (video: VkpiKolPoolVideoRow) => void;
  /** 一键「数据关注」;props 缺席时回落 DataWatchContext(详情弹窗经 libdetail 不改签名供给) */
  onDataWatch?: (video: VkpiKolPoolVideoRow) => void;
  dataWatchBusy?: boolean;
}) {
  const { t } = useT();
  const dataWatchCtx = React.useContext(DataWatchContext);
  const eid = Number(video.evidence_id ?? video.id) || 0;
  const handleDataWatch = onDataWatch ?? dataWatchCtx?.onDataWatch;
  const watchBusy = dataWatchBusy || Boolean(eid && dataWatchCtx?.busyEvidence.has(eid));
  const url = String(video.content_url || "").trim();
  const kind = String(video.media_kind ?? video.evidence_type ?? "video").toLowerCase();
  const isVideo = kind !== "image" && kind !== "carousel";
  const metricActive = isTaskActive(video.tasks?.metric_refresh);
  const tracked = String(video.tasks?.metric_refresh?.data?.tracking_status || video.tracking_status || "") === "tracked";
  const baseDisabledReason = readOnly
    ? readOnlyHint
    : !url
      ? t("该条未存原帖 URL,无法追踪;请先补采账号内容")
      : !isVideo
        ? t("图文/轮播没有播放数,不支持播放追踪")
        : "";
  const disabledReason = baseDisabledReason || (metricActive ? t("已有追踪任务在后台进行,完成后可再次发起") : "");
  // 数据关注不因在途任务禁用:服务端幂等(refresh already_queued 如实回执),仍可补登 SKU 关联。
  const dataWatchDisabledReason = baseDisabledReason || (!eid ? t("该条缺库内编号,暂不支持一键数据关注") : "");
  return (
    <>
      {handleDataWatch ? (
        <button
          type="button"
          className={TRACK_BTN}
          disabled={Boolean(dataWatchDisabledReason) || watchBusy}
          title={dataWatchDisabledReason || t("一键数据关注:系统自动识别该视频关联的产品并登记持久追踪,定时抓取播放/点赞;认不出产品会请你补一个 SKU(只排队,不是实时完成)")}
          onClick={() => handleDataWatch(video)}
        >
          {watchBusy ? t("关注提交中…") : t("数据关注")}
        </button>
      ) : null}
      {onTrack ? (
        <button
          type="button"
          className={TRACK_BTN}
          disabled={Boolean(disabledReason) || busy}
          title={disabledReason || t("登记这条视频的播放追踪:系统定时抓取播放/点赞并记录趋势(只排队,不是实时完成)")}
          onClick={() => onTrack(video)}
        >
          {busy ? t("提交中…") : metricActive ? t("追踪进行中") : tracked ? t("再测一次") : t("追踪播放")}
        </button>
      ) : null}
      {onLinkSku ? (
        <button
          type="button"
          className={SECONDARY_BTN}
          disabled={readOnly || !url}
          title={readOnly ? readOnlyHint : url ? t("把这条视频的播放归到产品:填入 SKU 后一起登记追踪") : t("该条未存原帖 URL,无法关联 SKU")}
          onClick={() => onLinkSku(video)}
        >
          {t("关联 SKU")}
        </button>
      ) : null}
    </>
  );
}

/** 一句引导(员工第一眼):放在视频区/内容墙工具行下方。 */
export function TrackGuideLine({ compact = false }: { compact?: boolean }) {
  const { t } = useT();
  return (
    <div className="rounded-lg border border-dashed border-accent bg-accent-soft px-3 py-2 text-[11.5px] leading-5 text-ink-2" data-vkpi-track-guide="">
      <span className="font-semibold text-accent">{t("想追踪某条视频的播放?")}</span>{" "}
      {t("点视频卡片上的「数据关注」一键登记:自动识别关联产品并定时抓取播放/点赞;「追踪播放」只登记播放追踪,「关联 SKU」可手动把播放归到具体产品。")}
      {compact ? null : <> {t("状态两行分别是:后台任务进展 + 数据实测时间;页面重开仍会显示在途任务。")}</>}
    </div>
  );
}

/** 轮询状态条:有活跃任务时每 2 秒同步;超出窗口暂停并给继续入口(绝不冒充完成)。 */
export function RecoveryPollingLine({ polling, paused, onResume }: { polling: boolean; paused: boolean; onResume: () => void }) {
  const { t } = useT();
  if (!polling && !paused) return null;
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[10.5px] leading-4 text-muted" data-vkpi-recovery-polling={paused ? "paused" : "active"}>
      {paused ? (
        <>
          <span>{t("后台任务仍在进行,已暂停自动同步(90 秒窗口)")}</span>
          <button type="button" className="text-accent transition-colors hover:text-accent-hover" onClick={onResume}>{t("继续同步")}</button>
        </>
      ) : (
        <span>{t("后台任务进行中 · 每 2 秒同步一次状态")}</span>
      )}
    </div>
  );
}
