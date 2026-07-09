import React from "react";
import {
  ArrowRight,
  Compass,
  Database,
  GitCommit,
  HeartPulse,
  ListChecks,
  Route,
  Server,
  Sparkles,
  Timer,
} from "lucide-react";
import { useCachedGet } from "../../../../hooks/useCachedGet";
import { Card, Empty, LearningBlock, confBadge } from "./GtmCommandPage.Sections";
import type { ActionInboxResponse } from "../../../../services/vkpi/actionInbox-api";
import type {
  GtmGoal,
  GtmPlanPreview,
  LearningDigest,
  MarketBrainSummary,
} from "../../../../services/vkpi/gtmCommand-api";

// Gate D · 增长指挥台(纯读展示层):把 GTM Command 首页从「卡片分析页」上提为
//   「路线 / 动作 / 学习 / 健康」四层指挥台 —— 聪明藏系统内,界面只展示决策与状态。
//   全部 React.createElement 风格;新增只读请求一律走 useCachedGet(切 tab 不闪、并发去重);
//   单卡错不拖垮整页(每块自判空 + 失败安静);零写库、零 LLM、不碰 v6_fit/rule_v0。
//   数据源全部复用既有端点,不新建后端:
//     · 健康条 HealthStrip ← GET /health(trust) + /api/admin/runtime/metrics + scheduler-tasks
//     · 增长路线 GrowthRouteLayer ← market-brain/summary(全局机会)或 gtm-plan/preview(SKU 路线)
//     · 执行队列 ExecutionQueueLayer ← actions/inbox(按类型聚合,只读 + 跳转 Inbox)
//     · 学习沉淀 LearningLayer ← market-brain/summary.learning_digest
//     · Bet 生命周期 BetTimeline ← preview 态七段进度(无数据段灰显,不编造)

const e = React.createElement;

const GOAL_LABEL: Record<string, string> = {
  exposure: "曝光",
  conversion: "转化",
  content: "素材",
  channel: "渠道铺货",
};

function asRow(v: unknown): Record<string, any> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, any>) : {};
}

function shortSha(value: unknown): string {
  const s = String(value ?? "").trim();
  return s ? s.slice(0, 7) : "--";
}

// ---------------------------------------------------------------------------
// ① Health Strip:顶部窄条,SHA/迁移/Worker/调度/留痕 五点;全部只读、失败安静。
// ---------------------------------------------------------------------------

function HealthChip({
  icon,
  label,
  children,
  tone,
}: {
  icon: any;
  label: string;
  children?: React.ReactNode;
  tone?: string;
}) {
  return e(
    "div",
    { className: "flex items-center gap-1.5 px-1" },
    e(icon, { size: 12, className: tone || "text-muted", strokeWidth: 2 }),
    e(
      "div",
      { className: "min-w-0 leading-none" },
      e("div", { className: "text-[8.5px] uppercase tracking-wide text-muted mb-0.5" }, label),
      e("div", { className: "text-[10.5px] text-ink-2 truncate" }, children),
    ),
  );
}

export function HealthStrip({ apiToken = "" }: { apiToken?: string }) {
  const enabled = Boolean(apiToken);
  const health = useCachedGet<{ trust?: Record<string, any> }>("/health", { token: apiToken, enabled, ttl: 60000 });
  const scheduler = useCachedGet<{ status?: Record<string, any> }>(
    "/api/admin/vkpi/settings/scheduler-tasks",
    { token: apiToken, enabled, ttl: 60000 },
  );
  const metrics = useCachedGet<{ requests_persisted?: Record<string, any> }>(
    "/api/admin/runtime/metrics",
    { token: apiToken, enabled, ttl: 60000 },
  );

  const trust = asRow(health.data?.trust);
  const aligned = trust.sha_aligned;
  const alignTone = aligned === true ? "text-good" : aligned === false ? "text-crit" : "text-warn";
  const alignLabel = aligned === true ? "对齐" : aligned === false ? "不一致" : "待确认";

  const workerOnline = trust.worker_online;
  const workerTone = workerOnline === true ? "text-good" : workerOnline === false ? "text-crit" : "text-muted";
  const workerLabel = workerOnline === true ? "在线" : workerOnline === false ? "离线" : "未知";

  const sched = asRow(scheduler.data?.status);
  const schedTotal = typeof sched.total === "number" ? sched.total : null;
  const schedEnabled = typeof sched.enabled === "number" ? sched.enabled : null;
  const schedText = schedTotal == null ? "待接入" : `${schedEnabled ?? 0}/${schedTotal} 启用`;

  const persisted = asRow(metrics.data?.requests_persisted);
  const persistedText = persisted.available === true ? "已留痕" : "待接入";
  const persistedTone = persisted.available === true ? "text-accent" : "text-muted";

  return e(
    "div",
    {
      className:
        "flex flex-wrap items-center gap-x-3 gap-y-1.5 divide-x divide-white/[0.06] rounded-xl border border-line bg-panel px-3 py-1.5",
    },
    e(
      "div",
      { className: "flex items-center gap-1.5 pr-1" },
      e(HeartPulse, { size: 13, className: "text-violet-400", strokeWidth: 2 }),
      e("span", { className: "text-[10.5px] font-medium text-ink-2" }, "系统健康"),
      (health.refreshing || scheduler.refreshing || metrics.refreshing)
        ? e("span", { className: "inline-block h-1 w-1 rounded-full bg-accent-soft/70 animate-pulse", title: "后台刷新中" })
        : null,
    ),
    e(HealthChip, { icon: GitCommit, label: "版本", tone: alignTone }, e(
      "span",
      { className: "flex items-center gap-1" },
      e("span", { className: alignTone }, alignLabel),
      e("span", { className: "font-mono text-[9.5px] text-muted", title: String(trust.server_git_sha ?? "") }, shortSha(trust.server_git_sha)),
    )),
    e(HealthChip, { icon: Database, label: "迁移" }, String(trust.db_migration_max ?? "--")),
    e(HealthChip, { icon: Server, label: "Worker", tone: workerTone }, e("span", { className: workerTone }, workerLabel)),
    e(HealthChip, { icon: Timer, label: "调度" }, schedText),
    e(HealthChip, { icon: HeartPulse, label: "留痕", tone: persistedTone }, e("span", { className: persistedTone }, persistedText)),
  );
}

// ---------------------------------------------------------------------------
// ② Growth Route Layer:今日增长路线。无 SKU → 全局 Top 机会;有 preview → 路线链。
// ---------------------------------------------------------------------------

function RouteSeg({ label, value }: { label: string; value: string }) {
  return e(
    "div",
    { className: "min-w-[92px] rounded-xl border border-line bg-panel px-3 py-2" },
    e("div", { className: "text-[9px] uppercase tracking-wide text-muted" }, label),
    e("div", { className: "mt-0.5 text-[12px] font-medium text-ink truncate" }, value || "—"),
  );
}

function channelMixLabel(plan: GtmPlanPreview): string {
  const p = plan.public_plan;
  const parts: string[] = [];
  if (p.kol_candidates.items.length > 0) parts.push("KOL");
  if (p.dealer_targets.items.length > 0) parts.push("Dealer");
  if (p.official_channel_actions.items.length > 0) parts.push("官号");
  if (p.shopify_indie_site_actions.items.length > 0) parts.push("独立站");
  if (p.content_angles.items.length > 0) parts.push("内容");
  return parts.length > 0 ? parts.join(" · ") : "渠道待定";
}

function PreviewRoute({
  selectedSku,
  plan,
  budgetText,
  goal,
}: {
  selectedSku: string;
  plan: GtmPlanPreview;
  budgetText: string;
  goal: GtmGoal;
}) {
  const p = plan.public_plan;
  const budget = Number(budgetText) > 0 ? Number(budgetText) : 3000;
  const arrow = e(ArrowRight, { size: 13, className: "shrink-0 self-center text-muted" });
  return e(
    "div",
    null,
    // 决策 + 置信度头部(go_nogo 带前缀,避免与 ① 主判断卡的裸值重复)。
    e(
      "div",
      { className: "mb-2 flex flex-wrap items-center gap-2" },
      e(
        "span",
        { className: "rounded-md border border-accent bg-accent-soft px-2 py-0.5 text-[11px] text-accent" },
        `决策 ${p.thesis.go_nogo || "—"}`,
      ),
      confBadge(p.thesis.confidence),
    ),
    // 路线链:SKU → 市场 → 渠道组合 → 预算 → 打法。
    e(
      "div",
      { className: "flex flex-wrap items-stretch gap-1.5" },
      e(RouteSeg, { label: "SKU", value: selectedSku }),
      arrow,
      e(RouteSeg, { label: "市场", value: p.thesis.market }),
      arrow,
      e(RouteSeg, { label: "渠道组合", value: channelMixLabel(plan) }),
      arrow,
      e(RouteSeg, { label: "预算", value: `$${budget.toLocaleString()} · ${GOAL_LABEL[goal] || goal}` }),
      arrow,
      e(RouteSeg, { label: "打法", value: p.thesis.mainline }),
    ),
    // 风险(合并成一句,避免与脚注区裸风险标签重复)。
    p.risks.length > 0
      ? e(
          "div",
          { className: "mt-2 text-[10.5px] leading-relaxed text-warn" },
          `风险 ${p.risks.length} 项 · ${p.risks.slice(0, 4).join("、")}`,
        )
      : null,
  );
}

function GlobalRoute({
  summary,
  onPickSku,
}: {
  summary: MarketBrainSummary | null;
  onPickSku?: (sku: string) => void;
}) {
  const items = summary?.product_opportunities.items ?? [];
  if (items.length === 0) {
    return e(Empty, {
      text: summary?.product_opportunities.note || "暂无成型增长机会 —— 选一个 SKU 生成专属路线(诚实空态)。",
    });
  }
  return e(
    "div",
    { className: "space-y-1.5" },
    items.slice(0, 5).map((o, i) =>
      e(
        "div",
        {
          key: i,
          className: "flex flex-wrap items-center gap-2 rounded-xl border border-line bg-panel px-3 py-2",
        },
        e("span", { className: "text-[9px] tabular-nums text-muted" }, `#${i + 1}`),
        e("span", { className: "text-[12px] font-medium text-ink" }, o.sku || "—"),
        o.market ? e("span", { className: "text-[10.5px] text-muted" }, `@ ${o.market}`) : null,
        o.persona ? e("span", { className: "text-[10px] text-muted" }, `· ${o.persona}`) : null,
        o.opportunity_score != null
          ? e("span", { className: "text-[10px] tabular-nums text-accent" }, `机会分 ${o.opportunity_score}`)
          : null,
        onPickSku && o.sku
          ? e(
              "button",
              {
                type: "button",
                onClick: () => onPickSku(o.sku),
                className:
                  "ml-auto flex items-center gap-1 rounded-lg border border-accent bg-accent-soft px-2 py-1 text-[10px] text-accent hover:bg-accent-soft",
              },
              "生成路线",
              e(ArrowRight, { size: 11 }),
            )
          : null,
      ),
    ),
  );
}

export function GrowthRouteLayer({
  summary,
  plan,
  selectedSku,
  budgetText,
  goal,
  onPickSku,
}: {
  summary: MarketBrainSummary | null;
  plan: GtmPlanPreview | null;
  selectedSku: string;
  budgetText: string;
  goal: GtmGoal;
  onPickSku?: (sku: string) => void;
}) {
  const preview = Boolean(plan);
  return e(
    Card,
    {
      title: preview ? `今日增长路线 · ${selectedSku}` : "今日增长路线 · 全局机会",
      hint: preview
        ? "SKU → 市场 → 渠道组合 → 预算 → 打法 · 一条可执行的作战线(详情见下方深度分析)"
        : "全库内既有数据里最值得推的机会 · 点「生成路线」出专属作战线",
      extra: e(Route, { size: 15, className: "shrink-0 text-accent" }),
    },
    plan
      ? e(PreviewRoute, { selectedSku, plan, budgetText, goal })
      : e(GlobalRoute, { summary, onPickSku }),
  );
}

// ---------------------------------------------------------------------------
// ③ Execution Queue Layer:今日执行队列(actions/inbox 按类型聚合,只读 + 跳转)。
// ---------------------------------------------------------------------------

const QUEUE_LABEL: Record<string, string> = {
  kol_discovery: "找 KOL",
  kol_profile: "补全资料",
  contact_enrich: "补联系",
  outreach: "催合作",
  deep_missing: "深析待跑",
  project_observation: "开观察窗",
  failed_retry: "失败重试",
  content_candidate: "内容确认",
  retrospective: "项目复盘",
  event_followup: "活动收尾",
  inventory_low: "库存预警",
  dealer_followup: "Dealer 跟进",
  project_shared_to_you: "项目共享",
};

export function ExecutionQueueLayer({
  apiToken = "",
  onNavigate,
}: {
  apiToken?: string;
  onNavigate?: (navKey: string) => void;
}) {
  const enabled = Boolean(apiToken);
  const inbox = useCachedGet<ActionInboxResponse>(
    "/api/admin/vkpi/actions/inbox?limit=50",
    { token: apiToken, enabled, ttl: 30000 },
  );
  const data = inbox.data;
  const items = Array.isArray(data?.items) ? data!.items : [];
  const available = data?.available !== false;

  // 按类型聚合计数(只读展示,不重做 Inbox)。
  const byCat = new Map<string, number>();
  for (const it of items) {
    const cat = String((it as any)?.category || "other");
    byCat.set(cat, (byCat.get(cat) ?? 0) + 1);
  }
  const buckets = Array.from(byCat.entries()).sort((a, b) => b[1] - a[1]);
  const jump = () => onNavigate?.("dashboard");

  let body: React.ReactNode;
  if (inbox.error) {
    body = e("div", { className: "text-[11px] text-crit" }, `建议源异常 · ${inbox.error}`);
  } else if (!available) {
    body = e(Empty, { text: "建议系统待启用(后端未就绪,诚实空态)。" });
  } else if (inbox.loading && items.length === 0) {
    body = e("div", { className: "py-2 text-[11px] text-muted" }, "队列读取中…");
  } else if (buckets.length === 0) {
    body = e(Empty, { text: "暂无待执行动作 · 一切已跟进(诚实空态)。" });
  } else {
    body = e(
      "div",
      { className: "flex flex-wrap gap-1.5" },
      buckets.map(([cat, count]) =>
        e(
          "button",
          {
            key: cat,
            type: "button",
            onClick: jump,
            title: "点击去 Action Inbox 人审执行",
            className:
              "flex items-center gap-1.5 rounded-lg border border-line bg-panel px-2.5 py-1.5 text-[11px] text-ink-2 hover:border-good hover:bg-good-soft",
          },
          e("span", null, QUEUE_LABEL[cat] || cat),
          e("span", { className: "rounded-full bg-panel px-1.5 py-px text-[9.5px] tabular-nums text-ink-2" }, String(count)),
        ),
      ),
    );
  }

  const todayRow = asRow(data?.today_summary);
  const executedToday = typeof todayRow.today_executed_count === "number" ? todayRow.today_executed_count : null;

  return e(
    Card,
    {
      title: "今日执行队列",
      hint: "按类型聚合的待办动作 · 只读展示,逐条人审执行在 Action Inbox 完成",
      extra: e(ListChecks, { size: 15, className: "shrink-0 text-good" }),
    },
    body,
    buckets.length > 0
      ? e(
          "div",
          { className: "mt-2 flex flex-wrap items-center gap-2" },
          e(
            "button",
            {
              type: "button",
              onClick: jump,
              className:
                "flex items-center gap-1 rounded-lg border border-good bg-good-soft px-2.5 py-1 text-[10.5px] text-good hover:bg-good-soft",
            },
            "去 Action Inbox 人审",
            e(ArrowRight, { size: 11 }),
          ),
          executedToday != null
            ? e("span", { className: "text-[9.5px] text-muted" }, `今日已执行 ${executedToday} 条`)
            : null,
        )
      : null,
  );
}

// ---------------------------------------------------------------------------
// ④ Learning Layer:学习沉淀(复用 learning_digest;诚实空态)。
// ---------------------------------------------------------------------------

export function LearningLayer({ digest }: { digest: LearningDigest }) {
  return e(
    Card,
    {
      title: "本周学习沉淀",
      hint: "有效风格 / 失效假设 / 权重变化 —— 复盘账本口径,可被新证据推翻",
      extra: e(Sparkles, { size: 15, className: "shrink-0 text-warn" }),
    },
    e(LearningBlock, { digest, footnote: "指挥台聚合 · 全局复盘账本口径(样本不足则诚实空态)" }),
  );
}

// ---------------------------------------------------------------------------
// ⑤ Bet Lifecycle Timeline:idea→preview→materialized→assigned→done→verdict→learned。
//   preview 态点亮到 preview,其后段无数据灰显(不编造进度)。
// ---------------------------------------------------------------------------

const BET_STAGES = [
  { key: "idea", label: "构想" },
  { key: "preview", label: "预览" },
  { key: "materialized", label: "落库" },
  { key: "assigned", label: "指派" },
  { key: "done", label: "执行" },
  { key: "verdict", label: "裁决" },
  { key: "learned", label: "沉淀" },
];

export function BetTimeline({ plan }: { plan: GtmPlanPreview | null }) {
  // preview 已生成 → 到「预览」段为止确凿;materialize/assign/done/verdict/learned
  // 由 Action Inbox 人审逐段推进,preview 端点不含其字段 → 灰显(诚实,不假装进度)。
  const reachedIdx = plan ? 1 : 0;
  return e(
    Card,
    {
      title: "Bet 生命周期",
      hint: "一个增长押注从构想到沉淀的七段进度;灰段随人审在 Action Inbox 逐步点亮",
      extra: e(Compass, { size: 15, className: "shrink-0 text-muted" }),
    },
    e(
      "div",
      { className: "flex flex-wrap items-center gap-1" },
      BET_STAGES.map((s, i) => {
        const lit = i <= reachedIdx;
        const seg = e(
          "div",
          {
            key: s.key,
            className:
              "flex flex-col items-center gap-1 rounded-lg border px-2 py-1 " +
              (lit
                ? "border-good bg-good-soft"
                : "border-line bg-panel"),
          },
          e("div", {
            className: "h-1 w-8 rounded-full " + (lit ? "bg-good-soft/80" : "bg-panel"),
          }),
          e("span", { className: "text-[9.5px] " + (lit ? "text-good" : "text-muted") }, s.label),
        );
        if (i === BET_STAGES.length - 1) return seg;
        return e(
          React.Fragment,
          { key: `${s.key}-wrap` },
          seg,
          e(ArrowRight, { size: 11, className: "shrink-0 self-start mt-1.5 text-muted" }),
        );
      }),
    ),
    e(
      "div",
      { className: "mt-2 text-[9.5px] leading-relaxed text-muted" },
      plan
        ? "已到「预览」段 · 落库(materialize)后进 Action Inbox,后续段随逐条人审推进点亮。"
        : "选 SKU 生成作战预览后,时间线从「构想」推进到「预览」。",
    ),
  );
}

// ---------------------------------------------------------------------------
// CommandDeck:四层指挥台合体(路线 / 执行 / 学习 / Bet 生命周期)。
//   preview 态多出 Bet 生命周期段;每块自判空,单块错不拖垮整页。
// ---------------------------------------------------------------------------

export function CommandDeck({
  apiToken = "",
  summary,
  plan,
  selectedSku,
  budgetText,
  goal,
  digest,
  onPickSku,
  onNavigate,
}: {
  apiToken?: string;
  summary: MarketBrainSummary | null;
  plan: GtmPlanPreview | null;
  selectedSku: string;
  budgetText: string;
  goal: GtmGoal;
  digest: LearningDigest;
  onPickSku?: (sku: string) => void;
  onNavigate?: (navKey: string) => void;
}) {
  return e(
    "div",
    { className: "space-y-4" },
    e(GrowthRouteLayer, { summary, plan, selectedSku, budgetText, goal, onPickSku }),
    e(
      "div",
      { className: "grid grid-cols-1 gap-4 xl:grid-cols-2" },
      e(ExecutionQueueLayer, { apiToken, onNavigate }),
      e(LearningLayer, { digest }),
    ),
    plan ? e(BetTimeline, { plan }) : null,
  );
}
