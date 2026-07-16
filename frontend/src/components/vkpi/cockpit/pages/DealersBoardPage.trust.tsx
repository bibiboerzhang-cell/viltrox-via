import type {
  VkpiCandidateStagingSummary,
  VkpiDealerCoverage,
  VkpiUsDealerSourceRegistry,
} from "../../../../services/vkpi/dealers-api";

function sumStatuses(counts: Record<string, number> | undefined, terminal: string[]): number {
  if (!counts) return 0;
  return Object.entries(counts).reduce((sum, [key, value]) => sum + (terminal.includes(key) ? 0 : Number(value || 0)), 0);
}

export function DealerTrustPipeline({
  businessTotal,
  verifiedBusinessTotal,
  businessJurisdictionMatrix,
  staging,
  registry,
  stagingError,
  registryError,
}: {
  businessTotal: number | null;
  verifiedBusinessTotal: number | null;
  businessJurisdictionMatrix?: VkpiDealerCoverage["us_jurisdiction_matrix"] | null;
  staging: VkpiCandidateStagingSummary | null;
  registry: VkpiUsDealerSourceRegistry | null;
  stagingError?: string;
  registryError?: string;
}) {
  const sourceCount = registry ? Number(registry.counts?.dealer_discovery_sources ?? registry.dealer_discovery_sources?.length ?? 0) : null;
  const sourceKinds = registry?.counts?.dealer_source_kinds || {};
  const jurisdictionMatrix = registry?.source_jurisdiction_matrix?.dealer_discovery_sources;
  const adapterReadiness = registry?.adapter_readiness;
  const adapterSourceCount = adapterReadiness?.mapped_adapter_source_count
    ?? adapterReadiness?.adapter_source_count;
  const adapterMissingCount = adapterReadiness?.sources_without_mapped_adapter?.length
    ?? adapterReadiness?.sources_without_verified_adapter?.length;
  const sourceReadiness = registry?.adapter_source_readiness || [];
  const sourceReadinessById = new Map(
    sourceReadiness.map((item) => [item.source_registry_id, item] as const),
  );
  const fixtureVerifiedCount = Number(adapterReadiness?.source_fixture_verified_count || 0);
  const termsReviewedCount = sourceReadiness.filter((item) => item.terms_robots_reviewed).length;
  const persistenceReadiness = registry?.reviewed_persistence_readiness;
  const candidateCount = staging ? Number(staging.total || 0) : null;
  const pendingReview = staging ? sumStatuses(staging.review_status, ["approved", "rejected"]) : null;
  const blocked = staging
    ? Object.entries(staging.promotion_gate_status || {}).reduce(
        (sum, [key, value]) => sum + (["eligible", "passed", "ready"].includes(key) ? 0 : Number(value || 0)),
        0,
      )
    : null;
  const cards = [
    ["登记发现来源", sourceCount, "目录入口 · 不是门店实体"],
    ["待核验候选", candidateCount, staging?.status === "migration_pending" ? "候选表待迁移" : "不上图 · 不写业务表"],
    ["已入库业务行", businessTotal, "是否上图另看公开来源核验 + 坐标 · 不默认为授权"],
    ["公开来源已核验", verifiedBusinessTotal, "仅证明门店公开页 · 仍不等于授权"],
    ["待人工审核", pendingReview, "逐店核对名称 / 地址 / 联系方式"],
    ["晋级门禁阻断", blocked, "自动晋级为 0"],
  ] as const;
  return (
    <div data-testid="dealer-trust-pipeline">
      <div className="grid grid-cols-2 gap-2 xl:grid-cols-6">
        {cards.map(([label, value, note]) => (
          <div key={label} className="rounded-[9px] border border-line bg-panel px-3 py-2.5">
            <div className="text-[9px] text-muted">{label}</div>
            <div className="mt-1 font-mono text-[18px] font-semibold tabular-nums text-ink">{value == null ? "—" : Number(value).toLocaleString()}</div>
            <div className="mt-0.5 text-[8.5px] leading-4 text-muted">{note}</div>
          </div>
        ))}
      </div>
      <div className="mt-2.5 rounded-[9px] border border-line bg-card px-3 py-2">
        <div className="flex flex-wrap items-center gap-2 text-[9.5px]">
          <span className="font-semibold text-ink">美国登记来源入口 {sourceCount == null ? "—" : sourceCount}</span>
          {jurisdictionMatrix ? (
            <span className="rounded-[5px] border border-line px-1.5 py-px text-[8.5px] text-muted">
              来源发现州 / DC {jurisdictionMatrix.covered_count}/{jurisdictionMatrix.jurisdiction_count}
            </span>
          ) : null}
          {businessJurisdictionMatrix ? (
            <span className="rounded-[5px] border border-line px-1.5 py-px text-[8.5px] text-muted" data-testid="dealer-business-jurisdiction-matrix">
              已入库门店州 / DC {businessJurisdictionMatrix.covered_count}/{businessJurisdictionMatrix.jurisdiction_count}
            </span>
          ) : null}
          <span className="rounded-[5px] border border-warn bg-warn-soft px-1.5 py-px text-[8.5px] text-warn">来源候选 ≠ Viltrox 授权</span>
          <span className="rounded-[5px] border border-line px-1.5 py-px text-[8.5px] text-muted">美国记录部分覆盖</span>
        </div>
        {registry ? (
          <div className="mt-2 flex flex-wrap gap-1.5 text-[8.5px]" aria-label="美国 Dealer 来源类型与采集门禁">
            <span className="rounded-[5px] border border-line bg-panel px-1.5 py-px text-ink-2">
              制造商目录 {Number(sourceKinds.manufacturer_dealer_directory || 0).toLocaleString()}
            </span>
            <span className="rounded-[5px] border border-line bg-panel px-1.5 py-px text-ink-2">
              零售商门店目录 {Number(sourceKinds.retailer_location_directory || 0).toLocaleString()}
            </span>
            <span className="rounded-[5px] border border-warn bg-warn-soft px-1.5 py-px text-warn">
              来源启用 {Number(registry.counts?.enabled || 0)}/{sourceCount ?? 0} · 直接导入 {Number(registry.counts?.direct_import_allowed || 0)}
            </span>
            <span className="rounded-[5px] border border-line bg-panel px-1.5 py-px text-ink-2" data-testid="dealer-adapter-readiness">
              离线格式已映射 {adapterSourceCount == null ? "—" : adapterSourceCount}/{adapterReadiness?.registered_source_count ?? sourceCount ?? "—"}
            </span>
            <span className="rounded-[5px] border border-warn bg-warn-soft px-1.5 py-px text-warn">
              待映射格式 {adapterMissingCount == null ? "—" : adapterMissingCount}
            </span>
            <span className="rounded-[5px] border border-warn bg-warn-soft px-1.5 py-px text-warn" data-testid="dealer-source-fixture-readiness">
              来源快照已核验 {fixtureVerifiedCount}/{sourceCount ?? 0}
            </span>
            <span className="rounded-[5px] border border-warn bg-warn-soft px-1.5 py-px text-warn" data-testid="dealer-source-terms-readiness">
              条款 / robots 已复核 {termsReviewedCount}/{sourceCount ?? 0}
            </span>
            <span
              className={`rounded-[5px] border px-1.5 py-px ${persistenceReadiness?.supported ? "border-emerald-500/25 bg-emerald-500/[0.08] text-emerald-300" : "border-warn bg-warn-soft text-warn"}`}
              data-testid="dealer-reviewed-persistence-readiness"
            >
              人工核验落库 {persistenceReadiness?.supported ? "就绪" : "待迁移"}
            </span>
          </div>
        ) : null}
        {registry?.dealer_discovery_sources?.length ? (
          <div className="mt-2 grid gap-1.5 md:grid-cols-2">
            {registry.dealer_discovery_sources.map((source) => {
              const readiness = sourceReadinessById.get(source.id);
              const snapshot = source.source_snapshot;
              const snapshotCount = Number(
                snapshot?.unique_organization_count
                  ?? snapshot?.listed_entry_count
                  ?? 0,
              );
              const snapshotLabel = snapshotCount > 0
                ? `${snapshotCount.toLocaleString()} 个官方目录组织/名称`
                : "未形成来源快照计数";
              return (
                <a
                  key={source.id}
                  href={source.canonical_url}
                  target="_blank"
                  rel="noreferrer"
                  className="flex min-w-0 flex-wrap items-start justify-between gap-2 rounded-[7px] border border-line px-2.5 py-2 text-[9px] text-ink-2 hover:border-accent hover:text-accent"
                  data-testid={`dealer-source-readiness-${source.id}`}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-semibold">{source.publisher} · {source.name}</span>
                    <span className="mt-0.5 block text-[8px] leading-3 text-muted">
                      {snapshotLabel}
                      {snapshot?.record_granularity ? ` · ${snapshot.record_granularity}` : ""}
                      {source.manufacturer_authorization_scope ? ` · ${source.manufacturer_authorization_scope}` : ""}
                      {snapshot ? " · 不可直接当分店坐标" : ""}
                    </span>
                  </span>
                  <span className="flex flex-none flex-wrap items-center justify-end gap-1 text-[8px]">
                    <span className={readiness?.format_mapped ? "text-emerald-300" : "text-warn"}>
                      {readiness ? `格式${readiness.format_mapped ? "已映射" : "待映射"}` : "格式状态待返回"}
                    </span>
                    <span className={readiness?.source_fixture_verified ? "text-emerald-300" : "text-warn"}>
                      快照{readiness?.source_fixture_verified ? "已核验" : "待核验"}
                    </span>
                    <span className={readiness?.terms_robots_reviewed ? "text-emerald-300" : "text-warn"}>
                      条款{readiness?.terms_robots_reviewed ? "已复核" : "待复核"}
                    </span>
                    <span className="text-muted">
                      {readiness?.snapshot_import_readiness === "ready" ? "候选可入暂存" : "导入阻断"}
                    </span>
                  </span>
                </a>
              );
            })}
          </div>
        ) : (
          <p className="mt-2 text-[9px] text-muted">暂无可引用的发现来源；不会用静态门店补数。</p>
        )}
        {jurisdictionMatrix ? (
          <p className="mt-2 text-[9px] leading-4 text-muted">
            51/51 如果显示，只表示登记的发布者自有公开入口枚举了该州 / DC；不表示候选已抓取、地图已上点或 Viltrox 已授权。
          </p>
        ) : null}
        {adapterReadiness ? (
          <p className="mt-1 text-[9px] leading-4 text-muted">
            格式映射不等于来源级快照已验证；适配器只处理已捕获的官方离线快照。来源未完成条款 / robots 复核时仍保持禁用，不会自动联网或写入门店业务表。
          </p>
        ) : null}
        {stagingError || registryError ? (
          <p className="mt-2 text-[9px] text-warn" role="status">
            部分数据降级：{[stagingError && `候选队列 ${stagingError}`, registryError && `来源注册表 ${registryError}`].filter(Boolean).join("；")}
          </p>
        ) : null}
      </div>
    </div>
  );
}
