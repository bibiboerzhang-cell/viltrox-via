import React from "react";
import type { FlowReceipt } from "../../pages/myKol/PoolEvidenceContent.helpers";

// 动作回执行(自 MyKolBoardPage.dialogs 拆出,供 dialogs / track-form 共用,避免环依赖)。
function toneCls(tone: FlowReceipt["tone"]): string {
  if (tone === "error") return "border-crit bg-crit-soft text-crit";
  if (tone === "ok") return "border-good bg-good-soft text-good";
  return "border-info bg-info-soft text-info";
}

export function ReceiptLine({ msg }: { msg: FlowReceipt | null }) {
  if (!msg) return null;
  return (
    <div role={msg.tone === "error" ? "alert" : "status"} className={`mt-2 rounded-lg border px-3 py-2 text-[12px] leading-5 ${toneCls(msg.tone)}`}>
      {msg.text}
    </div>
  );
}
