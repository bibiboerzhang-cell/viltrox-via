import React from "react";
import { getKolTwin } from "../../../services/vkpi/kolPool-api";

// K3 · 数字孪生卡(合作判断档案)：等级 + 为什么 + 合作建议 + 学习信号。Apple 风,清爽。
// graceful:取不到/无 token → 不渲染,绝不破坏宿主。红线:纯展示,grade 非 viltrox_fit_score。

const GRADE: Record<string, string> = {
  A: "text-emerald-300 border-emerald-400/40 bg-emerald-500/[0.12]",
  B: "text-sky-300 border-sky-400/40 bg-sky-500/[0.12]",
  C: "text-amber-300 border-amber-400/40 bg-amber-500/[0.12]",
  D: "text-slate-400 border-white/15 bg-white/[0.04]",
};

export function KolTwinCard({ kolPoolId, apiToken }: { kolPoolId?: string | number; apiToken?: string }) {
  const [twin, setTwin] = React.useState<any>(null);
  React.useEffect(() => {
    if (!apiToken || kolPoolId == null || kolPoolId === "") return;
    let alive = true;
    getKolTwin(apiToken, kolPoolId)
      .then((r: any) => { if (alive) setTwin(r && r.status === "ok" ? r : null); })
      .catch(() => {});
    return () => { alive = false; };
  }, [apiToken, kolPoolId]);

  if (!twin) return null;
  const grade = String(twin.data_grade || "");
  const perf = twin.performance || {};
  const oc = perf.recent_outcomes || {};
  return (
    <div className="rounded-2xl border border-white/[0.08] bg-white/[0.025] p-4">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-[12px] font-semibold text-white">合作判断档案</span>
        {grade ? <span className={`rounded-md border px-1.5 py-0.5 text-[10px] font-bold ${GRADE[grade] || GRADE.D}`}>等级 {grade}</span> : null}
      </div>
      <div className="rounded-lg bg-violet-500/[0.08] px-3 py-2 text-[12px] text-violet-100">
        {twin.cooperation_suggestion || "—"}
      </div>
      {Array.isArray(twin.why_recommended) && twin.why_recommended.length ? (
        <div className="mt-2">
          <div className="text-[10px] text-slate-500">为什么推荐</div>
          <ul className="mt-0.5 list-disc pl-4 text-[11px] text-slate-300">
            {twin.why_recommended.slice(0, 3).map((w: any, i: number) => <li key={i}>{String(w)}</li>)}
          </ul>
        </div>
      ) : null}
      <div className="mt-2 flex flex-wrap gap-2 text-[10px] text-slate-400">
        <span>权重 {perf.recommendation_weight ?? "—"}</span>
        <span>·</span>
        <span>历史 成功 {oc.success ?? 0} / 失败 {oc.fail ?? 0}</span>
        {twin.why_remembered ? <><span>·</span><span className="truncate max-w-[180px]" title={twin.why_remembered}>{twin.why_remembered}</span></> : null}
      </div>
    </div>
  );
}
