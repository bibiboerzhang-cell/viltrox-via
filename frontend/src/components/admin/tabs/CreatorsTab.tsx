import type { Dispatch, SetStateAction } from "react";
import { useTranslation } from "react-i18next";

import type { AdminCreatorsSnapshot } from "../../../services/admin.service";
import { EmptyState, MetricStrip, Panel, StatusPill } from "../../ui";
import { compactNumber, DataTable, formatDate, TablePager, titleCase, toNumber, toneForStatus } from "../shared";

interface CreatorsTabProps {
  creators: AdminCreatorsSnapshot | null;
  creatorSearch: string;
  setCreatorSearch: Dispatch<SetStateAction<string>>;
  selectedCreatorHandle: string;
  setSelectedCreatorHandle: Dispatch<SetStateAction<string>>;
  creatorPage: number;
  creatorTotalPages: number;
  creatorRosterFiltered: Array<Record<string, unknown>>;
  creatorRowsPaged: Array<Record<string, unknown>>;
  setCreatorPage: Dispatch<SetStateAction<number>>;
  busy: string;
  loadCreators: (handle?: string) => Promise<void>;
  setBusy: Dispatch<SetStateAction<string>>;
  setMessage: Dispatch<SetStateAction<{ tone: "success" | "warning" | "danger"; body: string } | null>>;
}

export function CreatorsTab({
  creators,
  creatorSearch,
  setCreatorSearch,
  selectedCreatorHandle,
  setSelectedCreatorHandle,
  creatorPage,
  creatorTotalPages,
  creatorRosterFiltered,
  creatorRowsPaged,
  setCreatorPage,
  busy,
  loadCreators,
  setBusy,
  setMessage,
}: CreatorsTabProps) {
  const { t } = useTranslation();
  return (
    <div className="admin-workspace-grid">
      <Panel title={t("admin.creators.command.title")} kicker={t("admin.creators.command.kicker")}>
        <MetricStrip
          columns={4}
          items={[
            { label: t("admin.creators.command.metrics.roster.label"), value: compactNumber(creators?.roster.length || 0), note: t("admin.creators.command.metrics.roster.note") },
            { label: t("admin.creators.command.metrics.topCreators.label"), value: compactNumber(creators?.dashboard?.creators?.length || 0), note: t("admin.creators.command.metrics.topCreators.note") },
            {
              label: t("admin.creators.command.metrics.selectedHandle.label"),
              value: selectedCreatorHandle || t("admin.creators.command.metrics.selectedHandle.auto"),
              note: creators?.growth ? t("admin.creators.command.metrics.selectedHandle.loaded") : t("admin.creators.command.metrics.selectedHandle.note"),
            },
            {
              label: t("admin.creators.command.metrics.trendRows.label"),
              value: compactNumber((creators?.growth?.score_history as Array<unknown> | undefined)?.length || 0),
              note: t("admin.creators.command.metrics.trendRows.note"),
            },
          ]}
        />
        <div className="admin-note-form">
          <div className="admin-inline-actions admin-inline-actions--stretch">
            <label className="auth-field admin-inline-actions__field">
              <span>{t("admin.creators.command.handleLabel")}</span>
              <input value={selectedCreatorHandle} onChange={(event) => setSelectedCreatorHandle(event.target.value)} placeholder={t("admin.creators.command.handlePlaceholder")} />
            </label>
            <button
              className="primary-button"
              type="button"
              disabled={!selectedCreatorHandle.trim() || busy === "creators:focus"}
              onClick={async () => {
                    setBusy("creators:focus");
                    setMessage(null);
                    try {
                      await loadCreators(selectedCreatorHandle.trim());
                      setMessage({ tone: "success", body: t("admin.creators.command.messages.loaded", { handle: selectedCreatorHandle.trim() }) });
                    } catch (error) {
                      setMessage({ tone: "danger", body: error instanceof Error ? error.message : t("admin.creators.command.messages.loadFailed") });
                    } finally {
                      setBusy("");
                    }
                  }}
                >
                  {busy === "creators:focus" ? t("admin.creators.command.loading") : t("admin.creators.command.loadAction")}
                </button>
          </div>
        </div>
      </Panel>

      <Panel title={t("admin.creators.roster.title")} kicker={t("admin.creators.roster.kicker")}>
        <div className="admin-note-form">
          <label className="auth-field admin-form-grid__full">
            <span>{t("admin.creators.roster.searchLabel")}</span>
            <input value={creatorSearch} onChange={(event) => setCreatorSearch(event.target.value)} placeholder={t("admin.creators.roster.searchPlaceholder")} />
          </label>
        </div>
        <TablePager page={creatorPage} totalPages={creatorTotalPages} totalItems={creatorRosterFiltered.length} label={t("admin.creators.roster.pagerLabel")} onChange={setCreatorPage} />
        <DataTable
          columns={[
            t("admin.creators.roster.columns.creator"),
            t("admin.creators.roster.columns.platform"),
            t("admin.creators.roster.columns.status"),
            t("admin.creators.roster.columns.points"),
            t("admin.creators.roster.columns.action"),
          ]}
          rows={creatorRowsPaged.map((item, index) => {
            const handle = String(item.handle || item.extracted_handle || item.creator_code || item.email || `creator-${index}`);
            const platform = String(item.platform || item.primary_platform || t("admin.shared.missing"));
            const status = String(item.status || item.vip_status || t("admin.shared.pending"));
            const points = compactNumber(item.points_balance || item.total_points_earned || item.total_score || 0);
            return [
              <div key={`${handle}-identity`}>
                <div className="table-primary">{String(item.name || item.display_name || handle)}</div>
                <small>{String(item.creator_code || handle)}</small>
              </div>,
              platform,
              <StatusPill key={`${handle}-status`} label={status} tone={toneForStatus(status)} />,
              points,
              <button
                key={`${handle}-focus`}
                className="outline-btn"
                type="button"
                onClick={() => {
                  setSelectedCreatorHandle(handle);
                  void (async () => {
                      setBusy("creators:focus");
                      setMessage(null);
                      try {
                        await loadCreators(handle);
                        setMessage({ tone: "success", body: t("admin.creators.command.messages.loaded", { handle }) });
                      } catch (error) {
                        setMessage({ tone: "danger", body: error instanceof Error ? error.message : t("admin.creators.command.messages.loadFailed") });
                      } finally {
                        setBusy("");
                      }
                    })();
                  }}
                >
                  {t("admin.operations.actions.inspect")}
                </button>,
            ];
          })}
          empty={t("admin.creators.roster.empty")}
        />
      </Panel>

      <Panel title={t("admin.creators.profile.title")} kicker={t("admin.creators.profile.kicker")}>
        {creators?.growth ? (
          <div className="admin-two-column">
            <div className="admin-list-stack">
              <article className="admin-list-item">
                <strong>{String(creators.growth.handle || selectedCreatorHandle || t("admin.shared.creator"))}</strong>
                <p>
                  {String(creators.growth.name || creators.growth.display_name || t("admin.creators.profile.noDisplayName"))} ·{" "}
                  {String(creators.growth.platform || creators.growth.primary_platform || t("admin.creators.profile.platformPending"))}
                </p>
              </article>
              <article className="admin-list-item">
                <strong>{t("admin.creators.profile.recentPerformance")}</strong>
                <p>
                  {t("admin.creators.profile.performanceLine", {
                    submissions: compactNumber(creators.growth.submission_count || 0),
                    views: compactNumber(creators.growth.total_views || 0),
                    likes: compactNumber(creators.growth.total_likes || 0),
                  })}
                </p>
              </article>
              <article className="admin-list-item">
                <strong>{t("admin.creators.profile.commercialSignal")}</strong>
                <p>
                  {t("admin.creators.profile.commercialLine", {
                    vip: String(creators.growth.vip_status || creators.growth.tier_label || t("admin.shared.pending")),
                    orders: compactNumber(creators.growth.orders_count || creators.growth.affiliate_orders || 0),
                    revenue: compactNumber(creators.growth.gmv || creators.growth.revenue || 0),
                  })}
                </p>
              </article>
              <article className="admin-list-item">
                <strong>{t("admin.creators.profile.topProducts")}</strong>
                <p>
                  {((creators.growth.top_products as Array<unknown> | undefined) || [])
                    .slice(0, 4)
                    .map((item) => String(item))
                    .join(" · ") || t("admin.creators.profile.noProductCluster")}
                </p>
              </article>
              <article className="admin-list-item">
                <strong>{t("admin.creators.profile.genreFocus")}</strong>
                <p>
                  {((creators.growth.top_genres as Array<unknown> | undefined) || [])
                    .slice(0, 4)
                    .map((item) => String(item))
                    .join(" · ") || t("admin.creators.profile.noGenreCluster")}
                </p>
              </article>
            </div>
            <div className="admin-list-stack">
              <article className="admin-list-item">
                <strong>{t("admin.creators.profile.weakAreas")}</strong>
                <p>
                  {((creators.growth.weak_areas as Array<unknown> | undefined) || []).map((item) => String(item)).join(" · ") || t("admin.creators.profile.noWeakAreas")}
                </p>
              </article>
              <article className="admin-list-item">
                <strong>{t("admin.creators.profile.recentSubmissions")}</strong>
                <p>
                  {((creators.growth.submissions_timeline as Array<Record<string, unknown>> | undefined) || [])
                    .slice(-3)
                    .reverse()
                    .map((item) => String(item.title || item.product_series || item.id || t("admin.shared.submission")))
                    .join(" · ") || t("admin.creators.profile.noSubmissionsLoaded")}
                </p>
              </article>
              <article className="admin-list-item">
                <strong>{t("admin.creators.profile.benchmarkLane")}</strong>
                <p>
                  {t("admin.creators.profile.benchmarkLine", {
                    trend: String((creators.growth.trend as Record<string, unknown> | undefined)?.direction || "new"),
                    genre: String((creators.growth.genre_benchmark as Record<string, unknown> | undefined)?.genre || "n/a"),
                    percentile: String((creators.growth.genre_benchmark as Record<string, unknown> | undefined)?.percentile || t("admin.shared.missing")),
                  })}
                </p>
              </article>
            </div>
          </div>
        ) : (
          <EmptyState title={t("admin.creators.profile.emptyTitle")} body={t("admin.creators.profile.emptyBody")} />
        )}
      </Panel>

      <Panel title={t("admin.creators.growthLens.title")} kicker={t("admin.creators.growthLens.kicker")}>
        {creators?.growth ? (
          <div className="admin-two-column">
            <div className="admin-list-stack">
              <article className="admin-list-item">
                <strong>{String(creators.growth.handle || selectedCreatorHandle || t("admin.creators.growthLens.creatorGrowth"))}</strong>
                <p>
                  {t("admin.creators.growthLens.lastSeenLine", {
                    submissions: compactNumber(creators.growth.submission_count || 0),
                    lastSeen: formatDate(creators.growth.last_seen || ""),
                  })}
                </p>
              </article>
              <article className="admin-list-item">
                <strong>{t("admin.creators.growthLens.averageScores")}</strong>
                <p>
                  {t("admin.creators.growthLens.averageLine", {
                    tech: toNumber((creators.growth.avg_scores as Record<string, unknown> | undefined)?.tech || 0).toFixed(1),
                    marketing: toNumber((creators.growth.avg_scores as Record<string, unknown> | undefined)?.mkt || 0).toFixed(1),
                    overall: toNumber((creators.growth.avg_scores as Record<string, unknown> | undefined)?.overall || 0).toFixed(1),
                  })}
                </p>
              </article>
              <article className="admin-list-item">
                <strong>{t("admin.creators.growthLens.trendBenchmark")}</strong>
                <p>
                  {t("admin.creators.growthLens.trendLine", {
                    trend: String((creators.growth.trend as Record<string, unknown> | undefined)?.direction || "new"),
                    genre: String((creators.growth.genre_benchmark as Record<string, unknown> | undefined)?.genre || "n/a"),
                  })}
                </p>
              </article>
            </div>
            <div className="admin-three-column admin-three-column--dense">
              <div className="admin-chip-cloud">
                <strong className="section-mini-head">{t("admin.creators.growthLens.cameras")}</strong>
                {((creators.growth.cameras as Array<unknown> | undefined) || []).length ? (
                  ((creators.growth.cameras as Array<unknown> | undefined) || []).map((item, index) => (
                    <span key={`camera-${index}`} className="admin-chip">
                      {String(item)}
                    </span>
                  ))
                ) : (
                  <span className="admin-chip admin-chip--muted">{t("admin.creators.growthLens.noCameras")}</span>
                )}
              </div>
              <div className="admin-chip-cloud">
                <strong className="section-mini-head">{t("admin.creators.growthLens.viltroxLenses")}</strong>
                {((creators.growth.viltrox_lenses as Array<unknown> | undefined) || []).length ? (
                  ((creators.growth.viltrox_lenses as Array<unknown> | undefined) || []).map((item, index) => (
                    <span key={`lens-${index}`} className="admin-chip">
                      {String(item)}
                    </span>
                  ))
                ) : (
                  <span className="admin-chip admin-chip--muted">{t("admin.creators.growthLens.noLenses")}</span>
                )}
              </div>
              <div className="admin-chip-cloud">
                <strong className="section-mini-head">{t("admin.creators.growthLens.competitorsSeen")}</strong>
                {((creators.growth.competitor_brands_seen as Array<unknown> | undefined) || []).length ? (
                  ((creators.growth.competitor_brands_seen as Array<unknown> | undefined) || []).map((item, index) => (
                    <span key={`competitor-${index}`} className="admin-chip">
                      {String(item)}
                    </span>
                  ))
                ) : (
                  <span className="admin-chip admin-chip--muted">{t("admin.creators.growthLens.noCompetitorSignals")}</span>
                )}
              </div>
            </div>
          </div>
        ) : (
          <EmptyState title={t("admin.creators.growthLens.emptyTitle")} body={t("admin.creators.growthLens.emptyBody")} />
        )}
      </Panel>

      <Panel title={t("admin.creators.timeline.title")} kicker={t("admin.creators.timeline.kicker")}>
        <DataTable
          columns={[
            t("admin.creators.timeline.columns.date"),
            t("admin.creators.timeline.columns.genre"),
            t("admin.creators.timeline.columns.scores"),
            t("admin.creators.timeline.columns.platform"),
            t("admin.creators.timeline.columns.title"),
          ]}
          rows={((creators?.growth?.submissions_timeline as Array<Record<string, unknown>> | undefined) || []).slice(-12).reverse().map((item, index) => [
            formatDate(item.created_at || ""),
            String(item.content_genre || t("admin.shared.missing")),
            t("admin.creators.timeline.scoreLine", {
              tech: toNumber(item.tech_score || 0).toFixed(0),
              marketing: toNumber(item.marketing_score || 0).toFixed(0),
              overall: toNumber(item.overall_score || item.final_score || 0).toFixed(0),
            }),
            String(item.platform || t("admin.shared.missing")),
            <div key={`timeline-${index}`}>
              <div className="table-primary">{String(item.title || `Submission #${item.id || index}`)}</div>
              <small>{String(item.id || t("admin.shared.missing"))}</small>
            </div>,
          ])}
          empty={t("admin.creators.timeline.empty")}
        />
      </Panel>

      <Panel title={t("admin.creators.scoreHistory.title")} kicker={t("admin.creators.scoreHistory.kicker")}>
        <DataTable
          columns={[
            t("admin.creators.scoreHistory.columns.date"),
            t("admin.creators.scoreHistory.columns.overall"),
            t("admin.creators.scoreHistory.columns.tech"),
            t("admin.creators.scoreHistory.columns.marketing"),
            t("admin.creators.scoreHistory.columns.delta"),
            t("admin.creators.scoreHistory.columns.note"),
          ]}
          rows={((creators?.growth?.score_history as Array<Record<string, unknown>> | undefined) || []).slice(-16).reverse().map((item, index) => [
            formatDate(item.created_at || item.date || ""),
            toNumber(item.overall_score || item.final_score || item.score || 0).toFixed(1),
            toNumber(item.tech_score || item.tech || 0).toFixed(1),
            toNumber(item.marketing_score || item.mkt || 0).toFixed(1),
            toNumber(item.delta || item.score_delta || 0).toFixed(1),
            <div key={`score-history-${index}`}>
              <div className="table-primary">{String(item.note || item.reason || item.genre || t("admin.creators.scoreHistory.trendSignal"))}</div>
              <small>{String(item.platform || item.product_series || t("admin.shared.missing"))}</small>
            </div>,
          ])}
          empty={t("admin.creators.scoreHistory.empty")}
        />
      </Panel>
    </div>
  );
}
