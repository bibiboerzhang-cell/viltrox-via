/**
 * Overview tab v2
 *
 * Keeps the current dark V-OS UI, but restores the proven v1 operator surface:
 * KPI grid, trend, platform/product splits, creator leaderboard, and a clickable
 * submissions table wired to real admin APIs.
 */
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { fetchAdminDashboard } from "../../../services/admin.service";
import type { AdminSubmission, AuthUser } from "../../../lib/api";
import { Icons } from "../Icons";
import {
  BulkBar,
  DataTable,
  EmptyCard,
  ErrorCard,
  KPIGrid,
  LoadingCard,
  PageHeader,
  SectionLabel,
  StatusPill,
  useAdminSnapshot,
  type DataColumn,
} from "../shared_v2";

interface Props {
  token: string;
  user: AuthUser;
}

type Bucket = Record<string, unknown>;

function num(value: unknown, fallback = 0): number {
  const next = Number(value);
  return Number.isFinite(next) ? next : fallback;
}

function stat(source: Record<string, unknown>, keys: string[], fallback = 0): number {
  for (const key of keys) {
    if (source[key] !== undefined && source[key] !== null) {
      return num(source[key], fallback);
    }
  }
  return fallback;
}

function formatCompact(value: unknown): string {
  const next = num(value);
  if (next >= 1_000_000) return `${(next / 1_000_000).toFixed(1)}M`;
  if (next >= 1_000) return `${(next / 1_000).toFixed(1)}K`;
  return next.toLocaleString();
}

function statusTone(status?: string) {
  const s = String(status || "").toLowerCase();
  if (s === "confirmed" || s === "approved") return "pass";
  if (s === "rejected" || s === "not_detected" || s === "failed") return "block";
  if (s === "suspected" || s === "pending" || s === "needs_review") return "review";
  return "queue";
}

function labelFromBucket(row: Bucket, keys: string[], fallback = "—") {
  for (const key of keys) {
    const value = String(row[key] ?? "").trim();
    if (value) return value;
  }
  return fallback;
}

function countFromBucket(row: Bucket) {
  return num(row.count ?? row.n ?? row.cnt);
}

function BarList({
  rows,
  labelKeys,
  emptyLabel,
}: {
  rows: Bucket[];
  labelKeys: string[];
  emptyLabel: string;
}) {
  const max = Math.max(...rows.map(countFromBucket), 1);
  if (!rows.length) {
    return <EmptyCard label={emptyLabel} hint="等待投稿进入统计" />;
  }
  return (
    <div style={{ display: "grid", gap: 10 }}>
      {rows.slice(0, 8).map((row, index) => {
        const count = countFromBucket(row);
        return (
          <div key={`${labelFromBucket(row, labelKeys)}-${index}`}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                color: "var(--ax-text-4)",
                fontSize: 11,
                marginBottom: 5,
              }}
            >
              <span>{labelFromBucket(row, labelKeys)}</span>
              <span className="ax-num">{count.toLocaleString()}</span>
            </div>
            <div
              style={{
                height: 5,
                borderRadius: 999,
                background: "var(--ax-bg-2)",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  width: `${Math.max(4, (count / max) * 100)}%`,
                  height: "100%",
                  background: "var(--ax-text-5)",
                }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function TrendChart({ rows }: { rows: Bucket[] }) {
  if (!rows.length) {
    return <EmptyCard label="暂无趋势数据" hint="有投稿后自动生成 90 天趋势" />;
  }
  const points = rows.slice(-30).map((row) => ({
    date: labelFromBucket(row, ["date", "day"]),
    count: countFromBucket(row),
  }));
  const max = Math.max(...points.map((p) => p.count), 1);
  const width = 480;
  const height = 132;
  const pad = 18;
  const coords = points.map((p, i) => {
    const x = points.length === 1 ? width / 2 : pad + (i / (points.length - 1)) * (width - pad * 2);
    const y = height - pad - (p.count / max) * (height - pad * 2);
    return { ...p, x, y };
  });
  const line = coords.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`).join(" ");
  const fill = `${line} L ${coords[coords.length - 1].x} ${height - pad} L ${coords[0].x} ${height - pad} Z`;
  return (
    <svg viewBox={`0 0 ${width} ${height}`} style={{ width: "100%", height: 150 }}>
      <path d={fill} fill="rgba(255,255,255,0.06)" />
      <path d={line} fill="none" stroke="var(--ax-text-5)" strokeWidth="2" />
      {coords.map((p) => (
        <g key={`${p.date}-${p.x}`}>
          <circle cx={p.x} cy={p.y} r="3" fill="var(--ax-text-5)" />
        </g>
      ))}
      <text x={pad} y={height - 2} fill="var(--ax-text-1)" fontSize="10">
        {coords[0]?.date?.slice(5) || ""}
      </text>
      <text x={width - pad} y={height - 2} fill="var(--ax-text-1)" fontSize="10" textAnchor="end">
        {coords[coords.length - 1]?.date?.slice(5) || ""}
      </text>
    </svg>
  );
}

export function OverviewTab({ token }: Props) {
  const { t } = useTranslation();
  const { data, loading, error, refresh } = useAdminSnapshot(token, fetchAdminDashboard);
  const [platformFilter, setPlatformFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const stats = (data?.stats ?? {}) as Record<string, unknown>;
  const byDate = Array.isArray(stats.by_date) ? (stats.by_date as Bucket[]) : [];
  const byPlatform = Array.isArray(stats.by_platform) ? (stats.by_platform as Bucket[]) : [];
  const bySeries = Array.isArray(stats.by_series) ? (stats.by_series as Bucket[]) : [];
  const submissions = data?.submissions ?? [];
  const leaderboard = data?.leaderboardMonth ?? [];

  const kpis = useMemo(
    () => [
      { label: "Total", value: stat(stats, ["total", "total_submissions"]) },
      { label: "Confirmed", value: stat(stats, ["confirmed", "confirmed_submissions"]) },
      { label: "Suspected", value: stat(stats, ["suspected", "pending_submissions"]) },
      { label: "Avg Campaign", value: stat(stats, ["avg_final_score", "avg_campaign"]).toFixed(1) },
      { label: "Avg Creator", value: stat(stats, ["avg_creator_score", "avg_creator"]).toFixed(1) },
      { label: "Total Views", value: formatCompact(stats.total_views) },
      { label: "Total Likes", value: formatCompact(stats.total_likes) },
      { label: "Total Comments", value: formatCompact(stats.total_comments) },
      { label: "Total Shares", value: formatCompact(stats.total_shares) },
      { label: "Creators", value: stat(stats, ["unique_creators"]).toLocaleString() },
    ],
    [stats],
  );

  const platformOptions = useMemo(
    () =>
      Array.from(new Set(submissions.map((s) => s.platform).filter(Boolean) as string[])),
    [submissions],
  );
  const statusOptions = useMemo(
    () =>
      Array.from(new Set(submissions.map((s) => s.detection_status).filter(Boolean) as string[])),
    [submissions],
  );

  const filteredSubmissions = useMemo(
    () =>
      submissions.filter((s) => {
        if (platformFilter && s.platform !== platformFilter) return false;
        if (statusFilter && s.detection_status !== statusFilter) return false;
        return true;
      }),
    [platformFilter, statusFilter, submissions],
  );

  const selectedSubmission = useMemo(
    () => filteredSubmissions.find((s) => String(s.id) === selectedId) || null,
    [filteredSubmissions, selectedId],
  );

  const submissionColumns: DataColumn<AdminSubmission>[] = [
    {
      key: "id",
      label: "#",
      width: "54px",
      render: (r) => <span className="ax-mono" style={{ color: "var(--ax-text-2)" }}>{r.id}</span>,
    },
    {
      key: "date",
      label: "日期",
      width: "116px",
      render: (r) => (
        <span style={{ color: "var(--ax-text-2)", fontSize: 10 }}>
          {r.created_at ? String(r.created_at).slice(0, 16).replace("T", " ") : "—"}
        </span>
      ),
    },
    {
      key: "platform",
      label: "平台",
      width: "92px",
      render: (r) => <span style={{ color: "var(--ax-text-4)" }}>{r.platform || "—"}</span>,
    },
    {
      key: "creator",
      label: "创作者",
      width: "1fr",
      render: (r) => (
        <div>
          <div style={{ color: "var(--ax-text-5)", fontWeight: 600 }}>
            {r.display_name || r.user_name || r.extracted_handle || "—"}
          </div>
          {r.creator_code ? (
            <div className="ax-mono" style={{ color: "var(--ax-text-1)", fontSize: 9 }}>
              {r.creator_code}
            </div>
          ) : null}
        </div>
      ),
    },
    {
      key: "title",
      label: "标题/链接",
      width: "2fr",
      render: (r) => (
        <div style={{ color: "var(--ax-text-4)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {r.title || r.url || "—"}
        </div>
      ),
    },
    {
      key: "status",
      label: "状态",
      width: "94px",
      render: (r) => <StatusPill tone={statusTone(r.detection_status) as never}>{r.detection_status || "—"}</StatusPill>,
    },
    {
      key: "product",
      label: "产品",
      width: "92px",
      render: (r) => <span style={{ color: "var(--ax-text-3)" }}>{r.product_series || "—"}</span>,
    },
    {
      key: "campaign",
      label: "Campaign",
      width: "88px",
      accent: true,
      render: (r) => <span className="ax-num">{Math.round(num(r.final_score))}</span>,
    },
    {
      key: "creatorScore",
      label: "Creator",
      width: "76px",
      render: (r) => <span className="ax-num">{Math.round(num(r.creator_score))}</span>,
    },
    {
      key: "views",
      label: "Views",
      width: "88px",
      render: (r) => <span className="ax-num">{formatCompact(r.views)}</span>,
    },
  ];

  const leaderColumns: DataColumn<Record<string, unknown>>[] = [
    {
      key: "rank",
      label: "排名",
      width: "64px",
      render: (r, i) => <span className="ax-num">{String(r.rank || i + 1)}</span>,
    },
    {
      key: "creator",
      label: "创作者",
      width: "1.6fr",
      render: (r) => (
        <div>
          <div style={{ color: "var(--ax-text-5)", fontWeight: 600 }}>
            {String(r.display_name || r.user_name || r.handle || "—")}
          </div>
          <div className="ax-mono" style={{ color: "var(--ax-text-1)", fontSize: 9 }}>
            {String(r.creator_code || "")}
          </div>
        </div>
      ),
    },
    {
      key: "platform",
      label: "平台",
      width: "90px",
      render: (r) => <span style={{ color: "var(--ax-text-3)" }}>{String(r.platforms || r.platform || "—")}</span>,
    },
    {
      key: "views",
      label: "总播放",
      width: "92px",
      render: (r) => <span className="ax-num">{formatCompact(r.total_views)}</span>,
    },
    {
      key: "submissions",
      label: "投稿",
      width: "70px",
      render: (r) => <span className="ax-num">{num(r.submissions)}</span>,
    },
    {
      key: "score",
      label: "均分",
      width: "70px",
      accent: true,
      render: (r) => <span className="ax-num">{Math.round(num(r.avg_score))}</span>,
    },
    {
      key: "points",
      label: "积分",
      width: "90px",
      render: (r) => (
        <span className="ax-num" style={{ color: "var(--ax-status-pass)" }}>
          {formatCompact(r.estimated_points)}
        </span>
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title={t("admin.overview.title", "Submission Dashboard")}
        subtitle={t("admin.overview.subtitle", "投稿总控 · 排行榜 · API 接入状态")}
        actions={
          <button type="button" className="ax-btn" onClick={refresh} disabled={loading}>
            <Icons.trending /> {loading ? "刷新中…" : "刷新"}
          </button>
        }
      />

      {error ? (
        <div style={{ padding: 16 }}>
          <ErrorCard detail={error} onRetry={refresh} />
        </div>
      ) : null}

      <div style={{ padding: 16 }}>
        {loading && !data ? (
          <LoadingCard label="加载后台总控台…" />
        ) : (
          <>
            <div style={{ marginBottom: 16 }}>
              <KPIGrid items={kpis} columns={5} />
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1.35fr 1fr 1fr", gap: 12, marginBottom: 16 }}>
              <div className="ax-card">
                <SectionLabel>投稿趋势 · 30d</SectionLabel>
                <TrendChart rows={byDate} />
              </div>
              <div className="ax-card">
                <SectionLabel>By Platform</SectionLabel>
                <BarList rows={byPlatform} labelKeys={["platform"]} emptyLabel="暂无平台数据" />
              </div>
              <div className="ax-card">
                <SectionLabel>By Product Series</SectionLabel>
                <BarList rows={bySeries} labelKeys={["series", "product_series"]} emptyLabel="暂无产品系列数据" />
              </div>
            </div>

            <div style={{ marginBottom: 16 }}>
              <SectionLabel>创作者排行榜 · 月榜</SectionLabel>
              <div style={{ border: "0.5px solid var(--ax-border-2)", borderRadius: 6, overflow: "hidden", background: "var(--ax-bg-1)" }}>
                {leaderboard.length === 0 ? (
                  <EmptyCard label="暂无排行榜数据" hint="确认后的投稿会进入排行" />
                ) : (
                  <DataTable
                    columns={leaderColumns}
                    rows={leaderboard.slice(0, 10)}
                    rowKey={(r, i) => String(r.handle || r.creator_code || i)}
                    showCheckbox={false}
                  />
                )}
              </div>
            </div>

            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 10 }}>
              <SectionLabel>Submissions · click any row to expand</SectionLabel>
              <div style={{ display: "flex", gap: 8 }}>
                <select className="ax-input" value={platformFilter} onChange={(e) => setPlatformFilter(e.target.value)} style={{ width: 150 }}>
                  <option value="">全部平台</option>
                  {platformOptions.map((p) => <option key={p}>{p}</option>)}
                </select>
                <select className="ax-input" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} style={{ width: 150 }}>
                  <option value="">全部状态</option>
                  {statusOptions.map((s) => <option key={s}>{s}</option>)}
                </select>
              </div>
            </div>

            <div style={{ border: "0.5px solid var(--ax-border-2)", borderRadius: 6, overflow: "hidden", background: "var(--ax-bg-1)" }}>
              {filteredSubmissions.length === 0 ? (
                <EmptyCard label="暂无提交" hint="上传或 URL 投稿后会进入这里" />
              ) : (
                <DataTable<AdminSubmission>
                  columns={submissionColumns}
                  rows={filteredSubmissions}
                  rowKey={(r) => String(r.id)}
                  showCheckbox={false}
                  onRowClick={(row) => setSelectedId(String(row.id))}
                  selectedId={selectedSubmission ? String(selectedSubmission.id) : null}
                />
              )}
              <BulkBar
                selectedCount={0}
                pager={<span>显示 {filteredSubmissions.length} / {data?.submissionsTotal ?? submissions.length}</span>}
              />
            </div>

            {selectedSubmission ? (
              <div className="ax-card" style={{ marginTop: 16 }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                  <div>
                    <SectionLabel>Submission detail</SectionLabel>
                    <h3 style={{ margin: "6px 0", color: "var(--ax-text-5)", fontSize: 16 }}>
                      {selectedSubmission.title || `Submission #${selectedSubmission.id}`}
                    </h3>
                    <div style={{ color: "var(--ax-text-2)", fontSize: 11 }}>
                      #{selectedSubmission.id} · {selectedSubmission.platform || "—"} · {selectedSubmission.extracted_handle || selectedSubmission.creator_code || "—"}
                    </div>
                  </div>
                  <button type="button" className="ax-btn ax-btn--sm" onClick={() => setSelectedId(null)}>
                    <Icons.close />
                  </button>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 10, marginTop: 14 }}>
                  <div><SectionLabel>Status</SectionLabel><StatusPill tone={statusTone(selectedSubmission.detection_status) as never}>{selectedSubmission.detection_status || "—"}</StatusPill></div>
                  <div><SectionLabel>Campaign</SectionLabel><div className="ax-num">{Math.round(num(selectedSubmission.final_score))}</div></div>
                  <div><SectionLabel>Creator</SectionLabel><div className="ax-num">{Math.round(num(selectedSubmission.creator_score))}</div></div>
                  <div><SectionLabel>Views</SectionLabel><div className="ax-num">{formatCompact(selectedSubmission.views)}</div></div>
                  <div><SectionLabel>Points</SectionLabel><div className="ax-num">{num(selectedSubmission.points_awarded).toLocaleString()}</div></div>
                </div>
                <p style={{ margin: "14px 0 0", color: "var(--ax-text-3)", fontSize: 12, lineHeight: 1.7 }}>
                  {selectedSubmission.recommendation || selectedSubmission.memo || "暂无后台建议。"}
                </p>
                {selectedSubmission.url ? (
                  <a className="ax-btn ax-btn--sm" href={selectedSubmission.url} target="_blank" rel="noreferrer" style={{ marginTop: 12, display: "inline-flex" }}>
                    Open link <Icons.externalLink />
                  </a>
                ) : null}
              </div>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}

export default OverviewTab;
