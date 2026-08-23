import { useEffect, useRef, useState } from "react";
import { ThumbsDown, ThumbsUp } from "lucide-react";

import {
  recordSearchFeedback,
  SEARCH_FEEDBACK_REASONS,
  useSearchFeedbackEntry,
  type SearchFeedbackReason,
  type SearchFeedbackSource,
} from "../../../../services/vkpi/searchFeedback-api";
import { useT } from "../lib/i18n";

// F4 最小标注(优化波 B):发现墙结果卡 + KOL 详情头部共用的 👍/👎。
//   👍 直接提交;👎 弹出原因闭集(not_relevant / wrong_region / too_small / brand_official / duplicate / other)
//   选一个才提交。乐观态 + 去重在 searchFeedback-api 的外部 store 里;本件只订阅自己那条。
//   无 token / 无 kol_pool_id(发现项尚未入库)→ 不渲染(不给假按钮)。门面零内部词。

const BTN = "inline-flex h-6 w-6 items-center justify-center rounded border text-[10px] transition-colors disabled:cursor-default";
const BTN_IDLE = "border-white/[0.08] text-slate-500 hover:border-white/[0.2] hover:text-slate-200";
const BTN_UP = "border-emerald-300/40 bg-emerald-400/[0.14] text-emerald-100";
const BTN_DOWN = "border-rose-300/40 bg-rose-400/[0.14] text-rose-100";

export function SearchFeedbackControl({
  source,
  kolPoolId,
  sessionItemId,
  apiToken,
  className = "",
  align = "right",
}: {
  source: SearchFeedbackSource;
  kolPoolId: number | string | null | undefined;
  sessionItemId?: number | null;
  apiToken: string;
  className?: string;
  align?: "left" | "right";
}) {
  const { t } = useT();
  const entry = useSearchFeedbackEntry(source, kolPoolId);
  const [reasonOpen, setReasonOpen] = useState(false);
  const boxRef = useRef<HTMLSpanElement | null>(null);
  const poolId = Number(kolPoolId) || 0;

  useEffect(() => {
    if (!reasonOpen) return;
    const onDown = (event: MouseEvent) => {
      const node = boxRef.current;
      if (node && event.target instanceof Node && !node.contains(event.target)) setReasonOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setReasonOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [reasonOpen]);

  if (!apiToken || poolId <= 0) return null;
  const pending = entry?.status === "pending";
  const verdict = entry?.verdict ?? null;
  const send = (next: "up" | "down", reason?: SearchFeedbackReason) => {
    setReasonOpen(false);
    void recordSearchFeedback(apiToken, {
      source,
      kol_pool_id: poolId,
      ...(sessionItemId ? { session_item_id: Number(sessionItemId) } : {}),
      verdict: next,
      ...(next === "down" && reason ? { reason } : {}),
    });
  };
  const reasonLabel = entry?.reason ? SEARCH_FEEDBACK_REASONS.find((item) => item.key === entry.reason)?.label || "" : "";
  const statusTitle = entry?.status === "error"
    ? `${t("标注未保存")}${entry.error ? ` · ${entry.error}` : ""} · ${t("点击重试")}`
    : entry?.status === "saved"
      ? `${t("已标注")}${reasonLabel ? ` · ${t(reasonLabel)}` : ""}`
      : pending ? t("提交中…") : "";
  return (
    <span
      ref={boxRef}
      data-testid="search-feedback-control"
      data-feedback-source={source}
      data-feedback-verdict={verdict || ""}
      data-feedback-status={entry?.status || ""}
      className={`relative inline-flex items-center gap-1 ${className}`}
      onClick={(event) => event.stopPropagation()}
    >
      <button
        type="button"
        aria-label={t("这条推荐合适")}
        aria-pressed={verdict === "up"}
        title={verdict === "up" && statusTitle ? statusTitle : t("这条推荐合适")}
        disabled={pending}
        onClick={() => send("up")}
        className={`${BTN} ${verdict === "up" ? BTN_UP : BTN_IDLE}`}
      >
        <ThumbsUp size={11} />
      </button>
      <button
        type="button"
        aria-label={t("这条推荐不合适")}
        aria-pressed={verdict === "down"}
        aria-expanded={reasonOpen}
        title={verdict === "down" && statusTitle ? statusTitle : t("这条推荐不合适")}
        disabled={pending}
        onClick={() => setReasonOpen((open) => !open)}
        className={`${BTN} ${verdict === "down" ? BTN_DOWN : BTN_IDLE}`}
      >
        <ThumbsDown size={11} />
      </button>
      {entry?.status === "error" ? (
        <span className="text-[8.5px] text-rose-300" title={statusTitle}>{t("未保存")}</span>
      ) : null}
      {reasonOpen ? (
        <span
          role="menu"
          data-testid="search-feedback-reasons"
          className={`absolute top-full z-40 mt-1 flex w-36 flex-col gap-0.5 rounded-md border border-white/[0.1] bg-[#0e1526] p-1 text-left shadow-xl ${align === "right" ? "right-0" : "left-0"}`}
        >
          <span className="px-1.5 py-0.5 text-[8.5px] uppercase tracking-wider text-slate-500">{t("不合适的原因")}</span>
          {SEARCH_FEEDBACK_REASONS.map((reason) => (
            <button
              key={reason.key}
              type="button"
              role="menuitem"
              data-feedback-reason={reason.key}
              onClick={() => send("down", reason.key)}
              className={`rounded px-1.5 py-1 text-left text-[10px] transition-colors hover:bg-white/[0.06] ${entry?.reason === reason.key ? "text-rose-100" : "text-slate-200"}`}
            >
              {t(reason.label)}
            </button>
          ))}
        </span>
      ) : null}
    </span>
  );
}
