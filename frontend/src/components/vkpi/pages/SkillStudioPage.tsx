import React from "react";
import {
  listSkills,
  listSkillRuns,
  runSkill,
  type SkillSummary,
  type SkillRunRow,
  type SkillRunResult,
} from "../../../services/vkpi/skills-api";

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

export function SkillStudioPage({ apiToken = "" }: { apiToken?: string }) {
  const [skills, setSkills] = React.useState<SkillSummary[]>([]);
  const [selected, setSelected] = React.useState<string>("");
  const [values, setValues] = React.useState<Record<string, string>>({});
  const [runs, setRuns] = React.useState<SkillRunRow[]>([]);
  const [result, setResult] = React.useState<SkillRunResult | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [running, setRunning] = React.useState(false);
  const [err, setErr] = React.useState<string>("");

  const loadSkills = React.useCallback(() => {
    if (!apiToken) {
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
  }, [apiToken]);

  const loadRuns = React.useCallback(
    (name: string) => {
      if (!apiToken) return;
      listSkillRuns(apiToken, name, 20)
        .then(setRuns)
        .catch(() => setRuns([]));
    },
    [apiToken],
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
    if (!apiToken || !selected) return;
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
  }, [apiToken, selected, fields, values, loadRuns, loadSkills]);

  if (loading) return <div className="p-6 text-sm text-slate-400">Skill Studio 加载中…</div>;
  if (!apiToken) return <div className="p-6 text-sm text-red-300/80">未登录 / 无 token</div>;

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
                  <Metric label="runs" value={String(s.runs ?? 0)} />
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
            <div className="mb-2 text-[12px] font-semibold text-white">最近 runs{selected ? ` · ${selected}` : ""}</div>
            {runs.length === 0 ? (
              <div className="text-[11px] text-slate-400">暂无运行记录</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-[10px]">
                  <thead className="text-slate-500">
                    <tr>
                      <th className="py-1 pr-2 font-medium">#</th>
                      <th className="py-1 pr-2 font-medium">model</th>
                      <th className="py-1 pr-2 font-medium">成本</th>
                      <th className="py-1 pr-2 font-medium">延迟</th>
                      <th className="py-1 pr-2 font-medium">采纳</th>
                      <th className="py-1 pr-2 font-medium">时间</th>
                    </tr>
                  </thead>
                  <tbody className="text-slate-200">
                    {runs.map((r) => (
                      <tr key={r.id} className="border-t border-white/[0.06]">
                        <td className="py-1 pr-2 text-slate-400">{r.id}</td>
                        <td className="py-1 pr-2 truncate">{r.model_used || "—"}</td>
                        <td className="py-1 pr-2">{cents(r.cost_cents)}</td>
                        <td className="py-1 pr-2">{ms(r.latency_ms)}</td>
                        <td className="py-1 pr-2">
                          {r.accepted == null ? (
                            <span className="text-slate-500">待评</span>
                          ) : r.accepted ? (
                            <span className="text-emerald-300">采纳</span>
                          ) : (
                            <span className="text-red-300">拒</span>
                          )}
                        </td>
                        <td className="py-1 pr-2 text-slate-400">{fmtTime(r.created_at)}</td>
                      </tr>
                    ))}
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
