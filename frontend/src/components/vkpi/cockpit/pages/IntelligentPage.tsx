import React from "react";
import {
  askIntelligent,
  fetchSuggestions,
  type IntelligentAnswer,
  type IntelligentAction,
  type IntelligentEvidence,
} from "../../../../services/vkpi/intelligent-api";

// 件1 · Intelligent 问答页(三车道)。
//   问题输入框 + suggestions chips + 答案卡(结论加粗 / 证据可展开 / 动作按钮直跳)。
//   后端:/api/admin/vkpi/intelligent/ask | /suggestions。全只读,前端不拼 SQL。
//   动作 route = cockpit nav key;跳转委托给父级 onNavigate(不自持路由表)。

const MODE_LABEL: Record<string, string> = {
  intent: "秒回",
  search: "检索",
  synth: "综合",
  degraded: "降级",
};

const MODE_TONE: Record<string, string> = {
  intent: "border-emerald-300/30 bg-emerald-500/[0.12] text-emerald-100",
  search: "border-sky-300/30 bg-sky-500/[0.12] text-sky-100",
  synth: "border-violet-300/30 bg-violet-500/[0.12] text-violet-100",
  degraded: "border-amber-300/30 bg-amber-500/[0.12] text-amber-100",
};

// 证据渲染:intent_result 走小表格;search_results 走候选列表;其余 JSON 折叠。
function EvidenceBlock({ ev }: { ev: IntelligentEvidence }) {
  if (ev.kind === "intent_result") {
    const columns = Array.isArray(ev.columns) ? (ev.columns as string[]) : [];
    const rows = Array.isArray(ev.rows) ? (ev.rows as Array<Record<string, unknown>>) : [];
    if (columns.length === 0) {
      return <div className="text-[12px] text-slate-500">无列返回</div>;
    }
    return (
      <div className="overflow-auto rounded-lg border border-white/[0.08]">
        <table className="w-full border-collapse text-[11px]">
          <thead>
            <tr className="border-b border-white/[0.08] bg-white/[0.03]">
              {columns.map((c) => (
                <th key={c} className="px-2 py-1.5 text-left font-medium text-sky-300/80">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 50).map((row, ri) => (
              <tr key={ri} className="border-b border-white/[0.04] last:border-0">
                {columns.map((c) => {
                  const v = row[c];
                  return (
                    <td key={c} className="px-2 py-1 text-slate-300/90">
                      {v === null || v === undefined ? "—" : String(v)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  if (ev.kind === "search_results") {
    const results = Array.isArray(ev.results) ? (ev.results as Array<Record<string, unknown>>) : [];
    if (results.length === 0) {
      return <div className="text-[12px] text-slate-500">无候选</div>;
    }
    return (
      <ul className="space-y-1">
        {results.slice(0, 20).map((r, i) => {
          const name = String(r.name ?? r.handle ?? r.username ?? r.kol_pool_id ?? `候选 ${i + 1}`);
          const platform = r.platform ? String(r.platform) : "";
          return (
            <li key={i} className="flex items-center justify-between rounded-md border border-white/[0.06] px-2 py-1 text-[12px] text-slate-300/90">
              <span className="truncate">{name}</span>
              {platform ? <span className="ml-2 shrink-0 text-[10px] text-slate-500">{platform}</span> : null}
            </li>
          );
        })}
      </ul>
    );
  }

  // synth / 未知 kind:JSON 折叠展示。
  return (
    <pre className="overflow-auto rounded-lg border border-white/[0.08] bg-black/20 p-2 text-[10px] text-slate-400">
      {JSON.stringify(ev, null, 2)}
    </pre>
  );
}

export function IntelligentPage({
  apiToken = "",
  onNavigate,
}: {
  apiToken?: string;
  onNavigate?: (navKey: string) => void;
}) {
  const [question, setQuestion] = React.useState<string>("");
  const [suggestions, setSuggestions] = React.useState<string[]>([]);
  const [answer, setAnswer] = React.useState<IntelligentAnswer | null>(null);
  const [loading, setLoading] = React.useState<boolean>(false);
  const [error, setError] = React.useState<string>("");
  const [evidenceOpen, setEvidenceOpen] = React.useState<boolean>(false);

  // 加载当日建议 chips。
  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    fetchSuggestions(apiToken)
      .then((list) => {
        if (alive) setSuggestions(list);
      })
      .catch(() => {
        // 建议加载失败静默:输入框仍可用。
      });
    return () => {
      alive = false;
    };
  }, [apiToken]);

  const ask = React.useCallback(
    (q: string) => {
      const text = q.trim();
      if (!apiToken) {
        setError("未登录 / 无 token");
        return;
      }
      if (!text) {
        setError("请输入一个问题");
        return;
      }
      setLoading(true);
      setError("");
      setAnswer(null);
      setEvidenceOpen(false);
      askIntelligent(apiToken, text)
        .then((res) => setAnswer(res))
        .catch((e: unknown) => setError(e instanceof Error ? e.message : "提问失败"))
        .finally(() => setLoading(false));
    },
    [apiToken],
  );

  const onAction = React.useCallback(
    (action: IntelligentAction) => {
      if (onNavigate && action.route) onNavigate(action.route);
    },
    [onNavigate],
  );

  return (
    <div className="space-y-4 p-4">
      <div>
        <h2 className="text-base font-semibold text-white">Intelligent 问答</h2>
        <p className="mt-1 text-[12px] text-slate-400">
          一个问题,三车道分诊:意图秒回 · 池内检索 · LLM 综合(预算不足自动降级)。全只读。
        </p>
      </div>

      {/* 输入框 + 提问按钮 */}
      <div className="flex items-center gap-2">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !loading) ask(question);
          }}
          placeholder="问点什么…(如:为什么最近转化率下降,给点建议)"
          className="flex-1 rounded-md border border-white/10 bg-white/[0.03] px-3 py-2 text-[13px] text-slate-200 placeholder:text-slate-600"
        />
        <button
          onClick={() => ask(question)}
          disabled={loading || !apiToken || !question.trim()}
          className="rounded-md border border-emerald-300/30 bg-emerald-500/[0.15] px-4 py-2 text-[13px] text-emerald-100 hover:bg-emerald-500/[0.25] disabled:opacity-50"
        >
          {loading ? "思考中…" : "提问"}
        </button>
      </div>

      {/* suggestions chips */}
      {suggestions.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {suggestions.map((s, i) => (
            <button
              key={i}
              onClick={() => {
                setQuestion(s);
                ask(s);
              }}
              disabled={loading}
              className="rounded-full border border-white/10 bg-white/[0.03] px-3 py-1 text-[11px] text-slate-300 hover:bg-white/[0.08] disabled:opacity-50"
            >
              {s}
            </button>
          ))}
        </div>
      ) : null}

      {error ? <div className="text-[12px] text-red-300/80">{error}</div> : null}

      {/* 答案卡 */}
      {answer ? (
        <div className="space-y-3 rounded-xl border border-white/[0.08] bg-white/[0.02] p-4">
          <div className="flex items-center gap-2">
            <span
              className={`rounded-full border px-2 py-0.5 text-[10px] ${
                MODE_TONE[answer.mode] ?? "border-white/10 bg-white/[0.06] text-slate-300"
              }`}
            >
              {MODE_LABEL[answer.mode] ?? answer.mode}
            </span>
            {answer.cached ? (
              <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[10px] text-slate-400">
                当日缓存
              </span>
            ) : null}
          </div>

          {/* 结论加粗 */}
          <div className="whitespace-pre-wrap text-[13px] font-semibold text-white">
            {answer.answer}
          </div>

          {/* 动作按钮直跳路由 */}
          {answer.actions.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {answer.actions.map((a, i) => (
                <button
                  key={i}
                  onClick={() => onAction(a)}
                  className="rounded-md border border-sky-300/30 bg-sky-500/[0.12] px-3 py-1.5 text-[12px] text-sky-100 hover:bg-sky-500/[0.2]"
                >
                  {a.label} →
                </button>
              ))}
            </div>
          ) : null}

          {/* 证据可展开 */}
          {answer.evidence.length > 0 ? (
            <div className="border-t border-white/[0.06] pt-3">
              <button
                onClick={() => setEvidenceOpen((v) => !v)}
                className="text-[11px] text-slate-400 hover:text-slate-200"
              >
                {evidenceOpen ? "收起证据" : `展开证据（${answer.evidence.length}）`}
              </button>
              {evidenceOpen ? (
                <div className="mt-2 space-y-3">
                  {answer.evidence.map((ev, i) => (
                    <EvidenceBlock key={i} ev={ev} />
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : !loading ? (
        <div className="rounded-md border border-dashed border-white/[0.08] px-3 py-10 text-center text-[12px] text-slate-500">
          输入问题或点一个建议开始（本地数据,只读）
        </div>
      ) : null}
    </div>
  );
}
