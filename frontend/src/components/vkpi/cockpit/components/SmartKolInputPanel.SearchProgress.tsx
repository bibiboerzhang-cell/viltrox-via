// 自订阅的会话横幅(M2「治卡」①)。
// 轮询这一拍的进度文案由本组件自己从 progressStore 订阅,只让这一行重渲;外面 68 props 的
// 结果巨树不再为「一行文案」整页重画。store 没数据时(未在轮询/历史会话回看)回落到 props。
//
// 计数(已找到/已入库/未完成)刻意仍走 props:那是 controller 合并后(keep-richer)的真值,
// 轮询手上的原始快照可能更稀疏,发布出去会让数字往回走。
//
// 红线:不新增百分比、不推断进度;文案与计数全部沿用原有口径,只是换了到达路径。
import { Loader2, RefreshCw } from "lucide-react";

import { display, type Row } from "./SmartKolInputPanel.helpers";
import { useSearchProgressSnapshot } from "./SmartKolInputPanel.progressStore";

export type SessionBanner = {
  tone: string;
  label: string;
  note: string;
} | null;

/** 会话横幅(排队/查找中/已完成/部分完成/未完成)+ 计数 + 自订阅的轮询文案。 */
export function LiveSessionStatusBanner({
  banner,
  isSessionPollPaused,
  counts,
  fallbackNotice,
  onResume,
}: {
  banner: SessionBanner;
  isSessionPollPaused: boolean;
  counts: Row;
  fallbackNotice: string;
  onResume: () => void;
}) {
  const live = useSearchProgressSnapshot();
  if (!banner) return null;
  const pollNotice = live.notice || fallbackNotice;
  return (
    <div className={`mt-2 rounded-md border px-2.5 py-2 text-[10px] leading-relaxed ${
      banner.tone === "error"
        ? "border-rose-300/20 bg-rose-500/[0.07] text-rose-100"
        : banner.tone === "warn"
          ? "border-amber-300/20 bg-amber-400/[0.07] text-amber-100"
          : banner.tone === "ok"
            ? "border-emerald-300/20 bg-emerald-400/[0.07] text-emerald-100"
            : "border-emerald-300/15 bg-black/15 text-emerald-100/75"
    }`}>
      <div className="flex flex-wrap items-center gap-1.5">
        {banner.tone === "info" && !isSessionPollPaused ? <Loader2 size={11} className="animate-spin" /> : null}
        <span className="font-medium">{isSessionPollPaused ? "后台状态待继续同步" : banner.label}</span>
        {Object.keys(counts).length ? (
          <>
            <span className="rounded border border-white/[0.1] bg-black/15 px-1.5 py-0.5">已找到 {display(counts.ready, "0")}</span>
            <span className="rounded border border-white/[0.1] bg-black/15 px-1.5 py-0.5">已入库 {display(counts.executed, "0")}</span>
            {Number(counts.errors) > 0 || Number(counts.failed) > 0 ? (
              <span className="rounded border border-rose-300/20 bg-black/15 px-1.5 py-0.5 text-rose-200/80">未完成 {display(Number(counts.errors || 0) + Number(counts.failed || 0), "0")}</span>
            ) : null}
          </>
        ) : null}
      </div>
      <div className="mt-0.5 opacity-85">{banner.note}</div>
      {pollNotice ? <div className="mt-0.5 opacity-70">{pollNotice}</div> : null}
      {isSessionPollPaused ? (
        <button
          type="button"
          onClick={onResume}
          className="mt-1.5 inline-flex min-h-[26px] items-center justify-center gap-1.5 rounded-md border border-amber-300/25 px-2.5 text-[10px] font-medium text-amber-100 hover:bg-amber-400/[0.08]"
        >
          <RefreshCw size={11} /> 继续同步原任务
        </button>
      ) : null}
    </div>
  );
}
