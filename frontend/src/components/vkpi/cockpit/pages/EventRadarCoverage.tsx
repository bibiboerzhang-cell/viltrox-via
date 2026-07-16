import React from "react";
import {
  listEventRadar,
  type CandidateStagingSummary,
  type EventRadarOpportunity,
  type EventRadarSummary,
  type EventUsSourceRegistry,
} from "../../../../services/vkpi/eventRadar-api";

export const SOURCE_KIND_LABEL: Record<string, string> = {
  major_expo: "会展来源",
  dealer_event: "Dealer 日历",
  venue_calendar: "场馆日历",
  school_calendar: "摄影学校",
  university_calendar: "高校日历",
  photo_club: "摄影社群",
  community_calendar: "社区活动",
  brand_event: "品牌活动",
  association_directory: "协会目录",
};

// Deliberately a tile map rather than venue points. Event Radar currently owns
// state/DC fields, not reviewed latitude/longitude for every opportunity.
const US_STATE_TILE_ROWS: ReadonlyArray<ReadonlyArray<string | null>> = [
  ["AK", null, null, null, null, null, null, null, null, null, null, null, "ME"],
  [null, "WA", "ID", "MT", "ND", "MN", null, "WI", "MI", "NY", "VT", "NH", "MA"],
  [null, "OR", "NV", "WY", "SD", "IA", "IL", "IN", "OH", "PA", "NJ", "CT", "RI"],
  [null, "CA", "UT", "CO", "NE", "MO", "KY", "WV", "VA", "MD", "DE", "DC", null],
  [null, null, "AZ", "NM", "KS", "AR", "TN", "NC", "SC", null, null, null, null],
  [null, null, null, "TX", "OK", "LA", "MS", "AL", "GA", "FL", null, null, null],
  ["HI", null, null, null, null, null, null, null, null, null, null, null, null],
];
const US_STATE_CODES = new Set(US_STATE_TILE_ROWS.flat().filter((value): value is string => Boolean(value)));

export function buildUsOpportunityMapAggregate(items: EventRadarOpportunity[]) {
  const opportunityCounts: Record<string, number> = {};
  const verificationCounts: Record<string, number> = {};
  for (const item of items) {
    if (String(item.country_code || "").trim().toUpperCase() !== "US") continue;
    const state = String(item.region || "").trim().toUpperCase();
    if (!US_STATE_CODES.has(state)) continue;
    opportunityCounts[state] = Number(opportunityCounts[state] || 0) + 1;
    if (["verified", "current"].includes(String(item.verification_status || "").trim().toLowerCase())) {
      verificationCounts[state] = Number(verificationCounts[state] || 0) + 1;
    }
  }
  return {
    opportunity_counts_by_state_dc: opportunityCounts,
    verification_marked_counts_by_state_dc: verificationCounts,
    opportunity_entity_count: Object.values(opportunityCounts).reduce((sum, value) => sum + value, 0),
    map_precision: "state_dc_aggregate_not_venue_coordinates" as const,
  };
}

export async function loadExactUsOpportunityMapAggregate(token: string) {
  const items: EventRadarOpportunity[] = [];
  let offset = 0;
  let expectedTotal: number | null = null;
  for (let pageIndex = 0; pageIndex < 20; pageIndex += 1) {
    const response = await listEventRadar(token, {
      limit: 500,
      offset,
      country: "US",
      include_past: true,
    });
    const pageItems = Array.isArray(response.items) ? response.items : [];
    const total = Number(response.count);
    if (!Number.isFinite(total) || total < 0 || total > 10_000) return null;
    expectedTotal = total;
    items.push(...pageItems);
    if (items.length >= total) break;
    const nextOffset = response.page?.next_offset;
    if (nextOffset == null || nextOffset <= offset || pageItems.length === 0) return null;
    offset = nextOffset;
  }
  if (expectedTotal == null || items.length !== expectedTotal) return null;
  return buildUsOpportunityMapAggregate(items);
}

const REVIEW_STATUS_LABEL: Record<string, string> = {
  pending: "待审核",
  in_review: "审核中",
  approved: "已审核",
  rejected: "已拒绝",
  needs_review: "待复核",
};

export function CountCard({ label, value, note }: { label: string; value: number | null; note: string }) {
  return (
    <div className="rounded-[10px] border border-line bg-card px-3 py-2.5">
      <div className="text-[9.5px] font-medium tracking-[0.04em] text-muted">{label}</div>
      <div className="mt-1 font-mono text-[20px] font-semibold tabular-nums text-ink">{value == null ? "—" : value.toLocaleString()}</div>
      <div className="mt-0.5 text-[9px] text-muted">{note}</div>
    </div>
  );
}

export function EventUsAggregateMap({
  registry,
  entityMatrix,
  selectedState,
  onSelectState,
}: {
  registry: EventUsSourceRegistry | null;
  entityMatrix?: EventRadarSummary["us_jurisdiction_matrix"] | null;
  selectedState: string;
  onSelectState: (state: string) => void;
}) {
  const sourceCounts = React.useMemo(() => {
    if (!registry) return null;
    const counts: Record<string, number> = {};
    for (const source of registry.event_sources || []) {
      for (const state of new Set(source.state_codes || [])) {
        const code = String(state || "").trim().toUpperCase();
        if (/^[A-Z]{2}$/.test(code)) counts[code] = Number(counts[code] || 0) + 1;
      }
    }
    return counts;
  }, [registry]);
  const sourceCovered = new Set(
    registry?.source_jurisdiction_matrix?.event_sources?.covered_states_dc || [],
  );
  const opportunityCounts = entityMatrix?.opportunity_counts_by_state_dc;
  const verificationCounts = entityMatrix?.verification_marked_counts_by_state_dc;
  const exactEntityCountsAvailable = Boolean(opportunityCounts);
  const exactVerificationCountsAvailable = Boolean(verificationCounts);

  return (
    <div className="rounded-[11px] border border-line bg-panel p-3" data-testid="event-us-state-tile-map">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="text-[11px] font-semibold text-ink">美国活动州级聚合地图</div>
          <p className="mt-0.5 text-[9px] leading-4 text-warn">
            州级聚合 / 非场地坐标。来源入口、已入库机会实体、核验状态分别计数；0 条实体不代表该州没有活动。
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5 text-[8.5px] text-muted" aria-label="活动州级地图图例">
          <span className="rounded-[5px] border border-good bg-good-soft px-1.5 py-1">有已入库机会实体</span>
          <span className="rounded-[5px] border border-accent bg-accent-soft px-1.5 py-1">有来源入口 · 0 实体</span>
          <span className="rounded-[5px] border border-line bg-card px-1.5 py-1">来源状态未知</span>
        </div>
      </div>
      <div className="mt-3 overflow-x-auto pb-1">
        <div className="grid min-w-[780px] gap-1" style={{ gridTemplateColumns: "repeat(13, minmax(0, 1fr))" }}>
          {US_STATE_TILE_ROWS.flatMap((row, rowIndex) => row.map((state, columnIndex) => {
            if (!state) return <span key={`empty-${rowIndex}-${columnIndex}`} aria-hidden="true" />;
            const sourceCount = sourceCounts == null ? null : Number(sourceCounts[state] || 0);
            const entityCount = exactEntityCountsAvailable ? Number(opportunityCounts?.[state] || 0) : null;
            const verifiedCount = exactVerificationCountsAvailable ? Number(verificationCounts?.[state] || 0) : null;
            const hasSource = sourceCovered.has(state) || Number(sourceCount || 0) > 0;
            const selected = state === selectedState;
            const tone = entityCount != null && entityCount > 0
              ? "border-good bg-good-soft text-good"
              : hasSource
                ? "border-accent bg-accent-soft text-accent"
                : "border-line bg-card text-muted";
            const label = `${state} · 来源入口 ${sourceCount == null ? "未知" : sourceCount} · 已入库机会实体 ${entityCount == null ? "未知" : entityCount} · 核验标记 ${verifiedCount == null ? "未知" : verifiedCount} · 州级聚合非场地坐标`;
            return (
              <button
                key={state}
                type="button"
                className={`min-h-[54px] rounded-[7px] border px-1 py-1 text-left transition-colors ${tone} ${selected ? "ring-1 ring-accent" : ""}`}
                aria-label={label}
                aria-pressed={selected}
                title={`${label}；点击进入该州机会列表`}
                data-testid={`event-map-state-${state}`}
                onClick={() => onSelectState(state)}
              >
                <span className="block font-mono text-[10px] font-semibold">{state}</span>
                <span className="block text-[7.5px] leading-3">实体 {entityCount == null ? "—" : entityCount}</span>
                <span className="block text-[7.5px] leading-3">核验 {verifiedCount == null ? "—" : verifiedCount} · 源 {sourceCount == null ? "—" : sourceCount}</span>
              </button>
            );
          }))}
        </div>
      </div>
      <p className="mt-2 text-[8.5px] leading-4 text-muted">
        “核验”仅表示机会记录 verification_status 标记为 verified/current；仍属于外部机会层，不等于内部 Event、Viltrox 参展、客流或商业结果。
      </p>
    </div>
  );
}

export function EventUsCoveragePanel({
  staging,
  registry,
  entityMatrix,
  error,
}: {
  staging: CandidateStagingSummary | null;
  registry: EventUsSourceRegistry | null;
  entityMatrix?: EventRadarSummary["us_jurisdiction_matrix"] | null;
  error: string;
}) {
  if (error && !staging && !registry) {
    return (
      <div className="mb-3 rounded-[9px] border border-crit bg-crit-soft px-3 py-2 text-[10px] text-crit" role="alert">
        美国来源 / 候选队列读取失败：{error}
      </div>
    );
  }
  if (!staging && !registry) {
    return <div className="mb-3 rounded-[9px] border border-line bg-panel px-3 py-2 text-[10px] text-muted">美国来源、实体覆盖与候选队列读取中…</div>;
  }
  const sourceKinds = registry?.counts?.event_source_kinds || {};
  const sourceCount = registry ? Number(registry.counts?.event_sources ?? registry.event_sources?.length ?? 0) : null;
  // Workshop/class is an event-format signal that can cross dealer, school,
  // community and brand publisher types.  Keep it explicitly overlapping
  // instead of mutating the canonical source_kind taxonomy.
  const workshopSourceCount = (registry?.event_sources || []).filter((source) => {
    const evidenceText = [source.name, source.canonical_url, source.published_scope_note]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return /\b(workshops?|classes?|courses?|training)\b/.test(evidenceText);
  }).length;
  const candidateBlockedSources = (registry?.event_sources || []).filter(
    (source) => source.candidate_generation_allowed === false,
  );
  const prioritySourceGroups = [
    ["Dealer / 门店活动入口", Number(sourceKinds.dealer_event || 0) + Number(sourceKinds.venue_calendar || 0)],
    ["摄影学校入口", Number(sourceKinds.school_calendar || 0)],
    ["高校入口", Number(sourceKinds.university_calendar || 0)],
    ["社区 / 社群入口", Number(sourceKinds.association_directory || 0) + Number(sourceKinds.photo_club || 0) + Number(sourceKinds.community_calendar || 0)],
    ["工作坊 / 课程线索入口", workshopSourceCount],
    ["大展会入口", Number(sourceKinds.major_expo || 0)],
    ["品牌活动入口", Number(sourceKinds.brand_event || 0)],
  ] as const;
  const jurisdictionMatrix = registry?.source_jurisdiction_matrix?.event_sources;
  const reviewCounts = staging?.review_status || {};
  const pendingReview = Object.entries(reviewCounts).reduce(
    (sum, [key, value]) => sum + (["approved", "rejected"].includes(key) ? 0 : Number(value || 0)),
    0,
  );
  return (
    <div className="mb-3 rounded-[11px] border border-line bg-panel p-3" data-testid="event-us-source-coverage">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="text-[11px] font-semibold text-ink">美国活动来源与实体覆盖</div>
          <p className="mt-0.5 text-[9px] leading-4 text-muted">
            来源入口、候选机会、正式 Event 三层隔离；来源注册不等于活动已发生，也不证明 Viltrox 参展。
          </p>
        </div>
        <span className="rounded-[6px] border border-warn bg-warn-soft px-2 py-1 text-[9px] font-semibold text-warn">
          {registry?.full_us_coverage === false ? "美国记录部分覆盖" : "覆盖口径待核验"}
        </span>
      </div>
      <div className="mt-2.5 grid grid-cols-2 gap-2 lg:grid-cols-6">
        <CountCard label="登记来源入口" value={sourceCount} note="只读来源注册表" />
        <CountCard
          label="来源发现州 / DC"
          value={jurisdictionMatrix ? Number(jurisdictionMatrix.covered_count || 0) : null}
          note={jurisdictionMatrix ? `${jurisdictionMatrix.covered_count}/${jurisdictionMatrix.jurisdiction_count} 只表示发布者自有公开入口枚举` : "不等于活动实体覆盖"}
        />
        <CountCard
          label="已入库活动州 / DC"
          value={entityMatrix ? Number(entityMatrix.covered_count || 0) : null}
          note={entityMatrix ? `${entityMatrix.covered_count}/${entityMatrix.jurisdiction_count} 实体记录覆盖` : "与来源辖区分开计数"}
        />
        <CountCard label="候选机会" value={staging ? Number(staging.total || 0) : null} note={staging?.status === "migration_pending" ? "候选表待迁移" : "未写入正式 Event"} />
        <CountCard label="待人工复核" value={staging ? pendingReview : null} note="候选不可自动晋级" />
        <CountCard label="字段证据链接" value={staging ? Number(staging.linked_field_evidence || 0) : null} note="逐字段来源证据" />
      </div>
      {Object.keys(sourceKinds).length > 0 ? (
        <div className="mt-2 grid grid-cols-2 gap-1.5 lg:grid-cols-4" data-testid="event-priority-source-groups">
          {prioritySourceGroups.map(([label, value]) => (
            <div key={label} className="rounded-[7px] border border-line bg-card px-2 py-1.5 text-[9px] text-ink-2">
              <span>{label}</span>
              <span className="ml-1 font-mono font-semibold tabular-nums text-ink">{value.toLocaleString()}</span>
            </div>
          ))}
        </div>
      ) : null}
      {Object.keys(sourceKinds).length > 0 ? (
        <p className="mt-1 text-[8.5px] leading-4 text-muted">
          工作坊 / 课程是从登记来源名称、URL 与发布范围明确文案中提取的跨来源线索，会与 Dealer、学校、社群或品牌分类重叠，不是已核验活动实体数。
        </p>
      ) : null}
      {Object.keys(sourceKinds).length > 0 ? (
        <div className="mt-2 flex flex-wrap gap-1.5" aria-label="美国活动来源类型">
          {Object.entries(sourceKinds).map(([kind, value]) => (
            <span key={kind} className="rounded-[6px] border border-line bg-card px-2 py-1 text-[9px] text-ink-2">
              {SOURCE_KIND_LABEL[kind] || kind} {Number(value).toLocaleString()}
            </span>
          ))}
        </div>
      ) : (
        <p className="mt-2 text-[9px] text-muted">尚无可引用的来源类型统计；不会用静态点位补齐。</p>
      )}
      {Object.keys(reviewCounts).length > 0 ? (
        <p className="mt-2 text-[9px] leading-4 text-muted">
          审核队列：{Object.entries(reviewCounts).map(([key, value]) => `${REVIEW_STATUS_LABEL[key] || key} ${Number(value)}`).join(" · ")}
        </p>
      ) : null}
      {jurisdictionMatrix ? (
        <p className="mt-2 text-[9px] leading-4 text-muted">
          州 / DC 数只是 source discovery jurisdiction matrix；已抓候选、已核验活动与 Viltrox 参与仍分层计数。
        </p>
      ) : null}
      {registry ? (
        <p className="mt-1 text-[9px] leading-4 text-warn" data-testid="event-source-import-gate">
          来源启用 {Number(registry.counts?.enabled || 0)}/{sourceCount ?? 0} · 直接导入 {Number(registry.counts?.direct_import_allowed || 0)} · 未通过条款 / robots 与逐条人工复核前不作业务写入。
        </p>
      ) : null}
      {candidateBlockedSources.length ? (
        <p className="mt-1 text-[9px] leading-4 text-warn" data-testid="event-source-live-quarantine">
          实时内容隔离 {candidateBlockedSources.length} 个：{candidateBlockedSources.map((source) => `${source.publisher}（${source.current_feed_state || "待复核"}）`).join(" · ")}；这些入口不会生成活动候选。
        </p>
      ) : null}
      {error ? <p className="mt-2 text-[9px] text-warn">部分来源降级：{error}</p> : null}
    </div>
  );
}
