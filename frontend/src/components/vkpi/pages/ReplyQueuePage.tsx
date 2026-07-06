import React from "react";
import {
  fetchReplyQueue,
  screenReplyQueue,
  draftReply,
  markReply,
  type ReplyQueueItem,
  type ReplyQueueStatus,
} from "../../../services/vkpi/replyQueue-api";

// 件3 · ReplyQueue 评论区销售员 v0 半自动页。
// 列表 = 原文 / 意向标签 / 语言 / 草稿 / 一键复制 / 标记已回;v0 绝不自动发帖。
// 全走后端 /api/admin/vkpi/reply-queue;红线:不触 fit_score,不在前端拼 SQL。

const STATUS_FILTERS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "pending", label: "待起草" },
  { value: "drafted", label: "待回复" },
  { value: "replied", label: "已回复" },
  { value: "dismissed", label: "已忽略" },
  { value: "", label: "全部" },
];

const INTENT_LABEL: Record<string, string> = {
  price: "价格/购买",
  compat: "兼容/型号",
  question: "问询",
};

function intentBadge(tag: string): string {
  const t = (tag || "").toLowerCase();
  if (t === "price") return "border-emerald-300/30 bg-emerald-500/[0.12] text-emerald-200";
  if (t === "compat") return "border-sky-300/30 bg-sky-500/[0.12] text-sky-200";
  return "border-amber-300/30 bg-amber-500/[0.12] text-amber-200";
}

export function ReplyQueuePage({ apiToken = "" }: { apiToken?: string }) {
  const [items, setItems] = React.useState<ReplyQueueItem[]>([]);
  const [statusFilter, setStatusFilter] = React.useState<string>("pending");
  const [loading, setLoading] = React.useState<boolean>(false);
  const [busyId, setBusyId] = React.useState<number | null>(null);
  const [error, setError] = React.useState<string>("");
  const [notice, setNotice] = React.useState<string>("");
  const [copiedId, setCopiedId] = React.useState<number | null>(null);

  const load = React.useCallback(() => {
    if (!apiToken) {
      setError("未登录 / 无 token");
      return;
    }
    setLoading(true);
    setError("");
    fetchReplyQueue(apiToken, { status: statusFilter, limit: 200 })
      .then((res) => setItems(res.items))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "加载队列失败"))
      .finally(() => setLoading(false));
  }, [apiToken, statusFilter]);

  React.useEffect(() => {
    load();
  }, [load]);

  const runScreen = React.useCallback(() => {
    if (!apiToken) {
      setError("未登录 / 无 token");
      return;
    }
    setLoading(true);
    setError("");
    setNotice("");
    screenReplyQueue(apiToken, { limit: 800 })
      .then((res) => {
        if (res.ok) {
          setNotice(`已扫 ${res.scanned ?? 0} 条,命中 ${res.matched ?? 0},新入队 ${res.enqueued ?? 0}`);
        } else {
          setError(res.reason || "筛选失败");
        }
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "筛选失败"))
      .finally(() => load());
  }, [apiToken, load]);

  const runDraft = React.useCallback(
    (id: number) => {
      if (!apiToken) return;
      setBusyId(id);
      setError("");
      draftReply(apiToken, id)
        .then((res) => {
          if (!res.ok) {
            setError(res.reason || "生成草稿失败");
            return;
          }
          setItems((prev) =>
            prev.map((it) =>
              it.id === id
                ? { ...it, draft_reply: res.draft_reply || it.draft_reply, status: "drafted" }
                : it,
            ),
          );
          setNotice(`#${id} 草稿已生成（${res.provider || "llm"}）`);
        })
        .catch((e: unknown) => setError(e instanceof Error ? e.message : "生成草稿失败"))
        .finally(() => setBusyId(null));
    },
    [apiToken],
  );

  const runMark = React.useCallback(
    (id: number, status: ReplyQueueStatus) => {
      if (!apiToken) return;
      setBusyId(id);
      setError("");
      markReply(apiToken, id, status)
        .then((res) => {
          if (!res.ok) {
            setError(res.reason || "标记失败");
            return;
          }
          // 标记后若与当前过滤不符,直接从列表移除;否则更新状态。
          setItems((prev) =>
            statusFilter && statusFilter !== status
              ? prev.filter((it) => it.id !== id)
              : prev.map((it) => (it.id === id ? { ...it, status } : it)),
          );
        })
        .catch((e: unknown) => setError(e instanceof Error ? e.message : "标记失败"))
        .finally(() => setBusyId(null));
    },
    [apiToken, statusFilter],
  );

  const copyDraft = React.useCallback((id: number, text: string) => {
    const doneFlag = () => {
      setCopiedId(id);
      globalThis.setTimeout(() => setCopiedId((cur) => (cur === id ? null : cur)), 1500);
    };
    try {
      if (navigator?.clipboard?.writeText) {
        navigator.clipboard.writeText(text).then(doneFlag).catch(() => doneFlag());
      } else {
        doneFlag();
      }
    } catch {
      doneFlag();
    }
  }, []);

  return (
    <div className="space-y-4 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-white">评论区销售员 · ReplyQueue</h2>
          <p className="mt-1 text-[12px] text-slate-400">
            从评论里筛购买意向 → 生成品牌口吻草稿 → 一键复制、人工回复。v0 不自动发帖。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={runScreen}
            disabled={loading || !apiToken}
            className="rounded-md border border-emerald-300/30 bg-emerald-500/[0.15] px-3 py-1.5 text-[12px] text-emerald-100 hover:bg-emerald-500/[0.25] disabled:opacity-50"
          >
            {loading ? "处理中…" : "扫描新意向"}
          </button>
          <button
            onClick={load}
            disabled={loading || !apiToken}
            className="rounded-md border border-white/10 px-3 py-1.5 text-[12px] text-slate-200 hover:bg-white/[0.06] disabled:opacity-40"
          >
            刷新
          </button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {STATUS_FILTERS.map((f) => (
          <button
            key={f.value || "all"}
            onClick={() => setStatusFilter(f.value)}
            className={
              "rounded-full border px-3 py-1 text-[11px] " +
              (statusFilter === f.value
                ? "border-sky-300/40 bg-sky-500/[0.15] text-sky-100"
                : "border-white/10 text-slate-400 hover:bg-white/[0.05]")
            }
          >
            {f.label}
          </button>
        ))}
        <span className="ml-1 text-[11px] text-slate-500">共 {items.length} 条</span>
      </div>

      {error ? <div className="text-[12px] text-red-300/80">{error}</div> : null}
      {notice ? <div className="text-[12px] text-emerald-300/80">{notice}</div> : null}

      {items.length === 0 && !loading ? (
        <div className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-8 text-center text-[12px] text-slate-500">
          该状态下暂无队列项。点「扫描新意向」从评论里筛购买意向。
        </div>
      ) : null}

      <div className="space-y-3">
        {items.map((it) => {
          const busy = busyId === it.id;
          return (
            <div
              key={it.id}
              className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-3"
            >
              <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px]">
                <span className="rounded border border-white/10 px-1.5 py-0.5 text-slate-400">
                  #{it.id}
                </span>
                <span className="rounded border border-white/10 px-1.5 py-0.5 text-slate-300">
                  {it.platform || "—"}
                </span>
                <span className={"rounded border px-1.5 py-0.5 " + intentBadge(it.intent_tag)}>
                  {INTENT_LABEL[it.intent_tag] || it.intent_tag || "意向"}
                </span>
                {it.lang ? (
                  <span className="rounded border border-white/10 px-1.5 py-0.5 text-slate-500 uppercase">
                    {it.lang}
                  </span>
                ) : null}
                <span className="rounded border border-white/10 px-1.5 py-0.5 text-slate-500">
                  {it.status}
                </span>
              </div>

              <div className="mb-2 text-[13px] text-slate-200">{it.comment_text}</div>

              {it.draft_reply ? (
                <div className="mb-2 rounded-lg border border-sky-300/20 bg-sky-500/[0.06] p-2 text-[12px] text-sky-100/90">
                  <div className="mb-1 text-[10px] uppercase tracking-wide text-sky-300/60">草稿</div>
                  {it.draft_reply}
                </div>
              ) : null}

              <div className="flex flex-wrap items-center gap-2">
                <button
                  onClick={() => runDraft(it.id)}
                  disabled={busy || !apiToken}
                  className="rounded-md border border-emerald-300/30 bg-emerald-500/[0.12] px-2.5 py-1 text-[11px] text-emerald-100 hover:bg-emerald-500/[0.22] disabled:opacity-50"
                >
                  {busy ? "生成中…" : it.draft_reply ? "重新起草" : "生成草稿"}
                </button>
                <button
                  onClick={() => copyDraft(it.id, it.draft_reply)}
                  disabled={!it.draft_reply}
                  className="rounded-md border border-white/10 px-2.5 py-1 text-[11px] text-slate-200 hover:bg-white/[0.06] disabled:opacity-40"
                >
                  {copiedId === it.id ? "已复制✓" : "复制草稿"}
                </button>
                <span className="mx-1 h-4 w-px bg-white/10" />
                <button
                  onClick={() => runMark(it.id, "replied")}
                  disabled={busy || !apiToken}
                  className="rounded-md border border-white/10 px-2.5 py-1 text-[11px] text-slate-200 hover:bg-white/[0.06] disabled:opacity-40"
                >
                  标记已回
                </button>
                <button
                  onClick={() => runMark(it.id, "dismissed")}
                  disabled={busy || !apiToken}
                  className="rounded-md border border-white/10 px-2.5 py-1 text-[11px] text-slate-400 hover:bg-white/[0.06] disabled:opacity-40"
                >
                  忽略
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default ReplyQueuePage;
