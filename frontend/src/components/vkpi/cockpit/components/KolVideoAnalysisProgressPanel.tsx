import React from "react";
import { Loader2 } from "lucide-react";

import {
  getKolVideoAnalysisProgress,
  type VkpiKolVideoAnalysisProgress,
} from "../../../../services/vkpi/kolPool-api";
import { etaLabelForLocale, etaSecondsOf, hasReadableFailure } from "../../../../services/vkpi/failureReason";
import { FailureGuidance } from "../lib/failureGuidance";
import { useT } from "../lib/i18n";

// MY KOL 深析进度(优化波 B · F3/F7):账号 N 条视频的整体进度 + 失败可读 + ETA 新口径。
//   数据源 GET /kol-pool/{id}/video-analysis-progress(O 车道,只读);旧后端 404 → 整块不渲染。
//   active 时 10s 轮询(最长 30 分钟);refreshKey 变化(入队动作回执)立即重拉。
//   门面:只说「完成 / 进行中 / 未完成 / 预计剩余」,失败项按 failure_category 给提示与动作。

const POLL_MS = 10_000;
const POLL_MAX_MS = 30 * 60_000;

export function KolVideoAnalysisProgressPanel({
  apiToken,
  kolPoolId,
  refreshKey = "",
  onReissue,
  reissueBusy = false,
}: {
  apiToken: string;
  kolPoolId: string | number | null | undefined;
  /** 父层入队动作的回执变化 → 立即重拉一次 */
  refreshKey?: string;
  /** authorization 类失败「从 MY KOL 重新发起」(抽屉内=跳 MY KOL,由父层决定) */
  onReissue?: () => void;
  reissueBusy?: boolean;
}) {
  const { t, lang } = useT();
  const [progress, setProgress] = React.useState<VkpiKolVideoAnalysisProgress | null>(null);
  const [unavailable, setUnavailable] = React.useState(false);
  const poolId = String(kolPoolId ?? "").trim();

  React.useEffect(() => {
    if (!apiToken || !poolId) {
      setProgress(null);
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const startedAt = Date.now();
    const controller = new AbortController();
    const load = async () => {
      try {
        const next = await getKolVideoAnalysisProgress(apiToken, poolId, { signal: controller.signal });
        if (cancelled) return;
        setProgress(next);
        setUnavailable(false);
        const active = String(next?.state || "") === "running" && Number(next?.in_progress || 0) > 0;
        if (active && Date.now() - startedAt < POLL_MAX_MS) timer = setTimeout(() => void load(), POLL_MS);
      } catch (error) {
        if (cancelled) return;
        const status = Number((error as { status?: unknown })?.status);
        // 旧后端没有该路由 / 无权读 → 静默不渲染(不编进度);其他错误保留上一份快照。
        if (status === 404 || status === 403) setUnavailable(true);
      }
    };
    void load();
    return () => {
      cancelled = true;
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [apiToken, poolId, refreshKey]);

  if (!apiToken || !poolId || unavailable || !progress) return null;
  const total = Number(progress.scope?.scope_total ?? 0) || 0;
  if (!total || progress.state === "no_evidence") return null;
  const completed = Number(progress.completed || 0);
  const inProgress = Number(progress.in_progress || 0);
  const failed = Number(progress.failed || 0);
  const running = progress.state === "running" && inProgress > 0;
  const eta = running ? etaLabelForLocale(etaSecondsOf(progress), lang) : "";
  const failedItems = (progress.items || []).filter((item) => hasReadableFailure(item)).slice(0, 5);
  return (
    <div className="mx-5 mb-2.5 rounded-md border border-white/[0.06] bg-white/[0.015] px-3 py-2" data-vkpi-video-progress={progress.state}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-slate-300">
        <span className="font-medium text-slate-200">{t("深析进度")}</span>
        <span className="tabular-nums">{t("完成")} {completed}/{total}</span>
        {inProgress > 0 ? (
          <span className="inline-flex items-center gap-1 tabular-nums text-cyan-200">
            {running ? <Loader2 size={10} className="animate-spin" /> : null}
            {t("进行中")} {inProgress}
          </span>
        ) : null}
        {failed > 0 ? <span className="tabular-nums text-amber-200">{t("未完成")} {failed}</span> : null}
        {eta ? <span className="text-slate-400" data-vkpi-eta-seconds={String(etaSecondsOf(progress))}>{t("预计剩余")} {eta}</span> : null}
      </div>
      {running ? (
        <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-white/[0.06]">
          <div className="h-full rounded-full bg-cyan-400/70 transition-[width] duration-300" style={{ width: `${Math.max(3, Math.min(100, Number(progress.percent || 0)))}%` }} />
        </div>
      ) : null}
      {failedItems.length ? (
        <div className="mt-1.5 space-y-1">
          {failedItems.map((item) => (
            <div key={item.evidence_id} className="text-[9.5px] text-slate-400">
              <span className="truncate text-slate-300" title={String(item.content_url || "")}>{item.title || `#${item.evidence_id}`}</span>
              <FailureGuidance source={item} onReissue={onReissue} reissueBusy={reissueBusy} compact />
            </div>
          ))}
          {failed > failedItems.length ? <div className="text-[9px] text-slate-600">{t("其余未完成项见 MY KOL 视频列表")}</div> : null}
        </div>
      ) : null}
    </div>
  );
}
