import React from "react";
import { AlertTriangle, CheckCircle2, CircleDashed } from "lucide-react";

import {
  getMyKolClosureReadiness,
  type VkpiMyKolClosureReadinessResponse,
} from "../../../../services/vkpi/myKolClosureReadiness-api";


const STATE_LABELS: Record<string, string> = {
  no_targets: "暂无可配置对象",
  needs_employee_selection: "待员工选择",
  needs_employee_choice: "待员工选择",
  configured_scheduler_disabled: "已配置 · 调度未开启",
  configured_waiting_first_success: "已配置 · 等待首次成功",
  configured_waiting_first_measurement: "已配置 · 等待首次实测",
  partially_measured: "部分已实测",
  operational: "已运行且有成功证据",
  configured: "已配置",
  no_tracked_videos: "尚无追踪视频",
  needs_product_link: "待关联产品",
  detected_pending_human_confirmation: "系统检出 · 待人工确认",
  partial: "部分已关联",
  no_final_v1_results: "暂无 final_v1 结果",
  lens_extraction_pending: "已深析 · 镜头证据待整理",
  ready_with_evidence: "已深析且有结构化证据",
};

const BLOCKER_LABELS: Record<string, string> = {
  content_monitoring_not_configured: "KOL 内容订阅未选择",
  content_monitoring_scheduler_disabled: "内容巡检调度未开启",
  videos_not_tracked: "视频尚未登记追踪",
  video_metric_scheduler_disabled: "视频指标刷新调度未开启",
  tracked_without_success_snapshot: "追踪视频还没有成功实测",
  tracked_without_sku: "追踪视频尚未关联 SKU",
  detected_sku_pending_confirmation: "自动检出 SKU 待员工确认",
  final_v1_missing: "视频尚无 Gemini final_v1 深析",
  lens_extraction_pending: "final_v1 镜头证据待结构化",
};

const OWNER_LABELS: Record<string, string> = {
  employee: "员工确认",
  manager: "管理员开闸",
  system: "系统补齐",
};

function n(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0;
}

function stateText(value: unknown): string {
  const key = String(value || "");
  return STATE_LABELS[key] || key || "状态待核实";
}

interface ClosureReadinessCardProps {
  apiToken: string;
  refreshKey?: number;
}

export function ClosureReadinessCard({ apiToken, refreshKey = 0 }: ClosureReadinessCardProps) {
  const [data, setData] = React.useState<VkpiMyKolClosureReadinessResponse | null>(null);
  const [error, setError] = React.useState("");

  React.useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setError("");
    getMyKolClosureReadiness(apiToken)
      .then((payload) => {
        if (alive) setData(payload && typeof payload === "object" ? payload : null);
      })
      .catch((err: unknown) => {
        const detail = (err as { detail?: unknown; message?: unknown }) || {};
        if (alive) setError(String(detail.detail || detail.message || "读取失败"));
      });
    return () => {
      alive = false;
    };
  }, [apiToken, refreshKey]);

  if (!apiToken) return null;

  const counts = data?.counts || {};
  const flows = data?.flows || {};
  const hasTrackingProvenance = counts.employee_explicit_tracked_videos != null
    && counts.system_seeded_tracked_videos != null;
  const hasGeminiEvidenceIntersection = counts.final_v1_lens_scanned_videos != null;
  const trackingProvenanceText = [
    `${n(counts.employee_explicit_tracked_videos)} 人工`,
    `${n(counts.system_seeded_tracked_videos)} 系统`,
    ...(n(counts.unclassified_tracked_videos) > 0
      ? [`${n(counts.unclassified_tracked_videos)} 待归类`]
      : []),
  ].join(" · ");
  const rows = [
    {
      key: "content",
      label: "内容订阅",
      value: `${n(counts.monitoring_active_kols)} / ${n(counts.writable_kol_count)}`,
      note: stateText(flows.content_monitoring?.state),
    },
    {
      key: "share",
      label: "KOL 共享",
      value: `${n(counts.share_grants)} 条授权`,
      note: stateText(flows.sharing?.state),
    },
    {
      key: "tracking",
      label: "视频追踪",
      value: hasTrackingProvenance
        ? trackingProvenanceText
        : `${n(counts.tracked_videos)} / ${n(counts.trackable_videos)}`,
      note: hasTrackingProvenance
        ? `总追踪 ${n(counts.tracked_videos)} / ${n(counts.trackable_videos)} · ${stateText(flows.video_tracking?.state)}`
        : stateText(flows.video_tracking?.state),
    },
    {
      key: "sku",
      label: "SKU 关联",
      value: `${n(counts.sku_linked_tracked_videos)} / ${n(counts.tracked_videos)}`,
      note: stateText(flows.sku_linking?.state),
    },
    {
      key: "gemini",
      label: "Gemini 视频深析",
      value: hasGeminiEvidenceIntersection
        ? `${n(counts.final_v1_lens_scanned_videos)} / ${n(counts.final_v1_ready_videos)} 成套`
        : `${n(counts.final_v1_ready_videos)} / ${n(counts.candidate_videos)}`,
      note: hasGeminiEvidenceIntersection
        ? `深析 ${n(counts.final_v1_ready_videos)} / ${n(counts.candidate_videos)} · ${stateText(flows.gemini_analysis?.state)}`
        : stateText(flows.gemini_analysis?.state),
    },
  ];
  const blockers = Array.isArray(data?.blockers) ? data.blockers.slice(0, 5) : [];
  const ready = data?.status === "ready";

  return (
    <section className="mb-4 rounded-2xl border border-line bg-card px-4 py-3 shadow-sm" aria-label="MY KOL 业务闭环状态">
      <div className="flex flex-wrap items-start gap-2">
        <div className="flex min-w-0 items-start gap-2">
          {ready ? (
            <CheckCircle2 className="mt-0.5 shrink-0 text-good" size={16} />
          ) : data ? (
            <AlertTriangle className="mt-0.5 shrink-0 text-warn" size={16} />
          ) : (
            <CircleDashed className="mt-0.5 shrink-0 text-muted" size={16} />
          )}
          <div>
            <h2 className="text-[13px] font-semibold text-ink">业务闭环状态</h2>
            <p className="mt-0.5 text-[10.5px] leading-4 text-muted">
              配置、调度、成功实测分层核验；本卡仅读，未自动选 KOL、开任务或调用模型。
            </p>
          </div>
        </div>
        <span className="ml-auto rounded-lg bg-accent-soft px-2 py-1 text-[9.5px] font-semibold text-accent">
          {data?.scope?.mode === "team" ? "团队口径" : "本人口径"} · 现状投影
        </span>
      </div>

      {error ? (
        <div className="mt-3 rounded-xl border border-danger/30 bg-danger/5 px-3 py-2 text-[11px] text-danger">
          闭环状态读取失败：{error}
        </div>
      ) : !data ? (
        <div className="mt-3 text-[11px] text-muted">闭环状态核验中…</div>
      ) : (
        <>
          <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
            {rows.map((row) => (
              <div key={row.key} className="min-w-0 rounded-xl border border-line/80 bg-surface px-3 py-2.5">
                <div className="text-[10px] font-medium text-muted">{row.label}</div>
                <div className="mt-1 text-[15px] font-semibold tabular-nums text-ink">{row.value}</div>
                <div className="mt-1 truncate text-[10px] text-muted" title={row.note}>{row.note}</div>
              </div>
            ))}
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-1.5">
            {blockers.length === 0 ? (
              <span className="rounded-lg bg-good/10 px-2 py-1 text-[10px] font-medium text-good">暂无未闭环项</span>
            ) : (
              blockers.map((item) => {
                const code = String(item.code || "");
                const owner = String(item.owner || "system");
                return (
                  <span key={`${code}:${owner}`} className="rounded-lg border border-line bg-surface px-2 py-1 text-[10px] text-muted">
                    {BLOCKER_LABELS[code] || code} · {n(item.count)} · {OWNER_LABELS[owner] || owner}
                  </span>
                );
              })
            )}
            {n(data?.summary?.blocker_kinds) > blockers.length && (
              <span className="text-[10px] text-muted">+{n(data?.summary?.blocker_kinds) - blockers.length} 类</span>
            )}
          </div>
        </>
      )}
    </section>
  );
}
