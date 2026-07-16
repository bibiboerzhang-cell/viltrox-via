// A2 今日焦点横条(2026-07-07):登录后第一眼看到「今天该做什么」。
// 数据(两路只读并行,互不拖垮):
//   1) Action Inbox top3 + 待办计数 —— 复用 listActionInbox(GET /api/admin/vkpi/actions/inbox,
//      后端已按 scope 过滤:管理层全局 / 成员仅自己 owner 的);
//   2) 夜班晨报 headline —— GET /api/admin/vkpi/morning-brief(后端纯 SQL 聚合,零 LLM)。
// 交互:一行可点直达 —— 点待办 chip / 计数 → onJumpToInbox(滚到右栏「今日该做什么」面板);
//       点晨报 headline → onJumpToBrief(滚到右栏晨报卡)。本条纯展示+跳转,不做任何状态流转。
// 诚实态:无 token 不渲染;建议源失败如实「建议源异常」;晨报失败如实「晨报读取失败」;
//         空待办如实「暂无待办 · 一切已跟进」。绝不编造数字。
// 红线:纯读聚合,零 LLM、不写业务数据;绝不渲染/触碰 viltrox_fit_score 与 rule_v0。
import React from "react";
import { ArrowRight, Loader2, Sunrise, Target } from "lucide-react";
import { listActionInbox } from "../../../../services/vkpi/actionInbox-api";
import { apiFetch } from "../../../../services/http";

const e = React.createElement;

// 类别 → 中文短标签(口径与 ActionInboxPanel 的 CATEGORY_META 一致;横条只取 label 保持轻量)。
const CATEGORY_LABEL: Record<string, string> = {
  kol_profile: "补全资料",
  deep_missing: "深析待跑",
  failed_retry: "失败重试",
  project_observation: "开观察窗",
  content_candidate: "内容确认",
  retrospective: "项目复盘",
  event_followup: "活动收尾",
  inventory_low: "库存预警",
  project_shared_to_you: "项目共享",
};

// 优先级小圆点(高/中/低),与 ActionInboxPanel 的 PRIORITY_META 同色系。
const PRIORITY_DOT: Record<string, string> = {
  high: "bg-red-400",
  medium: "bg-amber-400",
  low: "bg-slate-500",
};

type BriefResp = { status?: string; headline?: string };

type PendingCountMeta = {
  label: string;
  kind: "exact" | "lower-bound" | "unknown";
  value: number | null;
};

const EMPTY_PENDING_COUNT: PendingCountMeta = {
  label: "--",
  kind: "unknown",
  value: null,
};

function finiteCount(...values: unknown[]) {
  for (const value of values) {
    if (value == null || value === "" || typeof value === "boolean") continue;
    const numeric = Number(value);
    if (Number.isFinite(numeric) && numeric >= 0) return Math.floor(numeric);
  }
  return null;
}

/**
 * Backward-compatible Action Inbox count semantics.
 *
 * The current API returns `count` for the limited result window. Newer API
 * shapes may expose an exact total through `total_count`, `total`, summary, or
 * pagination fields. Prefer the exact total; otherwise report a lower bound at
 * the requested cap instead of presenting a truncated value as an exact total.
 */
function resolvePendingCount(resp: any, returnedCount: number, limit: number): PendingCountMeta {
  const summary = resp && typeof resp.summary === "object" ? resp.summary : {};
  const pagination = resp && typeof resp.pagination === "object" ? resp.pagination : {};
  const exactTotal = finiteCount(
    resp?.total_count,
    resp?.totalCount,
    resp?.total,
    summary?.total_count,
    summary?.total,
    pagination?.total_count,
    pagination?.total,
  );
  if (exactTotal != null) {
    return { label: String(exactTotal), kind: "exact", value: exactTotal };
  }

  const legacyCount = finiteCount(resp?.count, returnedCount) ?? returnedCount;
  const explicitHasMore = typeof resp?.has_more === "boolean"
    ? resp.has_more
    : typeof pagination?.has_more === "boolean"
      ? pagination.has_more
      : typeof resp?.truncated === "boolean"
        ? resp.truncated
        : null;

  // Some transitional APIs use `count` as an exact total while still returning
  // a limited `items` array. A value above the requested limit is therefore exact.
  if (legacyCount > limit || explicitHasMore === false) {
    return { label: String(legacyCount), kind: "exact", value: legacyCount };
  }
  if (explicitHasMore === true || legacyCount >= limit || returnedCount >= limit) {
    return { label: `${legacyCount}+`, kind: "lower-bound", value: legacyCount };
  }
  return { label: String(legacyCount), kind: "exact", value: legacyCount };
}

function jumpElement(
  callback: (() => void) | undefined,
  props: Record<string, unknown>,
  ...children: React.ReactNode[]
) {
  return callback
    ? e("button", { ...props, type: "button", onClick: callback }, ...children)
    : e("div", props, ...children);
}

export function TodayFocusStrip({
  apiToken = "",
  onJumpToInbox,
  onJumpToBrief,
}: {
  apiToken?: string;
  onJumpToInbox?: () => void;
  onJumpToBrief?: () => void;
}) {
  const [topItems, setTopItems] = React.useState<any[]>([]);
  const [pendingCount, setPendingCount] = React.useState<PendingCountMeta>(EMPTY_PENDING_COUNT);
  const [inboxAvailable, setInboxAvailable] = React.useState(true);
  const [inboxError, setInboxError] = React.useState("");
  const [headline, setHeadline] = React.useState("");
  const [briefFailed, setBriefFailed] = React.useState(false);
  const [inboxLoading, setInboxLoading] = React.useState(true);
  const [briefLoading, setBriefLoading] = React.useState(true);

  React.useEffect(() => {
    if (!apiToken) {
      setInboxLoading(false);
      setBriefLoading(false);
      return;
    }
    let cancelled = false;
    setTopItems([]);
    setPendingCount(EMPTY_PENDING_COUNT);
    setInboxAvailable(true);
    setInboxError("");
    setHeadline("");
    setBriefFailed(false);
    setInboxLoading(true);
    setBriefLoading(true);

    // 两路独立收口:任一路先回来就先展示,不被另一路拖住。
    void listActionInbox(apiToken, { limit: 20 })
      .then((value) => {
        if (cancelled) return;
        const resp: any = value || {};
        const arr = Array.isArray(resp.items) ? resp.items : [];
        setTopItems(arr.slice(0, 3));
        setPendingCount(resolvePendingCount(resp, arr.length, 20));
        setInboxAvailable(resp.available !== false);
        setInboxError("");
      })
      .catch((error: any) => {
        if (!cancelled) setInboxError(error?.message || "加载失败");
      })
      .finally(() => {
        if (!cancelled) setInboxLoading(false);
      });

    void apiFetch<BriefResp>("/api/admin/vkpi/morning-brief", {}, apiToken)
      .then((b) => {
        if (cancelled) return;
        if (b && typeof b === "object" && String(b.status || "") !== "error") {
          setHeadline(String(b.headline || ""));
          setBriefFailed(false);
        } else {
          setBriefFailed(true);
        }
      })
      .catch(() => {
        if (!cancelled) setBriefFailed(true);
      })
      .finally(() => {
        if (!cancelled) setBriefLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [apiToken]);

  if (!apiToken) return null;

  // ── 待办段:top3 chips(点击直达右栏面板)/ 诚实空态 / 源异常 ──
  let inboxSeg: React.ReactNode;
  if (inboxError || !inboxAvailable) {
    inboxSeg = e(
      "span",
      { className: "text-[10px] text-crit" },
      inboxAvailable ? `建议源异常 · ${inboxError}` : "建议系统待启用",
    );
  } else if (inboxLoading && topItems.length === 0) {
    inboxSeg = e("span", { className: "flex items-center gap-1.5 text-[10px] text-muted" },
      e(Loader2, { size: 12, className: "animate-spin" }),
      "读取待办",
    );
  } else if (topItems.length === 0) {
    inboxSeg = e("span", { className: "text-[10px] text-muted" }, "暂无待办 · 一切已跟进");
  } else {
    inboxSeg = e(
      "div",
      { className: "flex min-w-0 flex-wrap items-center gap-1.5" },
      topItems.map((it: any, index: number) =>
        jumpElement(
          onJumpToInbox,
          {
            key: it.id ?? `${it.category || "action"}-${index}`,
            "data-action": onJumpToInbox ? "jump-inbox" : "summary-only",
            "aria-label": onJumpToInbox ? `查看待办:${it.title || "未命名待办"}` : undefined,
            title: onJumpToInbox
              ? `${it.title || ""} · 点击直达「今日该做什么」`
              : `${it.title || ""} · 展示摘要`,
            className:
              `flex max-w-[220px] items-center gap-1.5 rounded-md border border-line bg-panel px-2 py-1 text-[10px] text-ink-2 ${onJumpToInbox ? "transition-colors hover:border-emerald-500/30 hover:bg-good-soft" : ""}`,
          },
          e("i", {
            className: `h-1.5 w-1.5 shrink-0 rounded-full ${PRIORITY_DOT[it.priority] || PRIORITY_DOT.low}`,
          }),
          e(
            "span",
            { className: "shrink-0 text-[9px] text-muted" },
            CATEGORY_LABEL[it.category] || it.category,
          ),
          e("span", { className: "min-w-0 truncate" }, it.title),
        ),
      ),
    );
  }

  // ── 晨报段:headline 一句话(点击直达右栏晨报卡)/ 失败如实 ──
  const briefSeg = briefFailed
    ? e("span", { className: "text-[10px] text-muted" }, "-- 晨报读取失败")
    : headline
      ? jumpElement(
          onJumpToBrief,
          {
            "data-action": onJumpToBrief ? "jump-brief" : "summary-only",
            "aria-label": onJumpToBrief ? "查看夜班晨报" : undefined,
            title: onJumpToBrief ? "点击直达夜班晨报" : "夜班晨报摘要",
            className:
              `group flex min-w-0 items-center gap-1.5 text-left text-[10px] text-ink-2 ${onJumpToBrief ? "transition-colors hover:text-ink" : ""}`,
          },
          e(Sunrise, { size: 11, className: "shrink-0 text-good" }),
          e("span", { className: "min-w-0 truncate" }, headline),
          onJumpToBrief ? e(ArrowRight, { size: 10, className: "shrink-0 text-muted group-hover:text-ink-2" }) : null,
        )
      : briefLoading
        ? e("span", { className: "flex items-center gap-1.5 text-[10px] text-muted" },
            e(Loader2, { size: 11, className: "animate-spin" }),
            "读取晨报",
          )
        : e("span", { className: "text-[10px] text-muted" }, "晨报暂无 headline");

  return e(
    "section",
    {
      "data-state": inboxError || briefFailed ? "degraded" : inboxLoading || briefLoading ? "loading" : "ready",
      "aria-label": "今日焦点",
      className:
        "grid gap-2 rounded-xl border border-emerald-500/15 bg-good-soft px-3 py-2 backdrop-blur-xl lg:grid-cols-[auto_minmax(0,1fr)_minmax(220px,0.8fr)] lg:items-center",
    },
    // 标头 + 待办计数(点击直达右栏面板)
    jumpElement(
      onJumpToInbox,
      {
        "data-action": onJumpToInbox ? "jump-inbox" : "summary-only",
        "aria-label": onJumpToInbox ? `查看全部待办,当前 ${pendingCount.label}` : undefined,
        title: pendingCount.kind === "lower-bound"
          ? `待办至少 ${pendingCount.value ?? 0} 条${onJumpToInbox ? "，点击直达面板" : ""}`
          : `待办 ${pendingCount.label} 条${onJumpToInbox ? "，点击直达面板" : ""}`,
        className: `flex shrink-0 items-center gap-1.5 ${onJumpToInbox ? "transition-opacity hover:opacity-80" : ""}`,
      },
      e(Target, { size: 13, className: "text-good" }),
      e("span", { className: "text-[11px] font-semibold text-ink" }, "今日焦点"),
      e(
        "span",
        {
          className:
            "rounded-full border border-emerald-500/25 bg-good-soft px-1.5 py-px text-[9px] tabular-nums text-good",
          "data-count-kind": pendingCount.kind,
        },
        `待办 ${pendingCount.label}`,
      ),
      inboxLoading ? e(Loader2, { size: 10, className: "animate-spin text-muted" }) : null,
    ),
    e("div", { className: "min-w-0 border-t border-white/[0.06] pt-2 lg:border-l lg:border-t-0 lg:pl-3 lg:pt-0", "aria-label": "优先待办摘要" },
      e("div", { className: "mb-1 text-[9px] uppercase tracking-[0.12em] text-muted" }, `优先待办${topItems.length ? ` · 展示 ${topItems.length}` : ""}`),
      inboxSeg,
    ),
    e("div", { className: "min-w-0 border-t border-white/[0.06] pt-2 lg:border-l lg:border-t-0 lg:pl-3 lg:pt-0", "aria-label": "夜班晨报摘要" },
      e("div", { className: "mb-1 text-[9px] uppercase tracking-[0.12em] text-muted" }, "夜班晨报"),
      briefSeg,
    ),
  );
}
