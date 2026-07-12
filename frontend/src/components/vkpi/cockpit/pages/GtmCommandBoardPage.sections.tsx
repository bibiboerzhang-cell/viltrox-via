import React from "react";
import { ThresholdBar } from "../components/ThresholdBar";
import { confBadge } from "./GtmCommandPage.Sections";
import { EmptyLine } from "./MarketVoicePage.modules";
import type {
  GtmActionItem,
  GtmMaterializeResult,
  GtmPlanMeta,
  GtmPlanSection,
  GtmPublicPlan,
} from "../../../../services/vkpi/gtmCommand-api";
import { formatLocal } from "../../lib/timeLocal";

// GTM Command · 板块页范式改版 —— 作战预览区块 body 族(GtmCommandBoardPage 专用)。
//   旧页 planCards ①②③④ 与脚注(GtmCommandPage.Sections.tsx,回滚垫零改动)在此按
//   金样板图形语言重铸为「卡体 body」:卡壳/标题/口径句全部上提到 ModuleCard + SrcChip,
//   卡面零介绍性文案(用户验收纪律:说明只进 SrcChip/溯源/tooltip,按钮动词直说)。
//   数据 = gtm-plan/preview public_plan(显示层白名单键;private 字段已在 api 层深度剥除)。
// 红线:本文件零直连网络(materialize 动作走 page 层回调);纯读展示;不触
//   viltrox_fit_score / rule_v0;颜色全 token 零写死色;零 opacity 修饰类;
//   空态 = 后端 note/reason 原样透出,缺席时短句如实,绝不编数据。

type Row = Record<string, any>;

/* ============ 通用段列表(旧 SectionList 同构;白名单键点名渲染,形状未知不倾倒) ============ */

function pickTitle(row: Row): string {
  for (const k of ["display_name", "handle", "name", "channel", "angle", "title", "action", "template", "metric", "plan_name", "sku"]) {
    if (typeof row[k] === "string" && row[k]) return row[k];
  }
  return "—";
}

function pickSub(row: Row): string {
  const parts: string[] = [];
  for (const k of ["play", "reason", "why_fit", "note", "basis", "summary", "format", "split", "suggestion", "recent_highlight"]) {
    if (typeof row[k] === "string" && row[k]) {
      parts.push(row[k]);
      if (parts.length >= 2) break;
    }
  }
  return parts.join(" · ");
}

function riskLabels(row: Row): string[] {
  const v = row.risk_labels ?? row.risk_tags;
  if (!Array.isArray(v)) return [];
  return v.filter((x) => typeof x === "string" && x).slice(0, 4);
}

export function SecList({ section, emptyText, max = 6 }: { section: GtmPlanSection; emptyText: string; max?: number }) {
  if (section.status && section.status !== "ok" && section.status !== "ready") {
    return (
      <div className="rounded-lg border border-warn bg-warn-soft px-3 py-2 text-[11px] leading-relaxed text-warn">
        {section.note || `本段暂不可用(${section.status})。`}
      </div>
    );
  }
  if (section.items.length === 0) return <EmptyLine text={section.note || emptyText} />;
  return (
    <div className="space-y-1.5">
      {section.items.slice(0, max).map((row, i) => (
        <div key={i} className="rounded-lg border border-line px-2.5 py-1.5">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[11.5px] text-ink-2">{pickTitle(row)}</span>
            {typeof row.confidence === "string" && row.confidence ? confBadge(row.confidence) : null}
            {riskLabels(row).map((r, j) => (
              <span key={j} className="rounded border border-crit bg-crit-soft px-1.5 py-0.5 text-[9.5px] text-crit">
                {r}
              </span>
            ))}
          </div>
          {pickSub(row) ? <div className="mt-0.5 text-[10px] leading-relaxed text-muted">{pickSub(row)}</div> : null}
        </div>
      ))}
      {section.note ? <div className="mt-1 text-[10px] text-muted">{section.note}</div> : null}
    </div>
  );
}

/* ============ 六要素行动行(原因/证据/成本/风险/预计收益 + 人审占位钮) ============ */

function ActionRowLine({ item }: { item: GtmActionItem }) {
  const cells: Array<[string, string]> = [
    ["原因", item.reason],
    ["证据摘要", item.evidence_summary],
    ["成本", item.cost_note],
    ["风险", item.risk],
    ["预计收益", item.expected_gain],
  ];
  return (
    <div className="rounded-xl border border-line bg-panel px-3 py-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 text-[12px] font-medium text-ink">{item.action || "—"}</div>
        <button
          type="button"
          disabled
          title="逐条人审执行在 Action Inbox 完成(GTM-3 接线)"
          className="shrink-0 cursor-not-allowed rounded-lg border border-line bg-panel px-2.5 py-1 text-[10px] text-muted"
        >
          人审执行
        </button>
      </div>
      <div className="mt-1.5 grid grid-cols-1 gap-x-3 gap-y-1 md:grid-cols-2">
        {cells.map(([label, value], i) => (
          <div key={i} className="flex gap-1.5 text-[10.5px] leading-relaxed">
            <span className="shrink-0 text-muted">{label}:</span>
            <span className="min-w-0 text-ink-2">{value || "—"}</span>
          </div>
        ))}
      </div>
      {item.ref ? <div className="mt-1 font-mono text-[9.5px] text-muted">ref: {item.ref}</div> : null}
    </div>
  );
}

export function ActionsList({ items, note, emptyText, max = 10 }: { items: GtmActionItem[]; note?: string; emptyText: string; max?: number }) {
  return (
    <div>
      {items.length === 0 ? (
        <EmptyLine text={emptyText} />
      ) : (
        <div className="space-y-2">
          {items.slice(0, max).map((it, i) => (
            <ActionRowLine key={i} item={it} />
          ))}
        </div>
      )}
      {note ? <div className="mt-2 text-[10px] text-muted">{note}</div> : null}
    </div>
  );
}

/* ============ ① 主判断 body(2×2 判断格 + 依据 + 市场机会段) ============ */

export function ThesisBody({ p }: { p: GtmPublicPlan }) {
  if (p.thesis.status && p.thesis.status !== "ok" && p.thesis.status !== "ready") {
    return <EmptyLine text={`主判断暂不可用(${p.thesis.status})。`} />;
  }
  const cells: Array<[string, string]> = [
    ["该不该推", p.thesis.go_nogo],
    ["优先市场", p.thesis.market],
    ["主打人群", p.thesis.persona],
    ["主线打法", p.thesis.mainline],
  ];
  return (
    <div>
      <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
        {cells.map(([label, value], i) => (
          <div key={i} className="rounded-xl border border-line bg-panel px-3 py-2">
            <div className="text-[10px] text-muted">{label}</div>
            <div className="mt-0.5 text-[13px] font-medium text-ink">{value || "—"}</div>
          </div>
        ))}
      </div>
      {p.thesis.basis_summary ? (
        <div className="mt-2 text-[11px] leading-relaxed text-muted">判断依据:{p.thesis.basis_summary}</div>
      ) : null}
      <div className="mt-3">
        <div className="mb-1.5 text-[10px] text-muted">市场机会</div>
        <SecList section={p.market_opportunity} emptyText="赛道段无数据。" max={4} />
      </div>
    </div>
  );
}

/* ============ ② 条件化预判 body(预判/依据/触发加码/撤退条件四段式 + 阈值条) ============ */

export function ForecastBody({ p }: { p: GtmPublicPlan }) {
  if (p.forecast.length === 0) return <EmptyLine text="预判段暂无内容。" />;
  return (
    <div className="grid grid-cols-1 gap-2 lg:grid-cols-3">
      {p.forecast.slice(0, 6).map((f, i) => (
        <div key={i} className="rounded-xl border border-line bg-panel p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] font-semibold text-ink-2">{f.horizon_days != null ? `${f.horizon_days} 天窗口` : "窗口 —"}</span>
            {confBadge(f.confidence)}
          </div>
          <div className="mt-2 space-y-1.5 text-[10.5px] leading-relaxed">
            <div className="border-l-2 border-accent pl-2">
              <span className="text-muted">预判:</span>
              <span className="text-ink-2">{f.statement || "—"}</span>
            </div>
            <div className="border-l-2 border-line pl-2">
              <span className="text-muted">依据:</span>
              <span className="text-ink-2">{f.signals_summary || "—"}</span>
            </div>
            <div className="border-l-2 border-good pl-2">
              <span className="text-muted">触发加码:</span>
              {f.escalate_if ? <span className="text-good">{f.escalate_if}</span> : <span className="text-crit">条件缺失</span>}
            </div>
            <div className="border-l-2 border-crit pl-2">
              <span className="text-muted">撤退条件:</span>
              {f.retreat_if ? <span className="text-crit">{f.retreat_if}</span> : <span className="text-crit">条件缺失</span>}
            </div>
          </div>
          {/* 阈值进度条:文本解析出量化值才出条,解析不出保持纯文本(不硬编) */}
          <ThresholdBar escalateIf={f.escalate_if} retreatIf={f.retreat_if} />
        </div>
      ))}
    </div>
  );
}

/* ============ ③ 增长路线图 body(三段推进 + 渠道段五宫格) ============ */

export function RoadmapBody({ p }: { p: GtmPublicPlan }) {
  return (
    <div>
      {p.roadmap.length === 0 ? (
        <EmptyLine text="端点未返回 roadmap 段 —— 不编造节奏,渠道配合看下方渠道段。" />
      ) : (
        <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
          {p.roadmap.map((ph) => (
            <div key={ph.key} className="rounded-xl border border-line bg-panel p-3">
              <div className="text-[11px] font-semibold text-accent">{ph.label}</div>
              {ph.channels.length > 0 ? (
                <div className="mt-1.5 space-y-1">
                  {ph.channels.map((c, j) => (
                    <div key={j} className="flex gap-1.5 text-[10.5px] leading-relaxed">
                      <span className="shrink-0 rounded border border-line px-1.5 py-0.5 text-[9px] text-muted">{c.channel || "—"}</span>
                      <span className="min-w-0 text-ink-2">{c.play || "—"}</span>
                    </div>
                  ))}
                </div>
              ) : null}
              {ph.items.length > 0 ? (
                <ul className="mt-1.5 list-disc space-y-0.5 pl-4 text-[10.5px] leading-relaxed text-ink-2">
                  {ph.items.slice(0, 6).map((s, j) => (
                    <li key={j}>{s}</li>
                  ))}
                </ul>
              ) : null}
              {ph.channels.length === 0 && ph.items.length === 0 ? (
                <div className="mt-1.5 text-[10px] text-muted">{ph.note || "本段暂无安排。"}</div>
              ) : ph.note ? (
                <div className="mt-1.5 text-[9.5px] text-muted">{ph.note}</div>
              ) : null}
            </div>
          ))}
        </div>
      )}
      <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
        {(
          [
            ["KOL 候选", p.kol_candidates, "候选池无匹配 KOL。"],
            ["Dealer 铺货", p.dealer_targets, "Dealer 数据未导入(GTM-2 激活)。"],
            ["官号动作", p.official_channel_actions, "官号快照无近期表现数据。"],
            ["独立站承接", p.shopify_indie_site_actions, "独立站段无建议(本地无订单)。"],
            ["内容角度", p.content_angles, "暂无内容角度建议。"],
          ] as Array<[string, GtmPlanSection, string]>
        ).map(([label, section, emptyText], i) => (
          <div key={i} className="rounded-xl border border-line bg-panel p-3">
            <div className="mb-1.5 text-[10.5px] font-semibold text-ink-2">{label}</div>
            <SecList section={section} emptyText={emptyText} max={4} />
          </div>
        ))}
      </div>
    </div>
  );
}

/* ============ ④ 今日行动 body(生成行动流:预演 → 确认落库;行动清单 + 预算段) ============ */

export interface MaterializeAdapter {
  matPreview: GtmMaterializeResult | null;
  matDone: GtmMaterializeResult | null;
  matBusy: "" | "dry" | "persist";
  matError: string;
  canPersistBets: boolean;
  run: (dryRun: boolean) => void;
  onNavigate?: (navKey: string) => void;
}

export function TodayActionsBody({ p, selectedSku, mat }: { p: GtmPublicPlan; selectedSku: string; mat: MaterializeAdapter }) {
  const { matPreview, matDone, matBusy, matError, canPersistBets } = mat;
  return (
    <div>
      <div className="mb-3 rounded-xl border border-accent bg-accent-soft p-3">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => mat.run(true)}
            disabled={!selectedSku || matBusy !== ""}
            title="dry-run:只出 bet 预览与幂等对账,零写库"
            className="rounded-lg border border-accent bg-accent-soft px-2.5 py-1.5 text-[11px] text-accent transition-colors hover:text-accent-hover disabled:cursor-not-allowed disabled:text-muted"
          >
            {matBusy === "dry" ? "预演中…" : "生成行动(预演)"}
          </button>
          {matPreview && canPersistBets ? (
            <button
              type="button"
              onClick={() => mat.run(false)}
              disabled={matBusy !== "" || (matPreview.would_insert ?? 0) === 0}
              title={
                (matPreview.would_insert ?? 0) === 0
                  ? "本次无可新增 bet(已存在的幂等不重插)"
                  : "真落库:bet 进 Action Inbox(status=suggested),逐条 requires_approval 人审;幂等不重插"
              }
              className="rounded-lg border border-good bg-good-soft px-2.5 py-1.5 text-[11px] text-good transition-colors disabled:cursor-not-allowed disabled:text-muted"
            >
              {matBusy === "persist" ? "落库中…" : `确认落库(新增 ${matPreview.would_insert ?? 0} 条)`}
            </button>
          ) : null}
        </div>
        {matError ? (
          <div className="mt-2 rounded-lg border border-crit bg-crit-soft px-2.5 py-1.5 text-[10.5px] text-crit">
            生成行动未生效 · {matError}
          </div>
        ) : null}
        {matPreview ? (
          <div className="mt-2">
            <div className="text-[10.5px] text-ink-2">
              {`预演对账:共 ${matPreview.bets_total} 条 bet · 可新增 ${matPreview.would_insert ?? 0} 条 · 已存在 ${matPreview.already_present} 条` +
                (matPreview.skipped_incomplete > 0 ? ` · 跳过不完整 ${matPreview.skipped_incomplete} 条` : "")}
            </div>
            {matPreview.bets.length > 0 ? (
              <ul className="mt-1.5 list-disc space-y-0.5 pl-4 text-[10px] leading-relaxed text-muted">
                {matPreview.bets.slice(0, 6).map((b, i) => (
                  <li key={i}>{b.title || b.dedupe_key || "—"}</li>
                ))}
                {matPreview.bets.length > 6 ? <li className="list-none text-muted">… 共 {matPreview.bets.length} 条</li> : null}
              </ul>
            ) : (
              <div className="mt-1.5 text-[10px] text-muted">本次 plan 无完整七要素 bet 可落。</div>
            )}
          </div>
        ) : null}
        {matDone ? (
          <div className="mt-2 rounded-lg border border-good bg-good-soft px-2.5 py-1.5">
            <div className="text-[10.5px] text-good">
              {`已落库:新增 ${matDone.inserted_new ?? 0} 条 · 已存在 ${matDone.already_present} 条未重插 · 逐条待人审`}
            </div>
            <button
              type="button"
              onClick={() => mat.onNavigate?.("dashboard")}
              className="mt-1.5 rounded-lg border border-good bg-good-soft px-2.5 py-1 text-[10px] text-good transition-colors"
            >
              去 Action Inbox 人审 →
            </button>
          </div>
        ) : null}
      </div>
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <ActionsList items={p.action_inbox_items} emptyText="本次预览未产出行动项。" />
        <div className="rounded-xl border border-line bg-panel p-3">
          <div className="mb-1.5 text-[10.5px] font-semibold text-ink-2">预算分配(三档模板)</div>
          <SecList section={p.budget_mix} emptyText="预算段无内容。" max={4} />
        </div>
      </div>
    </div>
  );
}

/* ============ 脚注 body(风险 / 数据缺口 / 成功指标 / 覆盖度 + 生成时间) ============ */

export function FootnoteBody({ p, meta }: { p: GtmPublicPlan; meta: GtmPlanMeta | undefined }) {
  const gaps = Array.from(new Set([...p.data_gaps, ...(meta?.data_gaps || [])]));
  return (
    <div>
      {p.risks.length > 0 ? (
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <span className="text-[10px] text-muted">风险:</span>
          {p.risks.slice(0, 8).map((r, i) => (
            <span key={i} className="rounded border border-warn bg-warn-soft px-2 py-0.5 text-[10px] text-warn">
              {r}
            </span>
          ))}
        </div>
      ) : null}
      {gaps.length > 0 ? (
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <span className="text-[10px] text-muted">数据缺口:</span>
          {gaps.slice(0, 10).map((g, i) => (
            <span key={i} className="rounded border border-line bg-panel px-2 py-0.5 text-[10px] text-muted">
              {g}
            </span>
          ))}
        </div>
      ) : null}
      {p.success_metrics.length > 0 ? (
        <div className="mb-2">
          <div className="mb-1 text-[10px] text-muted">成功指标</div>
          <div className="flex flex-wrap gap-1.5">
            {p.success_metrics.slice(0, 8).map((m, i) => (
              <span
                key={i}
                title={m.basis || "规则库口径,可被自有数据推翻"}
                className="rounded border border-accent bg-accent-soft px-2 py-0.5 text-[10px] text-accent"
              >
                {`${m.metric || "—"}${m.threshold ? ` ≥ ${m.threshold}` : ""}`}
                {m.confidence ? <span className="ml-1 text-muted">(置信 {m.confidence})</span> : null}
              </span>
            ))}
          </div>
        </div>
      ) : null}
      <div
        className="text-[10px] leading-relaxed text-muted"
        title={meta?.generated_at ? `${meta.generated_at}(UTC 存 · 按浏览器时区显示)` : undefined}
      >
        {`生成于 ${meta?.generated_at ? formatLocal(meta.generated_at) : "—"}`}
        {meta && Object.keys(meta.coverage).length > 0
          ? ` · 覆盖度:${Object.entries(meta.coverage)
              .slice(0, 6)
              .map(([k, v]) => `${k}=${typeof v === "string" || typeof v === "number" ? v : "…"}`)
              .join(" / ")}`
          : ""}
      </div>
    </div>
  );
}

/* ============ 预判/风险为空时的通用短句(SrcChip 承接长口径) ============ */
export function PlanPending({ text = "选 SKU 生成作战预览后点亮。" }: { text?: string }) {
  return <EmptyLine text={text} />;
}
