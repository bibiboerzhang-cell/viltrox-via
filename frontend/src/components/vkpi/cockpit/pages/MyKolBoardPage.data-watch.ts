import React from "react";

import {
  dataWatchMyKolVideo,
  type VkpiDataWatchResponse,
  type VkpiDataWatchSkuCandidate,
  type VkpiKolPoolVideoRow,
} from "../../../../services/vkpi/myKolBoard-api";
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

/** 成功回执:SKU 如实列出;refresh=already_queued 时如实注明「已在队列」,绝不当新排队。 */
export function dataWatchSuccessText(resp: VkpiDataWatchResponse): string {
  const skus = (Array.isArray(resp.skus) ? resp.skus : []).map((sku) => String(sku || "").trim()).filter(Boolean);
  const queueNote = String(resp.refresh || "") === "already_queued" ? "(指标刷新已在队列中)" : "";
  const provenanceNote = resp.sku_provenance?.relation_type === "detected"
    ? "；SKU 来自标题唯一命中，已标为系统检测、待人工确认"
    : "";
  return `已登记数据关注(SKU ${skus.length ? skus.join(" / ") : "—"})${provenanceNote}——系统定时抓取播放/点赞,结果见『单品播放数据』模块${queueNote}。`;
}

/** 详情弹窗 sku_required 提示:引导用既有「追踪并排队刷新」表单补 SKU;候选如实附上,不硬选。 */
export function skuRequiredHintText(candidates: VkpiDataWatchSkuCandidate[] = []): string {
  const codes = candidates.map((item) => String(item.sku_code || "").trim()).filter(Boolean).slice(0, 3);
  return `未能自动识别产品——请补一个 SKU 后用『追踪并排队刷新』提交${codes.length ? `(候选:${codes.join(" / ")})` : ""}。`;
}

/** 内容墙 sku_required 提示:墙上没有 SKU 表单,如实引导去 KOL 详情补,绝不冒充已登记。 */
export const WALL_SKU_REQUIRED_HINT =
  "未能自动识别产品——本次未登记;请打开该 KOL 详情,用「关联 SKU」补一个产品后提交。";

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
  /** status=tracking 后触发(详情/单 KOL 墙重读任务态;聚合墙缺席) */
  onTracked?: () => void;
}

/** 一键数据关注完整流程:回执一律以端点真实返回为准,未知状态如实报错。 */
export async function runDataWatchAction(video: VkpiKolPoolVideoRow, deps: RunDataWatchDeps): Promise<void> {
  const evidenceId = Number(video.evidence_id ?? video.id) || 0;
  if (!deps.apiToken || !deps.kolPoolId || !evidenceId || deps.readOnly || deps.isBusy(evidenceId)) return;
  const isCurrent = deps.isCurrent || (() => true);
  deps.setBusy(evidenceId, true);
  deps.setReceipt(null);
  try {
    const resp = await dataWatchMyKolVideo(deps.apiToken, deps.kolPoolId, evidenceId);
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
    deps.setReceipt({ text: dataWatchSuccessText(resp), tone: "info" });
    deps.onTracked?.();
  } catch (err) {
    if (isCurrent()) deps.setReceipt({ text: dataWatchErrorText(err), tone: "error" });
  } finally {
    if (isCurrent()) deps.setBusy(evidenceId, false);
  }
}
