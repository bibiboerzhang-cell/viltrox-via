/**
 * Creators tab v2 — HERO tab
 *
 * Layout:
 *   PageHeader
 *   LifecycleRow (All/New/Active/Idle/Churned/Blocked)
 *   FiltersBar (+ FiltersPanel when expanded: Time/VIP/Platform/Score/Tag)
 *   SortBanner
 *   DataTable (creator list with VID primary, VIP dot, platform, stats, tags, stage)
 *   BulkBar
 *   DetailDrawer (right panel, VID + VIP progress + KPIs + tags + heatmap + actions)
 *
 * Data: fetchAdminCreatorsSnapshot → { roster, dashboard, growth }
 */
import { FormEvent, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  deleteAdminCreatorShopHero,
  fetchAdminCreatorShopHeroes,
  fetchAdminCreatorsSnapshot,
  saveAdminCreatorShopHero,
  type AdminShopHero,
} from "../../../services/admin.service";
import type { AuthUser } from "../../../lib/api";
import { Icons } from "../Icons";
import {
  BulkBar,
  CreatorIdCell,
  DataTable,
  EmptyCard,
  ErrorCard,
  FilterCheck,
  FilterGroup,
  FiltersBar,
  FiltersPanel,
  Heatmap30,
  KPIGrid,
  LifecycleRow,
  LoadingCard,
  PageHeader,
  RangeInput,
  SectionLabel,
  SortBanner,
  StatusPill,
  TagList,
  TierBadge,
  TierDot,
  TierProgress,
  TIER_LABELS,
  TIER_MULTIPLIERS,
  TIER_REQUIREMENTS,
  deriveTier,
  formatVID,
  useAdminSnapshot,
  type DataColumn,
  type DataSort,
  type FilterChip,
  type LifecycleStage,
  type VipTier,
} from "../shared_v2";

interface Props {
  token: string;
  user: AuthUser;
}

interface CreatorRow {
  id: number;
  vid: string;
  handle: string;
  email: string;
  tier: VipTier;
  platform: string;
  submissions: number;
  score: number;
  trust: number;
  points: number;
  stage: "new" | "active" | "idle" | "churn" | "block";
  tags: string[];
  lastActiveDays: number;
  heatmap30: number[];
}

const STAGE_TO_STATUS_TONE: Record<CreatorRow["stage"], "new" | "active" | "idle" | "churn" | "block"> = {
  new: "new",
  active: "active",
  idle: "idle",
  churn: "churn",
  block: "block",
};

// ── Normalize raw backend row → CreatorRow ──
function normalize(raw: Record<string, unknown>): CreatorRow {
  const id = Number(raw.id || raw.user_id || 0);
  const submissions = Number(raw.submissions || raw.submissions_count || raw.submission_count || raw.valid_videos || 0);
  const points = Number(raw.points_balance || raw.points || raw.estimated_points || 0);
  const score = Number(raw.avg_score || raw.avg_creator || raw.score || raw.best_score || 0);
  const trust = Number(raw.trust_score || 50);
  const lastSeen = raw.last_seen ? Date.parse(String(raw.last_seen)) : NaN;
  const lastActiveDays = Number(raw.last_active_days ?? (Number.isFinite(lastSeen) ? Math.floor((Date.now() - lastSeen) / 86400000) : 999));
  const status = String(raw.status || "active").toLowerCase();
  const tierRaw = String(raw.tier || raw.vip_tier || "").toLowerCase();
  const tier: VipTier = ["student", "bronze", "silver", "gold", "platinum"].includes(tierRaw)
    ? (tierRaw as VipTier)
    : deriveTier(submissions, points);

  const stage: CreatorRow["stage"] =
    status === "blocked"
      ? "block"
      : lastActiveDays <= 7
      ? "new"
      : lastActiveDays <= 30
      ? "active"
      : lastActiveDays <= 90
      ? "idle"
      : "churn";

  // Try to build heatmap from activity_30d if provided, else zeros
  let heatmap30: number[] = [];
  if (Array.isArray(raw.activity_30d)) {
    heatmap30 = (raw.activity_30d as unknown[]).map((v) => Number(v) || 0);
  }

  const tags: string[] = Array.isArray(raw.tags)
    ? (raw.tags as unknown[]).map(String)
    : [];

  const vid = String(raw.creator_code || formatVID(id));
  const handle = String(raw.handle || raw.primary_handle || raw.display_name || "").replace(/^@/, "");
  const email = String(raw.email || "");
  const platform = String(raw.primary_platform || raw.platform || "—");

  return {
    id,
    vid,
    handle,
    email,
    tier,
    platform,
    submissions,
    score,
    trust,
    points,
    stage,
    tags,
    lastActiveDays,
    heatmap30,
  };
}

export function CreatorsTab({ token }: Props) {
  const { t } = useTranslation();
  const { data, loading, error, refresh } = useAdminSnapshot(token, fetchAdminCreatorsSnapshot);

  const rows: CreatorRow[] = useMemo(() => (data?.roster ?? []).map(normalize), [data]);

  // Filters state
  const [stage, setStage] = useState<string>("all");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [timeRange, setTimeRange] = useState("30d");
  const [tierSet, setTierSet] = useState<Set<VipTier>>(new Set());
  const [platformSet, setPlatformSet] = useState<Set<string>>(new Set());
  const [scoreRange, setScoreRange] = useState({ min: "0", max: "100" });
  const [trustRange, setTrustRange] = useState({ min: "0", max: "100" });
  const [tagSet, setTagSet] = useState<Set<string>>(new Set());

  // Sort + selection
  const [sort, setSort] = useState<DataSort | null>({ key: "score", dir: "desc" });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // ── Derived: lifecycle counts (before filters) ──
  const stageCounts = useMemo(() => {
    const counts = { new: 0, active: 0, idle: 0, churn: 0, block: 0 };
    rows.forEach((r) => {
      counts[r.stage]++;
    });
    return counts;
  }, [rows]);

  const lifecycleStages: LifecycleStage[] = [
    { key: "new", label: "New", count: stageCounts.new, colorVar: "--ax-status-review" },
    { key: "active", label: "Active", count: stageCounts.active, colorVar: "--ax-status-pass" },
    { key: "idle", label: "Idle", count: stageCounts.idle, colorVar: "--ax-status-idle" },
    { key: "churn", label: "Churned", count: stageCounts.churn, colorVar: "--ax-status-queue" },
    { key: "block", label: "Blocked", count: stageCounts.block, colorVar: "--ax-status-alert" },
  ];

  // ── Derived: filtered rows ──
  const filtered = useMemo(() => {
    return rows.filter((r) => {
      if (stage !== "all" && r.stage !== stage) return false;
      if (tierSet.size > 0 && !tierSet.has(r.tier)) return false;
      if (platformSet.size > 0 && !platformSet.has(r.platform)) return false;
      const sMin = parseFloat(scoreRange.min) || 0;
      const sMax = parseFloat(scoreRange.max) || 100;
      if (r.score < sMin || r.score > sMax) return false;
      const tMin = parseFloat(trustRange.min) || 0;
      const tMax = parseFloat(trustRange.max) || 100;
      if (r.trust < tMin || r.trust > tMax) return false;
      if (tagSet.size > 0) {
        const hasAny = r.tags.some((t) => tagSet.has(t));
        if (!hasAny) return false;
      }
      return true;
    });
  }, [rows, stage, tierSet, platformSet, scoreRange, trustRange, tagSet]);

  // ── Derived: sorted ──
  const sorted = useMemo(() => {
    if (!sort) return filtered;
    const key = sort.key as keyof CreatorRow;
    const dir = sort.dir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const av = a[key];
      const bv = b[key];
      if (typeof av === "number" && typeof bv === "number") return (av - bv) * dir;
      return String(av).localeCompare(String(bv)) * dir;
    });
  }, [filtered, sort]);

  // ── Derived: active filter chips ──
  const chips: FilterChip[] = useMemo(() => {
    const list: FilterChip[] = [];
    if (timeRange !== "30d")
      list.push({ key: "time", label: timeRange, onRemove: () => setTimeRange("30d") });
    if (tierSet.size > 0) {
      list.push({
        key: "tier",
        label: "VIP: " + Array.from(tierSet).map((t) => TIER_LABELS[t]).join(" + "),
        onRemove: () => setTierSet(new Set()),
      });
    }
    if (platformSet.size > 0) {
      list.push({
        key: "platform",
        label: Array.from(platformSet).join(", "),
        onRemove: () => setPlatformSet(new Set()),
      });
    }
    if (scoreRange.min !== "0" || scoreRange.max !== "100") {
      list.push({
        key: "score",
        label: `均分 ${scoreRange.min}-${scoreRange.max}`,
        onRemove: () => setScoreRange({ min: "0", max: "100" }),
      });
    }
    if (trustRange.min !== "0" || trustRange.max !== "100") {
      list.push({
        key: "trust",
        label: `Trust ${trustRange.min}-${trustRange.max}`,
        onRemove: () => setTrustRange({ min: "0", max: "100" }),
      });
    }
    if (tagSet.size > 0) {
      list.push({
        key: "tag",
        label: `Tag: ${Array.from(tagSet).join(" / ")}`,
        onRemove: () => setTagSet(new Set()),
      });
    }
    return list;
  }, [timeRange, tierSet, platformSet, scoreRange, trustRange, tagSet]);

  // ── Toggle helpers ──
  const toggleTier = (tier: VipTier) => {
    setTierSet((prev) => {
      const next = new Set(prev);
      if (next.has(tier)) next.delete(tier);
      else next.add(tier);
      return next;
    });
  };
  const togglePlatform = (p: string) => {
    setPlatformSet((prev) => {
      const next = new Set(prev);
      if (next.has(p)) next.delete(p);
      else next.add(p);
      return next;
    });
  };
  const toggleTag = (tag: string) => {
    setTagSet((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });
  };
  const clearAll = () => {
    setTimeRange("30d");
    setTierSet(new Set());
    setPlatformSet(new Set());
    setScoreRange({ min: "0", max: "100" });
    setTrustRange({ min: "0", max: "100" });
    setTagSet(new Set());
  };

  // ── Tier tallies for filter panel ──
  const tierCounts = useMemo(() => {
    const c: Record<VipTier, number> = { student: 0, bronze: 0, silver: 0, gold: 0, platinum: 0 };
    rows.forEach((r) => c[r.tier]++);
    return c;
  }, [rows]);
  const platformCounts = useMemo(() => {
    const c: Record<string, number> = {};
    rows.forEach((r) => {
      c[r.platform] = (c[r.platform] || 0) + 1;
    });
    return c;
  }, [rows]);
  const allTags = useMemo(() => {
    const c: Record<string, number> = {};
    rows.forEach((r) => r.tags.forEach((t) => (c[t] = (c[t] || 0) + 1)));
    return Object.entries(c).map(([label, count]) => ({ label, count }));
  }, [rows]);

  // ── Columns ──
  const columns: DataColumn<CreatorRow>[] = useMemo(
    () => [
      {
        key: "vid",
        label: "VID / 创作者",
        width: "1.6fr",
        sortable: true,
        render: (r) => <CreatorIdCell vid={r.vid} handle={r.handle} />,
      },
      {
        key: "tier",
        label: "VIP",
        width: "80px",
        sortable: true,
        render: (r) => (
          <div className={`ax-tier ax-tier--${r.tier}`}>
            <TierDot tier={r.tier} />
            {TIER_LABELS[r.tier]}
          </div>
        ),
      },
      {
        key: "platform",
        label: "主平台",
        width: "70px",
        render: (r) => <span style={{ color: "var(--ax-text-3)" }}>{r.platform}</span>,
      },
      {
        key: "submissions",
        label: "提交",
        width: "60px",
        sortable: true,
        render: (r) => (
          <span className="ax-num" style={{ color: "var(--ax-text-5)", fontWeight: 600 }}>
            {r.submissions}
          </span>
        ),
      },
      {
        key: "score",
        label: "均分",
        width: "60px",
        sortable: true,
        accent: true,
        render: (r) => (
          <span
            className="ax-num"
            style={{
              color: r.score >= 80 ? "var(--ax-status-pass)" : "var(--ax-text-5)",
              fontWeight: 600,
            }}
          >
            {Math.round(r.score)}
          </span>
        ),
      },
      {
        key: "trust",
        label: "Trust",
        width: "60px",
        sortable: true,
        render: (r) => (
          <span
            className="ax-num"
            style={{
              color:
                r.trust >= 80
                  ? "var(--ax-status-pass)"
                  : r.trust < 40
                  ? "var(--ax-status-alert)"
                  : "var(--ax-text-5)",
              fontWeight: 600,
            }}
          >
            {Math.round(r.trust)}
          </span>
        ),
      },
      {
        key: "tags",
        label: "Tags",
        width: "1.2fr",
        render: (r) =>
          r.tags.length > 0 ? <TagList tags={r.tags} maxInline={2} /> : (
            <span style={{ color: "var(--ax-text-0)", fontSize: 10 }}>—</span>
          ),
      },
      {
        key: "stage",
        label: "阶段",
        width: "80px",
        render: (r) => (
          <StatusPill tone={STAGE_TO_STATUS_TONE[r.stage]}>
            {r.stage.toUpperCase()}
          </StatusPill>
        ),
      },
    ],
    [],
  );

  const toggleSelect = (id: string, next: boolean) => {
    setSelected((prev) => {
      const n = new Set(prev);
      if (next) n.add(id);
      else n.delete(id);
      return n;
    });
  };

  const sortLabels: Record<string, string> = {
    score: "均分",
    trust: "Trust",
    submissions: "提交数",
    tier: "VIP",
    vid: "VID",
  };

  // ── Selected detail ──
  const detail = useMemo(
    () => (selectedId ? sorted.find((r) => String(r.id) === selectedId) : null),
    [sorted, selectedId],
  );

  // ── KPIs ──
  const kpis = [
    { label: "Total", value: rows.length },
    { label: "Active", value: stageCounts.active },
    { label: "Gold+", value: tierCounts.gold + tierCounts.platinum },
    {
      label: "Pending upgrade",
      value: rows.filter((r) => r.tier === "gold" && r.submissions >= 14).length,
      hint: "→ Platinum",
    },
  ];

  // ── Fixed platform order (5 platforms) ──
  const PLATFORMS = ["Facebook", "YouTube", "Instagram", "TikTok", "Reddit"];

  return (
    <div>
      <PageHeader
        title="Creators"
        subtitle={`${rows.length} total · ${stageCounts.new} new this week`}
        actions={
          <>
            <button type="button" className="ax-btn">
              <Icons.plus /> 邀请
            </button>
            <button type="button" className="ax-btn">
              <Icons.download /> Export
            </button>
            <button type="button" className="ax-btn" onClick={refresh} disabled={loading}>
              {loading ? "刷新中…" : "刷新"}
            </button>
          </>
        }
      />

      {error ? (
        <div style={{ padding: 16 }}>
          <ErrorCard detail={error} onRetry={refresh} />
        </div>
      ) : null}

      {/* Lifecycle row */}
      <LifecycleRow
        totalCount={rows.length}
        stages={lifecycleStages}
        active={stage}
        onChange={setStage}
      />

      {/* Filters bar (collapsed summary) */}
      <FiltersBar
        open={filtersOpen}
        onToggle={() => setFiltersOpen((v) => !v)}
        chips={chips}
        total={rows.length}
        shown={filtered.length}
        onClear={chips.length > 0 ? clearAll : undefined}
      />

      {/* Filters panel (expanded) */}
      {filtersOpen ? (
        <FiltersPanel>
          <FilterGroup label="Time range">
            <div style={{ display: "flex", flexWrap: "wrap", gap: 3 }}>
              {["24h", "7d", "30d", "90d", "all"].map((r) => (
                <button
                  key={r}
                  type="button"
                  className={`ax-seg__btn${timeRange === r ? " is-active" : ""}`}
                  onClick={() => setTimeRange(r)}
                >
                  {r}
                </button>
              ))}
            </div>
          </FilterGroup>

          <FilterGroup label="VIP 等级">
            {(["student", "bronze", "silver", "gold", "platinum"] as VipTier[]).map((t) => (
              <FilterCheck
                key={t}
                label={TIER_LABELS[t]}
                sub={TIER_MULTIPLIERS[t]}
                count={tierCounts[t]}
                checked={tierSet.has(t)}
                onChange={() => toggleTier(t)}
                dot={<TierDot tier={t} />}
              />
            ))}
          </FilterGroup>

          <FilterGroup label="平台">
            {PLATFORMS.map((p) => (
              <FilterCheck
                key={p}
                label={p}
                count={platformCounts[p] || 0}
                checked={platformSet.has(p)}
                onChange={() => togglePlatform(p)}
              />
            ))}
          </FilterGroup>

          <FilterGroup label="分数">
            <RangeInput
              label="均分"
              minValue={scoreRange.min}
              maxValue={scoreRange.max}
              onChange={setScoreRange}
            />
            <RangeInput
              label="Trust"
              minValue={trustRange.min}
              maxValue={trustRange.max}
              onChange={setTrustRange}
            />
          </FilterGroup>

          <FilterGroup label="Tag">
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 6 }}>
              {allTags.slice(0, 12).map((t) => (
                <span
                  key={t.label}
                  className="ax-chip"
                  style={
                    tagSet.has(t.label)
                      ? undefined
                      : {
                          background: "var(--ax-bg-3)",
                          borderColor: "var(--ax-border-3)",
                          color: "var(--ax-text-2)",
                        }
                  }
                  onClick={() => toggleTag(t.label)}
                >
                  {tagSet.has(t.label) ? "" : "+ "}
                  {t.label}
                  <span style={{ color: "var(--ax-text-0)", marginLeft: 4, fontSize: 9 }}>
                    {t.count}
                  </span>
                  {tagSet.has(t.label) ? <span className="ax-chip__close">×</span> : null}
                </span>
              ))}
              {allTags.length === 0 ? (
                <span style={{ color: "var(--ax-text-1)", fontSize: 10 }}>
                  暂无 tag — 在创作者详情里添加
                </span>
              ) : null}
            </div>
          </FilterGroup>
        </FiltersPanel>
      ) : null}

      {/* KPI + table + detail */}
      <div style={{ padding: 16 }}>
        {loading && rows.length === 0 ? (
          <LoadingCard label="加载创作者…" />
        ) : rows.length === 0 ? (
          <EmptyCard
            label="暂无创作者"
            hint="创作者注册后会出现在此"
          />
        ) : (
          <>
            <div style={{ marginBottom: 16 }}>
              <KPIGrid items={kpis} columns={4} />
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 290px", gap: 12 }}>
              <div
                style={{
                  border: "0.5px solid var(--ax-border-2)",
                  borderRadius: 6,
                  overflow: "hidden",
                  background: "var(--ax-bg-1)",
                }}
              >
                <SortBanner
                  column={sort ? sortLabels[sort.key] ?? sort.key : undefined}
                  dir={sort?.dir}
                  onClear={sort ? () => setSort(null) : undefined}
                />
                <DataTable
                  columns={columns}
                  rows={sorted}
                  rowKey={(r) => String(r.id)}
                  sort={sort}
                  onSortChange={setSort}
                  selected={selected}
                  onSelect={toggleSelect}
                  onRowClick={(r) => setSelectedId(String(r.id))}
                  selectedId={selectedId}
                  emptyLabel="无符合条件的创作者"
                />
                <BulkBar
                  selectedCount={selected.size}
                  pager={
                    <span>
                      {sorted.length} of {rows.length} · ‹ ›
                    </span>
                  }
                >
                  {selected.size > 0 ? (
                    <>
                      <button type="button" className="ax-btn ax-btn--sm">→ 升级</button>
                      <button type="button" className="ax-btn ax-btn--sm">+ 标签</button>
                      <button
                        type="button"
                        className="ax-btn ax-btn--sm"
                        style={{ color: "var(--ax-status-review)" }}
                      >
                        <Icons.mail /> 邀约
                      </button>
                      <button
                        type="button"
                        className="ax-btn ax-btn--sm"
                        style={{ color: "var(--ax-status-alert)" }}
                      >
                        <Icons.ban /> 封禁
                      </button>
                    </>
                  ) : null}
                </BulkBar>
              </div>

              {/* Detail drawer */}
              <CreatorDetail token={token} row={detail} onClose={() => setSelectedId(null)} />
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function CreatorDetail({ token, row, onClose }: { token: string; row: CreatorRow | null | undefined; onClose: () => void }) {
  if (!row) {
    return (
      <div
        style={{
          border: "0.5px solid var(--ax-border-2)",
          borderRadius: 6,
          background: "var(--ax-bg-1)",
          padding: 20,
          color: "var(--ax-text-1)",
          fontSize: 11,
          textAlign: "center",
          alignSelf: "start",
        }}
      >
        选择左侧任一创作者查看详情
      </div>
    );
  }

  // Determine next tier + progress
  const tierOrder: VipTier[] = ["student", "bronze", "silver", "gold", "platinum"];
  const idx = tierOrder.indexOf(row.tier);
  const nextTier = idx < tierOrder.length - 1 ? tierOrder[idx + 1] : undefined;
  const nextReq = nextTier ? TIER_REQUIREMENTS[nextTier] : null;
  const videoProgress = nextReq ? Math.min(1, row.submissions / Math.max(1, nextReq.videos)) : 1;
  const pointsProgress = nextReq ? Math.min(1, row.points / Math.max(1, nextReq.points || 1)) : 1;
  const progress = nextReq ? Math.min(videoProgress, pointsProgress) : 1;

  return (
    <div
      style={{
        border: "0.5px solid var(--ax-border-2)",
        borderRadius: 6,
        background: "var(--ax-bg-1)",
        padding: 12,
        alignSelf: "start",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
        <span className="ax-avatar ax-avatar--lg">
          {(row.handle || row.vid).charAt(0).toUpperCase()}
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            className="ax-mono"
            style={{ fontSize: 13, fontWeight: 600, color: "var(--ax-text-5)" }}
          >
            {row.vid}
          </div>
          <div style={{ fontSize: 10, color: "var(--ax-text-2)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {row.handle ? `@${row.handle}` : ""}
            {row.handle && row.email ? " · " : ""}
            {row.email}
          </div>
        </div>
        <TierBadge tier={row.tier} />
        <span
          onClick={onClose}
          style={{ cursor: "pointer", color: "var(--ax-text-1)", fontSize: 14, padding: 4 }}
          title="关闭"
        >
          ×
        </span>
      </div>

      {nextTier ? (
        <div style={{ marginBottom: 10 }}>
          <TierProgress
            current={TIER_LABELS[row.tier]}
            next={TIER_LABELS[nextTier]}
            progress={progress}
            breakdown={nextReq ? `${row.submissions}/${nextReq.videos} videos · ${row.points}/${nextReq.points} pts` : undefined}
          />
        </div>
      ) : null}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 6,
          marginBottom: 10,
        }}
      >
        <div className="ax-card" style={{ padding: 8 }}>
          <div style={{ fontSize: 9, color: "var(--ax-text-2)" }}>Trust</div>
          <div
            className="ax-num"
            style={{
              fontSize: 14,
              fontWeight: 600,
              color: row.trust >= 80 ? "var(--ax-status-pass)" : "var(--ax-text-5)",
            }}
          >
            {Math.round(row.trust)}
          </div>
        </div>
        <div className="ax-card" style={{ padding: 8 }}>
          <div style={{ fontSize: 9, color: "var(--ax-text-2)" }}>提交</div>
          <div className="ax-num" style={{ fontSize: 14, fontWeight: 600 }}>
            {row.submissions}
          </div>
        </div>
        <div className="ax-card" style={{ padding: 8 }}>
          <div style={{ fontSize: 9, color: "var(--ax-text-2)" }}>均分</div>
          <div className="ax-num" style={{ fontSize: 14, fontWeight: 600 }}>
            {Math.round(row.score)}
          </div>
        </div>
        <div className="ax-card" style={{ padding: 8 }}>
          <div style={{ fontSize: 9, color: "var(--ax-text-2)" }}>积分</div>
          <div className="ax-num" style={{ fontSize: 14, fontWeight: 600 }}>
            {row.points.toLocaleString()}
          </div>
        </div>
      </div>

      <SectionLabel>Tags</SectionLabel>
      <div style={{ marginBottom: 10 }}>
        <TagList tags={row.tags} addable onAdd={() => { /* TODO: persist */ }} />
      </div>

      <SectionLabel>30 天活动</SectionLabel>
      <div style={{ marginBottom: 10 }}>
        <Heatmap30 values={row.heatmap30} />
      </div>

      <SectionLabel>Public Shop Hero</SectionLabel>
      <CreatorShopHeroPanel token={token} row={row} />

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
        <button
          type="button"
          className="ax-btn ax-btn--primary"
          disabled={!nextTier}
        >
          → 升 {nextTier ? TIER_LABELS[nextTier] : "满级"}
        </button>
        <button type="button" className="ax-btn">
          查看全部
        </button>
      </div>
    </div>
  );
}

function CreatorShopHeroPanel({ token, row }: { token: string; row: CreatorRow }) {
  const [heroes, setHeroes] = useState<AdminShopHero[]>([]);
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({
    id: "",
    title: "Shop by Viltrox",
    subtitle: "Support the gear I use",
    imageUrl: "/mockups/viltrox-shop-vintage-z2.png",
    targetUrl: "https://viltrox.com/collections/all",
    badge: "Official Store",
    isActive: true,
    sortOrder: "0",
  });

  useEffect(() => {
    let active = true;
    if (!row.id) return undefined;
    setMessage("加载 Shop hero...");
    void fetchAdminCreatorShopHeroes(token, row.id)
      .then((items) => {
        if (!active) return;
        setHeroes(items);
        const first = items[0];
        if (first) {
          setForm({
            id: first.id,
            title: first.title || "Shop by Viltrox",
            subtitle: first.subtitle || "Support the gear I use",
            imageUrl: first.imageUrl || "/mockups/viltrox-shop-vintage-z2.png",
            targetUrl: first.targetUrl || "https://viltrox.com/collections/all",
            badge: first.badge || "Official Store",
            isActive: first.isActive ?? true,
            sortOrder: String(first.sortOrder ?? 0),
          });
        } else {
          setForm({
            id: "",
            title: "Shop by Viltrox",
            subtitle: "Support the gear I use",
            imageUrl: "/mockups/viltrox-shop-vintage-z2.png",
            targetUrl: "https://viltrox.com/collections/all",
            badge: "Official Store",
            isActive: true,
            sortOrder: "0",
          });
        }
        setMessage("");
      })
      .catch((error) => {
        if (active) setMessage(error instanceof Error ? error.message : "Shop hero 加载失败");
      });
    return () => {
      active = false;
    };
  }, [row.id, token]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!row.id) return;
    setSaving(true);
    setMessage("");
    try {
      const saved = await saveAdminCreatorShopHero(token, {
        id: form.id || undefined,
        user_id: row.id,
        title: form.title,
        subtitle: form.subtitle,
        imageUrl: form.imageUrl,
        targetUrl: form.targetUrl,
        badge: form.badge,
        isActive: form.isActive,
        sortOrder: Number(form.sortOrder || 0),
      });
      setHeroes((current) => {
        const rest = current.filter((item) => item.id !== saved.id);
        return [saved, ...rest].sort((a, b) => Number(a.sortOrder || 0) - Number(b.sortOrder || 0));
      });
      setForm((current) => ({ ...current, id: saved.id }));
      setMessage("已保存，可在公开页展示。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function removeHero(heroId: string) {
    if (!heroId) return;
    setSaving(true);
    try {
      await deleteAdminCreatorShopHero(token, heroId);
      setHeroes((current) => current.filter((item) => item.id !== heroId));
      if (form.id === heroId) {
        setForm((current) => ({ ...current, id: "" }));
      }
      setMessage("已删除。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "删除失败");
    } finally {
      setSaving(false);
    }
  }

  const inputStyle = {
    width: "100%",
    minHeight: 30,
    border: "0.5px solid var(--ax-border-3)",
    borderRadius: 6,
    background: "var(--ax-bg-3)",
    color: "var(--ax-text-5)",
    padding: "0 8px",
    fontSize: 10,
  };

  return (
    <form className="ax-card" style={{ padding: 10, marginBottom: 10 }} onSubmit={(event) => void submit(event)}>
      <div style={{ display: "grid", gap: 6 }}>
        <label style={{ fontSize: 9, color: "var(--ax-text-2)" }}>
          Title
          <input style={inputStyle} value={form.title} onChange={(event) => setForm((current) => ({ ...current, title: event.target.value }))} />
        </label>
        <label style={{ fontSize: 9, color: "var(--ax-text-2)" }}>
          Subtitle
          <input style={inputStyle} value={form.subtitle} onChange={(event) => setForm((current) => ({ ...current, subtitle: event.target.value }))} />
        </label>
        <label style={{ fontSize: 9, color: "var(--ax-text-2)" }}>
          Image URL
          <input style={inputStyle} value={form.imageUrl} onChange={(event) => setForm((current) => ({ ...current, imageUrl: event.target.value }))} />
        </label>
        <label style={{ fontSize: 9, color: "var(--ax-text-2)" }}>
          Target URL
          <input style={inputStyle} value={form.targetUrl} onChange={(event) => setForm((current) => ({ ...current, targetUrl: event.target.value }))} />
        </label>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 80px", gap: 6 }}>
          <label style={{ fontSize: 9, color: "var(--ax-text-2)" }}>
            Badge
            <input style={inputStyle} value={form.badge} onChange={(event) => setForm((current) => ({ ...current, badge: event.target.value }))} />
          </label>
          <label style={{ fontSize: 9, color: "var(--ax-text-2)" }}>
            Sort
            <input style={inputStyle} type="number" value={form.sortOrder} onChange={(event) => setForm((current) => ({ ...current, sortOrder: event.target.value }))} />
          </label>
        </div>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 10, color: "var(--ax-text-3)" }}>
          <input type="checkbox" checked={form.isActive} onChange={(event) => setForm((current) => ({ ...current, isActive: event.target.checked }))} />
          Active on public page
        </label>
      </div>
      <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
        <button type="submit" className="ax-btn ax-btn--sm ax-btn--primary" disabled={saving || !row.id}>
          {saving ? "保存中..." : "保存"}
        </button>
        {form.id ? (
          <button type="button" className="ax-btn ax-btn--sm" disabled={saving} onClick={() => void removeHero(form.id)}>
            删除
          </button>
        ) : null}
      </div>
      {heroes.length > 1 ? (
        <div style={{ marginTop: 8, color: "var(--ax-text-2)", fontSize: 9 }}>
          {heroes.length} heroes configured. 当前表单编辑排序最靠前的一张。
        </div>
      ) : null}
      {message ? <div style={{ marginTop: 8, color: "var(--ax-text-2)", fontSize: 10 }}>{message}</div> : null}
    </form>
  );
}

export default CreatorsTab;
