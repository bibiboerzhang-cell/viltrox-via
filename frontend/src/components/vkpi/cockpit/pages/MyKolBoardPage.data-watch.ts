import React from "react";

import {
  dataWatchMyKolVideo,
  type VkpiDataWatchResponse,
  type VkpiDataWatchSkuCandidate,
  type VkpiKolPoolVideoRow,
} from "../../../../services/vkpi/myKolBoard-api";
import { confirmDetectedMyKolVideoSku } from "../../../../services/vkpi/myKolDataWatchConfirmation-api";
import type { FlowReceipt } from "../../pages/myKol/PoolEvidenceContent.helpers";

// 「数据关注」一键流程(收口波 2026-08-23 · D 车道):内容墙卡片 + KOL 详情视频卡共用。
//   真端点 = POST /my-kol/{kol_pool_id}/videos/{evidence_id}/data-watch:服务端自动关联产品
//   (已关联 > 自动识别)+ 登记持久追踪 + 排队指标刷新;认不出产品时返回 sku_required + 候选,
//   由入口各自回落「关联 SKU」流程 —— 绝不无 SKU 假登记,绝不把排队说成完成。
//   为什么独立成件:MyKolBoardPage.dialogs.tsx 贴近千行硬闸,流程/文案全部住这里,
//   dialogs 只留最小接线;KolVideoSection(libdetail)不改签名,详情弹窗经 DataWatchContext
//   把处理器送进卡片(video-tasks.VideoTrackActions 无 props 时回落本 context)。
// 红线:纯动作封装,不触 fit 分 / rule_v0;门面文案零内部术语。

/** 详情弹窗给视频卡供给的一键入口(VideoTrackActions 的 onDataWatch props 缺席时回落)。 */
export interface DataWatchContextValue {
  onDataWatch: (video: VkpiKolPoolVideoRow) => void;
  /** 提交中的 evidence id(按钮忙态与防重放同一份真值) */
  busyEvidence: ReadonlySet<number>;
}

export const DataWatchContext = React.createContext<DataWatchContextValue | null>(null);

/**
 * 数据关注 / 手工 SKU 关联落库后的板面内部通知。
 * 单品播放模块是独立读模型，不监听这个事件就会一直保留点击前的 0 空态，
 * 直到用户手工点“刷新”或重载页面。事件只触发重读，不代表抓取已完成。
 */
export const SKU_PLAY_CHANGED_EVENT = "vkpi:sku-play-changed";

export interface SkuPlayChangedDetail {
  evidenceId: number;
  skus: string[];
}

export type DataWatchSubmitIntent = "auto" | "manual" | "confirm_detected";

export function notifySkuPlayChanged(evidenceId: number, skus: string[]): void {
  if (typeof window === "undefined") return;
  const detail: SkuPlayChangedDetail = {
    evidenceId: Number(evidenceId) || 0,
    skus: [...new Set(skus.map((sku) => String(sku || "").trim()).filter(Boolean))],
  };
  window.dispatchEvent(new CustomEvent<SkuPlayChangedDetail>(SKU_PLAY_CHANGED_EVENT, { detail }));
}

/** 成功回执:SKU 如实列出;refresh=already_queued 时如实注明「已在队列」,绝不当新排队。 */
export function dataWatchSuccessText(resp: VkpiDataWatchResponse): string {
  const skus = (Array.isArray(resp.skus) ? resp.skus : []).map((sku) => String(sku || "").trim()).filter(Boolean);
  const refresh = String(resp.refresh || "");
  const queueNote = refresh === "already_queued"
    ? "指标刷新已在队列中"
    : refresh === "queued"
      ? "指标刷新已排队"
      : "指标刷新状态待确认";
  const detectedSource = String(resp.sku_provenance?.source || "");
  const modalityLabels = (resp.sku_provenance?.modalities || []).map((value) => ({ visual: "画面", text: "字幕·文字", voice: "口播", unspecified: "未注明" }[value] || value));
  const provenanceNote = resp.sku_provenance?.relation_type === "confirmed"
    ? "；系统检测 SKU 已由你显式确认"
    : resp.sku_provenance?.relation_type === "detected"
    ? detectedSource === "final_v1_lens_evidence_v2"
      ? `；SKU 来自已有视频深析${modalityLabels.length ? `（${modalityLabels.join("/")}）` : ""}唯一命中，已标为系统检测、待人工确认`
      : detectedSource === "title_alias_v1"
        ? "；SKU 来自标题唯一命中，已标为系统检测、待人工确认"
        : "；SKU 来自已有系统检测关联，仍待人工确认"
    : resp.sku_provenance?.requires_human_confirmation
      ? "；关联中含系统检测项，仍待人工确认"
      : "";
  return `已登记数据关注(SKU ${skus.length ? skus.join(" / ") : "—"})${provenanceNote}；${queueNote}，不代表抓取完成。正在定位『单品播放数据』模块。`;
}

/** detected 只落系统候选关系；员工未确认时留在选择器，不提前跳转单品闭环。 */
export function dataWatchDetectedPendingText(resp: VkpiDataWatchResponse): string {
  const skus = [...new Set((resp.skus || []).map((sku) => String(sku || "").trim()).filter(Boolean))];
  const refresh = String(resp.refresh || "");
  const queue = refresh === "already_queued" ? "指标刷新已在队列中" : refresh === "queued" ? "指标刷新已排队" : "指标刷新状态待确认";
  return `系统检测到 SKU ${skus.length ? skus.join(" / ") : "候选"}，已保留为『系统检测·待确认』；${queue}，不代表抓取完成。尚未登记为员工确认的单品关注，请在当前 SKU 确认入口核对确认。`;
}

function detectedConfirmationCandidates(resp: VkpiDataWatchResponse): VkpiDataWatchSkuCandidate[] {
  const links = Array.isArray(resp.sku_provenance?.links) ? resp.sku_provenance.links : [];
  const detectedLinks = links.filter((link) => String(link?.relation_type || "") === "detected");
  const skus = [...new Set((detectedLinks.length ? detectedLinks.map((link) => link.sku) : resp.skus || [])
    .map((sku) => String(sku || "").trim())
    .filter(Boolean))];
  return skus.map((sku) => {
    const link = detectedLinks.find((item) => String(item?.sku || "").trim() === sku);
    return {
      sku_code: sku,
      sku_name: sku,
      match_source: String(link?.source || resp.sku_provenance?.source || ""),
      modalities: resp.sku_provenance?.modalities || [],
      evidence_excerpt: String(resp.sku_provenance?.evidence_excerpt || ""),
    };
  });
}

/** 详情弹窗 sku_required 提示:引导用既有「追踪并排队刷新」表单补 SKU;候选如实附上,不硬选。 */
export function skuRequiredHintText(candidates: VkpiDataWatchSkuCandidate[] = []): string {
  const codes = candidates.map((item) => String(item.sku_code || "").trim()).filter(Boolean).slice(0, 3);
  return `未能自动识别产品——请补一个 SKU 后用『追踪并排队刷新』提交${codes.length ? `(候选:${codes.join(" / ")})` : ""}。`;
}

/** 旧调用方兼容文案;新内容墙会就地展示候选 SKU 多选器。 */
export const WALL_SKU_REQUIRED_HINT =
  "未能自动识别产品——请在内容墙选择对应 SKU 后确认；未选择不会登记。";

/** 失败文案:与卡片「追踪播放」同一套错误码口径(共享只读 / 未采集 / 平台不支持如实说人话)。 */
export function dataWatchErrorText(err: unknown): string {
  const code = String((err as { detail?: unknown; message?: unknown })?.detail || (err as Error)?.message || "");
  if (code === "my_kol_video_write_forbidden") return "共享 KOL 仅可查看,数据关注请由收藏负责人发起。";
  if (code === "new_video_target_resolution_required") return "当前仅支持已采集视频,请先账号补采/深爬后重试。";
  if (code === "video_metric_platform_unsupported") return "该平台暂不支持播放追踪。";
  const status = Number((err as { status?: unknown })?.status || 0);
  if (status === 401 || status === 403 || /not_writable|not_owned|forbidden|permission/i.test(code)) {
    return "数据关注失败:服务端拒绝写入,当前账号对该 KOL 可能只有共享只读权限。";
  }
  return `数据关注失败:${code.slice(0, 80) || "请重试"}`;
}

/** Set<number> 忙态开关的 setState 适配(dialogs / content-wall 两入口同一份实现)。 */
export function toggleIdInSet(
  setState: React.Dispatch<React.SetStateAction<Set<number>>>,
): (id: number, on: boolean) => void {
  return (id, on) =>
    setState((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
}

export interface RunDataWatchDeps {
  apiToken: string;
  kolPoolId: number;
  /** 共享只读闸(按钮已禁用,这里双保险拦写) */
  readOnly?: boolean;
  /** 弹窗切换 KOL/卸载后丢弃迟到响应;墙内缺省恒真 */
  isCurrent?: () => boolean;
  isBusy: (evidenceId: number) => boolean;
  setBusy: (evidenceId: number, on: boolean) => void;
  setReceipt: (msg: FlowReceipt | null) => void;
  /** 服务端认不出产品时的回落(两入口各自引导;candidates 原样透传) */
  onSkuRequired: (video: VkpiKolPoolVideoRow, candidates: VkpiDataWatchSkuCandidate[]) => void;
  /** 服务端已落唯一 detected 时，由界面要求员工再确认；未确认不写 confirmed。 */
  onDetectedConfirmationRequired?: (video: VkpiKolPoolVideoRow, candidates: VkpiDataWatchSkuCandidate[]) => void;
  /** status=tracking 后触发(详情/单 KOL 墙重读任务态;聚合墙缺席) */
  onTracked?: () => void;
}

/**
 * 一键数据关注完整流程：回执一律以端点真实返回为准，未知状态如实报错。
 * productSkus 仅用于 sku_required 后员工明确选定的二次提交；空数组仍走服务端保守识别。
 */
export async function runDataWatchAction(
  video: VkpiKolPoolVideoRow,
  deps: RunDataWatchDeps,
  productSkus: string[] = [],
  submitIntent: DataWatchSubmitIntent = productSkus.length ? "manual" : "auto",
): Promise<void> {
  const evidenceId = Number(video.evidence_id ?? video.id) || 0;
  if (!deps.apiToken || !deps.kolPoolId || !evidenceId || deps.readOnly || deps.isBusy(evidenceId)) return;
  const isCurrent = deps.isCurrent || (() => true);
  deps.setBusy(evidenceId, true);
  deps.setReceipt(null);
  try {
    const resp = submitIntent === "confirm_detected"
      ? await confirmDetectedMyKolVideoSku(deps.apiToken, deps.kolPoolId, evidenceId, productSkus)
      : productSkus.length
        ? await dataWatchMyKolVideo(deps.apiToken, deps.kolPoolId, evidenceId, productSkus)
        : await dataWatchMyKolVideo(deps.apiToken, deps.kolPoolId, evidenceId);
    if (!isCurrent()) return;
    const status = String(resp?.status || "");
    if (status === "sku_required") {
      deps.onSkuRequired(video, Array.isArray(resp?.candidates) ? resp.candidates : []);
      return;
    }
    if (status !== "tracking") {
      deps.setReceipt({ text: `数据关注未获服务端确认:${status || "未知状态"}`, tone: "error" });
      return;
    }
    if (resp.sku_provenance?.requires_human_confirmation) {
      deps.setReceipt({ text: dataWatchDetectedPendingText(resp), tone: "info" });
      const candidates = detectedConfirmationCandidates(resp);
      if (deps.onDetectedConfirmationRequired) deps.onDetectedConfirmationRequired(video, candidates);
      else deps.onSkuRequired(video, candidates);
      return;
    }
    deps.setReceipt({ text: dataWatchSuccessText(resp), tone: "info" });
    notifySkuPlayChanged(evidenceId, Array.isArray(resp.skus) ? resp.skus : []);
    deps.onTracked?.();
  } catch (err) {
    if (isCurrent()) deps.setReceipt({ text: dataWatchErrorText(err), tone: "error" });
  } finally {
    // 迟到响应不得改 UI，但每条 evidence 自己的 busy 必须清理，避免永久锁死按钮。
    deps.setBusy(evidenceId, false);
  }
}
