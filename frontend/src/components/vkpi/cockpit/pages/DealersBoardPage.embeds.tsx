import React from "react";
import { ModuleCard } from "./MarketVoicePage.modules";
import { dealerBrandCodes, MODULE_SOURCES } from "./DealersBoardPage.modules";
import { useTheme } from "../../../../app/providers/ThemeProvider";
import { getDealerActivities } from "../../../../services/vkpi/dealers-api";
import type {
  VkpiDealerActivityFeed,
  VkpiDealerCoverage,
  VkpiDealerPin,
  VkpiUsDealerSourceRegistry,
} from "../../../../services/vkpi/dealers-api";

const RealMap = React.lazy(() =>
  import("../components/RealMap").then((module) => ({ default: module.RealMap })),
);

// Dealers · 地图收编包装(MyKolBoardPage.embeds / ShopifyBoardPage.embeds 同一手法)。
//   手法 = 非侵入收编:**绝不改 RealMap(地图渲染原件,隔离皮肤对象,换肤在后续
//   隔离皮肤波)**,本文件只做卡头包装 + pin 数据映射(旧页 toPin 同口径)。
//   DealerMapEmbed — RealMap 整体零改动内嵌:定位 pin 上图 + 最近邻覆盖线 +
//                    深浅底图随主题(旧件内置逻辑,原样保留);加载覆盖层与旧页
//                    同位;0 定位点 → 角标诚实注明(不装点)。
//   pin 颜色:运行时读 --ds-accent 计算值(demo「JS 取色」机制,主题切换随 useTheme
//   重读),本文件零写死色;jsdom / 取不到值时回退服务端 pin.color(旧件职责)。
// 红线:绝不写 fit 分 / rule_v0;包装层颜色全 token 零写死色(地图内部配色属旧文件
//   职责,本刀零改动);数据缺席=诚实缺席(0 定位点如实标注,绝不编 pin)。

const src = (key: string) => MODULE_SOURCES[key] || { label: key, rows: [] as Array<[string, string]> };

const RADAR_DECISION_LABEL: Record<string, string> = {
  new: "待判断",
  watching: "已关注",
  approved: "已批准",
  dismissed: "已忽略",
  promoted: "已转 Event",
};

const RADAR_VERIFICATION_LABEL: Record<string, string> = {
  verified: "已核验",
  current: "已核验",
  pending: "待核验",
  provisional: "暂定",
  needs_review: "待复核",
  stale: "已过期",
  conflict: "来源冲突",
  conflicted: "来源冲突",
};

/* ---- 运行时取 accent token 计算值(主题切换 → useTheme 触发重读重绘) ---- */
function useAccentToken(): string | undefined {
  const { theme } = useTheme();
  return React.useMemo(() => {
    if (typeof window === "undefined") return undefined;
    const value = window.getComputedStyle(document.documentElement).getPropertyValue("--ds-accent").trim();
    return value || undefined;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme]);
}

/* ---- pin 映射(旧页 toPin 同口径:lat/lng 齐全才成点;country=US 为采集管线口径) ---- */
type DealerMapPinShape = {
  lat: number;
  lng: number;
  name: string;
  city?: string | null;
  country: string;
  color?: string;
  note?: string;
  dealer: VkpiDealerPin;
};

/** A user-initiated search link, never stored or rendered as Google evidence. */
export function dealerGoogleMapsSearchUrl(
  dealer: Pick<VkpiDealerPin, "name" | "address" | "city" | "state">,
): string {
  const query = [dealer.name, dealer.address, dealer.city, dealer.state]
    .map((value) => String(value || "").trim())
    .filter(Boolean)
    .join(", ");
  return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(query)}`;
}

// This is a state/DC aggregate, deliberately not a fake set of store pins.
// Exact store coordinates stay in RealMap below and require reviewed business
// rows.  Source-entry coverage and business-entity coverage remain separate.
const US_DEALER_STATE_TILE_ROWS: ReadonlyArray<ReadonlyArray<string | null>> = [
  ["AK", null, null, null, null, null, null, null, null, null, null, null, "ME"],
  [null, "WA", "ID", "MT", "ND", "MN", null, "WI", "MI", "NY", "VT", "NH", "MA"],
  [null, "OR", "NV", "WY", "SD", "IA", "IL", "IN", "OH", "PA", "NJ", "CT", "RI"],
  [null, "CA", "UT", "CO", "NE", "MO", "KY", "WV", "VA", "MD", "DE", "DC", null],
  [null, null, "AZ", "NM", "KS", "AR", "TN", "NC", "SC", null, null, null, null],
  [null, null, null, "TX", "OK", "LA", "MS", "AL", "GA", "FL", null, null, null],
  ["HI", null, null, null, null, null, null, null, null, null, null, null, null],
];

export function DealerUsAggregateMap({
  registry,
  entityMatrix,
  selectedState,
  onSelectState,
}: {
  registry: VkpiUsDealerSourceRegistry | null;
  entityMatrix: VkpiDealerCoverage["us_jurisdiction_matrix"] | null;
  selectedState: string;
  onSelectState: (state: string) => void;
}) {
  const sourceCounts = React.useMemo(() => {
    if (!registry) return null;
    const counts: Record<string, number> = {};
    for (const source of registry.dealer_discovery_sources || []) {
      for (const state of new Set(source.state_codes || [])) {
        const code = String(state || "").trim().toUpperCase();
        if (/^[A-Z]{2}$/.test(code)) counts[code] = Number(counts[code] || 0) + 1;
      }
    }
    return counts;
  }, [registry]);
  const sourceCovered = new Set(
    registry?.source_jurisdiction_matrix?.dealer_discovery_sources?.covered_states_dc || [],
  );
  const entityCounts = entityMatrix?.dealer_counts_by_state_dc;
  const verifiedCounts = entityMatrix?.public_listing_verified_counts_by_state_dc;
  const coordinateCounts = entityMatrix?.coordinate_present_counts_by_state_dc;
  const mapEligibleCounts = entityMatrix?.map_eligible_counts_by_state_dc
    || entityMatrix?.located_counts_by_state_dc;

  return (
    <div className="mb-2 rounded-[10px] border border-line bg-panel p-3" data-testid="dealer-us-state-tile-map">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="text-[11px] font-semibold text-ink">美国 Dealer 州级覆盖矩阵</div>
          <p className="mt-0.5 text-[9px] leading-4 text-warn">
            来源入口、已入库门店、公开来源核验、坐标记录与可上图实体分别计数；州级色块不是门店坐标，0 行不代表该州没有相机店。
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5 text-[8.5px] text-muted" aria-label="Dealer 州级矩阵图例">
          <span className="rounded-[5px] border border-good bg-good-soft px-1.5 py-1">有已入库门店实体</span>
          <span className="rounded-[5px] border border-accent bg-accent-soft px-1.5 py-1">有发现入口 · 0 实体</span>
          <span className="rounded-[5px] border border-line bg-card px-1.5 py-1">来源状态未知</span>
        </div>
      </div>
      <div className="mt-2 overflow-x-auto pb-1">
        <div className="grid min-w-[780px] gap-1" style={{ gridTemplateColumns: "repeat(13, minmax(0, 1fr))" }}>
          {US_DEALER_STATE_TILE_ROWS.flatMap((row, rowIndex) => row.map((state, columnIndex) => {
            if (!state) return <span key={`empty-${rowIndex}-${columnIndex}`} aria-hidden="true" />;
            const sourceCount = sourceCounts == null ? null : Number(sourceCounts[state] || 0);
            const entityCount = entityCounts ? Number(entityCounts[state] || 0) : null;
            const verifiedCount = verifiedCounts ? Number(verifiedCounts[state] || 0) : null;
            const coordinateCount = coordinateCounts ? Number(coordinateCounts[state] || 0) : null;
            const mapEligibleCount = mapEligibleCounts ? Number(mapEligibleCounts[state] || 0) : null;
            const hasSource = sourceCovered.has(state) || Number(sourceCount || 0) > 0;
            const selected = selectedState === state;
            const tone = entityCount != null && entityCount > 0
              ? "border-good bg-good-soft text-good"
              : hasSource
                ? "border-accent bg-accent-soft text-accent"
                : "border-line bg-card text-muted";
            const label = `${state} · 来源入口 ${sourceCount == null ? "未知" : sourceCount} · 已入库门店实体 ${entityCount == null ? "未知" : entityCount} · 公开来源核验 ${verifiedCount == null ? "未知" : verifiedCount} · 坐标记录 ${coordinateCount == null ? "未知" : coordinateCount} · 可上图实体 ${mapEligibleCount == null ? "未知" : mapEligibleCount} · 州级聚合非门店坐标`;
            return (
              <button
                key={state}
                type="button"
                className={`min-h-[48px] rounded-[7px] border px-1 py-1 text-left transition-colors ${tone} ${selected ? "ring-1 ring-accent" : ""}`}
                aria-label={label}
                aria-pressed={selected}
                title={`${label}；点击筛选下方已发布定位点，证据状态另行显示`}
                data-testid={`dealer-map-state-${state}`}
                onClick={() => onSelectState(selected ? "" : state)}
              >
                <span className="block font-mono text-[10px] font-semibold">{state}</span>
                <span className="block text-[7.5px] leading-3">实体 {entityCount == null ? "—" : entityCount} · 核验 {verifiedCount == null ? "—" : verifiedCount}</span>
                <span className="block text-[7.5px] leading-3">上图 {mapEligibleCount == null ? "—" : mapEligibleCount} · 坐标 {coordinateCount == null ? "—" : coordinateCount} · 源 {sourceCount == null ? "—" : sourceCount}</span>
              </button>
            );
          }))}
        </div>
      </div>
      <p className="mt-1 text-[8.5px] leading-4 text-muted">
        点击州 / DC 只筛选下方已发布定位点；来源候选队列不上图，人工业务记录可显式发布，但仍保留「未核验」证据标签。
      </p>
    </div>
  );
}

export function toMapPin(p: VkpiDealerPin, accent?: string): DealerMapPinShape | null {
  if (typeof p.lat !== "number" || typeof p.lng !== "number") return null;
  return {
    lat: p.lat,
    lng: p.lng,
    name: p.name,
    city: p.city,
    country: "US",
    color: accent || p.color,
    dealer: p,
    note: [
      p.address,
      p.truth_status?.public_listing === "verified" || p.source_status === "public_listing_verified"
        ? "公开门店来源已核验"
        : "人工 / 公开来源待核验",
      p.truth_status?.product_evidence === "current_public_url"
        ? "Viltrox 产品页当前核验有效"
        : p.truth_status?.product_evidence === "verified_public_url"
          ? "Viltrox 产品页已核验（需复查时效）"
          : p.truth_status?.product_evidence === "declared_public_url"
            ? "Viltrox 产品页仅登记"
            : "无产品页证据",
      p.truth_status?.viltrox_authorization === "confirmed" ? "Viltrox 授权已确认" : "Viltrox 授权待确认",
    ].filter(Boolean).join(" · ") || undefined,
  };
}

/* ============ 经销商地图(span12 embed;地图本体零改动整体收编) ============ */
export function DealerMapEmbed({
  token,
  pins,
  loading,
  emptyNote,
  candidateCount,
  registry,
  entityMatrix,
  onOpenSrc,
}: {
  token: string;
  pins: VkpiDealerPin[];
  loading: boolean;
  emptyNote: string;
  candidateCount?: number | null;
  registry?: VkpiUsDealerSourceRegistry | null;
  entityMatrix?: VkpiDealerCoverage["us_jurisdiction_matrix"] | null;
  onOpenSrc?: () => void;
}) {
  const accent = useAccentToken();
  const [stateFilter, setStateFilter] = React.useState("");
  const [cityFilter, setCityFilter] = React.useState("");
  const [channelFilter, setChannelFilter] = React.useState<"all" | "offline_location" | "online_product_page" | "both">("all");
  const [evidenceFilter, setEvidenceFilter] = React.useState<"all" | "public_listing_verified" | "candidate">("all");
  const [brandFilter, setBrandFilter] = React.useState("");
  const [selectedDealer, setSelectedDealer] = React.useState<VkpiDealerPin | null>(null);
  const [linkedActivities, setLinkedActivities] = React.useState<VkpiDealerActivityFeed | null>(null);
  const [linkedActivitiesError, setLinkedActivitiesError] = React.useState("");
  const [linkedActivitiesLoading, setLinkedActivitiesLoading] = React.useState(false);
  const brands = React.useMemo(
    () => [...new Set(pins.flatMap((pin) => dealerBrandCodes(pin)))].sort(),
    [pins],
  );
  const states = React.useMemo(
    () => [...new Set(pins.map((pin) => String(pin.state || "").trim().toUpperCase()).filter(Boolean))].sort(),
    [pins],
  );
  const cities = React.useMemo(
    () => [...new Set(
      pins
        .filter((pin) => !stateFilter || String(pin.state || "").trim().toUpperCase() === stateFilter)
        .map((pin) => String(pin.city || "").trim())
        .filter(Boolean),
    )].sort((a, b) => a.localeCompare(b)),
    [pins, stateFilter],
  );
  React.useEffect(() => {
    if (cityFilter && !cities.includes(cityFilter)) setCityFilter("");
  }, [cities, cityFilter]);
  const visiblePins = React.useMemo(
    () => pins.filter((pin) => {
      if (stateFilter && String(pin.state || "").trim().toUpperCase() !== stateFilter) return false;
      if (cityFilter && String(pin.city || "").trim() !== cityFilter) return false;
      if (brandFilter && !dealerBrandCodes(pin).includes(brandFilter)) return false;
      const isPublic = pin.truth_status?.public_listing === "verified" || pin.source_status === "public_listing_verified";
      if (evidenceFilter === "candidate" && isPublic) return false;
      if (evidenceFilter === "public_listing_verified" && !isPublic) return false;
      const offline = pin.channel_evidence?.physical_location_registered !== false;
      const online = pin.channel_evidence?.online_product_page !== "unavailable" && Boolean(
        pin.channel_evidence?.online_product_page || pin.brand_listing_url,
      );
      if (channelFilter === "offline_location" && !offline) return false;
      if (channelFilter === "online_product_page" && !online) return false;
      if (channelFilter === "both" && !(offline && online)) return false;
      return true;
    }),
    [pins, stateFilter, cityFilter, brandFilter, channelFilter, evidenceFilter],
  );
  React.useEffect(() => {
    let cancelled = false;
    setLinkedActivities(null);
    setLinkedActivitiesError("");
    const dealerId = selectedDealer?.id;
    if (!token || dealerId == null || dealerId === "") {
      setLinkedActivitiesLoading(false);
      return () => { cancelled = true; };
    }
    setLinkedActivitiesLoading(true);
    void getDealerActivities(token, dealerId, 8)
      .then((response) => {
        if (!cancelled) setLinkedActivities(response);
      })
      .catch((error) => {
        if (!cancelled) setLinkedActivitiesError(error instanceof Error ? error.message : "活动关联读取失败");
      })
      .finally(() => {
        if (!cancelled) setLinkedActivitiesLoading(false);
      });
    return () => { cancelled = true; };
  }, [selectedDealer?.id, token]);
  const mapped = React.useMemo(
    () => visiblePins.map((p) => toMapPin(p, accent)).filter((x): x is DealerMapPinShape => x !== null),
    [visiblePins, accent],
  );
  return (
    <ModuleCard
      title="经销商地图"
      cnt={mapped.length > 0 ? `${mapped.length} 定位` : undefined}
      srcLabel={src("mapD").label}
      srcRows={src("mapD").rows}
      onOpenSrc={onOpenSrc}
    >
      <DealerUsAggregateMap
        registry={registry ?? null}
        entityMatrix={entityMatrix ?? null}
        selectedState={stateFilter}
        onSelectState={setStateFilter}
      />
      <div className="mb-2 flex flex-wrap items-center gap-2 rounded-[9px] border border-line bg-panel px-2.5 py-2">
        <span className="rounded-[5px] border border-good bg-good-soft px-1.5 py-px text-[8.5px] font-semibold text-good">
          地图层：已显式发布 + 坐标齐全
        </span>
        <span className="rounded-[5px] border border-warn bg-warn-soft px-1.5 py-px text-[8.5px] font-semibold text-warn">
          来源候选待审 {candidateCount == null ? "—" : Number(candidateCount).toLocaleString()} · 不上图
        </span>
        <label className="ml-auto flex items-center gap-1.5 text-[9px] text-muted" htmlFor="dealer-map-state-filter">
          州 / DC
          <select
            id="dealer-map-state-filter"
            value={stateFilter}
            onChange={(event) => setStateFilter(event.target.value)}
            className="rounded-[7px] border border-line bg-card px-2 py-1 text-[10px] text-ink outline-none focus:border-accent"
          >
            <option value="">全部美国已发布定位记录</option>
            {states.map((state) => <option key={state} value={state}>{state}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-[9px] text-muted" htmlFor="dealer-map-brand-filter">
          品牌
          <select id="dealer-map-brand-filter" value={brandFilter} onChange={(event) => setBrandFilter(event.target.value)} className="rounded-[7px] border border-line bg-card px-2 py-1 text-[10px] text-ink outline-none focus:border-accent">
            <option value="">全部厂商</option>
            {brands.map((brand) => <option key={brand} value={brand}>{brand.replace("_", " ")}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-[9px] text-muted" htmlFor="dealer-map-city-filter">
          城市
          <select id="dealer-map-city-filter" value={cityFilter} onChange={(event) => setCityFilter(event.target.value)} className="rounded-[7px] border border-line bg-card px-2 py-1 text-[10px] text-ink outline-none focus:border-accent">
            <option value="">全部城市</option>
            {cities.map((city) => <option key={city} value={city}>{city}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-[9px] text-muted" htmlFor="dealer-map-channel-filter">
          线上 / 线下
          <select id="dealer-map-channel-filter" value={channelFilter} onChange={(event) => setChannelFilter(event.target.value as typeof channelFilter)} className="rounded-[7px] border border-line bg-card px-2 py-1 text-[10px] text-ink outline-none focus:border-accent">
            <option value="all">全部</option>
            <option value="offline_location">有线下地址</option>
            <option value="online_product_page">有线上产品页</option>
            <option value="both">线上产品页 + 线下地址</option>
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-[9px] text-muted" htmlFor="dealer-map-evidence-filter">
          证据状态
          <select id="dealer-map-evidence-filter" value={evidenceFilter} onChange={(event) => setEvidenceFilter(event.target.value as typeof evidenceFilter)} className="rounded-[7px] border border-line bg-card px-2 py-1 text-[10px] text-ink outline-none focus:border-accent">
            <option value="all">全部已上图记录</option>
            <option value="public_listing_verified">公开门店已核验</option>
            <option value="candidate">人工 / 公开来源待核验</option>
          </select>
        </label>
      </div>
      <div data-embed="dealer-map" className="relative h-full min-h-[300px] overflow-hidden rounded-xl border border-line">
        <React.Suspense fallback={<div className="absolute inset-0 flex items-center justify-center text-[11px] text-muted">地图组件加载中…</div>}>
          <RealMap
            pins={mapped}
            accentColor={accent}
            defaultZoom={4}
            onPinClick={(pin: DealerMapPinShape) => setSelectedDealer(pin.dealer || null)}
          />
        </React.Suspense>
        {loading ? (
          <div className="pointer-events-none absolute inset-0 z-[1001] flex items-center justify-center text-[11px] text-muted">
            定位加载中…
          </div>
        ) : null}
        {!loading && mapped.length === 0 ? (
          <div className="pointer-events-none absolute left-2 top-2 z-[1001] rounded-lg border border-warn bg-warn-soft px-2.5 py-1 text-[10.5px] text-warn">
            0 个定位点 · {emptyNote}
          </div>
        ) : null}
        {selectedDealer ? (
          <aside className="absolute bottom-2 right-2 top-2 z-[1002] w-[min(360px,calc(100%-16px))] overflow-y-auto rounded-[11px] border border-line bg-card p-3 shadow-xl" role="dialog" aria-label={`Dealer 地图详情 ${selectedDealer.name}`}>
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px] font-semibold text-ink">{selectedDealer.name}</div>
                <div className="mt-0.5 text-[9.5px] leading-4 text-muted">{[selectedDealer.address, selectedDealer.city, selectedDealer.state].filter(Boolean).join(" · ")}</div>
              </div>
              <button type="button" onClick={() => setSelectedDealer(null)} aria-label="关闭 Dealer 地图详情" className="rounded-[6px] border border-line px-2 py-1 text-[10px] text-muted">关闭</button>
            </div>
            <div className="mt-3 flex flex-wrap gap-1.5">
              <span className={`rounded-[6px] border px-2 py-1 text-[8.5px] ${selectedDealer.truth_status?.public_listing === "verified" || selectedDealer.source_status === "public_listing_verified" ? "border-good bg-good-soft text-good" : "border-warn bg-warn-soft text-warn"}`}>
                {selectedDealer.truth_status?.public_listing === "verified" || selectedDealer.source_status === "public_listing_verified" ? "公开门店来源已核验" : "人工 / 公开来源待核验"}
              </span>
              {dealerBrandCodes(selectedDealer).length ? dealerBrandCodes(selectedDealer).map((brand) => (
                <span key={brand} className="rounded-[6px] border border-accent bg-accent-soft px-2 py-1 text-[8.5px] text-accent">{brand.replace("_", " ")}</span>
              )) : <span className="rounded-[6px] border border-line px-2 py-1 text-[8.5px] text-muted">品牌关系未收集</span>}
              <span className="rounded-[6px] border border-line bg-panel px-2 py-1 text-[8.5px] text-muted">
                地图状态 {selectedDealer.publication_status === "published" ? "已发布" : "旧记录 / 待迁移"}
              </span>
              <span className={`rounded-[6px] border px-2 py-1 text-[8.5px] ${selectedDealer.location_verification?.google_place_cross_check?.status === "matched" ? "border-good bg-good-soft text-good" : "border-warn bg-warn-soft text-warn"}`}>
                {selectedDealer.location_verification?.google_place_cross_check?.status === "matched" ? "Google Maps 已匹配" : "Google Maps 待复核"}
              </span>
            </div>
            <dl className="mt-3 space-y-2 text-[9.5px]">
              <div className="flex gap-2"><dt className="w-20 flex-none text-muted">电话</dt><dd className="text-ink-2">{selectedDealer.phone || "未收集"}</dd></div>
              <div className="flex gap-2"><dt className="w-20 flex-none text-muted">公开邮箱</dt><dd className="text-ink-2">{selectedDealer.contact_email || "未收集"}</dd></div>
              {/* 2026-07-18 体检修:此前读 activity_status 列(全库恒 unknown),与下方
                  Event Radar 真实关联条数自相矛盾——改以懒加载的精确关联结果为准。 */}
              <div className="flex gap-2"><dt className="w-20 flex-none text-muted">活动观测</dt><dd className="text-ink-2">{
                linkedActivities != null
                  ? (Number(linkedActivities.count || 0) > 0 || Number(linkedActivities.linked_count || 0) > 0 ? "有公开活动" : "本次未观测到")
                  : selectedDealer.activity?.status === "active" ? "有公开活动"
                  : selectedDealer.activity?.status === "none_observed" ? "本次未观测到" : "检索中…"
              }</dd></div>
              <div className="flex gap-2">
                <dt className="w-20 flex-none text-muted">坐标来源</dt>
                <dd className="text-ink-2">
                  {selectedDealer.location_verification?.coordinate?.provider === "us_census_geocoder"
                    ? "US Census 地址级地理编码（非 Google）"
                    : "未记录 / 待核验"}
                </dd>
              </div>
            </dl>
            <section className="mt-3 rounded-[8px] border border-line bg-panel p-2" aria-label="Event Radar 关联活动">
              <div className="flex items-center justify-between gap-2 text-[9.5px]">
                <span className="font-semibold text-ink">Event Radar 关联活动</span>
                <span className="text-muted">精确 dealer_id · {linkedActivities?.count ?? "—"} 条</span>
              </div>
              {linkedActivitiesLoading ? <p className="mt-2 text-[9px] text-muted">读取中…</p> : null}
              {linkedActivitiesError ? <p className="mt-2 text-[9px] text-crit">{linkedActivitiesError}</p> : null}
              {!linkedActivitiesLoading && !linkedActivitiesError && linkedActivities?.activities.length === 0 ? (
                <p className="mt-2 text-[9px] leading-4 text-muted">
                  {Number(linkedActivities.suppressed_count || 0) > 0
                    ? `已精确关联 ${Number(linkedActivities.linked_count || linkedActivities.suppressed_count).toLocaleString()} 条，但来源尚未启用 / 激活，安全闸下暂不展示活动详情。`
                    : "暂无精确关联的已启用来源活动；不使用名称模糊推断。"}
                </p>
              ) : null}
              {linkedActivities?.activities.slice(0, 3).map((activity) => (
                <div key={String(activity.id)} className="mt-2 border-t border-line pt-2 text-[9px]">
                  <div className="font-medium text-ink-2">{activity.title}</div>
                  <div className="mt-0.5 text-muted">{[activity.start_date, activity.local_time_text, activity.city, activity.region].filter(Boolean).join(" · ") || "时间 / 地点待核验"}</div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {activity.is_internal_event === true || Boolean(activity.converted_event_id) ? (
                      <span className="rounded-[5px] border border-good bg-good-soft px-1.5 py-px text-good">
                        正式 Event{activity.converted_event_id ? ` · ${activity.converted_event_id}` : " · 已核对 promotion receipt"}
                      </span>
                    ) : (
                      <span className="rounded-[5px] border border-warn bg-warn-soft px-1.5 py-px text-warn">外部机会候选 · 未转 Event</span>
                    )}
                    <span className="rounded-[5px] border border-line bg-card px-1.5 py-px text-muted">
                      人工判断 {RADAR_DECISION_LABEL[String(activity.decision_status || "new").toLowerCase()] || activity.decision_status || "待判断"}
                    </span>
                    <span className="rounded-[5px] border border-line bg-card px-1.5 py-px text-muted">
                      证据 {RADAR_VERIFICATION_LABEL[String(activity.verification_status || "pending").toLowerCase()] || activity.verification_status || "待核验"}
                    </span>
                  </div>
                  {activity.official_url ? <a href={activity.official_url} target="_blank" rel="noreferrer" className="mt-1 inline-block text-accent underline">登记来源</a> : null}
                </div>
              ))}
            </section>
            <div className="mt-3 flex flex-wrap gap-2 text-[9.5px]">
              {selectedDealer.website_url ? <a href={selectedDealer.website_url} target="_blank" rel="noreferrer" className="text-accent underline">门店网站</a> : null}
              {selectedDealer.activity?.page_url ? <a href={selectedDealer.activity.page_url} target="_blank" rel="noreferrer" className="text-accent underline">活动页</a> : null}
              {selectedDealer.location_source_url ? <a href={selectedDealer.location_source_url} target="_blank" rel="noreferrer" className="text-accent underline">门店来源</a> : null}
              <a href={dealerGoogleMapsSearchUrl(selectedDealer)} target="_blank" rel="noreferrer" className="text-accent underline">在 Google Maps 复核（待核验）</a>
            </div>
            <p className="mt-3 border-t border-line pt-2 text-[8.5px] leading-4 text-muted">地址、联系方式、品牌关系和 Event Radar 活动分开记录；品牌关系不等于 Viltrox 授权或实时库存。</p>
          </aside>
        ) : null}
      </div>
    </ModuleCard>
  );
}
