import {
  enqueueMyKolVideoKeyframeQa,
  type VkpiKolPoolVideoRow,
} from "../../../../services/vkpi/myKolBoard-api";
import type { FlowReceipt } from "../../pages/myKol/PoolEvidenceContent.helpers";

type Target = { poolId: number; epoch: number };

export interface KeyframeQaActionDeps {
  apiToken: string;
  target: Target;
  readOnly: boolean;
  isCurrent: (target: Target) => boolean;
  isBusy: (evidenceId: number) => boolean;
  setBusy: (evidenceId: number, on: boolean) => void;
  setReceipt: (receipt: FlowReceipt | null) => void;
  refresh: () => void;
  writeError: (error: unknown, action: string) => string;
}

const REFUSAL_COPY: Record<string, string> = {
  unsupported_platform: "关键帧复核当前仅支持 YouTube",
  final_v1_not_ready: "需先完成该视频的六层深析",
  ai_disabled: "视频复核模型或当前额度暂不可用",
};

/** 使排队回执、已完成结果和拒绝原因始终保持三态分层。 */
export async function runKeyframeQaAction(video: VkpiKolPoolVideoRow, deps: KeyframeQaActionDeps): Promise<void> {
  const evidenceId = Number(video.evidence_id ?? video.id) || 0;
  if (!deps.apiToken || !evidenceId || deps.readOnly || deps.isBusy(evidenceId)) return;
  deps.setBusy(evidenceId, true);
  deps.setReceipt(null);
  try {
    const response = await enqueueMyKolVideoKeyframeQa(deps.apiToken, deps.target.poolId, evidenceId);
    if (!deps.isCurrent(deps.target)) return;
    const status = String(response?.status || "");
    if (status === "queued" || status === "already_queued") {
      deps.setReceipt({
        text: status === "already_queued"
          ? `关键帧复核已在队列中（#${evidenceId}）。`
          : `关键帧复核已排队（#${evidenceId}）；完成后才会显示复核结果。`,
        tone: "info",
      });
    } else if (status === "already_reviewed") {
      deps.setReceipt({ text: `该视频已有关键帧复核结果（#${evidenceId}）。`, tone: "info" });
    } else {
      deps.setReceipt({ text: `${REFUSAL_COPY[status] || "关键帧复核未获服务端确认"}（#${evidenceId}）。`, tone: "error" });
    }
    deps.refresh();
  } catch (error) {
    if (deps.isCurrent(deps.target)) deps.setReceipt({ text: deps.writeError(error, `关键帧复核（#${evidenceId}）`), tone: "error" });
  } finally {
    if (deps.isCurrent(deps.target)) deps.setBusy(evidenceId, false);
  }
}
