import { useEffect, useState } from "react";
import { Loader2, MessageCircle } from "lucide-react";

import { listKolPoolVideoComments } from "../../../../services/vkpi/kolPool-api";
import { asRecord, cleanText, type Row } from "./SmartKolInputPanel.helpers";

type CommentSampleState = {
  identity: string;
  status: "loading" | "ready" | "error";
  items: Row[];
  source: string;
  error: string;
};

/**
 * Read-only comment evidence for a resolved video.
 *
 * This stays behind a lazy boundary because account/profile URL results never
 * need the comments API or its presentation code. Platform totals and stored
 * text samples remain deliberately separate so an empty local sample cannot
 * be mistaken for zero platform comments.
 */
export default function VideoCommentSamples({
  apiToken,
  kolPoolId,
  evidenceId,
  platformCommentCount,
}: {
  apiToken: string;
  kolPoolId: number;
  evidenceId: number;
  platformCommentCount: unknown;
}) {
  const identity = `${kolPoolId}:${evidenceId}`;
  const [stored, setStored] = useState<CommentSampleState>({
    identity: "",
    status: "loading",
    items: [],
    source: "",
    error: "",
  });
  const state = stored.identity === identity
    ? stored
    : { identity, status: "loading" as const, items: [], source: "", error: "" };

  useEffect(() => {
    let cancelled = false;
    setStored({ identity, status: "loading", items: [], source: "", error: "" });
    listKolPoolVideoComments(apiToken, kolPoolId, evidenceId, 20)
      .then((response) => {
        if (cancelled) return;
        const items = Array.isArray(response.items)
          ? response.items.map((item) => asRecord(item)).filter((item) => cleanText(item.comment_text))
          : [];
        setStored({
          identity,
          status: "ready",
          items,
          source: cleanText(asRecord(response).source),
          error: "",
        });
      })
      .catch(() => {
        if (!cancelled) {
          setStored({
            identity,
            status: "error",
            items: [],
            source: "",
            error: "评论样本暂时读取失败，平台评论计数仍可参考。",
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken, evidenceId, identity, kolPoolId]);

  const platformCount = Math.max(0, Number(platformCommentCount) || 0);
  const sourceLabel = state.source === "kol_comments_bridge" ? "账号评论桥接" : "视频评论采集";

  return (
    <div
      className="mt-2 rounded-lg border border-violet-300/15 bg-violet-950/[0.10] px-3 py-2.5"
      data-testid="video-comment-samples"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="inline-flex items-center gap-1.5 text-[10.5px] font-medium text-violet-100">
          <MessageCircle size={12} /> 评论证据
        </div>
        <div className="flex flex-wrap gap-1.5 text-[9px]">
          <span className="rounded border border-white/[0.08] bg-black/20 px-1.5 py-0.5 text-slate-300">
            平台评论指标 {platformCount.toLocaleString()}
          </span>
          <span className="rounded border border-violet-300/15 bg-violet-400/[0.08] px-1.5 py-0.5 text-violet-100">
            已读取评论样本 {state.items.length}
          </span>
        </div>
      </div>
      {state.status === "loading" ? (
        <div className="mt-2 inline-flex items-center gap-1.5 text-[10px] text-slate-400" role="status">
          <Loader2 size={11} className="animate-spin" /> 正在读取已持久化评论样本…
        </div>
      ) : state.status === "error" ? (
        <p className="mt-2 text-[10px] text-amber-100">{state.error}</p>
      ) : state.items.length ? (
        <div className="mt-2 space-y-1.5">
          {state.items.slice(0, 3).map((item, index) => {
            const author = cleanText(item.author_handle) || "匿名评论者";
            const likes = Math.max(0, Number(item.like_count) || 0);
            return (
              <div key={cleanText(item.id) || `${author}:${index}`} className="rounded border border-white/[0.06] bg-black/20 px-2 py-1.5">
                <div className="flex items-center justify-between gap-2 text-[9px] text-slate-500">
                  <span className="truncate">@{author}</span>
                  {likes ? <span className="shrink-0">{likes.toLocaleString()} 赞</span> : null}
                </div>
                <p className="mt-0.5 line-clamp-2 text-[10px] leading-relaxed text-slate-300">{cleanText(item.comment_text)}</p>
              </div>
            );
          })}
          <div className="text-[9px] text-slate-500">
            当前读取上限 20 条 · 来源：{sourceLabel}；样本数不等于平台全部评论数。
          </div>
        </div>
      ) : (
        <p className="mt-2 text-[10px] leading-relaxed text-slate-400">
          {platformCount > 0
            ? `平台显示 ${platformCount.toLocaleString()} 条评论，但本地尚无该视频的评论正文样本；评论采集完成后会在这里显示。`
            : "本地尚无该视频的评论正文样本；这不代表平台没有评论。"}
        </p>
      )}
    </div>
  );
}
