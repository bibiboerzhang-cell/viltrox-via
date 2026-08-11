import React from "react";
import {
  getSkillReviewCandidate,
  listSkills,
  listSkillRuns,
  reviewSkillRun,
  runSkill,
  type SkillSummary,
  type SkillRunRow,
  type SkillRunResult,
  type SkillReviewCandidate,
} from "../../../services/vkpi/skills-api";
import {
  normalizeSha256,
} from "../../../services/vkpi/review-integrity";
import { validateSkillReviewCandidate } from "../../../services/vkpi/skill-review-candidate";

// VOS Skill Studio —— 让「营销大脑」可见可操作。
//   ① 列出 skills + 采纳率 / 成本 / 延迟
//   ② 选一个 skill → 按 INPUT_SCHEMA 渲染表单 → Run → 展示 output + 落账提示
//   ③ 下方列最近 runs
// 纯前端;后端 3 端点由另一路建,契约一致即可。红线:绝不触 viltrox_fit_score。

// 后端 list 接口未必返回 input_schema;此处给已知 skill 的本地回退 schema(对齐各 skill 的 INPUT_SCHEMA)。
// 后端若在 summary 里带 input_schema,则优先用后端的。
const FALLBACK_SCHEMAS: Record<string, Record<string, string>> = {
  creator_match: {
    product: "string  产品名/描述(与 sku 二选一)",
    sku: "string  SKU(与 product 二选一)",
    market: "string  目标主市场(如 US/CN/HK)",
    secondary_markets: "string  可选,逗号分隔的次级市场",
    budget: "int  可选,预算上限(分)",
    limit: "int  可选,返回候选数上限,默认 20",
  },
  brief_generate: {
    kol_pool_id: "int (required) — 定位要合作的 KOL",
    product: "string (required) — 产品名",
    angle: "string (optional) — 内容切角/卖点提示",
  },
  content_score: {
    video_url: "string?  视频 URL(标识/回退)",
    analysis_cache_ref: "string?  JSON {target_id, derive_method?}",
  },
  roi_review: {
    project_id: "int?  复盘单个项目 ROI(与 kol_pool_id 二选一)",
    kol_pool_id: "int?  复盘单个 KOL ROI(与 project_id 二选一)",
    window: "int?  数据窗口天数,默认 30",
  },
  campaign_plan: {
    product: "string  产品 / SKU 名(必填)",
    market: "string  目标市场(如 US/EU/CN;可空=全球)",
    budget_cents: "int  战役总预算(分;>=0)",
    goal: "string  战役目标(awareness/conversion/launch;可空)",
  },
};

type FieldKind = "int" | "string";

interface FieldDef {
  name: string;
  hint: string;
  kind: FieldKind;
}

interface ReviewDraft {
  humanScore: string;
  businessResult: string;
  evidence: string;
  correlationId: string;
}

type RunIntegrity = {
  runId: number;
  status: "loading" | "valid" | "invalid" | "error";
  candidate: SkillReviewCandidate | null;
  inputSnapshot: Record<string, unknown> | null;
  outputSnapshot: unknown;
  expectedInput: string;
  expected: string;
  reason: string;
};

function reviewCorrelation(runId: number): string {
  const suffix = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `skill-review-${runId}-${suffix}`;
}

function outputSummary(output: unknown): string {
  if (!output || typeof output !== "object") return "无可读输出摘要";
  if (!Array.isArray(output)) {
    const row = output as Record<string, unknown>;
    for (const key of ["summary", "conclusion", "recommendation", "reason", "status"]) {
      const value = row[key];
      if (typeof value === "string" && value.trim()) return `${key}: ${value.trim().slice(0, 300)}`;
    }
  }
  const compact = JSON.stringify(output);
  return compact ? compact.slice(0, 300) : "无可读输出摘要";
}

function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? "—";
  } catch {
    return "JSON 无法序列化";
  }
}

function schemaToFields(schema: Record<string, unknown> | undefined | null): FieldDef[] {
  if (!schema || typeof schema !== "object") return [];
  return Object.entries(schema).map(([name, raw]) => {
    const hint = typeof raw === "string" ? raw : JSON.stringify(raw);
    const kind: FieldKind = /\bint\b/i.test(hint) ? "int" : "string";
    return { name, hint, kind };
  });
}

// 表单字段值 → run 入参:int 字段转数字(空则跳过);其余空串跳过;JSON-looking 串尝试解析。
function buildInput(fields: FieldDef[], values: Record<string, string>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of fields) {
    const v = (values[f.name] ?? "").trim();
    if (v === "") continue;
    if (f.kind === "int") {
      const n = Number(v);
      if (Number.isFinite(n)) out[f.name] = Math.trunc(n);
      continue;
    }
    if ((v.startsWith("{") && v.endsWith("}")) || (v.startsWith("[") && v.endsWith("]"))) {
      try {
        out[f.name] = JSON.parse(v);
        continue;
      } catch {
        /* 解析失败则当字符串落 */
      }
    }
    out[f.name] = v;
  }
  return out;
}

function pct(x: number | null | undefined): string {
  return x == null ? "—" : `${Math.round(x * 100)}%`;
}
function cents(x: number | null | undefined): string {
  return x == null ? "—" : `${(Number(x) / 100).toFixed(2)}$`;
}
function ms(x: number | null | undefined): string {
  return x == null ? "—" : `${Math.round(Number(x))}ms`;
}
function fmtTime(s: string | null | undefined): string {
  if (!s) return "—";
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? String(s) : d.toLocaleString();
}

export function SkillStudioPage({
  apiToken = "",
  viewMode = "manager",
}: {
  apiToken?: string;
  viewMode?: "manager" | "employee";
}) {
  const canManage = viewMode === "manager";
  const [skills, setSkills] = React.useState<SkillSummary[]>([]);
  const [selected, setSelected] = React.useState<string>("");
  const [values, setValues] = React.useState<Record<string, string>>({});
  const [runs, setRuns] = React.useState<SkillRunRow[]>([]);
  const [result, setResult] = React.useState<SkillRunResult | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [running, setRunning] = React.useState(false);
  const [reviewing, setReviewing] = React.useState<number | null>(null);
  const [reviewOpen, setReviewOpen] = React.useState<number | null>(null);
  const [reviewDraft, setReviewDraft] = React.useState<ReviewDraft | null>(null);
  const [reviewNote, setReviewNote] = React.useState("");
  const [reviewFilter, setReviewFilter] = React.useState<"pending" | "reviewed" | "all">("pending");
  const [err, setErr] = React.useState<string>("");
  const [runsErr, setRunsErr] = React.useState<string>("");
  const runsRequest = React.useRef(0);
  const integrityRequest = React.useRef(0);
  const [runIntegrity, setRunIntegrity] = React.useState<RunIntegrity | null>(null);

  const loadSkills = React.useCallback(() => {
    if (!apiToken || !canManage) {
      setLoading(false);
      return;
    }
    setLoading(true);
    listSkills(apiToken)
      .then((rows) => {
        setSkills(rows);
        setSelected((cur) => cur || (rows[0]?.skill_name ?? ""));
      })
      .catch((e) => setErr(String(e?.message || e)))
      .finally(() => setLoading(false));
  }, [apiToken, canManage]);

  const loadRuns = React.useCallback(
    (name: string) => {
      if (!apiToken || !canManage) return;
      const requestId = ++runsRequest.current;
      integrityRequest.current += 1;
      setReviewOpen(null);
      setReviewDraft(null);
      setRunIntegrity(null);
      setRunsErr("");
      listSkillRuns(apiToken, name, 100, reviewFilter)
        .then((rows) => {
          if (requestId !== runsRequest.current) return;
          setRuns(rows);
          setRunsErr("");
        })
        .catch((cause: any) => {
          if (requestId !== runsRequest.current) return;
          setRuns([]);
          setRunsErr(String(cause?.message || "运行记录加载失败"));
        });
    },
    [apiToken, canManage, reviewFilter],
  );

  React.useEffect(() => loadSkills(), [loadSkills]);
  React.useEffect(() => {
    if (selected) loadRuns(selected);
    setResult(null);
    setValues({});
  }, [selected, loadRuns]);

  const current = skills.find((s) => s.skill_name === selected);
  const fields = React.useMemo(() => {
    const fromBackend = schemaToFields(current?.input_schema);
    if (fromBackend.length) return fromBackend;
    return schemaToFields(FALLBACK_SCHEMAS[selected]);
  }, [current, selected]);

  const onRun = React.useCallback(async () => {
    if (!apiToken || !canManage || !selected) return;
    setRunning(true);
    setErr("");
    setResult(null);
    try {
      const input = buildInput(fields, values);
      const r = await runSkill(apiToken, selected, input);
      setResult(r);
      loadRuns(selected);
      loadSkills(); // 刷新聚合(runs/采纳率会变)
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setRunning(false);
    }
  }, [apiToken, canManage, selected, fields, values, loadRuns, loadSkills]);

  const openReview = React.useCallback((run: SkillRunRow) => {
    if (!canManage) return;
    setReviewNote("");
    if (reviewOpen === run.id) {
      integrityRequest.current += 1;
      setReviewOpen(null);
      setReviewDraft(null);
      setRunIntegrity(null);
      return;
    }
    setReviewOpen(run.id);
    setReviewDraft(run.accepted == null ? {
      humanScore: "",
      businessResult: "",
      evidence: "",
      correlationId: reviewCorrelation(run.id),
    } : null);
    const requestId = ++integrityRequest.current;
    setRunIntegrity({
      runId: run.id,
      status: "loading",
      candidate: null,
      inputSnapshot: null,
      outputSnapshot: null,
      expectedInput: "",
      expected: "",
      reason: "正在加载脱敏复核候选",
    });
    void getSkillReviewCandidate(apiToken, run.id)
      .then(async (candidate) => {
        if (requestId !== integrityRequest.current) return;
        const validation = await validateSkillReviewCandidate(candidate, run);
        if (requestId !== integrityRequest.current) return;
        if (!validation.ok) {
          setRunIntegrity({
            runId: run.id,
            status: "invalid",
            candidate: null,
            inputSnapshot: null,
            outputSnapshot: null,
            expectedInput: validation.expectedInput,
            expected: validation.expectedOutput,
            reason: validation.reason,
          });
          return;
        }
        setRunIntegrity({
          runId: run.id,
          status: "valid",
          candidate: validation.candidate,
          inputSnapshot: validation.inputSnapshot,
          outputSnapshot: validation.outputSnapshot,
          expectedInput: validation.expectedInput,
          expected: validation.expectedOutput,
          reason: "脱敏复核候选与输入/输出指纹一致",
        });
      })
      .catch((cause: any) => {
        if (requestId !== integrityRequest.current) return;
        setRunIntegrity({
          runId: run.id,
          status: "error",
          candidate: null,
          inputSnapshot: null,
          outputSnapshot: null,
          expectedInput: "",
          expected: "",
          reason: String(cause?.message || "脱敏复核候选加载失败"),
        });
      });
  }, [apiToken, canManage, reviewOpen]);

  const submitReview = React.useCallback(async (run: SkillRunRow, accepted: boolean) => {
    if (!apiToken || !canManage || !reviewDraft || reviewing != null) return;
    if (
      run.accepted != null
      || runIntegrity?.runId !== run.id
      || runIntegrity.status !== "valid"
      || !normalizeSha256(runIntegrity.expectedInput)
      || !normalizeSha256(runIntegrity.expected)
    ) {
      setErr("运行输入/输出与指纹未通过校验，禁止形成盲审样本");
      return;
    }
    const score = Number(reviewDraft.humanScore);
    const businessResult = reviewDraft.businessResult.trim();
    const evidence = reviewDraft.evidence
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((reference) => ({ source: "manual", type: "reference", reference }));
    if (
      !reviewDraft.humanScore.trim()
      || !Number.isFinite(score)
      || score < 0
      || score > 5
      || !businessResult
      || businessResult.length > 1000
      || evidence.length === 0
      || evidence.length > 20
      || evidence.some(({ reference }) => reference.length < 4 || reference.length > 500)
    ) {
      setErr("人工复核需要 0–5 分、业务结果，以及 1–20 条、每条 4–500 字的人工依据");
      return;
    }
    setReviewing(run.id);
    setErr("");
    try {
      const receipt = await reviewSkillRun(apiToken, run.id, {
        accepted,
        human_score: score,
        business_result: businessResult,
        evidence,
        correlation_id: reviewDraft.correlationId,
        expected_input_sha256: runIntegrity.expectedInput,
        expected_output_sha256: runIntegrity.expected,
      });
      setReviewNote(`已人工复核 #${receipt.run_id} · ${accepted ? "采纳" : "拒绝"} · 事件 #${receipt.event_id}`);
      setReviewOpen(null);
      setReviewDraft(null);
      loadRuns(selected);
      loadSkills();
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setReviewing(null);
    }
  }, [apiToken, canManage, reviewDraft, reviewing, runIntegrity, selected, loadRuns, loadSkills]);

  if (loading) return <div className="p-6 text-sm text-slate-400">Skill Studio 加载中…</div>;
  if (!apiToken) return <div className="p-6 text-sm text-red-300/80">未登录 / 无 token</div>;
  if (!canManage) {
    return (
      <div className="p-6 text-sm text-slate-400">
        Skill Studio 运行与人工复核仅对管理视角开放。
      </div>
    );
  }

  return (
    <div className="space-y-4 p-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-white">VOS Skill Studio</h2>
          <div className="text-[11px] text-slate-400">营销大脑 · 选 skill → 填入参 → Run → 看产出与落账</div>
        </div>
        <button
          type="button"
          onClick={loadSkills}
          className="rounded border border-white/10 px-2 py-1 text-[11px] text-slate-300 hover:bg-white/5"
        >
          刷新
        </button>
      </div>

      {err ? (
        <div className="rounded border border-red-500/25 bg-red-500/[0.08] px-3 py-2 text-[11px] text-red-300">
          {err}
        </div>
      ) : null}
      {reviewNote ? (
        <div className="rounded border border-emerald-500/25 bg-emerald-500/[0.08] px-3 py-2 text-[11px] text-emerald-300">
          {reviewNote}
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,360px)_minmax(0,1fr)]">
        {/* 左:skill 列表 + 指标 */}
        <div className="space-y-2">
          {skills.length === 0 ? (
            <div className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-4 text-[11px] text-slate-400">
              暂无 skill(后端 /api/admin/vkpi/skills 未返回数据)
            </div>
          ) : null}
          {skills.map((s) => {
            const active = s.skill_name === selected;
            return (
              <button
                key={s.skill_name}
                type="button"
                onClick={() => setSelected(s.skill_name)}
                className={`w-full rounded-xl border p-3 text-left transition ${
                  active
                    ? "border-sky-500/40 bg-sky-500/[0.10]"
                    : "border-white/[0.08] bg-white/[0.025] hover:bg-white/[0.05]"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-[12px] font-semibold text-white">{s.skill_name}</span>
                  <span className="shrink-0 rounded border border-white/10 px-1.5 py-0.5 text-[9px] text-slate-400">
                    {s.version || "v1"}
                  </span>
                </div>
                <div className="mt-2 grid grid-cols-4 gap-1 text-center">
                  <Metric label="复核/运行" value={`${s.reviewed_runs ?? 0}/${s.runs ?? 0}`} />
                  <Metric label="采纳" value={pct(s.acceptance_rate)} />
                  <Metric label="成本" value={cents(s.avg_cost_cents)} />
                  <Metric label="延迟" value={ms(s.avg_latency_ms)} />
                </div>
              </button>
            );
          })}
        </div>

        {/* 右:input 表单 + output + 落账提示 */}
        <div className="space-y-3">
          <div className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-3">
            <div className="mb-2 text-[12px] font-semibold text-white">
              {selected ? `输入 · ${selected}` : "选择一个 skill"}
            </div>
            {fields.length === 0 ? (
              <div className="text-[11px] text-slate-400">
                {selected ? "无可填字段 / 未知 input schema —— 仍可直接 Run(空入参)。" : "左侧选一个 skill。"}
              </div>
            ) : (
              <div className="space-y-2">
                {fields.map((f) => (
                  <label key={f.name} className="block">
                    <div className="mb-0.5 flex items-baseline gap-1.5">
                      <span className="text-[11px] font-medium text-slate-200">{f.name}</span>
                      <span className="rounded bg-white/5 px-1 text-[8px] text-slate-400">{f.kind}</span>
                    </div>
                    <input
                      type={f.kind === "int" ? "number" : "text"}
                      value={values[f.name] ?? ""}
                      onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))}
                      placeholder={f.hint}
                      className="w-full rounded border border-white/10 bg-black/20 px-2 py-1 text-[11px] text-slate-100 placeholder:text-slate-600 focus:border-sky-500/40 focus:outline-none"
                    />
                  </label>
                ))}
              </div>
            )}
            <div className="mt-3 flex items-center gap-2">
              <button
                type="button"
                disabled={!selected || running}
                onClick={onRun}
                className="rounded bg-sky-500/80 px-3 py-1.5 text-[11px] font-semibold text-white disabled:opacity-40 hover:bg-sky-500"
              >
                {running ? "运行中…" : "Run"}
              </button>
              <span className="text-[10px] text-slate-500">运行会落一行 vkpi_skill_runs 账本</span>
            </div>
          </div>

          {result ? (
            <div className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-[12px] font-semibold text-white">输出</span>
                <span className="flex items-center gap-2 text-[10px]">
                  <span
                    className={`rounded border px-1.5 py-0.5 ${
                      result.status === "ok" || result.status === "success"
                        ? "border-emerald-500/25 bg-emerald-500/[0.08] text-emerald-300"
                        : "border-amber-500/25 bg-amber-500/[0.08] text-amber-300"
                    }`}
                  >
                    {result.status || "?"}
                  </span>
                  {result.skill_run_id != null ? (
                    <span className="text-slate-400">已落账 #{result.skill_run_id}</span>
                  ) : (
                    <span className="text-slate-500">未返回 run id</span>
                  )}
                </span>
              </div>
              {result.error ? (
                <div className="mb-2 text-[11px] text-red-300">{result.error}</div>
              ) : null}
              <pre className="max-h-72 overflow-auto rounded bg-black/30 p-2 text-[10px] leading-relaxed text-slate-200">
                {JSON.stringify(result.output ?? result, null, 2)}
              </pre>
            </div>
          ) : null}

          <div className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <span className="text-[12px] font-semibold text-white">最近 runs{selected ? ` · ${selected}` : ""}</span>
              <div className="flex gap-1" aria-label="评审状态筛选">
                {(["pending", "reviewed", "all"] as const).map((value) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => setReviewFilter(value)}
                    className={`rounded border px-1.5 py-0.5 text-[9px] ${
                      reviewFilter === value
                        ? "border-sky-500/35 bg-sky-500/10 text-sky-300"
                        : "border-white/10 text-slate-500"
                    }`}
                  >
                    {value === "pending" ? "待评" : value === "reviewed" ? "已评" : "全部"}
                  </button>
                ))}
              </div>
            </div>
            {runsErr ? (
              <div className="text-[11px] text-red-300">运行记录加载失败，不等于暂无记录：{runsErr}</div>
            ) : runs.length === 0 ? (
              <div className="text-[11px] text-slate-400">暂无运行记录</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-[10px]">
                  <thead className="text-slate-500">
                    <tr>
                      <th className="py-1 pr-2 font-medium">#</th>
                      <th className="py-1 pr-2 font-medium">复核候选</th>
                      <th className="py-1 pr-2 font-medium">成本</th>
                      <th className="py-1 pr-2 font-medium">延迟</th>
                      <th className="py-1 pr-2 font-medium">采纳</th>
                      <th className="py-1 pr-2 font-medium">时间</th>
                    </tr>
                  </thead>
                  <tbody className="text-slate-200">
                    {runs.map((r) => {
                      const open = reviewOpen === r.id;
                      const integrity = runIntegrity?.runId === r.id ? runIntegrity : null;
                      const candidate = integrity?.status === "valid" ? integrity.candidate : null;
                      const canReview = r.accepted == null && candidate != null && reviewDraft != null;
                      return (
                        <React.Fragment key={r.id}>
                          <tr className="border-t border-white/[0.06]">
                            <td className="py-1 pr-2 text-slate-400">{r.id}</td>
                            <td className="py-1 pr-2">
                              <div className="truncate">
                                {r.review_candidate_available === false ? "详情不可用" : "经理权限按需回读"}
                              </div>
                              <div className="truncate text-[8.5px] text-slate-500">
                                {normalizeSha256(r.output_sha256) ? "输出 hash 已登记" : "hash 随候选确认"}
                              </div>
                            </td>
                            <td className="py-1 pr-2">{cents(r.cost_cents)}</td>
                            <td className="py-1 pr-2">{ms(r.latency_ms)}</td>
                            <td className="py-1 pr-2">
                              <div>
                                {r.accepted == null ? (
                                  <span className="text-amber-300">待人工复核</span>
                                ) : r.accepted ? (
                                  <span className="text-emerald-300">采纳 · {r.human_score ?? "—"}/5</span>
                                ) : (
                                  <span className="text-red-300">拒绝 · {r.human_score ?? "—"}/5</span>
                                )}
                              </div>
                              <button
                                type="button"
                                className="mt-0.5 rounded border border-sky-500/30 px-1.5 py-0.5 text-sky-300 hover:bg-sky-500/10"
                                onClick={() => openReview(r)}
                              >
                                {open ? "收起详情" : r.accepted == null ? "查看并复核" : "查看详情"}
                              </button>
                            </td>
                            <td className="py-1 pr-2 text-slate-400">{fmtTime(r.created_at)}</td>
                          </tr>
                          {open ? (
                            <tr className="border-t border-sky-500/15 bg-sky-500/[0.04]">
                              <td colSpan={6} className="p-2">
                                {candidate ? (
                                  <div className="space-y-2">
                                    <div className="grid gap-2 lg:grid-cols-2">
                                      <div className="rounded border border-white/[0.06] bg-black/20 p-2">
                                        <div className="text-[9px] font-semibold text-slate-300">运行输入（服务端脱敏快照）</div>
                                        <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap text-[9px] text-slate-400">{prettyJson(integrity?.inputSnapshot)}</pre>
                                      </div>
                                      <div className="rounded border border-white/[0.06] bg-black/20 p-2">
                                        <div className="text-[9px] font-semibold text-slate-300">输出摘要</div>
                                        <div className="mt-1 whitespace-pre-wrap text-[9px] text-slate-400">
                                          {outputSummary(integrity?.outputSnapshot)}
                                        </div>
                                      </div>
                                    </div>
                                    <details className="rounded border border-white/[0.06] bg-black/20 p-2">
                                      <summary className="cursor-pointer text-[9px] text-sky-300">完整脱敏输出 JSON</summary>
                                      <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap text-[9px] text-slate-400">{prettyJson(integrity?.outputSnapshot)}</pre>
                                    </details>
                                    <div className="grid gap-1 text-[9px] text-slate-500 md:grid-cols-2">
                                      <div>模型：<span className="text-slate-300">{candidate.model_used}</span></div>
                                      <div>提示版本：<span className="text-slate-300">{candidate.prompt_version}</span></div>
                                      <div className="break-all md:col-span-2">输入 SHA-256：<span className="font-mono text-slate-300">{integrity?.expectedInput}</span></div>
                                      <div className="break-all md:col-span-2">输出 SHA-256：<span className="font-mono text-slate-300">{integrity?.expected}</span></div>
                                    </div>
                                  </div>
                                ) : null}
                                <div
                                  role="status"
                                  className={`mt-2 text-[9px] ${
                                    integrity?.status === "valid"
                                      ? "text-emerald-300"
                                      : integrity?.status === "loading"
                                        ? "text-amber-300"
                                        : "text-red-300"
                                  }`}
                                >
                                  {integrity?.reason || "脱敏复核候选尚未加载"}
                                </div>
                                {r.accepted != null && r.business_result ? (
                                  <div className="mt-2 text-[9px] text-slate-400">已有人工复核记录：{r.business_result}</div>
                                ) : null}
                                {r.accepted == null && reviewDraft ? (
                                  <>
                                    <div className="mt-2 grid gap-2 md:grid-cols-[90px_minmax(0,1fr)]">
                                      <label className="text-[10px] text-slate-400">
                                        人工评分 0–5
                                        <input
                                          type="number"
                                          min={0}
                                          max={5}
                                          step={0.5}
                                          value={reviewDraft.humanScore}
                                          onChange={(event) => setReviewDraft((d) => d ? { ...d, humanScore: event.target.value } : d)}
                                          className="mt-1 w-full rounded border border-white/10 bg-black/20 px-2 py-1 text-slate-100"
                                        />
                                      </label>
                                      <label className="text-[10px] text-slate-400">
                                        人工复核结论
                                        <input
                                          value={reviewDraft.businessResult}
                                          onChange={(event) => setReviewDraft((d) => d ? { ...d, businessResult: event.target.value } : d)}
                                          placeholder="例如：进入 shortlist；人工判断与输出一致"
                                          maxLength={1000}
                                          className="mt-1 w-full rounded border border-white/10 bg-black/20 px-2 py-1 text-slate-100"
                                        />
                                      </label>
                                    </div>
                                    <label className="mt-2 block text-[10px] text-slate-400">
                                      人工依据（每行一条 URL、项目号或验收记录）
                                      <textarea
                                        value={reviewDraft.evidence}
                                        onChange={(event) => setReviewDraft((d) => d ? { ...d, evidence: event.target.value } : d)}
                                        rows={2}
                                        className="mt-1 w-full rounded border border-white/10 bg-black/20 px-2 py-1 text-slate-100"
                                      />
                                    </label>
                                    <div className="mt-2 flex gap-2">
                                      <button
                                        type="button"
                                        disabled={!canReview || reviewing != null}
                                        onClick={() => void submitReview(r, true)}
                                        className="rounded bg-emerald-500/80 px-2 py-1 text-[10px] text-white disabled:opacity-40"
                                      >
                                        {reviewing === r.id ? "提交中…" : "采纳并记录人工复核样本"}
                                      </button>
                                      <button
                                        type="button"
                                        disabled={!canReview || reviewing != null}
                                        onClick={() => void submitReview(r, false)}
                                        className="rounded border border-red-500/30 px-2 py-1 text-[10px] text-red-300 disabled:opacity-40"
                                      >
                                        拒绝并记录人工复核样本
                                      </button>
                                    </div>
                                  </>
                                ) : null}
                              </td>
                            </tr>
                          ) : null}
                        </React.Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded bg-white/[0.03] px-1 py-1">
      <div className="text-[11px] font-semibold text-white">{value}</div>
      <div className="text-[8px] text-slate-500">{label}</div>
    </div>
  );
}
