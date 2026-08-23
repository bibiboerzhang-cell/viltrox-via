import React from "react";

import {
  etaLabelForLocale,
  etaSecondsOf,
  failureGuidance,
  hasReadableFailure,
  type FailureAction,
} from "../../../../services/vkpi/failureReason";
import { useT } from "./i18n";

// 失败可读小件(优化波 B · F3):MY KOL 深析进度 / 视频列表 / 进度中心共用。
//   输入任意带 failure_category / failure_reason_human 的失败项;没有这两字段就什么都不渲染
//   (旧数据不编故事)。按类别给提示与动作:
//     authorization → 「从 MY KOL 重新发起」按钮(onReissue 由页面决定:MY KOL 内=原地重发,
//                     其他面=跳 MY KOL);budget → 预算提示;download/provider → 「稍后自动重试」。
//   门面禁内部词;动作只在 onReissue 存在时渲染(共享只读视图不给假按钮)。
//   放在 cockpit/lib(cockpit-core 层):KOL 分析核心块 / widgets / 页面都能引,不回引上层 UI。

const TONE: Record<NonNullable<FailureAction> | "none", string> = {
  reissue_from_my_kol: "border-warn bg-warn-soft text-warn",
  check_budget: "border-warn bg-warn-soft text-warn",
  auto_retry: "border-line text-muted",
  none: "border-line text-muted",
};

export function FailureGuidance({
  source,
  onReissue,
  reissueBusy = false,
  compact = false,
  className = "",
}: {
  source: unknown;
  onReissue?: () => void;
  reissueBusy?: boolean;
  compact?: boolean;
  className?: string;
}) {
  const { t, lang } = useT();
  if (!hasReadableFailure(source)) return null;
  const copy = failureGuidance(source, lang);
  const tone = TONE[copy.action ?? "none"];
  return (
    <div
      data-vkpi-failure-guidance={copy.category}
      className={`mt-1 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 rounded-md border px-2 py-1 text-[10px] leading-4 ${tone} ${className}`}
      role="note"
    >
      <span className="min-w-0 break-words" data-vkpi-failure-reason="">{copy.reason}</span>
      {compact ? null : <span className="opacity-80" data-vkpi-failure-hint="">{t(copy.hint)}</span>}
      {copy.action === "reissue_from_my_kol" && onReissue ? (
        <button
          type="button"
          className="inline-flex min-h-6 items-center rounded border border-current px-1.5 text-[10px] font-medium transition-colors hover:opacity-80 disabled:cursor-default disabled:opacity-50"
          disabled={reissueBusy}
          onClick={onReissue}
          data-vkpi-failure-action="reissue_from_my_kol"
        >
          {reissueBusy ? t("提交中…") : t(copy.actionLabel)}
        </button>
      ) : null}
    </div>
  );
}

/** ETA 新口径小字:只认 eta_seconds,缺失不渲染。 */
export function EtaHint({ source, className = "" }: { source: unknown; className?: string }) {
  const { t, lang } = useT();
  const seconds = etaSecondsOf(source);
  const label = etaLabelForLocale(seconds, lang);
  if (!label) return null;
  return (
    <span className={`font-mono text-[10px] leading-4 text-muted ${className}`} data-vkpi-eta-seconds={String(seconds)} title={t("按当前处理能力与排队位置估算;不是承诺时间")}>
      {t("预计剩余")} {label}
    </span>
  );
}
