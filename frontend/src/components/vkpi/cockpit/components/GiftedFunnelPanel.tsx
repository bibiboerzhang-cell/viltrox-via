// G2 件⑤ gifted→posted 履约漏斗面板:全池「送样→发布」对账板。
// 数据:GET /api/admin/vkpi/gifted/funnel —— 纯读端聚合已有派单/物流/证据/内容候选表,
// 零新采集/零 LLM。漏斗条:已送样 / 已发布 / 观察中 / 超期未发;超期红名单带天数与
// 建议动作「催更」;「预览催更建议」按钮走 POST /catch-up?dry_run=true(只预览),
// 「写入 Action Inbox」走 dry_run=false(幂等落台账,仍需人工审批,绝不自动外发)。
// 诚实态:status==="empty" 如实展示 reason;接口失败整块安静缺席(非阻塞增益块)。
// 红线:纯展示 + 台账建议;绝不渲染/触碰 viltrox_fit_score 与 rule_v0。
// U4 会呼吸的指挥室:四段漏斗条入场动态填充(逐段 stagger 100ms,各一次);
// 「超期未发」红段填充后脉冲一次提醒(仅入场,不循环);数字走 AnimatedNumber
// count-up。prefers-reduced-motion 全部降级为静态直显(useReducedMotion 读
// 同名 CSS media query)。数据契约零改,仍纯读后端聚合。
import React from "react";
import { m } from "framer-motion";
import { AlertTriangle, PackageCheck, Send } from "lucide-react";
import { apiFetch } from "../../../../services/http";
import { AnimatedNumber, usePrefersReducedMotion } from "./AnimatedNumber";

const e = React.createElement;

type StageItem = { key?: string; label?: string; n?: number };
type OverdueItem = {
  assignment_id?: number; project_id?: number; project_name?: string; product_name?: string;
  kol_pool_id?: number; kol_name?: string; platform?: string; stage?: string;
  sent_at?: string | null; sent_basis?: string; days_since_sent?: number | null;
  suggested_action?: string;
};
type FunnelResp = {
  status?: string; reason?: string; overdue_days?: number;
  gifted?: number; posted?: number; waiting?: number; overdue?: number;
  post_rate?: number | null; stages?: StageItem[];
  overdue_items?: OverdueItem[]; overdue_truncated?: boolean;
  basis_note?: string; note?: string;
};
type CatchUpResp = {
  ok?: boolean; dry_run?: boolean; overdue?: number; suggestions?: number;
  would_write?: number; written?: number; reason?: string; note?: string;
  items?: { dedupe_key?: string; title?: string; priority?: string; days_since_sent?: number }[];
};

const STAGE_COLORS: Record<string, string> = {
  gifted: "linear-gradient(90deg, rgba(56,189,248,0.35), rgba(56,189,248,0.75))",
  posted: "linear-gradient(90deg, rgba(52,211,153,0.35), rgba(52,211,153,0.75))",
  waiting: "linear-gradient(90deg, rgba(251,191,36,0.30), rgba(251,191,36,0.65))",
  overdue: "linear-gradient(90deg, rgba(244,63,94,0.35), rgba(244,63,94,0.80))",
};

// 漏斗单条:标签 + 比例条(按已送样总数归一)+ count-up 数字。
// 入场:宽度 0 → 目标比例(逐段 stagger 100ms,一次 350ms);「超期未发」红段
// 填充完成后红光脉冲一次(keyframes 走完即止,绝无循环)。reduced-motion 直显。
function FunnelBar({ stage, total, index, reduced }: {
  stage: StageItem; total: number; index: number; reduced: boolean;
}) {
  const raw = Number(stage.n);
  const n = Number.isFinite(raw) ? raw : 0;
  const widthPct = total > 0 ? Math.max(n > 0 ? 6 : 0, Math.round((n / total) * 100)) : 0;
  const isOverdue = String(stage.key) === "overdue" && n > 0;
  const fillDelay = index * 0.1;
  return e("div", { className: "flex items-center gap-2 text-[10px]" },
    e("span", { className: "w-[104px] shrink-0 truncate text-ink-2", title: stage.label }, stage.label || stage.key || "—"),
    e("div", { className: "relative h-[9px] flex-1 overflow-hidden rounded-full bg-card" },
      e(m.div, {
        "data-stage": String(stage.key || ""),
        "data-pulse-once": isOverdue ? "true" : undefined,
        initial: reduced ? false : { width: 0 },
        animate: {
          width: widthPct + "%",
          // 超期红段:入场后单次脉冲(红光起 → 灭),times 走完即静止。
          ...(isOverdue && !reduced
            ? { boxShadow: [
                "0 0 0px rgba(244,63,94,0)",
                "0 0 10px rgba(244,63,94,0.75)",
                "0 0 0px rgba(244,63,94,0)",
              ] }
            : {}),
        },
        transition: reduced
          ? { duration: 0 }
          : {
              width: { delay: fillDelay, duration: 0.35, ease: [0.16, 1, 0.3, 1] },
              boxShadow: { delay: fillDelay + 0.4, duration: 0.6, times: [0, 0.35, 1] },
            },
        className: "absolute inset-y-0 left-0 rounded-full",
        style: { background: STAGE_COLORS[String(stage.key)] || STAGE_COLORS.gifted },
      } as any),
    ),
    e("span", { className: "w-[44px] shrink-0 text-right tabular-nums text-ink-2 font-medium" },
      Number.isFinite(raw) ? e(AnimatedNumber, { value: raw, delayMs: index * 100 }) : "—"),
  );
}

// 超期红名单行:KOL + 项目 + 超期天数 + 建议动作「催更」。
function OverdueRow(it: OverdueItem, i: number) {
  const days = Number(it.days_since_sent);
  return e("div", { key: it.assignment_id || i, className: "rounded border border-rose-400/15 bg-crit-soft px-2 py-1.5" },
    e("div", { className: "flex items-center gap-1.5" },
      e("span", { className: "shrink-0 rounded bg-crit-soft px-1.5 py-0.5 text-[9px] font-bold tabular-nums text-crit" },
        Number.isFinite(days) ? `${days}天` : "—"),
      e("span", { className: "truncate text-[10.5px] text-ink-2", title: it.kol_name }, it.kol_name || `KOL#${it.kol_pool_id ?? "?"}`),
      it.platform && e("span", { className: "shrink-0 text-[9px] text-muted" }, it.platform),
      e("span", { className: "ml-auto shrink-0 rounded border border-amber-300/20 bg-warn-soft px-1.5 py-0.5 text-[9px] text-warn" },
        it.suggested_action || "催更"),
    ),
    e("div", { className: "mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[9px] text-muted" },
      e("span", { className: "truncate", title: it.project_name }, it.project_name || it.product_name || `项目#${it.project_id ?? "?"}`),
      it.sent_at && e("span", { className: "text-muted" }, `送样 ${it.sent_at}`),
      it.sent_basis && e("span", { className: "text-muted", title: "送样时间基准" }, `基准 ${it.sent_basis}`),
    ),
  );
}

export function GiftedFunnelPanel({ apiToken }: any) {
  const [data, setData] = React.useState<FunnelResp | null>(null);
  const [catchUp, setCatchUp] = React.useState<CatchUpResp | null>(null);
  const [busy, setBusy] = React.useState<boolean>(false);
  const reduced = usePrefersReducedMotion();

  React.useEffect(() => {
    setData(null);
    if (!apiToken) return;
    let cancelled = false;
    void apiFetch<FunnelResp>("/api/admin/vkpi/gifted/funnel", {}, apiToken)
      .then((payload) => { if (!cancelled) setData(payload && typeof payload === "object" ? payload : null); })
      .catch(() => { if (!cancelled) setData(null); });
    return () => { cancelled = true; };
  }, [apiToken]);

  // 催更建议:dryRun=true 只预览;false 幂等落 Action Inbox(后端仍要求人工审批)。
  const runCatchUp = (dryRun: boolean) => {
    if (!apiToken || busy) return;
    setBusy(true);
    void apiFetch<CatchUpResp>(
      `/api/admin/vkpi/gifted/funnel/catch-up?dry_run=${dryRun ? "true" : "false"}`,
      { method: "POST" },
      apiToken,
    )
      .then((payload) => setCatchUp(payload && typeof payload === "object" ? payload : null))
      .catch(() => setCatchUp({ ok: false, dry_run: dryRun, reason: "请求失败,稍后再试" }))
      .finally(() => setBusy(false));
  };

  if (!apiToken || !data) return null;
  if (String(data.status || "") === "error") return null; // 聚合失败:安静缺席,不甩后端报错

  const stages = Array.isArray(data.stages) ? data.stages : [];
  const overdueItems = Array.isArray(data.overdue_items) ? data.overdue_items : [];
  const gifted = Number(data.gifted) || 0;
  const overdueN = Number(data.overdue) || 0;
  const rate = typeof data.post_rate === "number" ? (Math.round(data.post_rate * 1000) / 10).toFixed(1) + "%" : null;

  return e("div", { className: "rounded-lg border border-line bg-panel px-4 py-3" },
    // ── 标题行 ──
    e("div", { className: "flex items-center gap-1.5" },
      e(PackageCheck, { size: 12, className: "text-accent" }),
      e("span", { className: "text-[10px] uppercase tracking-wider text-muted" }, "送样 → 发布 履约漏斗"),
      rate && e("span", { className: "rounded bg-good-soft px-1.5 py-0.5 text-[9px] text-good" }, `发布率 ${rate}`),
      overdueN > 0 && e("span", { className: "rounded bg-crit-soft px-1.5 py-0.5 text-[9px] text-crit" }, `超期 ×${overdueN}`),
    ),

    // ── 诚实空态 ──
    data.status === "empty"
      ? e("div", { className: "mt-2 text-[10px] leading-relaxed text-warn" }, String(data.reason || "暂无送样数据"))
      : e(React.Fragment, null,
          // ── 漏斗条(入场逐段 stagger 填充)──
          e("div", { className: "mt-2 space-y-1" },
            stages.map((s, i) => e(FunnelBar, { key: s.key || i, stage: s, total: gifted, index: i, reduced }))),

          // ── 超期红名单 ──
          overdueItems.length > 0 && e(React.Fragment, null,
            e("div", { className: "mt-2.5 mb-1 flex items-center gap-1.5" },
              e(AlertTriangle, { size: 10, className: "text-crit" }),
              e("span", { className: "text-[10px] font-medium text-ink-2" },
                `超期未发红名单(>${Number(data.overdue_days) || 21}天)`),
              data.overdue_truncated && e("span", { className: "text-[8.5px] text-muted" }, "仅展示最久的若干条"),
            ),
            e("div", { className: "max-h-[240px] space-y-1.5 overflow-y-auto pr-0.5" },
              overdueItems.slice(0, 30).map((it, i) => OverdueRow(it, i))),

            // ── 催更建议按钮(dry-run 预览 / 落 Action Inbox)──
            e("div", { className: "mt-2 flex flex-wrap items-center gap-2" },
              e("button", {
                type: "button",
                disabled: busy,
                onClick: () => runCatchUp(true),
                className: "flex items-center gap-1 rounded border border-accent bg-accent-soft px-2 py-1 text-[9.5px] text-accent hover:bg-accent-soft disabled:opacity-50",
              }, e(Send, { size: 9 }), busy ? "处理中…" : "预览催更建议(dry-run)"),
              catchUp && catchUp.dry_run && catchUp.ok && e("button", {
                type: "button",
                disabled: busy,
                onClick: () => runCatchUp(false),
                className: "rounded border border-amber-300/20 bg-warn-soft px-2 py-1 text-[9.5px] text-warn hover:bg-warn-soft disabled:opacity-50",
              }, `写入 Action Inbox(${Number(catchUp.would_write) || 0} 条,仍需人工审批)`),
            ),
            catchUp && e("div", { className: "mt-1 text-[9px] leading-relaxed " + (catchUp.ok ? "text-muted" : "text-crit") },
              catchUp.ok
                ? (catchUp.dry_run
                    ? `预览:${Number(catchUp.would_write) || 0} 条催更建议(未写库)。${String(catchUp.note || "")}`
                    : `已写入 ${Number(catchUp.written) || 0} 条催更建议到 Action Inbox(幂等;仍需人工审批,绝不自动外发)。`)
                : String(catchUp.reason || "催更建议生成失败")),
          ),

          // ── 口径脚注 ──
          e("div", { className: "mt-2 text-[9px] leading-relaxed text-muted" },
            String(data.basis_note || "") || "口径:送样=派单 stage 送样后词表;发布=stage/视频证据/确认内容候选。"),
        ),
  );
}
