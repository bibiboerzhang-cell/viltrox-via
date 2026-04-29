/**
 * Products tab v2
 *
 * Restores reward catalog mutations onto the v2 admin surface:
 *   - create / edit draft rewards
 *   - publish / archive / delete existing rewards
 * while keeping the dashboard product summary view.
 */
import { useMemo, useState, type ChangeEvent, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import {
  createAdminReward,
  fetchAdminProductsSnapshot,
  runAdminRewardAction,
  updateAdminReward,
  uploadAdminRewardImage,
} from "../../../services/admin.service";
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
  StatusPill,
  WarningCard,
  useAdminSnapshot,
  type DataColumn,
} from "../shared_v2";

interface Props {
  token: string;
  user: AuthUser;
}

interface ProductSummaryRow {
  key: string;
  name: string;
  series: string;
  sku: string;
  submissions: number;
  views: number;
  score: number;
}

interface RewardCatalogRow extends ProductSummaryRow {
  id: number;
  category: string;
  description: string;
  imageUrl: string;
  metaLabel: string;
  pointsCost: number;
  sortOrder: number;
  status: string;
  stock: number;
}

interface RewardDraft {
  id: number | null;
  title: string;
  description: string;
  category: string;
  metaLabel: string;
  imageUrl: string;
  pointsCost: string;
  stock: string;
  sortOrder: string;
  status: string;
}

const EMPTY_REWARD_DRAFT: RewardDraft = {
  id: null,
  title: "",
  description: "",
  category: "merch",
  metaLabel: "",
  imageUrl: "",
  pointsCost: "100",
  stock: "1",
  sortOrder: "0",
  status: "draft",
};

function normalizeProduct(raw: Record<string, unknown>, i: number): ProductSummaryRow {
  return {
    key: String(raw.sku || raw.key || raw.id || i),
    name: String(raw.label || raw.name || raw.product_label || raw.title || raw.series || "Unknown"),
    series: String(raw.series || raw.product_series || raw.label || raw.category || "—"),
    sku: String(raw.sku || raw.product_sku || raw.meta_label || "—"),
    submissions: Number(raw.submissions || raw.submissions_count || raw.count || raw.cnt || 0),
    views: Number(raw.views || raw.total_views || 0),
    score: Number(raw.avg_score || raw.score || 0),
  };
}

function normalizeCatalogReward(raw: Record<string, unknown>, i: number): RewardCatalogRow {
  const base = normalizeProduct(raw, i);
  return {
    ...base,
    id: Number(raw.id || 0),
    category: String(raw.category || raw.series || "misc"),
    description: String(raw.description || ""),
    imageUrl: String(raw.image_url || ""),
    metaLabel: String(raw.meta_label || raw.sku || ""),
    pointsCost: Number(raw.points_cost || 0),
    sortOrder: Number(raw.sort_order || 0),
    status: String(raw.status || "draft"),
    stock: Number(raw.stock ?? -1),
  };
}

function rewardStatusTone(status: string): "pass" | "review" | "idle" | "block" {
  const normalized = status.toLowerCase();
  if (normalized === "published") return "pass";
  if (normalized === "archived") return "idle";
  if (normalized === "deleted") return "block";
  return "review";
}

function rewardStatusLabel(status: string, t: (key: string, fallback: string) => string): string {
  const normalized = status.toLowerCase();
  if (normalized === "published") return t("admin.products.v2.status.published", "已发布");
  if (normalized === "archived") return t("admin.products.v2.status.archived", "已归档");
  if (normalized === "deleted") return t("admin.products.v2.status.deleted", "已删除");
  if (normalized === "draft") return t("admin.products.v2.status.draft", "草稿");
  return status || t("admin.products.v2.status.unknown", "未知");
}

function formatIssues(
  issues: Array<{ source: string; message: string }>,
): string {
  return issues
    .map((issue) => `${issue.source}: ${issue.message}`)
    .join("\n");
}

function draftFromRow(row: RewardCatalogRow): RewardDraft {
  return {
    id: row.id,
    title: row.name,
    description: row.description,
    category: row.category,
    metaLabel: row.metaLabel,
    imageUrl: row.imageUrl,
    pointsCost: String(row.pointsCost),
    stock: String(row.stock),
    sortOrder: String(row.sortOrder),
    status: row.status || "draft",
  };
}

export function ProductsTab({ token }: Props) {
  const { t } = useTranslation();
  const { data, loading, error, issues, refresh } = useAdminSnapshot(token, fetchAdminProductsSnapshot);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorDraft, setEditorDraft] = useState<RewardDraft>(EMPTY_REWARD_DRAFT);
  const [busy, setBusy] = useState("");
  const [toast, setToast] = useState<{ tone: "ok" | "err"; msg: string } | null>(null);

  const rows: RewardCatalogRow[] = useMemo(
    () => (data?.catalog ?? []).map(normalizeCatalogReward),
    [data],
  );

  const topProducts: ProductSummaryRow[] = useMemo(() => {
    const dash = data?.dashboard;
    if (!dash?.products) return [];
    return (dash.products as unknown[]).map((r, i) =>
      normalizeProduct(r as Record<string, unknown>, i),
    );
  }, [data]);

  const kpis = [
    { label: t("admin.products.v2.kpi.rewardSku", "奖励 SKU"), value: rows.length },
    { label: t("admin.products.v2.kpi.published", "已发布"), value: rows.filter((row) => row.status.toLowerCase() === "published").length },
    {
      label: t("admin.products.v2.kpi.lowStock", "库存告急"),
      value: rows.filter((row) => row.stock >= 0 && row.stock <= 3).length,
    },
    { label: t("admin.products.v2.kpi.views", "总曝光"), value: rows.reduce((a, r) => a + r.views, 0).toLocaleString() },
  ];

  function openCreateEditor() {
    setEditorDraft(EMPTY_REWARD_DRAFT);
    setEditorOpen(true);
    setToast(null);
  }

  function openEditEditor(row: RewardCatalogRow) {
    setEditorDraft(draftFromRow(row));
    setEditorOpen(true);
    setToast(null);
  }

  async function handleRewardSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy("reward:save");
    setToast(null);
    try {
      const payload = {
        title: editorDraft.title.trim(),
        description: editorDraft.description.trim(),
        category: editorDraft.category.trim(),
        points_cost: Math.max(0, Number(editorDraft.pointsCost || 0)),
        meta_label: editorDraft.metaLabel.trim(),
        image_url: editorDraft.imageUrl.trim(),
        stock: Number(editorDraft.stock || 0),
        sort_order: Number(editorDraft.sortOrder || 0),
        status: editorDraft.status || "draft",
      };
      if (!payload.title) {
        throw new Error(t("admin.products.v2.messages.titleRequired", "标题不能为空"));
      }
      if (editorDraft.id) {
        await updateAdminReward(token, editorDraft.id, payload);
        setToast({ tone: "ok", msg: t("admin.products.v2.messages.updated", "已更新奖品 #{{id}}", { id: editorDraft.id }) });
      } else {
        await createAdminReward(token, payload);
        setToast({ tone: "ok", msg: t("admin.products.v2.messages.created", "已创建新奖品草稿") });
      }
      setEditorOpen(false);
      setEditorDraft(EMPTY_REWARD_DRAFT);
      refresh();
    } catch (err) {
      setToast({ tone: "err", msg: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy("");
    }
  }

  async function handleRewardImageFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    if (!file) return;
    setBusy("reward:image");
    setToast(null);
    try {
      const imageUrl = await uploadAdminRewardImage(token, file);
      setEditorDraft((current) => ({ ...current, imageUrl }));
      setToast({ tone: "ok", msg: t("admin.products.v2.messages.imageUploaded", "图片已上传，URL 已自动填入") });
    } catch (err) {
      setToast({ tone: "err", msg: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy("");
      event.currentTarget.value = "";
    }
  }

  async function handleRewardAction(row: RewardCatalogRow, action: "publish" | "archive" | "delete") {
    if (action === "delete" && !window.confirm(t("admin.products.v2.messages.confirmDelete", "删除奖品 #{{id}} - {{name}} ?", { id: row.id, name: row.name }))) {
      return;
    }
    setBusy(`reward:${action}:${row.id}`);
    setToast(null);
    try {
      await runAdminRewardAction(token, row.id, action);
      if (editorDraft.id === row.id && action === "delete") {
        setEditorOpen(false);
        setEditorDraft(EMPTY_REWARD_DRAFT);
      }
      const labels: Record<typeof action, string> = {
        publish: t("admin.products.v2.messages.published", "已上架"),
        archive: t("admin.products.v2.messages.archived", "已归档"),
        delete: t("admin.products.v2.messages.deleted", "已删除"),
      };
      setToast({ tone: "ok", msg: `${labels[action]} #${row.id}` });
      refresh();
    } catch (err) {
      setToast({ tone: "err", msg: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy("");
    }
  }

  const summaryColumns: DataColumn<ProductSummaryRow>[] = [
    {
      key: "name",
      label: t("admin.products.v2.columns.product", "产品"),
      width: "2fr",
      render: (row) => (
        <div>
          <div style={{ color: "var(--ax-text-5)" }}>{row.name}</div>
          <div className="ax-mono" style={{ fontSize: 9, color: "var(--ax-text-1)" }}>
            {row.sku}
          </div>
        </div>
      ),
    },
    {
      key: "series",
      label: t("admin.products.v2.columns.series", "系列"),
      width: "100px",
      render: (row) => <span style={{ color: "var(--ax-text-3)" }}>{row.series}</span>,
    },
    {
      key: "submissions",
      label: t("admin.products.v2.columns.submissions", "提交"),
      width: "80px",
      sortable: true,
      render: (row) => (
        <span className="ax-num" style={{ color: "var(--ax-text-5)", fontWeight: 600 }}>
          {row.submissions}
        </span>
      ),
    },
    {
      key: "views",
      label: t("admin.products.v2.columns.views", "曝光"),
      width: "100px",
      sortable: true,
      render: (row) => (
        <span className="ax-num" style={{ color: "var(--ax-text-4)" }}>
          {row.views.toLocaleString()}
        </span>
      ),
    },
    {
      key: "score",
      label: t("admin.products.v2.columns.avgScore", "均分"),
      width: "60px",
      accent: true,
      sortable: true,
      render: (row) => (
        <span className="ax-num" style={{ color: "var(--ax-text-5)", fontWeight: 600 }}>
          {Math.round(row.score)}
        </span>
      ),
    },
  ];

  const catalogColumns: DataColumn<RewardCatalogRow>[] = [
    {
      key: "name",
      label: t("admin.products.v2.columns.reward", "奖品"),
      width: "1.8fr",
      render: (row) => (
        <div>
          <div style={{ color: "var(--ax-text-5)", fontWeight: 600 }}>{row.name}</div>
          <div style={{ fontSize: 9, color: "var(--ax-text-1)" }}>
            {row.category}
            {row.metaLabel ? ` · ${row.metaLabel}` : ""}
          </div>
        </div>
      ),
    },
    {
      key: "points",
      label: t("admin.products.v2.columns.points", "积分"),
      width: "80px",
      accent: true,
      render: (row) => <span className="ax-num">{row.pointsCost}</span>,
    },
    {
      key: "stock",
      label: t("admin.products.v2.columns.stock", "库存"),
      width: "80px",
      render: (row) => (
        <span
          className="ax-num"
          style={{
            color:
              row.stock >= 0 && row.stock <= 3
                ? "var(--ax-status-alert)"
                : "var(--ax-text-5)",
          }}
        >
          {row.stock < 0 ? "∞" : row.stock}
        </span>
      ),
    },
    {
      key: "status",
      label: t("admin.products.v2.columns.status", "状态"),
      width: "90px",
      render: (row) => <StatusPill tone={rewardStatusTone(row.status) as never}>{rewardStatusLabel(row.status, t)}</StatusPill>,
    },
    {
      key: "actions",
      label: "",
      width: "260px",
      render: (row) => (
        <div style={{ display: "flex", gap: 4 }} onClick={(event) => event.stopPropagation()}>
          <button
            type="button"
            className="ax-btn ax-btn--sm"
            disabled={busy === "reward:save"}
            onClick={() => openEditEditor(row)}
          >
            <Icons.edit /> {t("admin.products.v2.actions.edit", "编辑")}
          </button>
          {row.status.toLowerCase() === "published" ? (
            <button
              type="button"
              className="ax-btn ax-btn--sm"
              disabled={busy === `reward:archive:${row.id}`}
              onClick={() => handleRewardAction(row, "archive")}
            >
              {t("admin.products.v2.actions.archive", "归档")}
            </button>
          ) : (
            <button
              type="button"
              className="ax-btn ax-btn--sm"
              style={{ color: "var(--ax-status-pass)" }}
              disabled={busy === `reward:publish:${row.id}`}
              onClick={() => handleRewardAction(row, "publish")}
            >
              {t("admin.products.v2.actions.publish", "上架")}
            </button>
          )}
          <button
            type="button"
            className="ax-btn ax-btn--sm"
            style={{ color: "var(--ax-status-alert)" }}
            disabled={busy === `reward:delete:${row.id}`}
            onClick={() => handleRewardAction(row, "delete")}
          >
            {t("admin.products.v2.actions.delete", "删除")}
          </button>
        </div>
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title={t("admin.products.v2.title", "产品与奖励")}
        subtitle={t("admin.products.v2.subtitle", "{{count}} 个奖品条目 · 奖励 CRUD 已接回 v2", { count: rows.length })}
        actions={
          <>
            <button type="button" className="ax-btn" onClick={openCreateEditor}>
              <Icons.plus /> {t("admin.products.v2.actions.create", "新建奖品")}
            </button>
            <button type="button" className="ax-btn" onClick={refresh} disabled={loading}>
              <Icons.trending /> {loading ? t("admin.products.v2.actions.refreshing", "刷新中...") : t("admin.products.v2.actions.refresh", "刷新")}
            </button>
          </>
        }
      />

      {error ? (
        <div style={{ padding: 16 }}>
          <ErrorCard detail={error} onRetry={refresh} />
        </div>
      ) : null}

      {toast ? (
        <div
          style={{
            padding: "8px 16px",
            background:
              toast.tone === "ok"
                ? "rgba(99, 165, 30, 0.08)"
                : "rgba(209, 69, 32, 0.08)",
            color:
              toast.tone === "ok"
                ? "var(--ax-status-pass)"
                : "var(--ax-status-alert)",
            fontSize: 11,
            borderBottom: "0.5px solid var(--ax-border-2)",
            display: "flex",
            justifyContent: "space-between",
          }}
        >
          <span>{toast.msg}</span>
          <span style={{ cursor: "pointer", color: "var(--ax-text-1)" }} onClick={() => setToast(null)}>×</span>
        </div>
      ) : null}

      <div style={{ padding: 16 }}>
        {loading && !data ? (
          <LoadingCard />
        ) : (
          <>
            {issues.length > 0 ? (
              <div style={{ marginBottom: 16 }}>
                <WarningCard
                  label={t("admin.products.v2.partialLoad", "产品页部分数据加载失败")}
                  detail={formatIssues(issues)}
                />
              </div>
            ) : null}

            {editorOpen ? (
              <form onSubmit={handleRewardSubmit} className="ax-card" style={{ marginBottom: 16, display: "grid", gap: 12 }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "start" }}>
                  <div>
                    <SectionLabel>{editorDraft.id ? t("admin.products.v2.editor.editLabel", "编辑奖励") : t("admin.products.v2.editor.createLabel", "创建奖励")}</SectionLabel>
                    <div style={{ marginTop: 4, color: "var(--ax-text-4)", fontSize: 13 }}>
                      {editorDraft.id
                        ? t("admin.products.v2.editor.editTitle", "编辑奖品 #{{id}}", { id: editorDraft.id })
                        : t("admin.products.v2.editor.createTitle", "新建奖励目录草稿")}
                    </div>
                  </div>
                  <button
                    type="button"
                    className="ax-btn ax-btn--sm"
                    onClick={() => {
                      setEditorOpen(false);
                      setEditorDraft(EMPTY_REWARD_DRAFT);
                    }}
                    disabled={busy === "reward:save"}
                  >
                    {t("admin.products.v2.actions.close", "关闭")}
                  </button>
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 10 }}>
                  <label style={{ display: "grid", gap: 5, color: "var(--ax-text-2)", fontSize: 10, gridColumn: "span 2" }}>
                    {t("admin.products.v2.editor.name", "名称")}
                    <input
                      className="ax-input"
                      value={editorDraft.title}
                      onChange={(event) => setEditorDraft((current) => ({ ...current, title: event.target.value }))}
                      placeholder="Viltrox 85mm F1.8"
                    />
                  </label>
                  <label style={{ display: "grid", gap: 5, color: "var(--ax-text-2)", fontSize: 10 }}>
                    {t("admin.products.v2.editor.category", "分类")}
                    <input
                      className="ax-input"
                      value={editorDraft.category}
                      onChange={(event) => setEditorDraft((current) => ({ ...current, category: event.target.value }))}
                      placeholder="lens / merch / coupon"
                    />
                  </label>
                  <label style={{ display: "grid", gap: 5, color: "var(--ax-text-2)", fontSize: 10 }}>
                    {t("admin.products.v2.editor.metaLabel", "元标签")}
                    <input
                      className="ax-input"
                      value={editorDraft.metaLabel}
                      onChange={(event) => setEditorDraft((current) => ({ ...current, metaLabel: event.target.value }))}
                      placeholder="SKU / campaign label"
                    />
                  </label>
                  <label style={{ display: "grid", gap: 5, color: "var(--ax-text-2)", fontSize: 10 }}>
                    {t("admin.products.v2.editor.points", "所需积分")}
                    <input
                      className="ax-input"
                      type="number"
                      min="0"
                      value={editorDraft.pointsCost}
                      onChange={(event) => setEditorDraft((current) => ({ ...current, pointsCost: event.target.value }))}
                    />
                  </label>
                  <label style={{ display: "grid", gap: 5, color: "var(--ax-text-2)", fontSize: 10 }}>
                    {t("admin.products.v2.editor.stock", "库存")}
                    <input
                      className="ax-input"
                      type="number"
                      value={editorDraft.stock}
                      onChange={(event) => setEditorDraft((current) => ({ ...current, stock: event.target.value }))}
                    />
                  </label>
                  <label style={{ display: "grid", gap: 5, color: "var(--ax-text-2)", fontSize: 10 }}>
                    {t("admin.products.v2.editor.sortOrder", "排序")}
                    <input
                      className="ax-input"
                      type="number"
                      value={editorDraft.sortOrder}
                      onChange={(event) => setEditorDraft((current) => ({ ...current, sortOrder: event.target.value }))}
                    />
                  </label>
                  <label style={{ display: "grid", gap: 5, color: "var(--ax-text-2)", fontSize: 10 }}>
                    {t("admin.products.v2.editor.status", "当前状态")}
                    <input className="ax-input" value={editorDraft.status} disabled />
                  </label>
                  <div style={{ display: "grid", gap: 8, color: "var(--ax-text-2)", fontSize: 10, gridColumn: "span 4" }}>
                    <span>{t("admin.products.v2.editor.imageSource", "图片 URL / 文件")}</span>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 8, alignItems: "center" }}>
                      <input
                        className="ax-input"
                        value={editorDraft.imageUrl}
                        onChange={(event) => setEditorDraft((current) => ({ ...current, imageUrl: event.target.value }))}
                        placeholder="https://... 或上传后自动生成 /uploads/reward_images/..."
                      />
                      <label className="ax-btn" style={{ cursor: busy === "reward:image" ? "not-allowed" : "pointer", opacity: busy === "reward:image" ? 0.6 : 1 }}>
                        <Icons.upload />
                        {busy === "reward:image" ? t("admin.products.v2.actions.uploading", "上传中...") : t("admin.products.v2.actions.uploadImage", "上传图片")}
                        <input
                          type="file"
                          accept="image/png,image/jpeg,image/webp,image/gif"
                          disabled={busy === "reward:image" || busy === "reward:save"}
                          onChange={handleRewardImageFile}
                          style={{ display: "none" }}
                        />
                      </label>
                    </div>
                    {editorDraft.imageUrl ? (
                      <div style={{ display: "flex", gap: 10, alignItems: "center", minHeight: 58 }}>
                        <img
                          src={editorDraft.imageUrl}
                          alt=""
                          style={{
                            width: 56,
                            height: 56,
                            objectFit: "cover",
                            borderRadius: 6,
                            border: "0.5px solid var(--ax-border-2)",
                            background: "var(--ax-bg-2)",
                          }}
                          onError={(event) => {
                            event.currentTarget.style.display = "none";
                          }}
                        />
                        <span className="ax-mono" style={{ color: "var(--ax-text-1)", wordBreak: "break-all" }}>{editorDraft.imageUrl}</span>
                      </div>
                    ) : null}
                  </div>
                  <label style={{ display: "grid", gap: 5, color: "var(--ax-text-2)", fontSize: 10, gridColumn: "span 4" }}>
                    {t("admin.products.v2.editor.description", "描述")}
                    <textarea
                      className="ax-input"
                      value={editorDraft.description}
                      onChange={(event) => setEditorDraft((current) => ({ ...current, description: event.target.value }))}
                      placeholder={t("admin.products.v2.editor.descriptionPlaceholder", "简短描述、兑换说明、适用条件")}
                      rows={4}
                    />
                  </label>
                </div>

                <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
                  <button
                    type="button"
                    className="ax-btn"
                    onClick={() => {
                      setEditorOpen(false);
                      setEditorDraft(EMPTY_REWARD_DRAFT);
                    }}
                    disabled={busy === "reward:save"}
                  >
                    {t("admin.products.v2.actions.cancel", "取消")}
                  </button>
                  <button type="submit" className="ax-btn ax-btn--primary" disabled={busy === "reward:save"}>
                    {busy === "reward:save"
                      ? t("admin.products.v2.actions.saving", "保存中...")
                      : editorDraft.id
                        ? t("admin.products.v2.actions.saveChanges", "保存修改")
                        : t("admin.products.v2.actions.createDraft", "创建草稿")}
                  </button>
                </div>
              </form>
            ) : null}

            <div style={{ marginBottom: 16 }}>
              <KPIGrid items={kpis} columns={4} />
            </div>

            {topProducts.length > 0 ? (
              <>
                <SectionLabel>{t("admin.products.v2.topProducts", "Top 5 产品")}</SectionLabel>
                <div style={{ marginBottom: 16, border: "0.5px solid var(--ax-border-2)", borderRadius: 6, overflow: "hidden", background: "var(--ax-bg-1)" }}>
                  <DataTable
                    columns={summaryColumns}
                    rows={topProducts.slice(0, 5)}
                    rowKey={(row) => row.key}
                    showCheckbox={false}
                  />
                </div>
              </>
            ) : null}

            <SectionLabel>{t("admin.products.v2.rewardCatalog", "奖励目录")}</SectionLabel>
            <div style={{ border: "0.5px solid var(--ax-border-2)", borderRadius: 6, overflow: "hidden", background: "var(--ax-bg-1)" }}>
              {rows.length === 0 ? (
                <EmptyCard label={t("admin.products.v2.emptyTitle", "暂无奖品数据")} hint={t("admin.products.v2.emptyBody", "现在可以直接在这个 tab 里新建奖品草稿了。")} />
              ) : (
                <DataTable
                  columns={catalogColumns}
                  rows={rows}
                  rowKey={(row) => String(row.id || row.key)}
                  showCheckbox={false}
                />
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default ProductsTab;
