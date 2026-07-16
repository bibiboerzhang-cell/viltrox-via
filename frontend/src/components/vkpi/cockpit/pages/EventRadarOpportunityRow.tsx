import {
  AlertTriangle,
  ArrowUpRight,
  CalendarDays,
  Check,
  ExternalLink,
  Loader2,
  MapPin,
  ShieldCheck,
  X,
} from "lucide-react";
import { formatLocal } from "../../lib/timeLocal";
import type { EventRadarOpportunity } from "../../../../services/vkpi/eventRadar-api";
import { SOURCE_KIND_LABEL } from "./EventRadarCoverage";

const BUTTON =
  "inline-flex items-center justify-center gap-1.5 rounded-[8px] border px-2.5 py-1.5 text-[10.5px] font-medium transition-colors disabled:cursor-not-allowed disabled:text-muted";

const LANE_META: Record<string, { label: string; className: string }> = {
  dealer_event: { label: "Dealer 活动", className: "border-accent bg-accent-soft text-accent" },
  local_activity: { label: "线下活动", className: "border-accent bg-accent-soft text-accent" },
  major_expo: { label: "大展会记录", className: "border-accent-2 bg-card text-accent-2" },
};

const DECISION_LABEL: Record<string, string> = {
  new: "待判断",
  watching: "已关注",
  approved: "已批准",
  dismissed: "已忽略",
  promoted: "已转 Event",
  needs_review: "待复核",
};

const VERIFY_META: Record<string, { label: string; className: string }> = {
  verified: { label: "活动字段已核验", className: "border-good bg-good-soft text-good" },
  current: { label: "活动字段已核验", className: "border-good bg-good-soft text-good" },
  conflict: { label: "来源冲突", className: "border-crit bg-crit-soft text-crit" },
  conflicted: { label: "来源冲突", className: "border-crit bg-crit-soft text-crit" },
  provisional: { label: "暂定", className: "border-warn bg-warn-soft text-warn" },
  needs_review: { label: "待复核", className: "border-warn bg-warn-soft text-warn" },
  stale: { label: "待复核 / 过期", className: "border-warn bg-warn-soft text-warn" },
  pending: { label: "待核验", className: "border-warn bg-warn-soft text-warn" },
};

const EVIDENCE_LABEL: Record<string, string> = {
  a1: "A1 · 登记详情页",
  a2: "A2 · 登记日历页",
  b: "B · 二手来源",
  x: "X · 来源异常",
  official: "登记活动页",
  official_page: "登记活动页",
  official_listing: "登记活动页",
  dealer_official: "经销商登记页",
  primary: "登记一手来源",
  secondary: "登记二手来源",
};

function eventDate(item: EventRadarOpportunity): string {
  const start = item.start_date || "日期待核验";
  const end = item.end_date && item.end_date !== item.start_date ? ` → ${item.end_date}` : "";
  return `${start}${end}`;
}

function locationText(item: EventRadarOpportunity): string {
  return [item.venue, item.city, item.region, item.country_code].filter(Boolean).join(" · ") || "地点待核验";
}

function freshnessText(item: EventRadarOpportunity): string {
  const checked = item.last_verified_at || item.source_checked_at;
  return checked ? formatLocal(checked, { year: "numeric" }) : "未记录核验时间";
}

function relevanceBasisText(value: string): string {
  return value
    .split("Official dealer").join("Registered dealer-hosted")
    .split("official dealer").join("registered dealer-hosted")
    .split("官方门店活动页").join("登记门店活动页（发布者身份待核验）")
    .split("官方经销商").join("登记经销商（发布者身份待核验）");
}

function statusTone(item: EventRadarOpportunity): { label: string; className: string } {
  const freshness = String(item.freshness_status || "").toLowerCase();
  if (freshness === "stale" || freshness === "unverified") return VERIFY_META.stale;
  if (freshness === "aging") return { label: "即将复核", className: "border-warn bg-warn-soft text-warn" };
  const key = String(item.verification_status || "pending").toLowerCase();
  return VERIFY_META[key] || { label: item.verification_status || "待核验", className: "border-line bg-card text-muted" };
}

function confidenceText(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(Number(value))) return "置信度待评估";
  const normalized = Number(value) <= 1 ? Number(value) * 100 : Number(value);
  return `置信 ${Math.max(0, Math.min(100, Math.round(normalized)))}%`;
}

function dateOnly(value: string | null | undefined): Date | null {
  if (!value) return null;
  const parsed = new Date(`${value.slice(0, 10)}T00:00:00`);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function isVerifiedEvidence(item: EventRadarOpportunity): boolean {
  const verification = String(item.verification_status || "").toLowerCase();
  const freshness = String(item.freshness_status || "").toLowerCase();
  return ["verified", "current"].includes(verification) && freshness === "current";
}

/** Mirrors backend `_validate_approval_truth`; missing fields fail closed. */
export function hasCurrentApprovalTruth(
  item: EventRadarOpportunity,
  todayUtc = new Date().toISOString().slice(0, 10),
): boolean {
  const endDay = String(item.end_date || "").slice(0, 10);
  return String(item.source_status || "").toLowerCase() === "active"
    && item.source_enabled === true
    && String(item.verification_status || "").toLowerCase() === "verified"
    && String(item.event_status || "").toLowerCase() === "scheduled"
    && Boolean(item.start_date && item.end_date)
    && String(item.freshness_status || "").toLowerCase() === "current"
    && /^\d{4}-\d{2}-\d{2}$/.test(endDay)
    && endDay >= todayUtc;
}

export function matchesEvidence(item: EventRadarOpportunity, filter: string): boolean {
  if (!filter) return true;
  const verification = String(item.verification_status || "pending").toLowerCase();
  const freshness = String(item.freshness_status || "").toLowerCase();
  if (filter === "verified") return isVerifiedEvidence(item);
  if (filter === "conflict") return ["conflict", "conflicted"].includes(verification);
  if (filter === "review") {
    if (["conflict", "conflicted"].includes(verification)) return false;
    return !isVerifiedEvidence(item) || ["stale", "unverified", "aging"].includes(freshness);
  }
  return true;
}

export function matchesTimeWindow(item: EventRadarOpportunity, windowKey: string, now = new Date()): boolean {
  if (!windowKey) return true;
  const start = dateOnly(item.start_date);
  const end = dateOnly(item.end_date || item.start_date);
  if (!start || !end) return windowKey === "date_pending";
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  if (windowKey === "past") return end < today;
  const status = String(item.event_status || "").toLowerCase();
  if (windowKey === "upcoming") return end >= today && !["ended", "cancelled"].includes(status);
  const days = windowKey === "30d" ? 30 : windowKey === "90d" ? 90 : null;
  if (days == null) return true;
  const until = new Date(today);
  until.setDate(until.getDate() + days);
  return end >= today && start <= until && !["ended", "cancelled"].includes(status);
}

export function OpportunityRow({
  item,
  busy,
  canManage,
  onDecision,
  onPromote,
}: {
  item: EventRadarOpportunity;
  busy: string;
  canManage: boolean;
  onDecision: (item: EventRadarOpportunity, status: "watching" | "approved" | "dismissed") => void;
  onPromote: (item: EventRadarOpportunity) => void;
}) {
  const lane = LANE_META[item.lane] || { label: item.lane || "未分类", className: "border-line bg-card text-muted" };
  const verify = statusTone(item);
  const converted = Boolean(item.converted_event_id);
  const sourceActionable = String(item.source_status || "").toLowerCase() === "active" && item.source_enabled === true;
  const approvalTruthCurrent = hasCurrentApprovalTruth(item);
  const evidence = EVIDENCE_LABEL[String(item.evidence_grade || "").toLowerCase()] || item.evidence_grade || "证据等级待标注";
  const sourceKind = SOURCE_KIND_LABEL[String(item.source_kind || "").toLowerCase()] || "";
  return (
    <article className="rounded-[11px] border border-line bg-card p-3" aria-label={item.title}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
            <span className={`rounded-[5px] border px-1.5 py-px text-[8.5px] font-semibold ${lane.className}`}>{lane.label}</span>
            {item.preview_only ? (
              <span className="rounded-[5px] border border-warn bg-warn-soft px-1.5 py-px text-[8.5px] font-semibold text-warn">
                目录只读候选
              </span>
            ) : null}
            {sourceKind ? (
              <span
                className="rounded-[5px] border border-line bg-panel px-1.5 py-px text-[8.5px] text-muted"
                title="登记来源类型；不代表主办方身份、Viltrox 授权或参展已确认"
              >
                {sourceKind}
              </span>
            ) : null}
            <span className={`rounded-[5px] border px-1.5 py-px text-[8.5px] font-semibold ${verify.className}`}>{verify.label}</span>
            <span className="rounded-[5px] border border-line px-1.5 py-px text-[8.5px] text-muted">{evidence}</span>
            {Number(item.change_count || 0) > 0 ? (
              <span className="rounded-[5px] border border-warn bg-warn-soft px-1.5 py-px text-[8.5px] text-warn">
                变更 ×{Number(item.change_count)}
              </span>
            ) : null}
          </div>
          <h4 className="truncate text-[12.5px] font-semibold text-ink" title={item.title}>{item.title}</h4>
          <div className="mt-1 text-[10px] text-muted">
            {item.organizer || item.dealer_name || item.source_name || "主办方待核验"}
          </div>
        </div>
        <div className="flex-none text-right text-[9px] text-muted">
          <div>{DECISION_LABEL[item.decision_status] || item.decision_status || "待判断"}</div>
          <div className="mt-0.5">{confidenceText(item.confidence)}</div>
        </div>
      </div>

      <div className="mt-2 grid gap-1.5 text-[10px] text-ink-2 md:grid-cols-2">
        <div className="flex min-w-0 items-center gap-1.5"><CalendarDays size={12} className="flex-none text-accent" /><span>{eventDate(item)}</span></div>
        <div className="flex min-w-0 items-center gap-1.5"><MapPin size={12} className="flex-none text-accent" /><span className="truncate" title={locationText(item)}>{locationText(item)}</span></div>
        <div className="flex min-w-0 items-center gap-1.5 text-muted">
          <ShieldCheck size={12} className="flex-none" />
          <span>核验 {freshnessText(item)}</span>
        </div>
        <div className="min-w-0 truncate text-muted" title={item.local_time_text || item.timezone || ""}>
          当地时间 {item.local_time_text || "待核验"}{item.timezone ? ` · ${item.timezone}` : ""}
        </div>
      </div>

      <div
        className="mt-2 flex flex-wrap gap-1.5 text-[8.5px] text-muted"
        data-testid={`event-evidence-fields-${String(item.id)}`}
        aria-label={`${item.title} 机会与证据字段状态`}
      >
        <span className={`rounded-[5px] border px-1.5 py-1 ${converted ? "border-good bg-good-soft text-good" : "border-warn bg-warn-soft text-warn"}`}>
          {converted ? "已创建内部 Event" : "外部机会候选 · 未转 Event"}
        </span>
        <span className={`rounded-[5px] border px-1.5 py-1 ${item.official_url ? "border-line bg-panel" : "border-warn bg-warn-soft text-warn"}`}>
          来源链接 {item.official_url ? "已记录" : "缺失"}
        </span>
        <span className={`rounded-[5px] border px-1.5 py-1 ${item.start_date && item.end_date ? "border-line bg-panel" : "border-warn bg-warn-soft text-warn"}`}>
          日期字段 {item.start_date && item.end_date ? "已记录" : "待补证"}
        </span>
        <span className={`rounded-[5px] border px-1.5 py-1 ${item.country_code && item.region ? "border-line bg-panel" : "border-warn bg-warn-soft text-warn"}`}>
          地理字段 {item.country_code && item.region ? "已记录" : "待补证"}
        </span>
        <span className={`rounded-[5px] border px-1.5 py-1 ${isVerifiedEvidence(item) ? "border-good bg-good-soft text-good" : "border-warn bg-warn-soft text-warn"}`}>
          {isVerifiedEvidence(item) ? "机会字段核验当前" : "机会字段待核验 / 复核"}
        </span>
      </div>

      {item.relevance_basis ? (
        <div className="mt-2 rounded-[7px] border border-line bg-panel px-2 py-1.5 text-[9.5px] text-muted">
          相关性 {item.relevance_score == null ? "待评估" : Number(item.relevance_score).toFixed(0)} · {relevanceBasisText(item.relevance_basis)}
        </div>
      ) : null}

      <div className="mt-2.5 flex flex-wrap items-center gap-1.5 border-t border-line pt-2.5">
        {item.official_url ? (
          <a
            href={item.official_url}
            target="_blank"
            rel="noreferrer"
            className={`${BUTTON} border-line text-accent hover:border-accent hover:bg-accent-soft`}
            aria-label={`打开 ${item.title} 登记来源`}
          >
            <ExternalLink size={11} /> 登记来源
          </a>
        ) : (
          <span className="text-[9.5px] text-warn"><AlertTriangle size={11} className="mr-1 inline" />缺登记链接</span>
        )}
        {item.registration_url && item.registration_url !== item.official_url ? (
          <a
            href={item.registration_url}
            target="_blank"
            rel="noreferrer"
            className={`${BUTTON} border-line text-accent hover:border-accent hover:bg-accent-soft`}
          >
            报名 <ArrowUpRight size={11} />
          </a>
        ) : null}
        <span className="min-w-1 flex-1" />
        {canManage && sourceActionable && !converted && !["watching", "approved"].includes(item.decision_status) ? (
          <button
            type="button"
            onClick={() => onDecision(item, "watching")}
            disabled={Boolean(busy)}
            className={`${BUTTON} border-good text-good hover:bg-good-soft`}
            aria-label={`关注 ${item.title}`}
          >
            {busy === "watching" ? <Loader2 size={11} className="animate-spin" /> : <Check size={11} />} 关注
          </button>
        ) : null}
        {canManage && sourceActionable && !converted && item.decision_status !== "dismissed" ? (
          <button
            type="button"
            onClick={() => onDecision(item, "dismissed")}
            disabled={Boolean(busy)}
            className={`${BUTTON} border-line text-muted hover:border-crit hover:text-crit`}
            aria-label={`忽略 ${item.title}`}
          >
            {busy === "dismissed" ? <Loader2 size={11} className="animate-spin" /> : <X size={11} />} 忽略
          </button>
        ) : null}
        {converted ? (
          <span className="rounded-[6px] border border-good bg-good-soft px-2 py-1 text-[9.5px] text-good">
            已转 Event · {item.converted_event_id}
          </span>
        ) : canManage && item.decision_status === "approved" && approvalTruthCurrent ? (
          <button
            type="button"
            onClick={() => onPromote(item)}
            disabled={Boolean(busy)}
            className={`${BUTTON} border-accent bg-accent-soft text-accent hover:bg-card`}
            aria-label={`将 ${item.title} 转为 Event`}
          >
            {busy === "promote" ? <Loader2 size={11} className="animate-spin" /> : <ArrowUpRight size={11} />} 转为 Event
          </button>
        ) : canManage && item.decision_status === "watching" && approvalTruthCurrent ? (
          <button
            type="button"
            onClick={() => onDecision(item, "approved")}
            disabled={Boolean(busy)}
            className={`${BUTTON} border-good bg-good-soft text-good hover:bg-card`}
            aria-label={`批准 ${item.title}`}
          >
            {busy === "approved" ? <Loader2 size={11} className="animate-spin" /> : <ShieldCheck size={11} />} 批准
          </button>
        ) : canManage && !sourceActionable && !converted ? (
          <span className="rounded-[6px] border border-warn bg-warn-soft px-2 py-1 text-[9.5px] text-warn">
            来源非 enabled + active，决策与转 Event 已阻断
          </span>
        ) : canManage && ["watching", "approved"].includes(String(item.decision_status)) && !approvalTruthCurrent ? (
          <span className="rounded-[6px] border border-warn bg-warn-soft px-2 py-1 text-[9.5px] text-warn">
            当前来源、状态、日期或核验新鲜度未满足执行门禁
          </span>
        ) : !canManage && !converted ? (
          <span className="rounded-[6px] border border-line bg-panel px-2 py-1 text-[9.5px] text-muted">当前账号只读</span>
        ) : null}
      </div>
    </article>
  );
}
