// L1(2026-06-30):运维健康小卡 — Dashboard 右侧栏。
// 只读自取 /api/admin/vkpi/triage/jobs(失败队列待处理数),一键进运维 Triage 队列。
// 零触 fit_score;无 token / 拉取失败 → 静默降级为「--」,绝不编造数字。

import React from "react";
import { Loader2, ShieldCheck, ArrowRight } from "lucide-react";
import { listTriageJobs } from "../../../../services/vkpi/triage-api";

interface OpsHealthCardProps {
  apiToken?: string;
  /** 一键进运维 Triage 队列(cockpit 把它接到 setActiveNav("triage"))。 */
  onOpenTriage?: () => void;
}

export function OpsHealthCard({ apiToken = "", onOpenTriage }: OpsHealthCardProps) {
  const [count, setCount] = React.useState<number | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [errored, setErrored] = React.useState(false);

  React.useEffect(() => {
    if (!apiToken) {
      setCount(null);
      setErrored(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setErrored(false);
    listTriageJobs(apiToken, 100)
      .then((res) => {
        if (cancelled) return;
        const n = typeof res?.count === "number" ? res.count : (res?.items?.length ?? 0);
        setCount(n);
      })
      .catch(() => {
        if (!cancelled) {
          setErrored(true);
          setCount(null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken]);

  const hasBacklog = typeof count === "number" && count > 0;
  const valueLabel = loading ? "…" : errored || count === null ? "--" : String(count);

  return (
    <div className="rounded-xl border border-line bg-panel p-4 backdrop-blur-xl">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ShieldCheck size={14} className={hasBacklog ? "text-warn" : "text-good"} />
          <h3 className="text-sm font-semibold text-ink">运维健康</h3>
        </div>
        {loading ? <Loader2 size={12} className="animate-spin text-muted" /> : null}
      </div>

      <div className="flex items-end justify-between gap-3">
        <div>
          <div className="flex items-baseline gap-1.5">
            <span
              className={`text-2xl font-semibold tabular-nums ${hasBacklog ? "text-warn" : "text-ink"}`}
            >
              {valueLabel}
            </span>
            <span className="text-[11px] text-muted">条待处理</span>
          </div>
          <p className="mt-0.5 text-[10px] text-muted">
            {errored
              ? "队列读取失败"
              : hasBacklog
                ? "失败任务等待人工 Triage"
                : "失败队列已清空"}
          </p>
        </div>
      </div>

      <button
        type="button"
        onClick={() => onOpenTriage?.()}
        className="mt-3 flex w-full items-center justify-center gap-1.5 rounded-md border border-line bg-panel px-3 py-1.5 text-[11px] text-ink-2 transition hover:bg-accent-soft hover:text-ink"
      >
        进入 Triage 队列
        <ArrowRight size={12} />
      </button>
    </div>
  );
}

export default OpsHealthCard;
