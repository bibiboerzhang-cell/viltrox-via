import React from "react";
import { apiFetch, jsonBody } from "../../../../services/http";
import { PredictionLedgerPanel } from "../components/PredictionLedgerPanel";

// 件A · 自治驾照面板(作战地图廿一节「挣来的自治」L0-L4)。
//   每类 agent 动作一张驾照卡:当前级 / 五维能力 / 命中率 / 最近升降 / 人工调级。
//   后端:GET /api/admin/vkpi/autonomy/licenses | POST /autonomy/evaluate?dry_run= |
//   POST /autonomy/licenses/{action_type}/override。升降判定纯规则零 LLM。
//   红线:「影响评分」维度永久禁止(后端硬编码 False,本页只展示不提供开关);
//   L4 绝不自动授予,只有人工调级一条路;本轮只建判定与展示,不真执行外部动作。

type Row = Record<string, any>;

const ACTION_LABEL: Record<string, string> = {
  kol_recommend: "KOL 推荐",
  outreach_draft: "外联草稿",
  comment_reply_draft: "评论回复草稿",
  pool_enrich: "Pool 补全",
  report_generate: "报告生成",
};

const LEVEL_LABEL: Record<number, string> = {
  0: "观察",
  1: "建议",
  2: "内部执行",
  3: "半自主",
  4: "全自主",
};

const LEVEL_TONE: Record<number, string> = {
  0: "border-white/[0.15] bg-white/[0.06] text-slate-300",
  1: "border-sky-300/30 bg-sky-500/[0.12] text-sky-100",
  2: "border-emerald-300/30 bg-emerald-500/[0.12] text-emerald-100",
  3: "border-amber-300/30 bg-amber-500/[0.12] text-amber-100",
  4: "border-rose-300/30 bg-rose-500/[0.12] text-rose-100",
};

const DIMENSION_LABEL: [string, string][] = [
  ["write_db", "写库"],
  ["call_llm", "调LLM"],
  ["spend_money", "花钱"],
  ["contact_external", "联系外部"],
  ["change_project_status", "改项目状态"],
];

const DECISION_TONE: Record<string, string> = {
  promote: "border-emerald-300/30 bg-emerald-500/[0.12] text-emerald-100",
  demote: "border-rose-300/30 bg-rose-500/[0.12] text-rose-100",
  hold: "border-white/[0.12] bg-white/[0.04] text-slate-300",
};

const DECISION_LABEL: Record<string, string> = { promote: "升1级", demote: "降1级", hold: "hold" };

function fmtRate(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  return `${(Number(v) * 100).toFixed(0)}%`;
}

function SectionCard({ title, children, extra }: { title: string; children: React.ReactNode; extra?: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-white/[0.06] bg-white/[0.015] p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="text-[13px] font-semibold text-white">{title}</div>
        {extra ?? null}
      </div>
      {children}
    </div>
  );
}

function EmptyLine({ text }: { text: string }) {
  return <div className="rounded-lg border border-dashed border-white/[0.08] px-3 py-4 text-center text-[12px] text-slate-500">{text}</div>;
}

function DimensionChip({ label, allowed }: { label: string; allowed: boolean }) {
  return (
    <span
      className={`rounded-full border px-2 py-0.5 text-[10px] ${
        allowed ? "border-emerald-300/30 bg-emerald-500/[0.1] text-emerald-100" : "border-white/[0.1] bg-white/[0.02] text-slate-500"
      }`}
    >
      {label} {allowed ? "许" : "禁"}
    </span>
  );
}

export function AutonomyBoardPage({ apiToken = "" }: { apiToken?: string; onNavigate?: (navKey: string) => void }) {
  const [items, setItems] = React.useState<Row[]>([]);
  const [rules, setRules] = React.useState<Row>({});
  const [loadError, setLoadError] = React.useState("");
  const [emptyReason, setEmptyReason] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [evalResult, setEvalResult] = React.useState<Row | null>(null);
  const [evalBusy, setEvalBusy] = React.useState(false);
  const [overrideLevel, setOverrideLevel] = React.useState<Record<string, string>>({});
  const [overrideReason, setOverrideReason] = React.useState<Record<string, string>>({});
  const [overrideBusy, setOverrideBusy] = React.useState<Record<string, boolean>>({});
  const [overrideMsg, setOverrideMsg] = React.useState<Record<string, string>>({});

  const loadLicenses = React.useCallback(() => {
    if (!apiToken) return;
    setLoading(true);
    setLoadError("");
    apiFetch<Row>("/api/admin/vkpi/autonomy/licenses", { timeoutMs: 15000 }, apiToken)
      .then((res) => {
        setItems(Array.isArray(res.items) ? res.items : []);
        setRules(res.rules && typeof res.rules === "object" ? res.rules : {});
        setEmptyReason(res.status !== "ready" ? String(res.reason || "") : "");
      })
      .catch((err: any) => setLoadError(String(err?.detail || err?.message || "加载失败")))
      .finally(() => setLoading(false));
  }, [apiToken]);

  React.useEffect(() => {
    loadLicenses();
  }, [loadLicenses]);

  const runEvaluate = React.useCallback(
    (dryRun: boolean) => {
      if (!apiToken || evalBusy) return;
      if (!dryRun && !window.confirm("确认执行升降并写库(dry_run=false)。升降只动驾照级别,不执行任何外部动作。")) return;
      setEvalBusy(true);
      apiFetch<Row>(`/api/admin/vkpi/autonomy/evaluate?dry_run=${dryRun ? "true" : "false"}`, { method: "POST" }, apiToken)
        .then((res) => {
          setEvalResult(res);
          if (!dryRun) loadLicenses();
        })
        .catch((err: any) => setEvalResult({ status: "error", reason: String(err?.detail || err?.message || "评估失败") }))
        .finally(() => setEvalBusy(false));
    },
    [apiToken, evalBusy, loadLicenses],
  );

  const runOverride = React.useCallback(
    (actionType: string) => {
      if (!apiToken || overrideBusy[actionType]) return;
      const level = overrideLevel[actionType];
      const reason = (overrideReason[actionType] || "").trim();
      if (level === undefined || level === "") {
        setOverrideMsg((m) => ({ ...m, [actionType]: "先选目标级别" }));
        return;
      }
      if (!reason) {
        setOverrideMsg((m) => ({ ...m, [actionType]: "人工调级必须写 reason" }));
        return;
      }
      setOverrideBusy((m) => ({ ...m, [actionType]: true }));
      setOverrideMsg((m) => ({ ...m, [actionType]: "" }));
      apiFetch<Row>(
        `/api/admin/vkpi/autonomy/licenses/${encodeURIComponent(actionType)}/override`,
        { method: "POST", body: jsonBody({ level: Number(level), reason }) },
        apiToken,
      )
        .then((res) => {
          if (res.status === "error") {
            setOverrideMsg((m) => ({ ...m, [actionType]: String(res.reason || "调级失败") }));
            return;
          }
          setOverrideMsg((m) => ({
            ...m,
            [actionType]: `已调至 L${res.level}(原 L${res.previous_level ?? "?"})`,
          }));
          setOverrideReason((m) => ({ ...m, [actionType]: "" }));
          loadLicenses();
        })
        .catch((err: any) => setOverrideMsg((m) => ({ ...m, [actionType]: String(err?.detail || err?.message || "调级失败") })))
        .finally(() => setOverrideBusy((m) => ({ ...m, [actionType]: false })));
    },
    [apiToken, overrideBusy, overrideLevel, overrideReason, loadLicenses],
  );

  const evalItems: Row[] = Array.isArray(evalResult?.items) ? evalResult!.items : [];

  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4 md:p-6">
      <div>
        <div className="text-[18px] font-semibold text-white">自治驾照 L0-L4</div>
        <div className="mt-0.5 text-[12px] text-slate-400">
          挣来的自治:每类 agent 动作一张驾照,级别靠预测台账命中率挣、靠连续失手降。本页只建判定与展示,不执行任何外部动作。
        </div>
      </div>

      {/* 升降规则说明 */}
      <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5 text-[11px] leading-relaxed text-slate-400">
        <span className="text-emerald-200/90">升:</span>
        {rules.promote || "近20次命中率 >= 0.85 且样本 >= 20 → 升1级(自动封顶 L3,L4 仅人工)"}
        <span className="mx-1.5 text-slate-600">·</span>
        <span className="text-rose-200/90">降:</span>
        {rules.demote || "连续 5 次未命中 → 降1级(保底 L0)"}
        <span className="mx-1.5 text-slate-600">·</span>
        <span className="text-slate-300">hold:</span>
        {rules.hold || "台账缺席 / 样本不足 → 原地不动并说明"}
        <div className="mt-1 text-rose-300/80">红线:「影响评分」维度永久禁止 —— 任何级别、任何调级路径都不授予改评分能力(后端硬编码)。</div>
      </div>

      {/* 评估按钮 */}
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={() => runEvaluate(true)}
          disabled={!apiToken || evalBusy}
          className="rounded-lg border border-sky-300/30 bg-sky-500/[0.12] px-3 py-1.5 text-[12px] text-sky-100 hover:bg-sky-500/[0.2] disabled:opacity-50"
        >
          {evalBusy ? "评估中…" : "评估升降(演练 dry-run)"}
        </button>
        <button
          onClick={() => runEvaluate(false)}
          disabled={!apiToken || evalBusy}
          className="rounded-lg border border-amber-300/30 bg-amber-500/[0.12] px-3 py-1.5 text-[12px] text-amber-100 hover:bg-amber-500/[0.2] disabled:opacity-50"
        >
          执行升降(写库)
        </button>
        <button
          onClick={loadLicenses}
          disabled={!apiToken || loading}
          className="rounded-lg border border-white/[0.1] px-3 py-1.5 text-[12px] text-slate-300 hover:bg-white/[0.05] disabled:opacity-50"
        >
          刷新
        </button>
      </div>

      {/* 评估结果 */}
      {evalResult && (
        <SectionCard
          title={`评估结果${evalResult.dry_run ? "(演练,未落库)" : "(已执行)"}`}
          extra={<span className="text-[10px] text-slate-500">{evalResult.evaluated_at || ""}</span>}
        >
          {evalResult.status === "error" || evalResult.status === "empty" ? (
            <EmptyLine text={String(evalResult.reason || "评估无结果")} />
          ) : evalItems.length === 0 ? (
            <EmptyLine text="没有驾照可评估。" />
          ) : (
            <div className="space-y-1.5">
              {evalItems.map((it) => (
                <div key={String(it.action_type)} className="flex flex-wrap items-center gap-2 rounded-lg border border-white/[0.06] px-3 py-2">
                  <span className="text-[12px] text-slate-200">{ACTION_LABEL[String(it.action_type)] || it.action_type}</span>
                  <span className={`rounded-md border px-2 py-0.5 text-[10px] ${DECISION_TONE[String(it.decision)] || DECISION_TONE.hold}`}>
                    {DECISION_LABEL[String(it.decision)] || it.decision}
                    {it.decision !== "hold" ? ` L${it.current_level}→L${it.proposed_level}` : ""}
                  </span>
                  {it.applied ? <span className="rounded-md border border-emerald-300/30 px-1.5 py-0.5 text-[10px] text-emerald-200">已落库</span> : null}
                  {it.apply_error ? <span className="text-[10px] text-rose-300">落库失败:{it.apply_error}</span> : null}
                  <span className="min-w-0 flex-1 truncate text-[11px] text-slate-500" title={String(it.reason || "")}>
                    {it.reason || ""}
                  </span>
                </div>
              ))}
            </div>
          )}
        </SectionCard>
      )}

      {!apiToken && <EmptyLine text="未登录 / 无 token,无法加载数据。" />}
      {loadError && <div className="rounded-lg border border-rose-300/30 bg-rose-500/[0.08] px-3 py-2 text-[12px] text-rose-200">{loadError}</div>}
      {loading && <div className="py-10 text-center text-[12px] text-slate-400">驾照加载中…</div>}
      {!loading && !loadError && apiToken && items.length === 0 && (
        <EmptyLine text={emptyReason || "暂无驾照记录(迁移212的初始种子未落库)。"} />
      )}

      {/* 驾照卡片 */}
      {!loading &&
        items.map((it) => {
          const action = String(it.action_type || "");
          const level = Number(it.level ?? 0);
          const dims: Row = it.dimensions && typeof it.dimensions === "object" ? it.dimensions : {};
          const ledger: Row = it.ledger && typeof it.ledger === "object" ? it.ledger : {};
          const liveRate = typeof ledger.hit_rate === "number" ? ledger.hit_rate : null;
          return (
            <SectionCard
              key={action}
              title={ACTION_LABEL[action] || action}
              extra={
                <span className={`rounded-md border px-2 py-0.5 text-[11px] ${LEVEL_TONE[level] || LEVEL_TONE[0]}`}>
                  L{level} {it.level_label || LEVEL_LABEL[level] || ""}
                </span>
              }
            >
              <div className="space-y-3">
                {/* 五维能力 + 永久禁止维度 */}
                <div className="flex flex-wrap items-center gap-1.5">
                  {DIMENSION_LABEL.map(([key, label]) => (
                    <DimensionChip key={key} label={label} allowed={Boolean(dims[key])} />
                  ))}
                  <span
                    className="rounded-full border border-rose-300/40 bg-rose-500/[0.1] px-2 py-0.5 text-[10px] text-rose-200"
                    title="红线:任何级别、任何调级路径都不授予 agent 改评分能力(后端硬编码永久 False)"
                  >
                    影响评分 永久禁止
                  </span>
                </div>

                {/* 命中率读数 */}
                <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
                  <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2">
                    <div className="text-[10px] text-slate-500">台账实时命中率(近20次)</div>
                    <div className="mt-0.5 text-[15px] font-semibold text-white">{fmtRate(liveRate)}</div>
                  </div>
                  <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2">
                    <div className="text-[10px] text-slate-500">台账样本数</div>
                    <div className="mt-0.5 text-[15px] font-semibold text-white">
                      {typeof ledger.sample_count === "number" ? ledger.sample_count : "—"}
                    </div>
                  </div>
                  <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2">
                    <div className="text-[10px] text-slate-500">上次变更时快照</div>
                    <div className="mt-0.5 text-[15px] font-semibold text-white">
                      {fmtRate(it.hit_rate_snapshot)}
                      <span className="ml-1 text-[10px] font-normal text-slate-500">样本 {it.sample_count ?? 0}</span>
                    </div>
                  </div>
                  <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2">
                    <div className="text-[10px] text-slate-500">台账状态</div>
                    <div className="mt-0.5 truncate text-[12px] text-slate-300" title={String(ledger.reason || "")}>
                      {String(ledger.status || "—")}
                    </div>
                  </div>
                </div>
                {ledger.status !== "ready" && ledger.status !== "ok" && ledger.reason ? (
                  <div className="text-[11px] text-amber-200/80">{String(ledger.reason)}</div>
                ) : null}

                {/* 升降历史(表只存最近一次变更,诚实说明) */}
                <div className="rounded-lg border border-white/[0.06] px-3 py-2">
                  <div className="text-[10px] text-slate-500">最近一次升降(表只存最近一条,非全量历史)</div>
                  <div className="mt-0.5 text-[12px] text-slate-300">{it.last_change_reason || "—"}</div>
                  <div className="mt-0.5 text-[10px] text-slate-500">{it.changed_at || ""}</div>
                </div>

                {/* 人工调级 */}
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[11px] text-slate-500">人工调级:</span>
                  <select
                    value={overrideLevel[action] ?? ""}
                    onChange={(ev) => setOverrideLevel((m) => ({ ...m, [action]: ev.target.value }))}
                    className="rounded-lg border border-white/[0.1] bg-[#10151f] px-2 py-1.5 text-[12px] text-slate-200 outline-none focus:border-sky-300/40"
                  >
                    <option value="">目标级别…</option>
                    {[0, 1, 2, 3, 4].map((lv) => (
                      <option key={lv} value={String(lv)}>
                        L{lv} {LEVEL_LABEL[lv]}
                        {lv === 4 ? "(仅人工可授)" : ""}
                      </option>
                    ))}
                  </select>
                  <input
                    value={overrideReason[action] ?? ""}
                    onChange={(ev) => setOverrideReason((m) => ({ ...m, [action]: ev.target.value }))}
                    placeholder="调级理由(必填)"
                    className="min-w-[200px] flex-1 rounded-lg border border-white/[0.1] bg-white/[0.03] px-2 py-1.5 text-[12px] text-slate-200 placeholder:text-slate-600 outline-none focus:border-sky-300/40"
                  />
                  <button
                    onClick={() => runOverride(action)}
                    disabled={Boolean(overrideBusy[action])}
                    className="rounded-lg border border-white/[0.12] px-3 py-1.5 text-[12px] text-slate-200 hover:bg-white/[0.06] disabled:opacity-50"
                  >
                    {overrideBusy[action] ? "调级中…" : "确认调级"}
                  </button>
                  {overrideMsg[action] ? <span className="text-[11px] text-amber-200/90">{overrideMsg[action]}</span> : null}
                </div>
              </div>
            </SectionCard>
          );
        })}

      {/* 第5轮收口:预测台账与驾照板同屏——升降的证据底座 */}
      <PredictionLedgerPanel apiToken={apiToken} />

      <div className="text-right text-[10px] text-slate-600">升降判定纯规则零 LLM · 台账数据来自 prediction_ledger(缺席诚实 hold)</div>
    </div>
  );
}
