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
  const [pendingCount, setPendingCount] = React.useState<number | null>(null);
  const [inboxAvailable, setInboxAvailable] = React.useState(true);
  const [inboxError, setInboxError] = React.useState("");
  const [headline, setHeadline] = React.useState("");
  const [briefFailed, setBriefFailed] = React.useState(false);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    if (!apiToken) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    // allSettled:晨报挂了不影响待办,反之亦然(各自诚实降级)。
    void Promise.allSettled([
      listActionInbox(apiToken, { limit: 20 }),
      apiFetch<BriefResp>("/api/admin/vkpi/morning-brief", {}, apiToken),
    ]).then(([inboxRes, briefRes]) => {
      if (cancelled) return;
      if (inboxRes.status === "fulfilled") {
        const resp: any = inboxRes.value || {};
        const arr = Array.isArray(resp.items) ? resp.items : [];
        setTopItems(arr.slice(0, 3));
        // count = 本次返回条数(受 limit=20 截断);够用作「待办计数」,超限如实标 20+。
        setPendingCount(typeof resp.count === "number" ? resp.count : arr.length);
        setInboxAvailable(resp.available !== false);
        setInboxError("");
      } else {
        setInboxError((inboxRes.reason as any)?.message || "加载失败");
      }
      if (briefRes.status === "fulfilled") {
        const b = briefRes.value;
        if (b && typeof b === "object" && String(b.status || "") !== "error") {
          setHeadline(String(b.headline || ""));
          setBriefFailed(false);
        } else {
          setBriefFailed(true);
        }
      } else {
        setBriefFailed(true);
      }
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [apiToken]);

  if (!apiToken) return null;

  const countCapped = pendingCount != null && pendingCount >= 20;
  const countLabel = pendingCount == null ? "--" : countCapped ? "20+" : String(pendingCount);

  // ── 待办段:top3 chips(点击直达右栏面板)/ 诚实空态 / 源异常 ──
  let inboxSeg: React.ReactNode;
  if (inboxError || !inboxAvailable) {
    inboxSeg = e(
      "span",
      { className: "text-[10px] text-crit" },
      inboxAvailable ? `建议源异常 · ${inboxError}` : "建议系统待启用",
    );
  } else if (loading && topItems.length === 0) {
    inboxSeg = e(Loader2, { size: 12, className: "animate-spin text-muted" });
  } else if (topItems.length === 0) {
    inboxSeg = e("span", { className: "text-[10px] text-muted" }, "暂无待办 · 一切已跟进");
  } else {
    inboxSeg = e(
      "div",
      { className: "flex min-w-0 flex-wrap items-center gap-1.5" },
      topItems.map((it: any) =>
        e(
          "button",
          {
            key: it.id,
            type: "button",
            onClick: onJumpToInbox,
            title: `${it.title || ""} · 点击直达「今日该做什么」`,
            className:
              "flex max-w-[220px] items-center gap-1.5 rounded-md border border-line bg-panel px-2 py-1 text-[10px] text-ink-2 transition-colors hover:border-emerald-500/30 hover:bg-good-soft",
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
      ? e(
          "button",
          {
            type: "button",
            onClick: onJumpToBrief,
            title: "点击直达夜班晨报",
            className:
              "group flex min-w-0 items-center gap-1.5 text-left text-[10px] text-ink-2 transition-colors hover:text-ink",
          },
          e(Sunrise, { size: 11, className: "shrink-0 text-good" }),
          e("span", { className: "min-w-0 truncate" }, headline),
          e(ArrowRight, { size: 10, className: "shrink-0 text-muted group-hover:text-ink-2" }),
        )
      : loading
        ? null
        : e("span", { className: "text-[10px] text-muted" }, "晨报暂无 headline");

  return e(
    "div",
    {
      className:
        "flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-xl border border-emerald-500/15 bg-good-soft px-3 py-2 backdrop-blur-xl",
    },
    // 标头 + 待办计数(点击直达右栏面板)
    e(
      "button",
      {
        type: "button",
        onClick: onJumpToInbox,
        title: "点击直达「今日该做什么」面板",
        className: "flex shrink-0 items-center gap-1.5 transition-opacity hover:opacity-80",
      },
      e(Target, { size: 13, className: "text-good" }),
      e("span", { className: "text-[11px] font-semibold text-ink" }, "今日焦点"),
      e(
        "span",
        {
          className:
            "rounded-full border border-emerald-500/25 bg-good-soft px-1.5 py-px text-[9px] tabular-nums text-good",
        },
        `待办 ${countLabel}`,
      ),
      loading ? e(Loader2, { size: 10, className: "animate-spin text-muted" }) : null,
    ),
    e("span", { className: "hidden h-3 w-px bg-white/[0.08] sm:block" }),
    inboxSeg,
    briefSeg ? e("span", { className: "hidden h-3 w-px bg-white/[0.08] sm:block" }) : null,
    briefSeg,
  );
}
