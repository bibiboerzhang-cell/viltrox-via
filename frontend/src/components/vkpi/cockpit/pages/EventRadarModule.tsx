import React from "react";
import {
  List,
  Loader2,
  Map as MapIcon,
  RefreshCw,
} from "lucide-react";
import { formatLocal } from "../../lib/timeLocal";
import {
  getEventCandidateStagingSummary,
  getEventRadarSummary,
  getEventUsSourceRegistry,
  listEventRadar,
  getEventRadarOpportunity,
  previewEventRadarRefresh,
  promoteEventRadarOpportunity,
  refreshEventRadar,
  setEventRadarDecision,
  type CandidateStagingSummary,
  type EventRadarOpportunity,
  type EventRadarRefreshResult,
  type EventRadarSummary,
  type EventUsSourceRegistry,
} from "../../../../services/vkpi/eventRadar-api";
import {
  CountCard,
  EventUsAggregateMap,
  EventUsCoveragePanel,
  loadExactUsOpportunityMapAggregate,
} from "./EventRadarCoverage";
import {
  OpportunityRow,
  hasCurrentApprovalTruth,
  matchesEvidence,
  matchesTimeWindow,
} from "./EventRadarOpportunityRow";

export { buildUsOpportunityMapAggregate } from "./EventRadarCoverage";
export { hasCurrentApprovalTruth };

// External opportunities are not internal Events. Only an explicit「转为 Event」
// action creates the operational record. All trust labels below mirror server fields.

const INPUT =
  "rounded-[8px] border border-line bg-card px-2.5 py-1.5 text-[11px] text-ink outline-none focus:border-accent";
const BUTTON =
  "inline-flex items-center justify-center gap-1.5 rounded-[8px] border px-2.5 py-1.5 text-[10.5px] font-medium transition-colors disabled:cursor-not-allowed disabled:text-muted";

const SOURCE_KIND_OPTIONS = [
  ["major_expo", "大展会"],
  ["dealer_event", "Dealer 活动"],
  ["venue_calendar", "线下场馆 / 门店"],
  ["school_calendar", "摄影学校"],
  ["university_calendar", "高校"],
  ["photo_club", "协会 / 摄影社群"],
  ["community_calendar", "社区活动"],
  ["brand_event", "品牌活动"],
] as const;

function errorText(error: unknown): string {
  if (error && typeof error === "object" && "message" in error) return String((error as { message?: unknown }).message || "读取失败");
  return String(error || "读取失败");
}

function numberValue(value: unknown): number | null {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function summaryCount(summary: EventRadarSummary | null, key: string, nestedKey?: string): number | null {
  if (!summary) return null;
  if (nestedKey) return numberValue((summary[key] as Record<string, unknown> | undefined)?.[nestedKey]);
  return numberValue(summary[key]);
}

function coverageText(listClaim?: string | null, summaryClaim?: string | null): string {
  const claim = listClaim || summaryClaim || "";
  if (["registered_publisher_owned_public_entries_only", "known_official_sources_only"].includes(claim)) {
    return "当前仅覆盖数据库已登记的发布者自有公开入口与已入库活动记录；来源辖区覆盖与活动实体覆盖分开计数，不代表美国或全球全量活动、Viltrox 参展或授权。";
  }
  if (claim === "migration_pending") return "活动雷达数据表待迁移；当前没有可用于业务判断的活动机会。";
  const safeClaim = claim
    .split("已登记官方来源").join("已登记的发布者自有公开入口")
    .split("官方来源").join("登记的发布者自有公开入口");
  return safeClaim || "当前仅表示数据库已登记的发布者自有公开入口和已入库活动记录；来源覆盖与实体覆盖分开计数，不代表全量覆盖。";
}

function refreshReceipt(result: EventRadarRefreshResult, mode: "preview" | "write"): string {
  const parts = [
    result.discovered != null ? `发现 ${result.discovered}` : result.opportunity_count != null ? `发现 ${result.opportunity_count}` : "",
    result.source_count != null ? `来源 ${result.source_count}` : "",
    result.inserted != null ? `新增 ${result.inserted}` : "",
    result.updated != null ? `更新 ${result.updated}` : "",
    result.conflicted != null ? `冲突 ${result.conflicted}` : "",
  ].filter(Boolean);
  return `${mode === "preview" ? "内置目录快照预检完成（未写入）" : "内置目录快照导入完成"}${parts.length ? ` · ${parts.join(" · ")}` : ""}`;
}

export interface EventRadarModuleProps {
  apiToken: string;
  limit?: number;
  className?: string;
  showHeading?: boolean;
  /** Mirrors backend manager + vkpi:write gate. Cross-board embeds stay read-only by default. */
  canManage?: boolean;
  onPromoted?: (eventId: string, item: EventRadarOpportunity) => void;
}

type PreviewGate = {
  importAllowed: boolean;
  qualityStatus: string;
};

type RadarPage = {
  limit: number;
  offset: number;
  returned: number;
  next_offset: number | null;
  has_more: boolean;
};

export function EventRadarModule({
  apiToken,
  limit = 100,
  className = "",
  showHeading = true,
  canManage = false,
  onPromoted,
}: EventRadarModuleProps) {
  const [items, setItems] = React.useState<EventRadarOpportunity[]>([]);
  const [count, setCount] = React.useState(0);
  const [offset, setOffset] = React.useState(0);
  const [page, setPage] = React.useState<RadarPage>({
    limit,
    offset: 0,
    returned: 0,
    next_offset: null,
    has_more: false,
  });
  const [summary, setSummary] = React.useState<EventRadarSummary | null>(null);
  const [candidateStaging, setCandidateStaging] = React.useState<CandidateStagingSummary | null>(null);
  const [usSourceRegistry, setUsSourceRegistry] = React.useState<EventUsSourceRegistry | null>(null);
  const [coverageIntelError, setCoverageIntelError] = React.useState("");
  const [coverageClaim, setCoverageClaim] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(Boolean(apiToken));
  const [error, setError] = React.useState("");
  const [lane, setLane] = React.useState("");
  const [sourceKind, setSourceKind] = React.useState("");
  const [decision, setDecision] = React.useState("");
  const [country, setCountry] = React.useState("");
  const [stateCode, setStateCode] = React.useState("");
  const [cityFilter, setCityFilter] = React.useState("");
  const [startFrom, setStartFrom] = React.useState("");
  const [startTo, setStartTo] = React.useState("");
  const [timeWindow, setTimeWindow] = React.useState("upcoming");
  const [evidenceStatus, setEvidenceStatus] = React.useState("");
  const [viewMode, setViewMode] = React.useState<"list" | "map">("list");
  const [busyId, setBusyId] = React.useState<Record<string, string>>({});
  const [refreshing, setRefreshing] = React.useState<"preview" | "write" | "">("");
  const [receipt, setReceipt] = React.useState("");
  const [actionError, setActionError] = React.useState("");
  const [previewGate, setPreviewGate] = React.useState<PreviewGate | null>(null);
  const [discoveryPreviewItems, setDiscoveryPreviewItems] = React.useState<EventRadarOpportunity[]>([]);
  const [discoveryPreviewCount, setDiscoveryPreviewCount] = React.useState(0);

  const load = React.useCallback(async () => {
    if (!apiToken) {
      setItems([]);
      setCount(0);
      setOffset(0);
      setPage({ limit, offset: 0, returned: 0, next_offset: null, has_more: false });
      setSummary(null);
      setLoading(false);
      return;
    }
    const normalizedCountry = country.trim().toUpperCase();
    // Keep the last valid result visible while a user is midway through a two-letter code.
    if (normalizedCountry && !/^[A-Z]{2}$/.test(normalizedCountry)) return;
    setLoading(true);
    setError("");
    try {
      const [list, summaryResult] = await Promise.all([
        listEventRadar(apiToken, {
          limit,
          offset,
          lane: lane || undefined,
          source_kind: sourceKind || undefined,
          decision_status: decision || undefined,
          evidence_status: evidenceStatus
            ? evidenceStatus as "verified" | "review" | "conflict"
            : undefined,
          time_window: timeWindow as "upcoming" | "30d" | "90d" | "past" | "date_pending",
          country: normalizedCountry || undefined,
          region: stateCode || undefined,
          start_from: startFrom || undefined,
          start_to: startTo || undefined,
        }),
        getEventRadarSummary(apiToken),
      ]);
      setItems(Array.isArray(list.items) ? list.items : []);
      const total = Number.isFinite(Number(list.count)) ? Number(list.count) : (list.items || []).length;
      const returned = Array.isArray(list.items) ? list.items.length : 0;
      setCount(total);
      setPage(list.page || {
        limit,
        offset,
        returned,
        next_offset: offset + returned < total ? offset + returned : null,
        has_more: offset + returned < total,
      });
      setCoverageClaim(list.coverage_claim || null);
      let effectiveSummary = summaryResult || null;
      const summaryMatrix = effectiveSummary?.us_jurisdiction_matrix;
      if (
        effectiveSummary
        && (!summaryMatrix?.opportunity_counts_by_state_dc
          || !summaryMatrix?.verification_marked_counts_by_state_dc)
      ) {
        const fallbackAggregate = await loadExactUsOpportunityMapAggregate(apiToken);
        if (fallbackAggregate) {
          effectiveSummary = {
            ...effectiveSummary,
            us_jurisdiction_matrix: {
              ...(summaryMatrix || {}),
              ...fallbackAggregate,
            },
          };
        }
      }
      setSummary(effectiveSummary);
    } catch (err) {
      setItems([]);
      setCount(0);
      setPage({ limit, offset, returned: 0, next_offset: null, has_more: false });
      setSummary(null);
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, [apiToken, country, decision, evidenceStatus, lane, limit, offset, sourceKind, stateCode, startFrom, startTo, timeWindow]);

  React.useEffect(() => {
    void load();
  }, [load]);

  React.useEffect(() => {
    let alive = true;
    if (!apiToken) {
      setCandidateStaging(null);
      setUsSourceRegistry(null);
      setDiscoveryPreviewItems([]);
      setDiscoveryPreviewCount(0);
      setCoverageIntelError("");
      return () => {
        alive = false;
      };
    }
    setCoverageIntelError("");
    void Promise.allSettled([
      getEventCandidateStagingSummary(apiToken),
      getEventUsSourceRegistry(apiToken),
      previewEventRadarRefresh(apiToken),
    ]).then(([stagingResult, registryResult, previewResult]) => {
      if (!alive) return;
      const errors: string[] = [];
      if (stagingResult.status === "fulfilled") setCandidateStaging(stagingResult.value);
      else {
        setCandidateStaging(null);
        errors.push(`候选队列 ${errorText(stagingResult.reason)}`);
      }
      if (registryResult.status === "fulfilled") setUsSourceRegistry(registryResult.value);
      else {
        setUsSourceRegistry(null);
        errors.push(`来源注册表 ${errorText(registryResult.reason)}`);
      }
      if (previewResult.status === "fulfilled") {
        const previewItems = Array.isArray(previewResult.value.preview_items)
          ? previewResult.value.preview_items.filter((item) => item?.preview_only === true)
          : [];
        setDiscoveryPreviewItems(previewItems);
        setDiscoveryPreviewCount(Number(previewResult.value.preview_item_count ?? previewItems.length) || 0);
      } else {
        setDiscoveryPreviewItems([]);
        setDiscoveryPreviewCount(0);
        errors.push(`目录候选预览 ${errorText(previewResult.reason)}`);
      }
      setCoverageIntelError(errors.join("；"));
    });
    return () => {
      alive = false;
    };
  }, [apiToken]);

  const availableStates = React.useMemo(
    () => [...new Set([
      ...items
        .filter((item) => String(item.country_code || "").toUpperCase() === "US")
        .map((item) => String(item.region || "").trim().toUpperCase()),
      ...(usSourceRegistry?.event_sources || []).flatMap((source) => source.state_codes || []),
    ].filter((state) => /^[A-Z]{2}$/.test(state)))].sort(),
    [items, usSourceRegistry],
  );
  const availableCities = React.useMemo(
    () => [...new Set(
      items.map((item) => String(item.city || "").trim()).filter(Boolean),
    )].sort((a, b) => a.localeCompare(b)),
    [items],
  );
  const filteredItems = React.useMemo(() => {
    const candidateCountry = country.trim().toUpperCase();
    const normalizedCountry = /^[A-Z]{2}$/.test(candidateCountry) ? candidateCountry : "";
    return items.filter((item) => {
      if (normalizedCountry && String(item.country_code || "").toUpperCase() !== normalizedCountry) return false;
      if (stateCode && String(item.region || "").trim().toUpperCase() !== stateCode) return false;
      if (cityFilter && String(item.city || "").trim().toLowerCase() !== cityFilter.toLowerCase()) return false;
      if (sourceKind && String(item.source_kind || "").toLowerCase() !== sourceKind) return false;
      if (!matchesEvidence(item, evidenceStatus)) return false;
      return matchesTimeWindow(item, timeWindow);
    });
  }, [country, evidenceStatus, items, sourceKind, stateCode, cityFilter, timeWindow]);
  const filteredDiscoveryPreviewItems = React.useMemo(() => {
    const candidateCountry = country.trim().toUpperCase();
    const normalizedCountry = /^[A-Z]{2}$/.test(candidateCountry) ? candidateCountry : "";
    return discoveryPreviewItems.filter((item) => {
      if (decision) return false;
      if (lane && String(item.lane || "").toLowerCase() !== lane) return false;
      if (normalizedCountry && String(item.country_code || "").toUpperCase() !== normalizedCountry) return false;
      if (stateCode && String(item.region || "").trim().toUpperCase() !== stateCode) return false;
      if (sourceKind && String(item.source_kind || "").toLowerCase() !== sourceKind) return false;
      if (!matchesEvidence(item, evidenceStatus)) return false;
      return matchesTimeWindow(item, timeWindow);
    });
  }, [country, decision, discoveryPreviewItems, evidenceStatus, lane, sourceKind, stateCode, timeWindow]);

  const actDecision = React.useCallback(async (item: EventRadarOpportunity, status: "watching" | "approved" | "dismissed") => {
    if (!canManage) {
      setActionError("当前账号无活动雷达管理权限。");
      return;
    }
    if (String(item.source_status || "").toLowerCase() !== "active" || item.source_enabled !== true) {
      setActionError("决策已阻断：活动机会来源未同时满足 enabled + active。");
      return;
    }
    if (status === "approved" && !hasCurrentApprovalTruth(item)) {
      setActionError("批准已阻断：来源、活动状态、日期或核验新鲜度不满足后端门禁。");
      return;
    }
    const id = String(item.id);
    setBusyId((prev) => ({ ...prev, [id]: status }));
    setActionError("");
    try {
      await setEventRadarDecision(apiToken, item.id, status);
      await load();
    } catch (err) {
      const action = status === "watching" ? "关注" : status === "approved" ? "批准" : "忽略";
      setActionError(`${action}失败：${errorText(err)}`);
    } finally {
      setBusyId((prev) => ({ ...prev, [id]: "" }));
    }
  }, [apiToken, canManage, load]);

  const promote = React.useCallback(async (item: EventRadarOpportunity) => {
    if (!canManage) {
      setActionError("当前账号无活动雷达管理权限。");
      return;
    }
    if (!hasCurrentApprovalTruth(item)) {
      setActionError("转为 Event 已阻断：来源、活动状态、日期或核验新鲜度不满足后端门禁。");
      return;
    }
    const id = String(item.id);
    setBusyId((prev) => ({ ...prev, [id]: "promote" }));
    setActionError("");
    try {
      const response = await promoteEventRadarOpportunity(apiToken, item.id);
      if (!response.ok || !response.event_id) throw new Error("后端未返回 Event ID");
      const updated = {
        ...item,
        ...(response.item || {}),
        converted_event_id: response.item?.converted_event_id || response.event_id,
      };
      onPromoted?.(response.event_id, updated);
      await load();
      setReceipt(`已转为内部 Event · ${response.event_id}`);
    } catch (err) {
      setActionError(`转为 Event 失败：${errorText(err)}`);
    } finally {
      setBusyId((prev) => ({ ...prev, [id]: "" }));
    }
  }, [apiToken, canManage, load, onPromoted]);

  const deepLookup = React.useCallback(async (item: EventRadarOpportunity) => {
    if (!apiToken) return;
    const id = String(item.id);
    setBusyId((prev) => ({ ...prev, [id]: "deep" }));
    setActionError("");
    try {
      const fresh = await getEventRadarOpportunity(apiToken, id);
      setItems((prev) => prev.map((row) => (String(row.id) === id ? { ...row, ...fresh } : row)));
      const verifyText = fresh.verification_status || fresh.evidence_grade || "未标注";
      const changeText = typeof fresh.change_count === "number" ? `${fresh.change_count} 次变更` : "变更未知";
      setReceipt(`已刷新单条「${fresh.title || item.title}」· 核验 ${verifyText} · ${changeText}`);
    } catch (err) {
      setActionError(`单活动深查失败：${errorText(err)}`);
    } finally {
      setBusyId((prev) => ({ ...prev, [id]: "" }));
    }
  }, [apiToken]);

  const runRefresh = React.useCallback(async (mode: "preview" | "write") => {
    if (mode === "write" && !canManage) {
      setActionError("内置目录快照导入已阻断：仅管理层且具备 vkpi 写权限的账号可执行。");
      return;
    }
    if (mode === "write" && previewGate?.importAllowed !== true) {
      setActionError("内置目录快照导入已阻断：须先完成预检，且后端明确返回 import_allowed=true。");
      return;
    }
    setRefreshing(mode);
    setActionError("");
    setReceipt("");
    try {
      const result = mode === "preview" ? await previewEventRadarRefresh(apiToken) : await refreshEventRadar(apiToken);
      if (mode === "preview") {
        setPreviewGate({
          importAllowed: result.import_allowed === true,
          qualityStatus: String(result.quality_status || "未返回"),
        });
        const previewItems = Array.isArray(result.preview_items)
          ? result.preview_items.filter((item) => item?.preview_only === true)
          : [];
        setDiscoveryPreviewItems(previewItems);
        setDiscoveryPreviewCount(Number(result.preview_item_count ?? previewItems.length) || 0);
      }
      setReceipt(refreshReceipt(result, mode));
      if (mode === "write") {
        setPreviewGate(null);
        await load();
      }
    } catch (err) {
      if (mode === "preview") setPreviewGate(null);
      setActionError(`${mode === "preview" ? "目录快照预检" : "目录快照导入"}失败：${errorText(err)}`);
    } finally {
      setRefreshing("");
    }
  }, [apiToken, canManage, load, previewGate]);

  const dealerLane = summaryCount(summary, "lane_counts", "dealer_event");
  const localLane = summaryCount(summary, "lane_counts", "local_activity");
  const dealerCount = dealerLane == null && localLane == null ? null : (dealerLane || 0) + (localLane || 0);
  const expoCount = summaryCount(summary, "lane_counts", "major_expo");
  const staleCount = summaryCount(summary, "stale_count") ?? summaryCount(summary, "verification_counts", "stale");
  const conflictCount = summaryCount(summary, "conflict_count") ?? summaryCount(summary, "verification_counts", "conflict");
  const sourceTotal = summaryCount(summary, "source_count") ?? summaryCount(summary, "sources", "total");
  const sourcePassportCount = summaryCount(summary, "source_identity_verified_count")
    ?? summaryCount(summary, "sources", "verified_fresh_passports");
  const sourceCurrent = summaryCount(summary, "source_freshness_counts", "current")
    ?? numberValue(summary?.sources?.by_freshness?.current);
  const usMatrix = summary?.us_jurisdiction_matrix;
  const claim = coverageText(coverageClaim, typeof summary?.coverage_claim === "string" ? summary.coverage_claim : null);

  return (
    <section className={`min-w-0 ${className}`} aria-label="活动雷达">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          {showHeading ? (
            <div className="flex items-center gap-2">
              <h3 className="text-[13px] font-semibold text-ink">活动雷达</h3>
              <span className="rounded-[6px] bg-accent-soft px-1.5 py-0.5 text-[9px] font-semibold text-accent">
                {filteredItems.length === count ? `${count} 条` : `${filteredItems.length} / ${count} 条`}
              </span>
            </div>
          ) : null}
          <p className={`${showHeading ? "mt-1" : ""} max-w-[760px] text-[9.5px] leading-4 text-muted`}>{claim}</p>
          {summary ? (
            <>
              <p className="mt-1 text-[9px] leading-4 text-muted">
                数据库最近来源快照 {sourceTotal ?? "—"} · 快照当时系统检查 {sourceCurrent ?? 0} · 快照已核验发布者身份护照 {sourcePassportCount ?? 0}
              </p>
              {usMatrix ? (
                <p className="mt-1 text-[9px] leading-4 text-muted" data-testid="event-us-jurisdiction-matrix">
                  美国已入库活动实体地理 {numberValue(usMatrix.covered_count) ?? 0} / {numberValue(usMatrix.jurisdiction_count) ?? 51} 州/DC · 与来源辖区覆盖分开计数，不代表美国活动市场全量覆盖
                </p>
              ) : null}
            </>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-1.5">
          <div className="inline-flex rounded-[8px] border border-line bg-panel p-0.5" aria-label="活动雷达视图">
            <button
              type="button"
              className={`${BUTTON} border-transparent ${viewMode === "list" ? "bg-card text-accent" : "text-muted"}`}
              aria-pressed={viewMode === "list"}
              onClick={() => setViewMode("list")}
            >
              <List size={11} /> 列表
            </button>
            <button
              type="button"
              className={`${BUTTON} border-transparent ${viewMode === "map" ? "bg-card text-accent" : "text-muted"}`}
              aria-pressed={viewMode === "map"}
              onClick={() => {
                setOffset(0);
                setCountry("US");
                setViewMode("map");
              }}
            >
              <MapIcon size={11} /> 州级地图
            </button>
          </div>
          <button
            type="button"
            className={`${BUTTON} border-line text-ink-2 hover:border-accent hover:text-accent`}
            disabled={!apiToken || Boolean(refreshing)}
            onClick={() => void runRefresh("preview")}
          >
            {refreshing === "preview" ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />} 预检目录快照
          </button>
          {canManage ? (
            <button
              type="button"
              className={`${BUTTON} border-accent bg-accent-soft text-accent hover:bg-card`}
              disabled={!apiToken || Boolean(refreshing) || previewGate?.importAllowed !== true}
              title={previewGate?.importAllowed === true ? "最新目录快照预检已通过" : "须先预检且 import_allowed=true"}
              onClick={() => void runRefresh("write")}
            >
              {refreshing === "write" ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />} 导入目录快照
            </button>
          ) : null}
        </div>
      </div>

      <EventUsCoveragePanel staging={candidateStaging} registry={usSourceRegistry} entityMatrix={usMatrix} error={coverageIntelError} />

      <div
        className={`mb-3 rounded-[8px] border px-2.5 py-2 text-[9.5px] ${
          previewGate == null
            ? "border-line bg-panel text-muted"
            : previewGate.importAllowed
              ? "border-good bg-good-soft text-good"
              : "border-crit bg-crit-soft text-crit"
        }`}
        role={previewGate != null && !previewGate.importAllowed ? "alert" : "status"}
      >
        {!canManage
          ? "当前账号只读 · 可校验内置目录快照；导入快照与机会决策仅限管理层且要求 vkpi 写权限。本操作不联网抓取。"
          : previewGate == null
          ? "目录快照导入门禁待预检 · 仅当 import_allowed=true 才可导入；本操作不联网抓取。"
          : previewGate.importAllowed
            ? `质量门禁通过 · import_allowed=true · quality_status=${previewGate.qualityStatus}`
            : `质量门禁已阻断 · import_allowed=false · quality_status=${previewGate.qualityStatus}`}
      </div>

      <div className="mb-3 grid grid-cols-2 gap-2 lg:grid-cols-4">
        <CountCard label="经销商 / 线下" value={dealerCount} note="单独配额 · 不与大展会混算" />
        <CountCard label="大展会记录" value={expoCount} note="数据库已登记记录 · 不代表全球全量" />
        <CountCard label="待复核 / 过期" value={staleCount} note="须重查发布者身份与来源后再行动" />
        <CountCard label="来源冲突" value={conflictCount} note="时间或地点口径不一致" />
      </div>

      <div className="mb-3 grid grid-cols-2 gap-2 rounded-[9px] border border-line bg-panel p-2.5 md:grid-cols-3 xl:grid-cols-7">
        <label className="flex min-w-0 flex-col gap-1 text-[9.5px] text-muted" htmlFor="event-radar-lane">
          活动层级
        <select id="event-radar-lane" className={INPUT} value={lane} onChange={(event) => { setOffset(0); setLane(event.target.value); }}>
          <option value="">全部</option>
          <option value="dealer_event">Dealer 活动</option>
          <option value="local_activity">线下活动</option>
          <option value="major_expo">大展会</option>
        </select>
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-[9.5px] text-muted" htmlFor="event-radar-source-kind">
          活动类型
          <select id="event-radar-source-kind" className={INPUT} value={sourceKind} onChange={(event) => { setOffset(0); setSourceKind(event.target.value); }}>
            <option value="">全部类型</option>
            {SOURCE_KIND_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-[9.5px] text-muted" htmlFor="event-radar-time">
          时间
          <select id="event-radar-time" className={INPUT} value={timeWindow} onChange={(event) => { setOffset(0); setTimeWindow(event.target.value); }}>
            <option value="upcoming">全部未来</option>
            <option value="30d">未来 30 天</option>
            <option value="90d">未来 90 天</option>
            <option value="past">历史活动</option>
            <option value="date_pending">日期待核验</option>
          </select>
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-[9.5px] text-muted" htmlFor="event-radar-evidence">
          证据状态
          <select id="event-radar-evidence" className={INPUT} value={evidenceStatus} onChange={(event) => { setOffset(0); setEvidenceStatus(event.target.value); }}>
            <option value="">全部证据</option>
            <option value="verified">已核验</option>
            <option value="review">待核验 / 过期</option>
            <option value="conflict">来源冲突</option>
          </select>
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-[9.5px] text-muted" htmlFor="event-radar-state">
          州 / DC
          <select id="event-radar-state" className={INPUT} value={stateCode} onChange={(event) => { setOffset(0); setStateCode(event.target.value); }}>
            <option value="">全部州</option>
            {availableStates.map((state) => <option key={state} value={state}>{state}</option>)}
          </select>
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-[9.5px] text-muted" htmlFor="event-radar-city">
          城市
          <select id="event-radar-city" className={INPUT} value={cityFilter} onChange={(event) => { setOffset(0); setCityFilter(event.target.value); }}>
            <option value="">全部城市</option>
            {availableCities.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-[9.5px] text-muted" htmlFor="event-radar-start-from">
          起始日期从
          <input id="event-radar-start-from" type="date" className={INPUT} value={startFrom} onChange={(event) => { setOffset(0); setStartFrom(event.target.value); }} aria-label="起始日期从" />
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-[9.5px] text-muted" htmlFor="event-radar-start-to">
          起始日期至
          <input id="event-radar-start-to" type="date" className={INPUT} value={startTo} onChange={(event) => { setOffset(0); setStartTo(event.target.value); }} aria-label="起始日期至" />
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-[9.5px] text-muted" htmlFor="event-radar-decision">人工判断
        <select id="event-radar-decision" className={INPUT} value={decision} onChange={(event) => { setOffset(0); setDecision(event.target.value); }}>
          <option value="">全部</option>
          <option value="new">待判断</option>
          <option value="watching">已关注</option>
          <option value="approved">已批准</option>
          <option value="dismissed">已忽略</option>
        </select>
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-[9.5px] text-muted" htmlFor="event-radar-country">国家
        <input
          id="event-radar-country"
          value={country}
          onChange={(event) => {
            const next = event.target.value.replace(/[^A-Za-z]/g, "").slice(0, 2);
            setCountry(next);
            if (!next || /^[A-Za-z]{2}$/.test(next)) setOffset(0);
          }}
          className={`${INPUT} w-[68px] uppercase`}
          placeholder="US"
          aria-label="国家代码"
        />
        </label>
        <div className="col-span-2 flex flex-wrap items-center gap-2 text-[9px] text-muted md:col-span-3 xl:col-span-7">
          <button type="button" className={`${BUTTON} border-accent text-accent`} onClick={() => { setOffset(0); setCountry("US"); setStateCode(""); }}>筛选全部美国记录</button>
          <button type="button" className={`${BUTTON} border-line text-muted`} onClick={() => { setOffset(0); setCountry(""); setStateCode(""); }}>恢复全球</button>
          {country && !/^[A-Za-z]{2}$/.test(country) ? <span className="text-warn">请输入完整两位国家代码；当前保留上一批结果。</span> : null}
          <span className="ml-auto">{summary?.last_refresh_at ? `最近目录快照导入 ${formatLocal(summary.last_refresh_at, { year: "numeric" })}` : "目录快照导入时间待记录"}</span>
        </div>
      </div>

      {receipt ? (
        <div
          className={`mb-2 rounded-[8px] border px-2.5 py-2 text-[10px] ${
            previewGate != null && !previewGate.importAllowed
              ? "border-warn bg-warn-soft text-warn"
              : "border-good bg-good-soft text-good"
          }`}
          role="status"
        >
          {receipt}
        </div>
      ) : null}
      {actionError ? <div className="mb-2 rounded-[8px] border border-crit bg-crit-soft px-2.5 py-2 text-[10px] text-crit" role="alert">{actionError}</div> : null}

      {!apiToken ? (
        <div className="rounded-[9px] border border-warn bg-warn-soft px-3 py-5 text-center text-[11px] text-warn">未登录 / 无 token，登录后读取活动机会。</div>
      ) : error ? (
        <div className="rounded-[9px] border border-crit bg-crit-soft px-3 py-5 text-center text-[11px] text-crit" role="alert">活动雷达读取失败：{error}</div>
      ) : loading ? (
        <div className="flex items-center justify-center gap-2 py-8 text-[11px] text-muted"><Loader2 size={14} className="animate-spin" />活动机会读取中…</div>
      ) : viewMode === "map" ? (
        <EventUsAggregateMap
          registry={usSourceRegistry}
          entityMatrix={usMatrix}
          selectedState={stateCode}
          onSelectState={(state) => {
            setOffset(0);
            setCountry("US");
            setStateCode(state);
            setViewMode("list");
          }}
        />
      ) : filteredItems.length === 0 && filteredDiscoveryPreviewItems.length > 0 ? (
        <div data-testid="event-discovery-preview">
          <div className="mb-2 rounded-[9px] border border-warn bg-warn-soft px-3 py-2 text-[9.5px] leading-4 text-warn">
            当前筛选没有已入库机会；以下显示 {filteredDiscoveryPreviewItems.length} / {discoveryPreviewCount} 条内置公开目录候选。它们未写入数据库、未通过当前发布者身份与新鲜度护照，不能批准或转为 Event。
          </div>
          <div className="grid grid-cols-1 gap-2 xl:grid-cols-2">
            {filteredDiscoveryPreviewItems.map((item) => (
              <OpportunityRow
                key={String(item.id)}
                item={item}
                busy=""
                canManage={canManage}
                onDecision={() => undefined}
                onPromote={() => undefined}
              />
            ))}
          </div>
        </div>
      ) : filteredItems.length === 0 ? (
        <div className="rounded-[9px] border border-dashed border-line-strong px-3 py-6 text-center text-[11px] text-muted">
          当前筛选下没有已入库机会；可校验内置目录快照，但该操作不会联网抓取活动。
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-2 xl:grid-cols-2">
          {filteredItems.map((item) => (
            <OpportunityRow
              key={String(item.id)}
              item={item}
              busy={busyId[String(item.id)] || ""}
              canManage={canManage}
              onDecision={(row, status) => void actDecision(row, status)}
              onPromote={(row) => void promote(row)}
              onDeepLookup={(row) => void deepLookup(row)}
            />
          ))}
        </div>
      )}
      {viewMode === "list" && !loading && !error && count > 0 ? (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-[8px] border border-line bg-panel px-2.5 py-2 text-[9.5px] text-muted" data-testid="event-radar-pagination">
          <span>
            当前 {items.length ? offset + 1 : 0}-{offset + items.length} / {count.toLocaleString()} 条 · 服务端筛选后分页
          </span>
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              className={`${BUTTON} border-line text-ink-2`}
              disabled={offset <= 0 || Boolean(refreshing)}
              onClick={() => setOffset(Math.max(0, offset - page.limit))}
            >
              上一页
            </button>
            <button
              type="button"
              className={`${BUTTON} border-accent text-accent`}
              disabled={!page.has_more || page.next_offset == null || Boolean(refreshing)}
              onClick={() => setOffset(Number(page.next_offset))}
            >
              下一页
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}

export default EventRadarModule;
