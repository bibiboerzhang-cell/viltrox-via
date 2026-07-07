import React from "react";
import { ThresholdBar } from "../components/ThresholdBar";
import type {
  GtmActionItem,
  GtmPlanMeta,
  GtmPlanSection,
  GtmPublicPlan,
  LearningDigest,
} from "../../../../services/vkpi/gtmCommand-api";

// GtmCommandPage 抽段(千行卫兵还债,照 MyKolPage.Sections.tsx 先例):
//   共享渲染基元(confBadge / Card / Empty / PreviewSkeleton / SectionList /
//   ActionRow / ActionsBlock / LearningBlock)+ planCards 区块 ①②③ 与脚注。
//   保持 React.createElement 风格、props 显式传递;对外渲染行为零变。

const e = React.createElement;

const CONF_TONE: Record<string, string> = {
  high: "border-emerald-300/30 bg-emerald-500/[0.12] text-emerald-100",
  medium: "border-amber-300/30 bg-amber-500/[0.12] text-amber-100",
  low: "border-rose-300/30 bg-rose-500/[0.12] text-rose-100",
};

export function confBadge(confidence: string, prefix = "置信") {
  const key = confidence.toLowerCase();
  const tone = CONF_TONE[key] || "border-white/[0.1] bg-white/[0.04] text-slate-300";
  return e(
    "span",
    { className: `shrink-0 rounded-md border px-2 py-0.5 text-[10px] ${tone}` },
    confidence ? `${prefix} ${confidence}` : `${prefix} —`,
  );
}

export function Card({ title, hint, extra, children }: { title: string; hint?: string; extra?: React.ReactNode; children?: React.ReactNode }) {
  return e(
    "div",
    { className: "rounded-2xl border border-white/[0.06] bg-white/[0.015] p-4" },
    e(
      "div",
      { className: "mb-3 flex items-start justify-between gap-2" },
      e(
        "div",
        { className: "min-w-0" },
        e("div", { className: "text-[13px] font-semibold text-white" }, title),
        hint ? e("div", { className: "mt-0.5 text-[10px] text-slate-500" }, hint) : null,
      ),
      extra ?? null,
    ),
    children,
  );
}

export function Empty({ text }: { text: string }) {
  return e(
    "div",
    { className: "rounded-lg border border-dashed border-white/[0.08] px-3 py-3 text-center text-[11px] text-slate-500" },
    text,
  );
}

// U3 · preview 加载骨架屏(U2 骨架基元未就绪 → 本页极简版,收口时统一替换)。
// 五卡轮廓 + animate-pulse;motion-reduce:animate-none 尊重 prefers-reduced-motion;
// 加载态短暂脉动非常驻循环,结束即卸载。诚实脚注保留原文案。
export function PreviewSkeleton() {
  return e(
    "div",
    { className: "space-y-4", role: "status", "aria-label": "作战预览聚合中", "data-testid": "preview-skeleton" },
    [0, 1, 2, 3, 4].map((i) =>
      e(
        "div",
        { key: i, className: "rounded-2xl border border-white/[0.06] bg-white/[0.015] p-4 animate-pulse motion-reduce:animate-none" },
        e("div", { className: "h-3 w-40 rounded bg-white/[0.06]" }),
        e("div", { className: "mt-3 h-2.5 w-full rounded bg-white/[0.04]" }),
        e("div", { className: "mt-2 h-2.5 w-2/3 rounded bg-white/[0.04]" }),
      ),
    ),
    e("div", { className: "text-center text-[11px] text-slate-500" }, "作战预览聚合中(纯读,零写库)…"),
  );
}

// 渠道段通用渲染:只点名白名单键(显示层宪法),形状未知也不 JSON 倾倒。
function pickTitle(row: Record<string, any>): string {
  for (const k of ["display_name", "handle", "name", "channel", "angle", "title", "action", "template", "metric", "plan_name", "sku"]) {
    if (typeof row[k] === "string" && row[k]) return row[k];
  }
  return "—";
}

function pickSub(row: Record<string, any>): string {
  const parts: string[] = [];
  for (const k of ["play", "reason", "why_fit", "note", "basis", "summary", "format", "split", "suggestion", "recent_highlight"]) {
    if (typeof row[k] === "string" && row[k]) {
      parts.push(row[k]);
      if (parts.length >= 2) break;
    }
  }
  return parts.join(" · ");
}

function riskLabels(row: Record<string, any>): string[] {
  const v = row.risk_labels ?? row.risk_tags;
  if (!Array.isArray(v)) return [];
  return v.filter((x) => typeof x === "string" && x).slice(0, 4);
}

export function SectionList({ section, emptyText, max = 6 }: { section: GtmPlanSection; emptyText: string; max?: number }) {
  if (section.status && section.status !== "ok" && section.status !== "ready") {
    return e(
      "div",
      { className: "rounded-lg border border-amber-300/20 bg-amber-500/[0.05] px-3 py-2 text-[11px] leading-relaxed text-amber-200/90" },
      section.note || `该段暂不可用(${section.status},诚实空态)。`,
    );
  }
  if (section.items.length === 0) {
    return e(
      "div",
      null,
      e(Empty, { text: section.note || emptyText }),
    );
  }
  return e(
    "div",
    { className: "space-y-1.5" },
    section.items.slice(0, max).map((row, i) =>
      e(
        "div",
        { key: i, className: "rounded-lg border border-white/[0.06] px-2.5 py-1.5" },
        e(
          "div",
          { className: "flex flex-wrap items-center gap-1.5" },
          e("span", { className: "text-[11.5px] text-slate-200" }, pickTitle(row)),
          typeof row.confidence === "string" && row.confidence ? confBadge(row.confidence) : null,
          riskLabels(row).map((r, j) =>
            e("span", { key: j, className: "rounded border border-rose-300/25 bg-rose-500/[0.08] px-1.5 py-0.5 text-[9.5px] text-rose-200" }, r),
          ),
        ),
        pickSub(row) ? e("div", { className: "mt-0.5 text-[10px] leading-relaxed text-slate-500" }, pickSub(row)) : null,
      ),
    ),
    section.note ? e("div", { className: "mt-1 text-[10px] text-slate-500" }, section.note) : null,
  );
}

// 六要素行动行:原因/证据摘要/成本/风险/预计收益/人审按钮(v1 disabled,GTM-3 接线)。
function ActionRow({ item }: { item: GtmActionItem }) {
  const cells: Array<[string, string]> = [
    ["原因", item.reason],
    ["证据摘要", item.evidence_summary],
    ["成本", item.cost_note],
    ["风险", item.risk],
    ["预计收益", item.expected_gain],
  ];
  return e(
    "div",
    { className: "rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2" },
    e(
      "div",
      { className: "flex items-start justify-between gap-2" },
      e("div", { className: "min-w-0 text-[12px] font-medium text-slate-100" }, item.action || "—"),
      e(
        "button",
        {
          disabled: true,
          title: "GTM-3 接线",
          className: "shrink-0 cursor-not-allowed rounded-lg border border-white/[0.08] bg-white/[0.03] px-2.5 py-1 text-[10px] text-slate-500 opacity-60",
        },
        "人审执行",
      ),
    ),
    e(
      "div",
      { className: "mt-1.5 grid grid-cols-1 gap-x-3 gap-y-1 md:grid-cols-2" },
      cells.map(([label, value], i) =>
        e(
          "div",
          { key: i, className: "flex gap-1.5 text-[10.5px] leading-relaxed" },
          e("span", { className: "shrink-0 text-slate-500" }, `${label}:`),
          e("span", { className: "min-w-0 text-slate-300" }, value || "—"),
        ),
      ),
    ),
    item.ref ? e("div", { className: "mt-1 text-[9.5px] text-slate-600" }, `ref: ${item.ref}`) : null,
  );
}

export function ActionsBlock({ items, note, emptyText }: { items: GtmActionItem[]; note?: string; emptyText: string }) {
  return e(
    "div",
    null,
    items.length === 0
      ? e(Empty, { text: emptyText })
      : e("div", { className: "space-y-2" }, items.slice(0, 10).map((it, i) => e(ActionRow, { key: i, item: it }))),
    note ? e("div", { className: "mt-2 text-[10px] text-slate-500" }, note) : null,
  );
}

export function LearningBlock({ digest, footnote }: { digest: LearningDigest; footnote: string }) {
  const groups: Array<[string, string[]]> = [
    ["本轮验证了什么", digest.validated],
    ["哪些内容风格有效", digest.effective_styles],
    ["哪些渠道不值", digest.dropped_channels],
  ];
  const allEmpty = groups.every(([, arr]) => arr.length === 0) && !digest.next_change;
  return e(
    "div",
    null,
    allEmpty
      ? e(Empty, { text: digest.honesty_note || "复盘账本暂无可总结条目(样本不足,诚实空态)。" })
      : e(
          "div",
          { className: "grid grid-cols-1 gap-3 md:grid-cols-3" },
          groups.map(([label, arr], i) =>
            e(
              "div",
              { key: i, className: "rounded-lg border border-white/[0.06] px-3 py-2" },
              e("div", { className: "text-[10px] text-slate-500" }, label),
              arr.length === 0
                ? e("div", { className: "mt-1 text-[11px] text-slate-600" }, "暂无")
                : e(
                    "ul",
                    { className: "mt-1 list-disc space-y-0.5 pl-4 text-[11px] leading-relaxed text-slate-300" },
                    arr.slice(0, 5).map((s, j) => e("li", { key: j }, s)),
                  ),
            ),
          ),
        ),
    digest.next_change
      ? e(
          "div",
          { className: "mt-2 rounded-lg border border-sky-300/20 bg-sky-500/[0.06] px-3 py-2 text-[11px] leading-relaxed text-sky-100" },
          `下次推荐怎么变:${digest.next_change}`,
        )
      : null,
    e("div", { className: "mt-2 text-[10px] leading-relaxed text-slate-600" }, [digest.honesty_note, footnote].filter(Boolean).join(" · ")),
  );
}

// ---- planCards 区块 ①:主判断(该不该推 / 押哪个市场 / 主打人群 / 主线)----
export function PlanThesisCard({ selectedSku, p }: { selectedSku: string; p: GtmPublicPlan }) {
  return e(
    Card,
    {
      title: `① 主判断 · ${selectedSku}`,
      hint: "该不该推 / 押哪个市场 / 主打人群 / 走哪条主线",
      extra: confBadge(p.thesis.confidence),
    },
    p.thesis.status && p.thesis.status !== "ok" && p.thesis.status !== "ready"
      ? e(Empty, { text: `主判断暂不可用(${p.thesis.status})。` })
      : e(
          "div",
          null,
          e(
            "div",
            { className: "grid grid-cols-1 gap-2 md:grid-cols-2" },
            ([
              ["该不该推", p.thesis.go_nogo],
              ["优先市场", p.thesis.market],
              ["主打人群", p.thesis.persona],
              ["主线打法", p.thesis.mainline],
            ] as Array<[string, string]>).map(([label, value], i) =>
              e(
                "div",
                { key: i, className: "rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2" },
                e("div", { className: "text-[10px] text-slate-500" }, label),
                e("div", { className: "mt-0.5 text-[13px] font-medium text-white" }, value || "—"),
              ),
            ),
          ),
          p.thesis.basis_summary
            ? e("div", { className: "mt-2 text-[11px] leading-relaxed text-slate-400" }, `判断依据:${p.thesis.basis_summary}`)
            : null,
          e(
            "div",
            { className: "mt-3" },
            e("div", { className: "mb-1.5 text-[10px] text-slate-500" }, "市场机会(赛道口径)"),
            e(SectionList, { section: p.market_opportunity, emptyText: "赛道段无数据(诚实空态)。", max: 4 }),
          ),
        ),
  );
}

// ---- planCards 区块 ②:条件化预判(预判/依据+置信/触发加码/撤退条件四段式)----
export function PlanForecastCard({ p }: { p: GtmPublicPlan }) {
  return e(
    Card,
    {
      title: "② 市场预判(条件化)",
      hint: "每条都是「条件成立才行动」的情景,不是结果承诺;触发加码与撤退线缺一不可",
      extra: e(
        "span",
        { className: "shrink-0 rounded-md border border-purple-300/30 bg-purple-500/[0.12] px-2 py-0.5 text-[10px] text-purple-100" },
        "条件 ≠ 断言",
      ),
    },
    p.forecast.length === 0
      ? e(Empty, { text: "预判段暂无内容(样本不足或端点未就绪,诚实空态)。" })
      : e(
          "div",
          { className: "grid grid-cols-1 gap-2 lg:grid-cols-3" },
          p.forecast.slice(0, 6).map((f, i) =>
            e(
              "div",
              { key: i, className: "rounded-xl border border-white/[0.06] bg-white/[0.02] p-3" },
              e(
                "div",
                { className: "flex items-center justify-between gap-2" },
                e("span", { className: "text-[11px] font-semibold text-slate-200" }, f.horizon_days != null ? `${f.horizon_days} 天窗口` : "窗口 —"),
                confBadge(f.confidence),
              ),
              e(
                "div",
                { className: "mt-2 space-y-1.5 text-[10.5px] leading-relaxed" },
                e(
                  "div",
                  { className: "border-l-2 border-sky-300/50 pl-2" },
                  e("span", { className: "text-slate-500" }, "预判:"),
                  e("span", { className: "text-slate-200" }, f.statement || "—"),
                ),
                e(
                  "div",
                  { className: "border-l-2 border-slate-400/40 pl-2" },
                  e("span", { className: "text-slate-500" }, "依据:"),
                  e("span", { className: "text-slate-300" }, f.signals_summary || "—"),
                ),
                e(
                  "div",
                  { className: "border-l-2 border-emerald-300/50 pl-2" },
                  e("span", { className: "text-slate-500" }, "触发加码:"),
                  f.escalate_if
                    ? e("span", { className: "text-emerald-100" }, f.escalate_if)
                    : e("span", { className: "text-rose-300" }, "条件缺失(不合规,待后端补齐)"),
                ),
                e(
                  "div",
                  { className: "border-l-2 border-rose-300/50 pl-2" },
                  e("span", { className: "text-slate-500" }, "撤退条件:"),
                  f.retreat_if
                    ? e("span", { className: "text-rose-100" }, f.retreat_if)
                    : e("span", { className: "text-rose-300" }, "条件缺失(不合规,待后端补齐)"),
                ),
              ),
              // U3 阈值进度条:文本里解析得出量化值才出条(解析不出保持纯文本卡,不硬编)。
              e(ThresholdBar, { escalateIf: f.escalate_if, retreatIf: f.retreat_if }),
            ),
          ),
        ),
  );
}

// ---- planCards 区块 ③:增长路线图(三段推进 + 渠道段五宫格)----
export function PlanRoadmapCard({ p }: { p: GtmPublicPlan }) {
  return e(
    Card,
    { title: "③ 增长路线图", hint: "W1 / W2-4 / M2-3 三段推进,每段列 KOL·Dealer·官号·自媒体·独立站怎么配合" },
    p.roadmap.length === 0
      ? e(Empty, { text: "端点未返回 roadmap 段 —— 前端不编造节奏,渠道配合先看下方渠道段(诚实空态)。" })
      : e(
          "div",
          { className: "grid grid-cols-1 gap-2 md:grid-cols-3" },
          p.roadmap.map((ph) =>
            e(
              "div",
              { key: ph.key, className: "rounded-xl border border-white/[0.06] bg-white/[0.02] p-3" },
              e("div", { className: "text-[11px] font-semibold text-sky-200" }, ph.label),
              ph.channels.length > 0
                ? e(
                    "div",
                    { className: "mt-1.5 space-y-1" },
                    ph.channels.map((c, j) =>
                      e(
                        "div",
                        { key: j, className: "flex gap-1.5 text-[10.5px] leading-relaxed" },
                        e("span", { className: "shrink-0 rounded border border-white/[0.08] px-1.5 py-0.5 text-[9px] text-slate-400" }, c.channel || "—"),
                        e("span", { className: "min-w-0 text-slate-300" }, c.play || "—"),
                      ),
                    ),
                  )
                : null,
              ph.items.length > 0
                ? e(
                    "ul",
                    { className: "mt-1.5 list-disc space-y-0.5 pl-4 text-[10.5px] leading-relaxed text-slate-300" },
                    ph.items.slice(0, 6).map((s, j) => e("li", { key: j }, s)),
                  )
                : null,
              ph.channels.length === 0 && ph.items.length === 0
                ? e("div", { className: "mt-1.5 text-[10px] text-slate-600" }, ph.note || "该段暂无安排(诚实空态)。")
                : ph.note
                  ? e("div", { className: "mt-1.5 text-[9.5px] text-slate-600" }, ph.note)
                  : null,
            ),
          ),
        ),
    // 渠道段:KOL 候选(风险只出标签)/ Dealer / 官号 / 独立站 / 内容角度
    e(
      "div",
      { className: "mt-3 grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3" },
      (
        [
          ["KOL 候选", p.kol_candidates, "候选池无匹配 KOL(诚实空态)。"],
          ["Dealer 铺货", p.dealer_targets, "Dealer 数据未导入(GTM-2 激活)。"],
          ["官号动作", p.official_channel_actions, "官号快照无近期表现数据。"],
          ["独立站承接", p.shopify_indie_site_actions, "独立站段无建议(本地无订单,诚实标注)。"],
          ["内容角度", p.content_angles, "暂无内容角度建议。"],
        ] as Array<[string, GtmPlanSection, string]>
      ).map(([label, section, emptyText], i) =>
        e(
          "div",
          { key: i, className: "rounded-xl border border-white/[0.06] bg-white/[0.02] p-3" },
          e("div", { className: "mb-1.5 text-[10.5px] font-semibold text-slate-300" }, label),
          e(SectionList, { section, emptyText, max: 4 }),
        ),
      ),
    ),
  );
}

// ---- planCards 脚注:风险 / 数据缺口 / 成功指标 / 覆盖度 ----
export function PlanFootnote({ p, meta }: { p: GtmPublicPlan; meta: GtmPlanMeta | undefined }) {
  return e(
    "div",
    { className: "rounded-2xl border border-white/[0.06] bg-white/[0.015] p-4" },
    p.risks.length > 0 &&
      e(
        "div",
        { className: "mb-2 flex flex-wrap items-center gap-1.5" },
        e("span", { className: "text-[10px] text-slate-500" }, "风险:"),
        p.risks.slice(0, 8).map((r, i) =>
          e("span", { key: i, className: "rounded border border-amber-300/25 bg-amber-500/[0.08] px-2 py-0.5 text-[10px] text-amber-200" }, r),
        ),
      ),
    (p.data_gaps.length > 0 || (meta?.data_gaps.length ?? 0) > 0) &&
      e(
        "div",
        { className: "mb-2 flex flex-wrap items-center gap-1.5" },
        e("span", { className: "text-[10px] text-slate-500" }, "数据缺口:"),
        Array.from(new Set([...p.data_gaps, ...(meta?.data_gaps || [])])).slice(0, 10).map((g, i) =>
          e("span", { key: i, className: "rounded border border-white/[0.08] bg-white/[0.03] px-2 py-0.5 text-[10px] text-slate-400" }, g),
        ),
      ),
    p.success_metrics.length > 0 &&
      e(
        "div",
        { className: "mb-2" },
        e("div", { className: "mb-1 text-[10px] text-slate-500" }, "成功指标(规则库口径,可被自有数据推翻)"),
        e(
          "div",
          { className: "flex flex-wrap gap-1.5" },
          p.success_metrics.slice(0, 8).map((m, i) =>
            e(
              "span",
              { key: i, className: "rounded border border-sky-300/20 bg-sky-500/[0.06] px-2 py-0.5 text-[10px] text-sky-100" },
              `${m.metric || "—"}${m.threshold ? ` ≥ ${m.threshold}` : ""}`,
              m.confidence ? e("span", { className: "ml-1 text-sky-300/70" }, `(置信 ${m.confidence})`) : null,
            ),
          ),
        ),
      ),
    e(
      "div",
      { className: "text-[10px] leading-relaxed text-slate-600" },
      `生成于 ${meta?.generated_at || "—"}(UTC)· 纯读预览零写库零 LLM 零采集 · 外部信号来源:内部缓存/计划口径,外部雷达 GTM-5 待接入`,
      meta && Object.keys(meta.coverage).length > 0
        ? ` · 覆盖度:${Object.entries(meta.coverage)
            .slice(0, 6)
            .map(([k, v]) => `${k}=${typeof v === "string" || typeof v === "number" ? v : "…"}`)
            .join(" / ")}`
        : "",
    ),
  );
}
