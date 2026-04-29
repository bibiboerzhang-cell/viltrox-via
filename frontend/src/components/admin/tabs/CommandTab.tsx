import type { Dispatch, FormEvent, SetStateAction } from "react";
import { useTranslation } from "react-i18next";

import type { AdminDashboardSnapshot } from "../../../services/admin.service";
import { EmptyState, Panel, StatusPill } from "../../ui";
import { compactNumber, DataTable, JsonInfoList, toneForStatus } from "../shared";

interface RewardFormState {
  title: string;
  description: string;
  category: string;
  points_cost: string;
  meta_label: string;
  image_url: string;
  stock: string;
  sort_order: string;
  status: string;
}

interface CommandTabProps {
  command: AdminDashboardSnapshot | null;
  busy: string;
  editingRewardId: number | null;
  setEditingRewardId: Dispatch<SetStateAction<number | null>>;
  rewardForm: RewardFormState;
  setRewardForm: Dispatch<SetStateAction<RewardFormState>>;
  rewardAction: (rewardId: number, action: "publish" | "archive" | "delete") => Promise<void>;
  submitReward: (event: FormEvent) => Promise<void>;
}

const DEFAULT_REWARD_FORM: RewardFormState = {
  title: "",
  description: "",
  category: "coupon",
  points_cost: "500",
  meta_label: "",
  image_url: "",
  stock: "0",
  sort_order: "100",
  status: "draft",
};

export function CommandTab({
  command,
  busy,
  editingRewardId,
  setEditingRewardId,
  rewardForm,
  setRewardForm,
  rewardAction,
  submitReward,
}: CommandTabProps) {
  const { t } = useTranslation();
  const rewardStatusOptions = [
    { value: "draft", label: t("admin.command.rewardMutation.statusOptions.draft") },
    { value: "published", label: t("admin.command.rewardMutation.statusOptions.published") },
    { value: "archived", label: t("admin.command.rewardMutation.statusOptions.archived") },
  ];

  return (
    <div className="admin-workspace-grid">
      <Panel title={t("admin.command.snapshot.title")} kicker={t("admin.command.snapshot.kicker")}>
        <JsonInfoList payload={command?.health} emptyTitle={t("admin.command.snapshot.emptyTitle")} emptyBody={t("admin.command.snapshot.emptyBody")} />
      </Panel>
      <Panel title={t("admin.command.reviewQueue.title")} kicker={t("admin.command.reviewQueue.kicker")}>
        <DataTable
          columns={[
            t("admin.command.reviewQueue.columns.submission"),
            t("admin.command.reviewQueue.columns.status"),
            t("admin.command.reviewQueue.columns.score"),
            t("admin.command.reviewQueue.columns.points"),
          ]}
          rows={(command?.submissions || []).slice(0, 10).map((item) => [
            <div key={`${item.id}-title`}>
              <div className="table-primary">
                {item.title || t("admin.command.reviewQueue.fallbackTitle", { id: item.id })}
              </div>
              <small>
                {item.platform || t("admin.command.reviewQueue.platformFallback")} ·{" "}
                {item.creator_code || item.extracted_handle || t("admin.command.reviewQueue.creatorFallback")}
              </small>
            </div>,
            <StatusPill
              key={`${item.id}-status`}
              label={String(item.detection_status || t("admin.shared.pending"))}
              tone={toneForStatus(String(item.detection_status || ""))}
            />,
            compactNumber(item.overall_score || item.final_score || 0),
            compactNumber(item.points_awarded || 0),
          ])}
          empty={t("admin.command.reviewQueue.empty")}
          emptyTitle={t("admin.command.reviewQueue.emptyTitle")}
        />
      </Panel>
      <Panel title={t("admin.command.rewardCatalog.title")} kicker={t("admin.command.rewardCatalog.kicker")}>
        {(command?.rewards || []).length ? (
          <div className="admin-card-list">
            {command?.rewards.slice(0, 8).map((reward) => (
              <article key={reward.id} className="admin-mini-card">
                <strong>{reward.title}</strong>
                <p>{reward.description || reward.category}</p>
                <span>{t("admin.command.rewardCatalog.pointsLine", { points: compactNumber(reward.points_cost) })}</span>
                <div className="table-actions">
                  <button
                    className="outline-btn"
                    type="button"
                    onClick={() => {
                      setEditingRewardId(reward.id);
                      setRewardForm({
                        title: reward.title || "",
                        description: reward.description || "",
                        category: reward.category || "coupon",
                        points_cost: String(reward.points_cost || 0),
                        meta_label: reward.meta_label || "",
                        image_url: reward.image_url || "",
                        stock: String(reward.stock || 0),
                        sort_order: String(reward.sort_order || 100),
                        status: String(reward.status || "draft"),
                      });
                    }}
                  >
                    {t("admin.command.rewardCatalog.actions.edit")}
                  </button>
                  <button
                    className="outline-btn"
                    type="button"
                    disabled={busy === `command:reward:${reward.id}:publish`}
                    onClick={() => void rewardAction(reward.id, "publish")}
                  >
                    {t("admin.command.rewardCatalog.actions.publish")}
                  </button>
                  <button
                    className="outline-btn"
                    type="button"
                    disabled={busy === `command:reward:${reward.id}:archive`}
                    onClick={() => void rewardAction(reward.id, "archive")}
                  >
                    {t("admin.command.rewardCatalog.actions.archive")}
                  </button>
                  <button
                    className="outline-btn outline-btn--danger"
                    type="button"
                    disabled={busy === `command:reward:${reward.id}:delete`}
                    onClick={() => void rewardAction(reward.id, "delete")}
                  >
                    {t("admin.command.rewardCatalog.actions.delete")}
                  </button>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <EmptyState title={t("admin.command.rewardCatalog.emptyTitle")} body={t("admin.command.rewardCatalog.emptyBody")} />
        )}
      </Panel>
      <Panel title={t("admin.command.rewardMutation.title")} kicker={t("admin.command.rewardMutation.kicker")}>
        <form className="admin-form-grid" onSubmit={submitReward}>
          <label className="auth-field">
            <span>{t("admin.command.rewardMutation.fields.title")}</span>
            <input value={rewardForm.title} onChange={(event) => setRewardForm((current) => ({ ...current, title: event.target.value }))} required />
          </label>
          <label className="auth-field">
            <span>{t("admin.command.rewardMutation.fields.category")}</span>
            <input value={rewardForm.category} onChange={(event) => setRewardForm((current) => ({ ...current, category: event.target.value }))} required />
          </label>
          <label className="auth-field">
            <span>{t("admin.command.rewardMutation.fields.points")}</span>
            <input type="number" min={0} value={rewardForm.points_cost} onChange={(event) => setRewardForm((current) => ({ ...current, points_cost: event.target.value }))} required />
          </label>
          <label className="auth-field">
            <span>{t("admin.command.rewardMutation.fields.status")}</span>
            <select value={rewardForm.status} onChange={(event) => setRewardForm((current) => ({ ...current, status: event.target.value }))}>
              {rewardStatusOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label className="auth-field admin-form-grid__full">
            <span>{t("admin.command.rewardMutation.fields.description")}</span>
            <textarea rows={3} value={rewardForm.description} onChange={(event) => setRewardForm((current) => ({ ...current, description: event.target.value }))} />
          </label>
          <label className="auth-field">
            <span>{t("admin.command.rewardMutation.fields.metaLabel")}</span>
            <input value={rewardForm.meta_label} onChange={(event) => setRewardForm((current) => ({ ...current, meta_label: event.target.value }))} />
          </label>
          <label className="auth-field">
            <span>{t("admin.command.rewardMutation.fields.imageUrl")}</span>
            <input value={rewardForm.image_url} onChange={(event) => setRewardForm((current) => ({ ...current, image_url: event.target.value }))} />
          </label>
          <label className="auth-field">
            <span>{t("admin.command.rewardMutation.fields.stock")}</span>
            <input type="number" min={0} value={rewardForm.stock} onChange={(event) => setRewardForm((current) => ({ ...current, stock: event.target.value }))} />
          </label>
          <label className="auth-field">
            <span>{t("admin.command.rewardMutation.fields.sortOrder")}</span>
            <input type="number" min={0} value={rewardForm.sort_order} onChange={(event) => setRewardForm((current) => ({ ...current, sort_order: event.target.value }))} />
          </label>
          <div className="auth-actions">
            <button className="primary-button" type="submit" disabled={busy === "command:reward"}>
              {busy === "command:reward"
                ? t("admin.command.rewardMutation.saving")
                : editingRewardId
                  ? t("admin.command.rewardMutation.updateAction", { id: editingRewardId })
                  : t("admin.command.rewardMutation.createAction")}
            </button>
            {editingRewardId ? (
              <button
                className="ghost-button"
                type="button"
                onClick={() => {
                  setEditingRewardId(null);
                  setRewardForm({ ...DEFAULT_REWARD_FORM });
                }}
              >
                {t("admin.command.rewardMutation.clearAction")}
              </button>
            ) : null}
          </div>
        </form>
      </Panel>
    </div>
  );
}
