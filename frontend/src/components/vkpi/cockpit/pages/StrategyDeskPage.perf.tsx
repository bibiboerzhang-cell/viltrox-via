import React from "react";
import { EmptyLine, ModuleCard } from "./MarketVoicePage.modules";
import { formatLocal } from "../../lib/timeLocal";
import { StrategySimPanel } from "../components/StrategySimPanel";
import { ConfChip, fmtNum, fmtPct, MODULE_SOURCES } from "./StrategyDeskPage.modules";
import type { BetsBlock, FulfillmentBlock, LessonItem, PlanVsActualSample, PredGroup, PredictionsBlock } from "./StrategyDeskPage.modules";

// 战略台 · 表现四 body(押注 / 预测 / 履约 / 教训)+ 模拟器 embed。
//   金样板 = GtmCommandBoardPage.embeds 同款手法:StrategySimPanel 旧组件文件绝不改,
//   只用包装容器的 Tailwind 任意变体选择器压平旧卡壳、隐藏与新卡头重复的标题行
//   (控件/结果全保留);其口径已登记 MODULE_SOURCES.sim,诚实信息不丢只挪位置。
//   数据 page 层注入;本文件除 embed 内旧组件自取数外零直连网络。
// 红线:纯读展示;不触 viltrox_fit_score / rule_v0;颜色全 token 零写死色;
//   空态诚实短句(reason 直出后端,不本地编造);时间戳一律绝对时间(存 UTC,
//   按浏览器时区显示,formatLocal 口径)。

const OUTCOME_STYLE: Record<string, string> = {
  won: "border-good bg-good-soft text-good",
  lost: "border-crit bg-crit-soft text-crit",
  open: "border-info bg-info-soft text-info",
  void: "border-line bg-card text-muted",
};
const OUTCOME_LABEL: Record<string, string> = { won: "押对", lost: "押错", open: "未结算", void: "作废" };

/* ============ bets · 押注台账(won/lost/open + 最老 open 注账龄) ============ */

export function BetsBody({ bets }: { bets: BetsBlock }) {
  if (String(bets.status || "") !== "ok") return <EmptyLine text={String(bets.reason || "押注账不可用")} />;
  const oldest = bets.oldest_open;
  const counts: Array<[string, number]> = [
    ["won", Number(bets.won) || 0],
    ["lost", Number(bets.lost) || 0],
    ["open", Number(bets.open) || 0],
    ["void", Number(bets.void) || 0],
  ];
  return (
    <div>
      <div className="flex flex-wrap items-center gap-1.5">
        {counts
          .filter(([k, n]) => n > 0 || k !== "void")
          .map(([k, n]) => (
            <span key={k} className={`rounded-md border px-2 py-0.5 font-mono text-[11px] tabular-nums ${OUTCOME_STYLE[k] || ""}`}>
              {`${OUTCOME_LABEL[k] || k} ${n}`}
            </span>
          ))}
        <ConfChip level={bets.confidence} extra={`已结算 ${Number(bets.settled) || 0}`} />
      </div>
      {typeof bets.hit_rate === "number" ? (
        <div className="mt-1 font-mono text-[10px] tabular-nums text-muted">
          已结算命中 {fmtPct(bets.hit_rate)}({Number(bets.settled) || 0} 注)
        </div>
      ) : null}
      {oldest ? (
        <div className="mt-1.5 rounded-lg border border-info bg-info-soft px-2 py-1.5">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px]">
            <span className="font-medium text-info">最老未结算注</span>
            <span className="font-mono tabular-nums text-ink-2">账龄 {oldest.age_days ?? "—"} 天</span>
            {oldest.review_overdue ? (
              <span className="rounded-md border border-crit bg-crit-soft px-1.5 py-0.5 text-crit">
                复盘已过期(约定 {formatLocal(oldest.review_at)})
              </span>
            ) : (
              <span className="text-muted">约定复盘 {formatLocal(oldest.review_at)}</span>
            )}
            {typeof oldest.probability === "number" ? (
              <span className="font-mono tabular-nums text-muted">押 p={oldest.probability}</span>
            ) : null}
          </div>
          <div className="mt-0.5 text-[10.5px] leading-relaxed text-ink-2" title={oldest.hypothesis}>
            {oldest.hypothesis || "—"}
          </div>
        </div>
      ) : null}
    </div>
  );
}

/* ============ preds · 预测命中率(组条形 + 置信/样本 + 积压总账) ============ */

function PredRow({ group }: { group: PredGroup }) {
  const sample = Number(group.sample_count) || 0;
  const rate = typeof group.hit_rate === "number" ? group.hit_rate : null;
  const pending = Number(group.pending_count) || 0;
  const widthPct = sample > 0 && rate != null ? Math.max(3, Math.round(rate * 100)) : 0;
  const barColor =
    sample <= 0 || rate == null
      ? "color-mix(in srgb, var(--ds-muted) 30%, transparent)"
      : rate >= 0.7
        ? "linear-gradient(90deg, color-mix(in srgb, var(--ds-good) 45%, transparent), var(--ds-good))"
        : rate >= 0.4
          ? "linear-gradient(90deg, color-mix(in srgb, var(--ds-warn) 45%, transparent), var(--ds-warn))"
          : "linear-gradient(90deg, color-mix(in srgb, var(--ds-crit) 45%, transparent), var(--ds-crit))";
  return (
    <div className="flex items-center gap-2">
      <span className="w-[104px] flex-none truncate text-[11px] text-ink-2" title={`${group.label || ""}(${group.action_type || ""})`}>
        {group.label || group.action_type || "—"}
      </span>
      <span className="h-[6px] min-w-0 flex-1 overflow-hidden rounded-[3px] bg-line">
        {widthPct > 0 ? <i className="block h-full rounded-[3px]" style={{ width: `${widthPct}%`, background: barColor }} /> : null}
      </span>
      <span className={`w-[46px] flex-none text-right font-mono text-[10.5px] tabular-nums ${sample > 0 ? "text-ink" : "text-muted"}`}>
        {fmtPct(rate)}
      </span>
      <ConfChip level={group.confidence} extra={`样本 ${sample}`} />
      {pending > 0 ? <span className="flex-none font-mono text-[9px] tabular-nums text-muted">积压 {fmtNum(pending)}</span> : null}
    </div>
  );
}

export function PredsBody({ preds }: { preds: PredictionsBlock }) {
  if (String(preds.status || "") !== "ok") return <EmptyLine text={String(preds.reason || "预测台账不可用")} />;
  const groups = Array.isArray(preds.groups) ? preds.groups : [];
  const backlog = preds.backlog_top;
  return (
    <div>
      {groups.length === 0 ? (
        <EmptyLine text="台账无任何分组(诚实空账)。" />
      ) : (
        <div className="space-y-1.5">
          {groups.map((g, i) => (
            <PredRow key={g.action_type || i} group={g} />
          ))}
        </div>
      )}
      {(Number(preds.pending_total) || 0) > 0 ? (
        <div className="mt-2 rounded-lg border border-warn bg-warn-soft px-2 py-1.5 text-[10px] leading-relaxed text-ink-2">
          待对答案积压 <b className="font-semibold text-warn">{fmtNum(preds.pending_total)}</b> 条(有预测无结果)
          {backlog ? `,大头在「${backlog.label || backlog.action_type}」${fmtNum(backlog.pending_count)} 条` : ""};已裁决合计{" "}
          {fmtNum(preds.judged_total)} 条。
        </div>
      ) : null}
    </div>
  );
}

/* ============ ful · 履约对账(loop 步骤留痕 + planned vs actual 样例) ============ */

function SampleRow({ sample }: { sample: PlanVsActualSample }) {
  const planned = sample.planned || {};
  const actual = sample.actual || {};
  const views = Number(actual.view_count) || 0;
  const within = sample.published_within_window;
  return (
    <div className="rounded-lg border border-line bg-panel px-2 py-1.5">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10.5px]">
        <span className="font-medium text-ink">{sample.project_name || `项目 #${sample.project_id ?? "—"}`}</span>
        <span className="font-mono text-muted">KOL #{sample.kol_pool_id ?? "—"}</span>
        <span className="rounded-md border border-line bg-card px-1.5 py-0.5 text-[9px] text-ink-2">
          {sample.post_status === "retrospective_ready" ? "人工确认·已复盘" : sample.post_status === "matched" ? "窗口匹配" : String(sample.post_status || "—")}
        </span>
        {sample.content_url ? (
          <a
            href={sample.content_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[9.5px] text-accent transition-colors hover:text-accent-hover hover:underline"
          >
            内容链接 ↗
          </a>
        ) : null}
      </div>
      <div className="mt-0.5 grid grid-cols-1 gap-x-3 font-mono text-[9.5px] tabular-nums md:grid-cols-2">
        <div className="text-muted">
          <span className="text-ink-2">计划:</span>
          {planned.window_id != null
            ? ` 观察窗口 ${formatLocal(planned.starts_at)} ~ ${formatLocal(planned.ends_at)}(扫 ${Number(planned.scan_count) || 0} 次)`
            : " 无观察窗口(人工手录)"}
        </div>
        <div className="text-muted">
          <span className="text-ink-2">实际:</span>
          {` 发布 ${formatLocal(actual.published_at)} · 播放 ${views > 0 ? fmtNum(views) : "0(未回填)"}`}
        </div>
      </div>
      {within != null ? (
        <div className={`mt-0.5 text-[9.5px] ${within ? "text-good" : "text-warn"}`}>
          {within ? "发布落在计划窗口内" : "发布时点在计划窗口之外(如实记录,不粉饰)"}
        </div>
      ) : null}
    </div>
  );
}

export function FulBody({ ful }: { ful: FulfillmentBlock }) {
  if (String(ful.status || "") !== "ok") return <EmptyLine text={String(ful.reason || "履约账不可用")} />;
  const loop = ful.first_loop;
  const windows = ful.windows || {};
  const posts = ful.posts || {};
  const samples = Array.isArray(ful.plan_vs_actual?.samples) ? ful.plan_vs_actual!.samples! : [];
  return (
    <div>
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-0.5 font-mono text-[10.5px] tabular-nums text-ink-2">
        <span>
          完成闭环 <b className="font-semibold text-good">{Number(ful.loops_completed) || 0}</b>
        </span>
        <span>
          观察窗口 {Number(windows.total) || 0}(匹配 {Number(windows.matched) || 0} / 扫描中 {Number(windows.scanning) || 0})
        </span>
        <span>
          内容帖 确认 {Number(posts.confirmed) || 0} / 候选 {Number(posts.candidates) || 0}
        </span>
        <ConfChip level={ful.confidence} extra={`确认 ${Number(posts.confirmed) || 0}`} />
      </div>
      {loop ? (
        <div className="mt-1.5 flex flex-wrap items-center gap-1 text-[9.5px]">
          <span className="text-muted">
            首条真实闭环 run #{loop.run_id}({formatLocal(loop.created_at)}):
          </span>
          {(loop.steps || []).map((st, i) => (
            <span
              key={i}
              className={`rounded-md border px-1.5 py-0.5 font-mono tabular-nums ${
                String(st.status) === "done" ? "border-good bg-good-soft text-good" : "border-warn bg-warn-soft text-warn"
              }`}
            >
              {`${st.step_name}${String(st.status) === "done" ? " ✓" : ` (${st.status})`}`}
            </span>
          ))}
        </div>
      ) : null}
      {samples.length > 0 ? (
        <div className="mt-2 space-y-1.5">
          <div className="text-[10px] text-muted">计划 vs 实际 样例(计划=真实观察窗口,不编计划日期):</div>
          {samples.slice(0, 3).map((s, i) => (
            <SampleRow key={s.post_id || i} sample={s} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

/* ============ lessons · 已沉淀教训 top5 + 数据荒诚实条 ============ */

export function LessonsBody({
  lessons,
  honesty,
  generatedAt,
}: {
  lessons: { status?: string; reason?: string | null; items?: LessonItem[] };
  honesty: string[];
  generatedAt?: string;
}) {
  const items = Array.isArray(lessons.items) ? lessons.items : [];
  return (
    <div>
      {String(lessons.status || "") !== "ok" || items.length === 0 ? (
        <EmptyLine text={String(lessons.reason || "还没沉淀出教训。")} />
      ) : (
        <div className="space-y-1">
          {items.map((item, i) => (
            <div key={i} className="rounded-lg border border-line bg-panel px-2 py-1">
              <div className="text-[10.5px] leading-relaxed text-ink-2">{`${i + 1}. ${item.text || ""}`}</div>
              <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[9px] text-muted">
                <span>
                  来源 {item.source || "—"}({item.ref || "—"})
                </span>
                {item.context ? <span>{item.context}</span> : null}
                {item.at ? <span className="font-mono tabular-nums">{formatLocal(item.at)}</span> : null}
              </div>
            </div>
          ))}
        </div>
      )}
      {honesty.length > 0 ? (
        <div className="mt-2 rounded-lg border border-warn bg-warn-soft px-2 py-1.5">
          <div className="text-[9.5px] font-medium text-warn">哪本账还空着 · 直说</div>
          <div className="mt-0.5 space-y-0.5">
            {honesty.map((line, i) => (
              <div key={i} className="text-[9.5px] leading-relaxed text-ink-2">
                · {line}
              </div>
            ))}
          </div>
        </div>
      ) : null}
      {generatedAt ? (
        <div className="mt-1.5 font-mono text-[9px] tabular-nums text-muted" title={`${generatedAt}(UTC 存 · 按浏览器时区显示)`}>
          生成于 {formatLocal(generatedAt)}
        </div>
      ) : null}
    </div>
  );
}

/* ============ sim · 策略模拟器 embed(旧组件零改动收编;GTM 金样板同款压平) ============ */

// StrategySimPanel:外壳 rounded-2xl border bg-white/[0.015] p-3(换肤层糊 !important)压平;
// 标题行 + 介绍副题两行隐藏(口径在 MODULE_SOURCES.sim);控件/结果全保留。
const SIM_TRIM = [
  "[&.vkpi-embed[data-embed]>div]:!rounded-none [&.vkpi-embed[data-embed]>div]:!border-0",
  "[&.vkpi-embed[data-embed]>div]:!bg-transparent [&.vkpi-embed[data-embed]>div]:!p-0",
  "[&>div>div:first-child]:hidden [&>div>div:nth-child(2)]:hidden",
].join(" ");

export function SimEmbed({ apiToken, noToken }: { apiToken: string; noToken: React.ReactNode }) {
  const src = MODULE_SOURCES.sim;
  return (
    <ModuleCard title="策略模拟器" srcLabel={src.label} srcRows={src.rows}>
      {apiToken ? (
        <div data-embed="sim" className={`vkpi-embed ${SIM_TRIM}`}>
          <StrategySimPanel apiToken={apiToken} />
        </div>
      ) : (
        noToken
      )}
    </ModuleCard>
  );
}
