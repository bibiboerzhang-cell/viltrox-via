import React from "react";
import { ArrowRight } from "lucide-react";
import { EmptyLine, ErrorCard, KpiCard, PendingCard } from "./MarketVoicePage.modules";
import { confBadge } from "./GtmCommandPage.Sections";
import type { ActionInboxItem, ActionInboxResponse } from "../../../../services/vkpi/actionInbox-api";
import type {
  GtmGoal,
  GtmPlanPreview,
  LearningDigest,
  MarketBrainSummary,
} from "../../../../services/vkpi/gtmCommand-api";

// GTM Command · 板块页范式改版 —— 模块口径注册表 + KPI 带 + 全局模块 body 族。
//   金样板 = MarketVoicePage.modules / MyKolBoardPage.modules 同构:MODULE_SOURCES
//   一份注册表(label=真实表名/端点,rows=口径;行数为 2026-07-12 只读 PG 实测),
//   SrcChip 与溯源弹窗共用,零第二份口径。卡面零介绍性文案(验收纪律):旧页的
//   hint 句/方法论句全部收进本注册表,功能靠标签自明。
//   数据:page 层注入(summary / plan / actions-inbox),本文件零直连网络。
// 红线:纯读展示;不触 viltrox_fit_score / rule_v0;绝不调用 marketing-brain/daily
//   与 market/trends 的 GET(总脑纯读红线,两端点有隐藏写入 —— 本页全族绕开);
//   颜色全 token 零写死色;零 opacity 修饰类;数字全真源,空态诚实短句。

export type Row = Record<string, any>;

/* ============ 模块口径注册表(SrcChip label=真源;行数=2026-07-12 只读实测) ============ */

export const MODULE_SOURCES: Record<string, { label: string; rows: Array<[string, string]> }> = {
  kpiG: {
    label: "market-brain/summary · vkpi_action_inbox",
    rows: [
      ["方法", "纯读聚合既有数据 · 零采集零写库 · 同输入同输出"],
      ["本周信号", "market-brain/summary weekly_signals(brand pulse / 赛道 / 市场之声内部聚合;外部雷达 GTM-5 待接)"],
      ["产品机会", "product_opportunities(vkpi_products 369 行 × 赛道位次 × 内容表现)"],
      ["待执行动作", "vkpi_action_inbox 291 行(近 50 条 suggested 窗口计数,非全量)"],
      ["增长押注", "vkpi_action_inbox category=gtm_bet 20 行(18 suggested / 1 approved / 1 executed)"],
      ["趋势线", "GTM 域无按日时序端点 —— 四卡虚线如实待接,不编序列"],
    ],
  },
  route: {
    label: "market-brain/summary · gtm-plan/preview",
    rows: [
      ["全局态", "product_opportunities Top5 · 点「生成路线」直出该 SKU 作战预览"],
      ["预览态", "public_plan.thesis 路线链:SKU → 市场 → 渠道组合 → 预算 → 打法"],
      ["红线", "gtm-plan/preview 纯读零写库;marketing-brain/daily 与 market/trends GET 有隐藏写入,本页全族绕开"],
    ],
  },
  signals: {
    label: "market-brain/summary · weekly_signals",
    rows: [
      ["源", "brand pulse / 赛道 / 市场之声(库内既有数据聚合)"],
      ["逐条", "freshness / 样本量 / 置信随行透出,缺失如实缺席"],
      ["盲区", "外部信号雷达 GTM-5 待接入"],
    ],
  },
  opps: {
    label: "vkpi_products · 赛道位次聚合",
    rows: [
      ["口径", "赛道位次 × 内容表现 × 人群画像(vkpi_products 369 行)"],
      ["机会分", "规则聚合打分,依据随行透出"],
    ],
  },
  actions: {
    label: "vkpi_action_inbox · suggested",
    rows: [
      ["口径", "六要素行:原因 / 证据摘要 / 成本 / 风险 / 预计收益 / ref"],
      ["源", "action inbox suggested + 超期寄样 + 待深析"],
      ["执行", "逐条人审执行在 Action Inbox 完成(GTM-3 接线)"],
    ],
  },
  queue: {
    label: "vkpi_action_inbox(291 行 · 2026-07-12 实测)",
    rows: [
      ["口径", "近 50 条 suggested 按类型聚合计数(只读)"],
      ["执行", "本页零执行 —— 点击跳 Action Inbox 逐条人审"],
      ["今日已执行", "today_summary.today_executed_count 真值"],
    ],
  },
  learn: {
    label: "vkpi_prediction_runs 433 行 · vkpi_gtm_outcomes 0 行",
    rows: [
      ["源", "prediction ledger(vkpi_prediction_runs 433 行,至 2026-07-12)"],
      ["复盘", "vkpi_prediction_evals 0 行 · vkpi_gtm_outcomes 0 行 —— 已建未跑,如实空"],
      ["口径", "全局复盘账本(preview 无 SKU 级学习段);结论可被新证据推翻"],
    ],
  },
  health: {
    label: "/health trust · runtime/metrics · scheduler-tasks",
    rows: [
      ["版本", "/health trust.sha_aligned + server_git_sha"],
      ["迁移", "trust.db_migration_max"],
      ["Worker / 调度", "trust.worker_online · scheduler-tasks status(启用/总数)"],
      ["留痕", "runtime/metrics requests_persisted.available"],
    ],
  },
  northstar: {
    label: "vkpi_product_launches 1 行 · vkpi_dealers 0 行",
    rows: [
      ["口径", "90 天北极星:Launch Brief / Dealer / 裁决率,真库现查,表缺诚实 0"],
      ["实测", "vkpi_product_launches 1 行(active)· vkpi_dealers 0 行(未导入)· vkpi_gtm_outcomes 0 行(2026-07-12)"],
    ],
  },
  bets: {
    label: "vkpi_bet_ledger 2 行 · vkpi_action_inbox gtm_bet",
    rows: [
      ["台账", "vkpi_bet_ledger 2 行(1 open / 1 won,2026-07-12 实测)"],
      ["七段", "构想→预览→落库→指派→执行→裁决→沉淀;preview 态确凿到「预览」,其后随 Action Inbox 人审逐段点亮,不编进度"],
    ],
  },
  thesis: {
    label: "gtm-plan/preview · public_plan.thesis",
    rows: [
      ["口径", "该不该推 / 押哪个市场 / 主打人群 / 主线打法(规则聚合,零模型调用)"],
      ["显示层", "只吃 public_plan 白名单键;score_details / raw_* / 竞品笔记等 private 字段 api 层深度剥除"],
    ],
  },
  forecast: {
    label: "gtm-plan/preview · forecast",
    rows: [
      ["口径", "条件化四段式:预判 / 依据+置信 / 触发加码 / 撤退条件 —— 条件成立才行动,非结果承诺"],
      ["缺失", "缺触发/撤退条件 = 如实标「条件缺失」,不许装完整"],
    ],
  },
  roadmap: {
    label: "gtm-plan/preview · roadmap + 渠道五段",
    rows: [
      ["节奏", "W1 / W2-4 / M2-3 三段推进;后端缺段不编节奏"],
      ["渠道", "KOL 候选(风险只出标签)/ Dealer / 官号 / 独立站 / 内容角度"],
    ],
  },
  todayActions: {
    label: "gtm-plan/materialize · vkpi_action_inbox",
    rows: [
      ["预演", "dry-run 零写库,只出 bet 预览 + 幂等对账(would_insert / already_present)"],
      ["落库", "显式确认才写 vkpi_action_inbox,逐条 requires_approval 人审,无自动执行"],
      ["权限", "确认落库钮 manager/owner 可见;后端 vkpi:write 403 兜底"],
    ],
  },
  footnote: {
    label: "gtm-plan/preview · meta",
    rows: [
      ["内容", "风险标签 / 数据缺口 / 成功指标(规则库口径,可被自有数据推翻)/ 覆盖度"],
      ["时间", "generated_at 存 UTC,按浏览器时区显示"],
    ],
  },
  sim: {
    label: "/strategy/simulate · 决定性模拟",
    rows: [
      ["口径", "选 SKU + 预算 → 三策略并排(头部集中 / 长尾铺量 / 混合 50/50)"],
      ["方法", "零模型调用决定性模拟,数字全带 basis 可追溯"],
    ],
  },
  dealer: {
    label: "/channel/dealer-fit · vkpi_dealers 0 行",
    rows: [
      ["口径", "geo / category 适配打分"],
      ["实测", "vkpi_dealers 0 行(2026-07-12)—— Dealer 导入未点火,空态为真"],
    ],
  },
  officialPlan: {
    label: "/channel/official-plan · 官号快照",
    rows: [
      ["口径", "官号内容计划:逐账号近期表现 → 建议形式;官号 0 / 帖子 0 如实标注"],
    ],
  },
  indie: {
    label: "/channel/indie-site-actions · vkpi_products + Shopify",
    rows: [
      ["口径", "承接就绪 checklist;本地 0 Shopify 订单 → data_missing 如实标注"],
    ],
  },
  mix: {
    label: "/channel/mix · Binet-Field 60/40",
    rows: [
      ["口径", "品牌 / 转化两层预算配比(规则库),按 SKU + 预算 + 目标出建议"],
    ],
  },
};

export const GTM_PROV_TITLES: Record<string, string> = {
  kpiG: "指挥台总览",
  route: "今日增长路线",
  signals: "本周信号",
  opps: "产品机会",
  actions: "建议行动",
  queue: "今日执行队列",
  learn: "复盘学习",
  health: "系统健康",
  northstar: "90 天北极星",
  bets: "Bet 生命周期",
  thesis: "主判断",
  forecast: "条件化预判",
  roadmap: "增长路线图",
  todayActions: "今日行动",
  footnote: "风险与缺口",
  sim: "策略模拟器",
  dealer: "Dealer 适配",
  officialPlan: "官号内容计划",
  indie: "独立站承接",
  mix: "渠道组合",
};

/* ============ KPI 带四卡(现值全真;GTM 域无时序端点 → 四卡诚实虚线) ============ */

export function GtmKpiBand({
  summary,
  inbox,
  inboxError,
}: {
  summary: MarketBrainSummary;
  /** actions/inbox 近 50 条窗口(与执行队列同请求同缓存,零重复拉取);null=未就绪 */
  inbox: ActionInboxResponse | null;
  inboxError: string;
}) {
  const items: ActionInboxItem[] = Array.isArray(inbox?.items) ? inbox!.items : [];
  const inboxReady = inbox != null && inbox.available !== false;
  const betCount = items.filter((it) => String((it as Row).category || "") === "gtm_bet").length;
  const pendNote = inboxError ? "建议源读取失败" : inbox && inbox.available === false ? "建议系统未启用" : "建议队列读取中…";
  return (
    <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      <KpiCard label="本周信号" value={summary.weekly_signals.items.length} unit="条" seriesColor="var(--ds-accent)" />
      <KpiCard label="产品机会" value={summary.product_opportunities.items.length} unit="个" seriesColor="var(--ds-accent-2)" />
      <KpiCard
        label="待执行动作"
        value={items.length}
        unit="条"
        tone="warn"
        pending={!inboxReady}
        pendingNote={pendNote}
        seriesColor="var(--ds-warn)"
      />
      <KpiCard
        label="增长押注"
        value={betCount}
        unit="条"
        pending={!inboxReady}
        pendingNote={pendNote}
        seriesColor="var(--ds-good)"
      />
    </div>
  );
}

/* ============ 今日增长路线 body(全局=Top 机会一键生成路线;预览=五段路线链) ============ */

function RouteSeg({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-[92px] rounded-xl border border-line bg-panel px-3 py-2">
      <div className="text-[9px] uppercase tracking-wide text-muted">{label}</div>
      <div className="mt-0.5 truncate text-[12px] font-medium text-ink">{value || "—"}</div>
    </div>
  );
}

const GOAL_LABEL: Record<string, string> = { exposure: "曝光", conversion: "转化", content: "素材", channel: "渠道铺货" };

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

export function RouteBody({
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
  if (plan) {
    const p = plan.public_plan;
    const budget = Number(budgetText) > 0 ? Number(budgetText) : 3000;
    const arrow = <ArrowRight size={13} className="shrink-0 self-center text-muted" />;
    return (
      <div>
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <span className="rounded-md border border-accent bg-accent-soft px-2 py-0.5 text-[11px] text-accent">
            决策 {p.thesis.go_nogo || "—"}
          </span>
          {confBadge(p.thesis.confidence)}
        </div>
        <div className="flex flex-wrap items-stretch gap-1.5">
          <RouteSeg label="SKU" value={selectedSku} />
          {arrow}
          <RouteSeg label="市场" value={p.thesis.market} />
          {arrow}
          <RouteSeg label="渠道组合" value={channelMixLabel(plan)} />
          {arrow}
          <RouteSeg label="预算" value={`$${budget.toLocaleString()} · ${GOAL_LABEL[goal] || goal}`} />
          {arrow}
          <RouteSeg label="打法" value={p.thesis.mainline} />
        </div>
        {p.risks.length > 0 ? (
          <div className="mt-2 text-[10.5px] leading-relaxed text-warn">
            风险 {p.risks.length} 项 · {p.risks.slice(0, 4).join("、")}
          </div>
        ) : null}
      </div>
    );
  }
  const items = summary?.product_opportunities.items ?? [];
  if (items.length === 0) {
    return <EmptyLine text={summary?.product_opportunities.note || "暂无成型机会 · 上方选 SKU 生成路线。"} />;
  }
  return (
    <div className="space-y-1.5">
      {items.slice(0, 5).map((o, i) => (
        <div key={i} className="flex flex-wrap items-center gap-2 rounded-xl border border-line bg-panel px-3 py-2">
          <span className="font-mono text-[9px] tabular-nums text-muted">#{i + 1}</span>
          <span className="text-[12px] font-medium text-ink">{o.sku || "—"}</span>
          {o.market ? <span className="text-[10.5px] text-muted">@ {o.market}</span> : null}
          {o.persona ? <span className="text-[10px] text-muted">· {o.persona}</span> : null}
          {o.opportunity_score != null ? (
            <span className="text-[10px] tabular-nums text-accent">机会分 {o.opportunity_score}</span>
          ) : null}
          {onPickSku && o.sku ? (
            <button
              type="button"
              onClick={() => onPickSku(o.sku)}
              className="ml-auto flex items-center gap-1 rounded-lg border border-accent bg-accent-soft px-2 py-1 text-[10px] text-accent transition-colors hover:text-accent-hover"
            >
              生成路线
              <ArrowRight size={11} />
            </button>
          ) : null}
        </div>
      ))}
    </div>
  );
}

/* ============ 本周信号 body(kind 徽 + 信号句 + freshness/样本/置信) ============ */

export function SignalsBody({ summary }: { summary: MarketBrainSummary }) {
  const ws = summary.weekly_signals;
  if (ws.items.length === 0) return <EmptyLine text={ws.sources_note || "本周暂无信号。"} />;
  return (
    <div className="space-y-1.5">
      {ws.items.slice(0, 8).map((s, i) => (
        <div key={i} className="flex flex-wrap items-center gap-1.5 rounded-lg border border-line px-2.5 py-1.5">
          <span className="rounded border border-line px-1.5 py-0.5 text-[9.5px] text-muted">{s.kind || "signal"}</span>
          <span className="min-w-0 text-[11.5px] text-ink-2">{s.signal || "—"}</span>
          <span className="ml-auto flex shrink-0 items-center gap-1.5 font-mono text-[9.5px] text-muted">
            {s.freshness ? <span>{s.freshness}</span> : null}
            {s.sample_size != null ? <span>样本 {s.sample_size}</span> : null}
          </span>
          {confBadge(s.confidence)}
        </div>
      ))}
    </div>
  );
}

/* ============ 产品机会 body(palette;逐条含人群/角度/依据) ============ */

export function OppsBody({ summary }: { summary: MarketBrainSummary }) {
  const po = summary.product_opportunities;
  if (po.items.length === 0) return <EmptyLine text={po.note || "暂无成型产品机会。"} />;
  return (
    <div className="space-y-1.5">
      {po.items.slice(0, 6).map((o, i) => (
        <div key={i} className="rounded-lg border border-line px-2.5 py-1.5">
          <div className="flex flex-wrap items-center gap-1.5 text-[11.5px]">
            <span className="font-medium text-ink">{o.sku || "—"}</span>
            {o.market ? <span className="text-muted">@ {o.market}</span> : null}
            {o.opportunity_score != null ? (
              <span className="ml-auto text-[10px] tabular-nums text-accent">机会分 {o.opportunity_score}</span>
            ) : null}
          </div>
          <div className="mt-0.5 text-[10px] leading-relaxed text-muted">
            {[o.persona && `人群:${o.persona}`, o.content_angle && `角度:${o.content_angle}`, o.basis && `依据:${o.basis}`]
              .filter(Boolean)
              .join(" · ") || "—"}
          </div>
        </div>
      ))}
    </div>
  );
}

/* ============ 今日执行队列 body(类型 chips + 跳 Action Inbox;与 KPI 带同源同缓存) ============ */

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
  gtm_bet: "增长押注",
  discovery_enroll: "发现入库",
};

export function QueueBody({
  inbox,
  loading,
  error,
  onNavigate,
}: {
  inbox: ActionInboxResponse | null;
  loading: boolean;
  error: string;
  onNavigate?: (navKey: string) => void;
}) {
  if (error) return <ErrorCard title="建议源读取失败" text={error} />;
  if (inbox && inbox.available === false) {
    return (
      <PendingCard>
        <b>建议系统未启用</b> —— 后端就绪后自动点亮。
      </PendingCard>
    );
  }
  const items: ActionInboxItem[] = Array.isArray(inbox?.items) ? inbox!.items : [];
  if (loading && items.length === 0) return <EmptyLine text="队列读取中…" />;
  if (items.length === 0) return <EmptyLine text="队列为空。" />;
  const byCat = new Map<string, number>();
  for (const it of items) {
    const cat = String((it as Row).category || "other");
    byCat.set(cat, (byCat.get(cat) ?? 0) + 1);
  }
  const buckets = Array.from(byCat.entries()).sort((a, b) => b[1] - a[1]);
  const today = (inbox?.today_summary || {}) as Row;
  const executedToday = typeof today.today_executed_count === "number" ? today.today_executed_count : null;
  const jump = () => onNavigate?.("dashboard");
  return (
    <div>
      <div className="flex flex-wrap gap-1.5">
        {buckets.map(([cat, count]) => (
          <button
            key={cat}
            type="button"
            onClick={jump}
            title="去 Action Inbox 逐条人审执行"
            className="flex items-center gap-1.5 rounded-lg border border-line bg-panel px-2.5 py-1.5 text-[11px] text-ink-2 transition-colors hover:border-good"
          >
            <span>{QUEUE_LABEL[cat] || cat}</span>
            <span className="rounded-full bg-line px-1.5 py-px font-mono text-[9.5px] tabular-nums text-ink-2">{count}</span>
          </button>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={jump}
          className="flex items-center gap-1 rounded-lg border border-good bg-good-soft px-2.5 py-1 text-[10.5px] text-good transition-colors"
        >
          去 Action Inbox 人审
          <ArrowRight size={11} />
        </button>
        {executedToday != null ? <span className="font-mono text-[9.5px] text-muted">今日已执行 {executedToday} 条</span> : null}
      </div>
    </div>
  );
}

/* ============ 复盘学习 body(验证/有效风格/不值渠道 + 下次怎么变) ============ */

export function LearnBody({ digest }: { digest: LearningDigest }) {
  const groups: Array<[string, string[]]> = [
    ["本轮验证了什么", digest.validated],
    ["哪些内容风格有效", digest.effective_styles],
    ["哪些渠道不值", digest.dropped_channels],
  ];
  const allEmpty = groups.every(([, arr]) => arr.length === 0) && !digest.next_change;
  if (allEmpty) return <EmptyLine text={digest.honesty_note || "复盘账本暂无可总结条目。"} />;
  return (
    <div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        {groups.map(([label, arr], i) => (
          <div key={i} className="rounded-lg border border-line px-3 py-2">
            <div className="text-[10px] text-muted">{label}</div>
            {arr.length === 0 ? (
              <div className="mt-1 text-[11px] text-muted">暂无</div>
            ) : (
              <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[11px] leading-relaxed text-ink-2">
                {arr.slice(0, 5).map((s, j) => (
                  <li key={j}>{s}</li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
      {digest.next_change ? (
        <div className="mt-2 rounded-lg border border-accent bg-accent-soft px-3 py-2 text-[11px] leading-relaxed text-accent">
          下次推荐怎么变:{digest.next_change}
        </div>
      ) : null}
      {digest.honesty_note ? <div className="mt-2 text-[10px] leading-relaxed text-muted">{digest.honesty_note}</div> : null}
    </div>
  );
}

/* ============ Bet 生命周期 body(七段;preview 态确凿到「预览」,其后灰显不编进度) ============ */

const BET_STAGES = [
  { key: "idea", label: "构想" },
  { key: "preview", label: "预览" },
  { key: "materialized", label: "落库" },
  { key: "assigned", label: "指派" },
  { key: "done", label: "执行" },
  { key: "verdict", label: "裁决" },
  { key: "learned", label: "沉淀" },
];

export function BetsBody({ plan }: { plan: GtmPlanPreview | null }) {
  const reachedIdx = plan ? 1 : 0;
  return (
    <div
      className="flex flex-wrap items-center gap-1"
      title={plan ? "已到「预览」段;落库后进 Action Inbox,后续段随逐条人审推进点亮" : "选 SKU 生成作战预览后,从「构想」推进到「预览」"}
    >
      {BET_STAGES.map((s, i) => {
        const lit = i <= reachedIdx;
        return (
          <React.Fragment key={s.key}>
            <div className={`flex flex-col items-center gap-1 rounded-lg border px-2 py-1 ${lit ? "border-good bg-good-soft" : "border-line bg-panel"}`}>
              <div className={`h-1 w-8 rounded-full ${lit ? "bg-good" : "bg-line"}`} />
              <span className={`text-[9.5px] ${lit ? "text-good" : "text-muted"}`}>{s.label}</span>
            </div>
            {i < BET_STAGES.length - 1 ? <ArrowRight size={11} className="mt-1.5 shrink-0 self-start text-muted" /> : null}
          </React.Fragment>
        );
      })}
    </div>
  );
}
