import type { Dispatch, FormEvent, SetStateAction } from "react";
import { useTranslation } from "react-i18next";

import type { AdminProductsSnapshot } from "../../../services/admin.service";
import type { AdminSubmission } from "../../../lib/api";
import type { ViosDashboardProductRecord } from "../../../services/admin.service";
import { EmptyState, MetricStrip, Panel, StatusPill } from "../../ui";
import { compactNumber, DataTable, formatDate, TablePager, toNumber, toneForStatus } from "../shared";

interface CorrectionForm {
  submission_id: string;
  correct_series: string;
  correct_label: string;
  note: string;
}

interface ProductsTabProps {
  products: AdminProductsSnapshot | null;
  productSearch: string;
  setProductSearch: Dispatch<SetStateAction<string>>;
  productPage: number;
  productTotalPages: number;
  productRowsFiltered: ViosDashboardProductRecord[];
  productRowsPaged: ViosDashboardProductRecord[];
  setProductPage: Dispatch<SetStateAction<number>>;
  selectedProductKey: string;
  setSelectedProductKey: Dispatch<SetStateAction<string>>;
  selectedProductSummary: ViosDashboardProductRecord | null;
  selectedProductCatalogRows: Array<Record<string, unknown>>;
  selectedProductRecentRows: Array<Record<string, unknown>>;
  correctionTargetRows: AdminSubmission[];
  correctionForm: CorrectionForm;
  setCorrectionForm: Dispatch<SetStateAction<CorrectionForm>>;
  submitCorrection: (event: FormEvent, submissionId?: number) => Promise<void>;
  busy: string;
}

export function ProductsTab({
  products,
  productSearch,
  setProductSearch,
  productPage,
  productTotalPages,
  productRowsFiltered,
  productRowsPaged,
  setProductPage,
  selectedProductKey,
  setSelectedProductKey,
  selectedProductSummary,
  selectedProductCatalogRows,
  selectedProductRecentRows,
  correctionTargetRows,
  correctionForm,
  setCorrectionForm,
  submitCorrection,
  busy,
}: ProductsTabProps) {
  const { t } = useTranslation();
  return (
    <div className="admin-workspace-grid">
      <Panel title={t("admin.products.command.title")} kicker={t("admin.products.command.kicker")}>
        <MetricStrip
          columns={4}
          items={[
            { label: t("admin.products.command.metrics.catalogRows.label"), value: compactNumber(products?.catalog.length || 0), note: t("admin.products.command.metrics.catalogRows.note") },
            { label: t("admin.products.command.metrics.trackedSeries.label"), value: compactNumber(products?.dashboard?.products?.length || 0), note: t("admin.products.command.metrics.trackedSeries.note") },
            { label: t("admin.products.command.metrics.platforms.label"), value: compactNumber(products?.dashboard?.platforms?.length || 0), note: t("admin.products.command.metrics.platforms.note") },
            { label: t("admin.products.command.metrics.recentItems.label"), value: compactNumber(products?.dashboard?.recent?.length || 0), note: t("admin.products.command.metrics.recentItems.note") },
          ]}
        />
        <div className="admin-note-form">
          <label className="auth-field admin-form-grid__full">
            <span>{t("admin.products.command.searchLabel")}</span>
            <input value={productSearch} onChange={(event) => setProductSearch(event.target.value)} placeholder={t("admin.products.command.searchPlaceholder")} />
          </label>
        </div>
      </Panel>

      <Panel title={t("admin.products.series.title")} kicker={t("admin.products.series.kicker")}>
        <TablePager page={productPage} totalPages={productTotalPages} totalItems={productRowsFiltered.length} label={t("admin.products.series.pagerLabel")} onChange={setProductPage} />
        <DataTable
          columns={[t("admin.products.series.columns.series"), t("admin.products.series.columns.count"), t("admin.products.series.columns.views"), t("admin.products.series.columns.likes"), t("admin.products.series.columns.avgScore"), t("admin.products.series.columns.action")]}
          rows={productRowsPaged.map((item) => [
            String(item.series || t("admin.shared.missing")),
            compactNumber(item.count || 0),
            compactNumber(item.views || 0),
            compactNumber(item.likes || 0),
            toNumber(item.avg_score || 0).toFixed(1),
            <button key={`product-focus-${item.series || "series"}`} className="outline-btn" type="button" onClick={() => setSelectedProductKey(String(item.series || ""))}>
              {t("admin.operations.actions.inspect")}
            </button>,
          ])}
          empty={t("admin.products.series.empty")}
        />
      </Panel>

      <Panel title={t("admin.products.catalog.title")} kicker={t("admin.products.catalog.kicker")}>
        <DataTable
          columns={[t("admin.products.catalog.columns.series"), t("admin.products.catalog.columns.label"), t("admin.products.catalog.columns.action")]}
          rows={(selectedProductCatalogRows || []).slice(0, 20).map((item, index) => [
            String(item.series || t("admin.shared.missing")),
            <div key={`catalog-${index}`}>
              <div className="table-primary">{String(item.label || t("admin.shared.missing"))}</div>
            </div>,
            <button
              key={`catalog-pick-${index}`}
              className="outline-btn"
              type="button"
              onClick={() => {
                setSelectedProductKey(String(item.series || item.label || ""));
                setCorrectionForm((current) => ({
                  ...current,
                  correct_series: String(item.series || current.correct_series || ""),
                  correct_label: String(item.label || current.correct_label || ""),
                }));
              }}
            >
              {t("admin.products.catalog.use")}
            </button>,
          ])}
          empty={t("admin.products.catalog.empty")}
        />
      </Panel>

      <Panel title={t("admin.products.story.title")} kicker={t("admin.products.story.kicker")}>
        {selectedProductSummary ? (
          <div className="admin-two-column">
            <div className="admin-list-stack">
              <article className="admin-list-item">
                <strong>{String(selectedProductSummary.series || t("admin.products.story.fallbackTitle"))}</strong>
                <p>
                  {t("admin.products.story.storyLine", {
                    videos: compactNumber(selectedProductSummary.count || 0),
                    views: compactNumber(selectedProductSummary.views || 0),
                    likes: compactNumber(selectedProductSummary.likes || 0),
                  })}
                </p>
              </article>
              <article className="admin-list-item">
                <strong>{t("admin.products.story.averageScore")}</strong>
                <p>{t("admin.products.story.averageLine", { score: toNumber(selectedProductSummary.avg_score || 0).toFixed(1) })}</p>
              </article>
              <article className="admin-list-item">
                <strong>{t("admin.products.story.platformExposure")}</strong>
                <p>
                  {(products?.dashboard?.platforms || [])
                    .slice(0, 3)
                    .map((item) => `${String(item.platform || t("admin.shared.platform"))} ${compactNumber(item.count || 0)}`)
                    .join(" · ") || t("admin.products.story.platformSplitPending")}
                </p>
              </article>
              <article className="admin-list-item">
                <strong>{t("admin.products.story.catalogVariants")}</strong>
                <p>
                  {selectedProductCatalogRows
                    .slice(0, 5)
                    .map((item) => String(item.label || item.series || t("admin.products.story.variantFallback")))
                    .join(" · ") || t("admin.products.story.noCatalogVariants")}
                </p>
              </article>
            </div>
            <div className="admin-list-stack">
              {selectedProductRecentRows.length ? (
                selectedProductRecentRows.slice(0, 4).map((item, index) => (
                  <article key={`product-story-${index}`} className="admin-list-item">
                    <strong>{String(item.title || item.product || item.product_series || t("admin.products.story.submissionFallback"))}</strong>
                    <p>
                      {t("admin.products.story.recentRow", {
                        handle: String(item.handle || t("admin.products.story.creatorFallback")),
                        createdAt: formatDate(item.created_at || ""),
                        platform: String(item.platform || t("admin.products.story.platformFallback")),
                      })}
                    </p>
                  </article>
                ))
              ) : (
                <article className="admin-list-item">
                  <strong>{t("admin.products.story.noStoryRows")}</strong>
                  <p>{t("admin.products.story.noStoryRowsBody")}</p>
                </article>
              )}
              <article className="admin-list-item">
                <strong>{t("admin.products.story.correctionHandoff")}</strong>
                <p>{t("admin.products.story.correctionHandoffBody")}</p>
              </article>
            </div>
          </div>
        ) : (
          <EmptyState title={t("admin.products.story.emptyTitle")} body={t("admin.products.story.emptyBody")} />
        )}
      </Panel>

      <Panel title={t("admin.products.correction.title")} kicker={t("admin.products.correction.kicker")}>
        <form className="admin-form-grid" onSubmit={submitCorrection}>
          <label className="auth-field">
            <span>{t("admin.products.correction.submission")}</span>
            <select value={correctionForm.submission_id} onChange={(event) => setCorrectionForm((current) => ({ ...current, submission_id: event.target.value }))}>
              <option value="">{t("admin.products.correction.selectSubmission")}</option>
              {correctionTargetRows.map((item) => (
                <option key={String(item.id)} value={String(item.id)}>
                  #{String(item.id)} · {String(item.title || item.extracted_handle || t("admin.shared.submission"))}
                </option>
              ))}
            </select>
          </label>
          <label className="auth-field">
            <span>{t("admin.products.correction.correctSeries")}</span>
            <input value={correctionForm.correct_series} onChange={(event) => setCorrectionForm((current) => ({ ...current, correct_series: event.target.value }))} placeholder={t("admin.products.correction.correctSeriesPlaceholder")} />
          </label>
          <label className="auth-field">
            <span>{t("admin.products.correction.correctLabel")}</span>
            <input value={correctionForm.correct_label} onChange={(event) => setCorrectionForm((current) => ({ ...current, correct_label: event.target.value }))} placeholder={t("admin.products.correction.correctLabelPlaceholder")} />
          </label>
          <label className="auth-field admin-form-grid__full">
            <span>{t("admin.products.correction.note")}</span>
            <textarea rows={3} value={correctionForm.note} onChange={(event) => setCorrectionForm((current) => ({ ...current, note: event.target.value }))} placeholder={t("admin.products.correction.notePlaceholder")} />
          </label>
          <div className="auth-actions">
            <button className="primary-button" type="submit" disabled={busy === "products:correction"}>
              {busy === "products:correction" ? t("admin.products.correction.saving") : t("admin.products.correction.save")}
            </button>
          </div>
        </form>
      </Panel>

      <Panel title={t("admin.products.platformMix.title")} kicker={t("admin.products.platformMix.kicker")}>
        <div className="admin-card-list">
          {(products?.dashboard?.platforms || []).slice(0, 8).map((item, index) => (
            <article key={`platform-${index}`} className="admin-mini-card">
              <strong>{String(item.platform || t("admin.products.platformMix.platformFallback"))}</strong>
              <p>
                {t("admin.products.platformMix.platformLine", {
                  submissions: compactNumber(item.count || 0),
                  views: compactNumber(item.views || 0),
                  likes: compactNumber(item.likes || 0),
                })}
              </p>
              <span>{t("admin.products.platformMix.avgCreator", { value: toNumber(item.avg_creator || 0).toFixed(1) })}</span>
            </article>
          ))}
        </div>
      </Panel>

      <Panel title={t("admin.products.recent.title")} kicker={t("admin.products.recent.kicker")}>
        <DataTable
          columns={[t("admin.products.recent.columns.date"), t("admin.products.recent.columns.handle"), t("admin.products.recent.columns.product"), t("admin.products.recent.columns.status"), t("admin.products.recent.columns.scores")]}
          rows={(products?.dashboard?.recent || []).slice(0, 12).map((item, index) => [
            formatDate(item.created_at || ""),
            String(item.handle || t("admin.shared.missing")),
            <div key={`recent-product-${index}`}>
              <div className="table-primary">{String(item.product || item.product_series || t("admin.shared.missing"))}</div>
              <small>{String(item.title || t("admin.products.recent.untitledSubmission"))}</small>
            </div>,
            <StatusPill key={`recent-status-${index}`} label={String(item.status || t("admin.shared.pending"))} tone={toneForStatus(String(item.status || ""))} />,
            t("admin.products.recent.scoreLine", {
              campaign: toNumber(item.campaign || item.final_score || 0).toFixed(0),
              creator: toNumber(item.creator || 0).toFixed(0),
            }),
          ])}
          empty={t("admin.products.recent.empty")}
        />
      </Panel>
    </div>
  );
}
