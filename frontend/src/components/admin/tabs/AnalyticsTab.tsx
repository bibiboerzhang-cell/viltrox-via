import { useTranslation } from "react-i18next";

import type { AdminAnalyticsSnapshot } from "../../../services/admin.service";
import { MetricStrip, Panel } from "../../ui";
import { compactNumber, DataTable, formatDate, JsonInfoList, toNumber } from "../shared";

interface AnalyticsTabProps {
  analytics: AdminAnalyticsSnapshot | null;
}

export function AnalyticsTab({ analytics }: AnalyticsTabProps) {
  const { t } = useTranslation();

  return (
    <div className="admin-workspace-grid">
      <Panel title={t("admin.analytics.command.title")} kicker={t("admin.analytics.command.kicker")}>
        <MetricStrip
          columns={4}
          items={[
            {
              label: t("admin.analytics.command.metrics.genres"),
              value: compactNumber(Object.keys((analytics?.benchmarks as Record<string, unknown> | null) || {}).length),
              note: t("admin.analytics.command.metrics.genresNote"),
            },
            {
              label: t("admin.analytics.command.metrics.monthBoard"),
              value: compactNumber(analytics?.leaderboardMonth.length || 0),
              note: t("admin.analytics.command.metrics.monthBoardNote"),
            },
            {
              label: t("admin.analytics.command.metrics.yearBoard"),
              value: compactNumber(analytics?.leaderboardYear.length || 0),
              note: t("admin.analytics.command.metrics.yearBoardNote"),
            },
            {
              label: t("admin.analytics.command.metrics.trendRows"),
              value: compactNumber(analytics?.dashboard?.trend?.length || 0),
              note: t("admin.analytics.command.metrics.trendRowsNote"),
            },
          ]}
        />
      </Panel>

      <Panel title={t("admin.analytics.marketInsights.title")} kicker={t("admin.analytics.marketInsights.kicker")}>
        <JsonInfoList
          payload={analytics?.insights || null}
          emptyTitle={t("admin.analytics.marketInsights.emptyTitle")}
          emptyBody={t("admin.analytics.marketInsights.emptyBody")}
        />
      </Panel>

      <Panel title={t("admin.analytics.learningSystem.title")} kicker={t("admin.analytics.learningSystem.kicker")}>
        <div className="admin-two-column">
          <div>
            <strong className="section-mini-head">{t("admin.analytics.learningSystem.statsTitle")}</strong>
            <JsonInfoList
              payload={analytics?.learningStats || null}
              emptyTitle={t("admin.analytics.learningSystem.statsEmptyTitle")}
              emptyBody={t("admin.analytics.learningSystem.statsEmptyBody")}
            />
          </div>
          <DataTable
            columns={[
              t("admin.analytics.learningSystem.columns.correctedTo"),
              t("admin.analytics.learningSystem.columns.original"),
              t("admin.analytics.learningSystem.columns.when"),
            ]}
            rows={(analytics?.corrections || []).slice(0, 8).map((item, index) => [
              <div key={`correction-label-${index}`}>
                <div className="table-primary">{String(item.correct_label || item.label || t("admin.analytics.learningSystem.fallbackCorrection"))}</div>
                <small>{String(item.correct_series || item.series || t("admin.analytics.learningSystem.fallbackSeries"))}</small>
              </div>,
              String(item.original_label || item.url || t("admin.analytics.learningSystem.fallbackSource")),
              formatDate(item.corrected_at || item.created_at || ""),
            ])}
            empty={t("admin.analytics.learningSystem.empty")}
            emptyTitle={t("admin.analytics.learningSystem.emptyTitle")}
          />
        </div>
      </Panel>

      <Panel title={t("admin.analytics.genreBenchmarks.title")} kicker={t("admin.analytics.genreBenchmarks.kicker")}>
        <DataTable
          columns={[
            t("admin.analytics.genreBenchmarks.columns.genre"),
            t("admin.analytics.genreBenchmarks.columns.samples"),
            t("admin.analytics.genreBenchmarks.columns.tech"),
            t("admin.analytics.genreBenchmarks.columns.marketing"),
            t("admin.analytics.genreBenchmarks.columns.overall"),
          ]}
          rows={Object.entries((analytics?.benchmarks as Record<string, Record<string, unknown>> | null) || {})
            .slice(0, 18)
            .map(([genre, payload]) => [
              genre,
              compactNumber(payload.sample_size || payload.count || 0),
              toNumber(payload.avg_tech || payload.tech_mean || 0).toFixed(1),
              toNumber(payload.avg_mkt || payload.marketing_mean || 0).toFixed(1),
              toNumber(payload.avg_overall || payload.overall_mean || 0).toFixed(1),
            ])}
          empty={t("admin.analytics.genreBenchmarks.empty")}
          emptyTitle={t("admin.analytics.genreBenchmarks.emptyTitle")}
        />
      </Panel>

      <Panel title={t("admin.analytics.leaderboard.title")} kicker={t("admin.analytics.leaderboard.kicker")}>
        <div className="admin-two-column">
          <div>
            <strong className="section-mini-head">{t("admin.analytics.leaderboard.monthTitle")}</strong>
            <DataTable
              columns={[
                t("admin.analytics.leaderboard.columns.creator"),
                t("admin.analytics.leaderboard.columns.score"),
                t("admin.analytics.leaderboard.columns.submissions"),
              ]}
              rows={(analytics?.leaderboardMonth || []).slice(0, 12).map((item, index) => [
                <div key={`month-${index}`}>
                  <div className="table-primary">{String(item.display_name || item.name || item.handle || item.creator_code || t("admin.analytics.leaderboard.fallbackCreator"))}</div>
                  <small>{String(item.creator_code || item.handle || t("admin.shared.missing"))}</small>
                </div>,
                compactNumber(item.total_score || item.total_campaign_score || item.points || item.total_points_earned || 0),
                compactNumber(item.submission_count || item.submissions || 0),
              ])}
              empty={t("admin.analytics.leaderboard.monthEmpty")}
              emptyTitle={t("admin.analytics.leaderboard.emptyTitle")}
            />
          </div>
          <div>
            <strong className="section-mini-head">{t("admin.analytics.leaderboard.yearTitle")}</strong>
            <DataTable
              columns={[
                t("admin.analytics.leaderboard.columns.creator"),
                t("admin.analytics.leaderboard.columns.score"),
                t("admin.analytics.leaderboard.columns.submissions"),
              ]}
              rows={(analytics?.leaderboardYear || []).slice(0, 12).map((item, index) => [
                <div key={`year-${index}`}>
                  <div className="table-primary">{String(item.display_name || item.name || item.handle || item.creator_code || t("admin.analytics.leaderboard.fallbackCreator"))}</div>
                  <small>{String(item.creator_code || item.handle || t("admin.shared.missing"))}</small>
                </div>,
                compactNumber(item.total_score || item.total_campaign_score || item.points || item.total_points_earned || 0),
                compactNumber(item.submission_count || item.submissions || 0),
              ])}
              empty={t("admin.analytics.leaderboard.yearEmpty")}
              emptyTitle={t("admin.analytics.leaderboard.emptyTitle")}
            />
          </div>
        </div>
      </Panel>

      <Panel title={t("admin.analytics.trendVios.title")} kicker={t("admin.analytics.trendVios.kicker")}>
        <div className="admin-two-column">
          <DataTable
            columns={[
              t("admin.analytics.trendVios.columns.date"),
              t("admin.analytics.trendVios.columns.count"),
              t("admin.analytics.trendVios.columns.views"),
              t("admin.analytics.trendVios.columns.likes"),
            ]}
            rows={(analytics?.dashboard?.trend || []).slice(-12).reverse().map((item) => [
              formatDate(item.date || ""),
              compactNumber(item.count || 0),
              compactNumber(item.views || 0),
              compactNumber(item.likes || 0),
            ])}
            empty={t("admin.analytics.trendVios.empty")}
            emptyTitle={t("admin.analytics.trendVios.emptyTitle")}
          />
          <div className="admin-card-list">
            {(analytics?.dashboard?.creators || []).slice(0, 6).map((item, index) => (
              <article key={`analytics-creator-${index}`} className="admin-mini-card">
                <strong>{String(item.handle || t("admin.analytics.trendVios.fallbackCreator"))}</strong>
                <p>
                  {String(item.platform || t("admin.shared.missing"))} · {compactNumber(item.submissions || 0)} {t("admin.analytics.trendVios.submissionsLabel")} · {t("admin.analytics.trendVios.bestLabel")}{" "}
                  {toNumber(item.best_score || 0).toFixed(0)}
                </p>
                <span>
                  {compactNumber(item.total_views || 0)} {t("admin.analytics.trendVios.viewsLabel")} · {compactNumber(item.total_likes || 0)} {t("admin.analytics.trendVios.likesLabel")}
                </span>
              </article>
            ))}
          </div>
        </div>
      </Panel>

      <Panel title={t("admin.analytics.pointsLog.title")} kicker={t("admin.analytics.pointsLog.kicker")}>
        <DataTable
          columns={[
            t("admin.analytics.pointsLog.columns.time"),
            t("admin.analytics.pointsLog.columns.user"),
            t("admin.analytics.pointsLog.columns.delta"),
            t("admin.analytics.pointsLog.columns.reason"),
            t("admin.analytics.pointsLog.columns.actor"),
          ]}
          rows={(analytics?.pointsLog || []).slice(0, 16).map((item, index) => [
            formatDate(item.created_at || ""),
            String(item.email || item.user_id || `row-${index}`),
            compactNumber(item.delta || 0),
            String(item.reason || t("admin.analytics.pointsLog.fallbackReason")),
            String(item.admin_actor || item.actor || item.operator || t("admin.analytics.pointsLog.fallbackActor")),
          ])}
          empty={t("admin.analytics.pointsLog.empty")}
          emptyTitle={t("admin.analytics.pointsLog.emptyTitle")}
        />
      </Panel>
    </div>
  );
}
