/**
 * Analytics tab v2
 *
 * Sections: insights card, monthly leaderboard, learning stats.
 * Data: fetchAdminAnalyticsSnapshot
 */
import { useMemo } from "react";

import { fetchAdminAnalyticsSnapshot } from "../../../services/admin.service";
import type { AuthUser } from "../../../lib/api";
import { Icons } from "../Icons";
import {
  DataTable,
  EmptyCard,
  ErrorCard,
  KPIGrid,
  LoadingCard,
  PageHeader,
  SectionLabel,
  useAdminSnapshot,
  formatVID,
  type DataColumn,
} from "../shared_v2";

interface Props {
  token: string;
  user: AuthUser;
}

interface LeaderRow {
  id: string;
  vid: string;
  handle: string;
  submissions: number;
  score: number;
  points: number;
}

export function AnalyticsTab({ token }: Props) {
  const { data, loading, error, refresh } = useAdminSnapshot(token, fetchAdminAnalyticsSnapshot);

  const leaders: LeaderRow[] = useMemo(() => {
    const src = data?.leaderboardMonth ?? [];
    return src.map((r, i) => {
      const id = String((r as Record<string, unknown>).id || i);
      return {
        id,
        vid: String((r as Record<string, unknown>).creator_code || formatVID(id)),
        handle: String((r as Record<string, unknown>).handle || (r as Record<string, unknown>).display_name || ""),
        submissions: Number((r as Record<string, unknown>).submissions || (r as Record<string, unknown>).submissions_count || (r as Record<string, unknown>).submission_count || 0),
        score: Number((r as Record<string, unknown>).avg_score || 0),
        points: Number((r as Record<string, unknown>).estimated_points || (r as Record<string, unknown>).points_earned || (r as Record<string, unknown>).total_points || 0),
      };
    });
  }, [data]);

  const insights = data?.insights as Record<string, unknown> | null;
  const kpis = useMemo(() => {
    if (!insights) return [];
    return Object.entries(insights)
      .slice(0, 4)
      .map(([k, v]) => ({
        label: k.replace(/_/g, " "),
        value: typeof v === "number" ? v.toLocaleString() : String(v),
      }));
  }, [insights]);

  const columns: DataColumn<LeaderRow>[] = [
    {
      key: "rank",
      label: "#",
      width: "40px",
      render: (_r, i) => (
        <span
          className="ax-num"
          style={{ color: "var(--ax-text-1)", fontWeight: 600, fontSize: 10 }}
        >
          {i + 1}
        </span>
      ),
    },
    {
      key: "vid",
      label: "创作者",
      width: "1.6fr",
      render: (r) => (
        <div>
          <div className="ax-mono" style={{ fontSize: 11, color: "var(--ax-text-5)" }}>
            {r.vid}
          </div>
          {r.handle ? (
            <div style={{ fontSize: 9, color: "var(--ax-text-1)" }}>@{r.handle}</div>
          ) : null}
        </div>
      ),
    },
    {
      key: "submissions",
      label: "提交",
      width: "80px",
      render: (r) => (
        <span className="ax-num" style={{ fontWeight: 600 }}>
          {r.submissions}
        </span>
      ),
    },
    {
      key: "score",
      label: "均分",
      width: "80px",
      accent: true,
      render: (r) => (
        <span className="ax-num" style={{ color: "var(--ax-status-pass)", fontWeight: 600 }}>
          {Math.round(r.score)}
        </span>
      ),
    },
    {
      key: "points",
      label: "积分",
      width: "100px",
      render: (r) => (
        <span className="ax-num" style={{ fontWeight: 600 }}>
          {r.points.toLocaleString()}
        </span>
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title="Analytics"
        subtitle="平台级洞察 · 月度排行榜 · 学习系统"
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
          <LoadingCard />
        ) : (
          <>
            {kpis.length > 0 ? (
              <div style={{ marginBottom: 16 }}>
                <KPIGrid items={kpis} columns={Math.min(kpis.length, 4)} />
              </div>
            ) : null}

            <SectionLabel>本月 Top Creators</SectionLabel>
            <div
              style={{
                border: "0.5px solid var(--ax-border-2)",
                borderRadius: 6,
                overflow: "hidden",
                background: "var(--ax-bg-1)",
                marginBottom: 16,
              }}
            >
              {leaders.length === 0 ? (
                <EmptyCard label="暂无排行数据" hint="数据于月初开始汇总" />
              ) : (
                <DataTable
                  columns={columns}
                  rows={leaders}
                  rowKey={(r) => r.id}
                  showCheckbox={false}
                />
              )}
            </div>

            {data?.learningStats ? (
              <>
                <SectionLabel>学习系统状态</SectionLabel>
                <div className="ax-card">
                  <pre
                    style={{
                      margin: 0,
                      fontFamily: "var(--ax-font-mono)",
                      fontSize: 10,
                      color: "var(--ax-text-4)",
                      whiteSpace: "pre-wrap",
                    }}
                  >
                    {JSON.stringify(data.learningStats, null, 2)}
                  </pre>
                </div>
              </>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}

export default AnalyticsTab;
