import React from "react";
import type { VkpiProductCatalogItem } from "../../vkpiTypes";
import { CardHeader } from "../../shared/CardHeader";
import { InfoBlock } from "../../shared/InfoBlock";
import { ProviderStatusTable } from "../../tables/ProviderStatusTable";

type Row = Record<string, unknown>;

export function ProviderHealthCard({
  providers,
  providerBusy,
  providerError,
  onReload,
  onProbe,
}: {
  providers: Row[];
  providerBusy: string;
  providerError: string;
  onReload: () => void;
  onProbe: (provider: string) => void;
}) {
  return (
    <section className="vkpi-card vkpi-table-card vkpi-action-card--wide vkpi-settings-switch-panel">
      <div className="vkpi-table-card__header">
        <div><h2>API 是否工作</h2><span>{providers.length} 个服务</span></div>
        <button className="vkpi-button" type="button" disabled={providerBusy === "all"} onClick={onReload}>{providerBusy === "all" ? "刷新中" : "刷新"}</button>
      </div>
      <ProviderStatusTable rows={providers} busyProvider={providerBusy} onProbe={onProbe} />
      {providerError ? <div className="vkpi-inline-message">{providerError}</div> : null}
    </section>
  );
}

export function StaffInviteCard({
  email,
  name,
  role,
  permission,
  busy,
  canInvite,
  onEmailChange,
  onNameChange,
  onRoleChange,
  onPermissionChange,
  onSubmit,
}: {
  email: string;
  name: string;
  role: string;
  permission: "none" | "read" | "write";
  busy: boolean;
  canInvite: boolean;
  onEmailChange: (value: string) => void;
  onNameChange: (value: string) => void;
  onRoleChange: (value: string) => void;
  onPermissionChange: (value: "none" | "read" | "write") => void;
  onSubmit: React.FormEventHandler;
}) {
  return (
    <section className="vkpi-card vkpi-action-card">
      <CardHeader title="授权账户" />
      <form className="vkpi-form-stack" onSubmit={onSubmit}>
        <input value={email} onChange={(event) => onEmailChange(event.target.value)} placeholder="员工邮箱，建议使用 @viltrox.com" />
        <input value={name} onChange={(event) => onNameChange(event.target.value)} placeholder="员工姓名 / 拼音 ID" />
        <select value={role} onChange={(event) => onRoleChange(event.target.value)}><option value="employee">员工 / 运营</option><option value="manager">管理层</option><option value="analyst">数据分析</option><option value="readonly">只读</option><option value="admin">管理员</option></select>
        <select value={permission} onChange={(event) => onPermissionChange(event.target.value as "none" | "read" | "write")}><option value="write">可操作 Viltrox Marketing</option><option value="read">只读 Viltrox Marketing</option><option value="none">无 Viltrox Marketing 权限</option></select>
        <button className="vkpi-button vkpi-button--primary" type="submit" disabled={busy || !canInvite}>发送邀请</button>
      </form>
    </section>
  );
}

function numberValue(value: unknown): number {
  const next = Number(value ?? 0);
  return Number.isFinite(next) ? next : 0;
}

function recordValue(row: Row, key: string): Row {
  const value = row[key];
  return value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
}

function countLine(row: Row, key: string): string {
  return numberValue(row[key]).toLocaleString("en-US");
}

export function RbacStatusCard({
  status,
  loading,
  error,
  onReload,
}: {
  status: Row;
  loading: boolean;
  error: string;
  onReload: () => void;
}) {
  const staff = recordValue(status, "staff");
  const access = recordValue(status, "effective_access");
  const inviteTokens = recordValue(status, "invite_tokens");
  const permissions = recordValue(status, "active_vkpi_permissions");
  const gaps = Array.isArray(status.gaps) ? status.gaps.map(String) : [];
  const writeDb = String(Boolean(status.write_db));
  const providerCalls = String(Boolean(status.provider_calls));
  return (
    <section className="vkpi-card vkpi-action-card">
      <div className="vkpi-table-card__header">
        <div><h2>V-KPI 权限状态</h2><span>{loading ? "读取中" : `${countLine(staff, "active")} active`}</span></div>
        <button className="vkpi-button" type="button" disabled={loading} onClick={onReload}>{loading ? "刷新中" : "刷新"}</button>
      </div>
      <InfoBlock label="Owner / Admin" value={`${countLine(staff, "active_owners")} / ${countLine(access, "active_can_admin_vkpi")}`} />
      <InfoBlock label="V-KPI read / write" value={`${countLine(access, "active_can_read_vkpi")} / ${countLine(access, "active_can_write_vkpi")}`} />
      <InfoBlock label="Invite tokens" value={`${countLine(inviteTokens, "active")} active · ${countLine(inviteTokens, "expired_unused")} expired`} />
      <InfoBlock label="RBAC write / provider" value={`${writeDb} / ${providerCalls}`} />
      <div className="vkpi-table-wrap">
        <table className="vkpi-table">
          <thead>
            <tr>
              <th>Permission</th>
              <th>Active Staff</th>
            </tr>
          </thead>
          <tbody>
            {["admin", "write", "read", "none"].map((key) => (
              <tr key={key}>
                <td>{key}</td>
                <td>{countLine(permissions, key)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {gaps.length ? (
        <div className="vkpi-inline-message is-error">{gaps.join(" / ")}</div>
      ) : (
        <div className="vkpi-inline-message">RBAC gaps: none</div>
      )}
      {error ? <div className="vkpi-inline-message is-error">{error}</div> : null}
    </section>
  );
}

export function ProductCostFormCard({
  costSku,
  costProductName,
  unitCostUsd,
  costNote,
  selectedProduct,
  busy,
  canUpsert,
  onCostSkuChange,
  onCostProductNameChange,
  onUnitCostUsdChange,
  onCostNoteChange,
  onSubmit,
}: {
  costSku: string;
  costProductName: string;
  unitCostUsd: string;
  costNote: string;
  selectedProduct?: VkpiProductCatalogItem | null;
  busy: boolean;
  canUpsert: boolean;
  onCostSkuChange: (value: string) => void;
  onCostProductNameChange: (value: string) => void;
  onUnitCostUsdChange: (value: string) => void;
  onCostNoteChange: (value: string) => void;
  onSubmit: React.FormEventHandler;
}) {
  return (
    <section className="vkpi-card vkpi-action-card">
      <CardHeader title="SKU 录入" />
      <form className="vkpi-form-stack" onSubmit={onSubmit}>
        <input value={costSku} onChange={(event) => onCostSkuChange(event.target.value)} placeholder="产品 SKU，例如 AF35-F1.2" />
        <input value={costProductName} onChange={(event) => onCostProductNameChange(event.target.value)} placeholder="产品名称，例如 AF 35mm F1.2" />
        <input value={unitCostUsd} onChange={(event) => onUnitCostUsdChange(event.target.value)} placeholder="镜头内部成本 USD" inputMode="decimal" />
        <input value={costNote} onChange={(event) => onCostNoteChange(event.target.value)} placeholder="备注（可选）" />
        {selectedProduct ? (
          <div className="vkpi-selected-sku-price">
            <span>当前 SKU</span>
            <strong>{selectedProduct.sku}</strong>
            <em>{selectedProduct.priceUsd == null ? "未定价" : `$${selectedProduct.priceUsd.toLocaleString("en-US")}`}</em>
          </div>
        ) : null}
        <button className="vkpi-button vkpi-button--primary" type="submit" disabled={busy || !canUpsert}>保存 SKU</button>
      </form>
    </section>
  );
}

const PRODUCT_GROUPS = [
  { key: "lens", title: "镜头", categories: ["Lens", "Cine Lens"] },
  { key: "lighting", title: "闪光灯", categories: ["Lighting/Flash"] },
  { key: "adapter", title: "转接环", categories: ["Adapter"] },
];

function productLabel(product: VkpiProductCatalogItem) {
  return product.marketingName || product.modelName || product.sku;
}

export function ProductCatalogPreviewCard({
  products,
  loading,
  error,
  query,
  selectedSku,
  onQueryChange,
  onSelectProduct,
}: {
  products: VkpiProductCatalogItem[];
  loading: boolean;
  error: string;
  query: string;
  selectedSku?: string;
  onQueryChange: (value: string) => void;
  onSelectProduct: (product: VkpiProductCatalogItem) => void;
}) {
  const priceLabel = (value: number | null | undefined) => (
    value === null || value === undefined ? "未定价" : `$${value.toLocaleString("en-US")}`
  );
  const needle = query.trim().toLowerCase();
  const visibleProducts = needle
    ? products.filter((product) => [
      product.sku,
      product.modelName,
      product.marketingName,
      product.categoryMain,
      product.categoryDetail,
    ].filter(Boolean).join(" ").toLowerCase().includes(needle))
    : products;
  return (
    <section className="vkpi-card vkpi-action-card vkpi-product-catalog-card">
      <div className="vkpi-table-card__header">
        <div><h2>现有产品</h2><span>{loading ? "读取中" : `${visibleProducts.length} / ${products.length} 个 SKU`}</span></div>
      </div>
      <input
        className="vkpi-product-search-input"
        value={query}
        onChange={(event) => onQueryChange(event.target.value)}
        placeholder="搜索 SKU / 产品名 / 分类"
      />
      {error ? <div className="vkpi-inline-message is-error">{error}</div> : null}
      <div className="vkpi-product-catalog-groups">
        {PRODUCT_GROUPS.map((group) => {
          const rows = visibleProducts.filter((product) => group.categories.includes(product.categoryMain));
          return (
            <section className="vkpi-product-catalog-group" key={group.key}>
              <header><strong>{group.title}</strong><span>{rows.length}</span></header>
              <div className="vkpi-product-catalog-list">
                {rows.length ? rows.map((product) => (
                  <button
                    className={`vkpi-product-catalog-row ${selectedSku === product.sku ? "is-selected" : ""}`}
                    key={product.sku}
                    type="button"
                    onClick={() => onSelectProduct(product)}
                  >
                    <strong>{productLabel(product)}</strong>
                    <span>{product.sku}</span>
                    <em>{priceLabel(product.priceUsd)} · {product.status || "unknown"}</em>
                  </button>
                )) : <div className="vkpi-empty-panel">{loading ? "正在读取产品目录" : "暂无产品"}</div>}
              </div>
            </section>
          );
        })}
      </div>
    </section>
  );
}

export function SystemSummaryCards({
  controlSummary,
  syncPolicy,
  youtubeKpi,
  claudeConfigured,
  claudeStatus,
}: {
  controlSummary: Row;
  syncPolicy: Row;
  youtubeKpi: Row;
  claudeConfigured: boolean;
  claudeStatus: string;
}) {
  return (
    <>
      <section className="vkpi-card vkpi-action-card">
        <CardHeader title="总控状态" />
        <InfoBlock label="高成本开关" value={`${String(controlSummary.enabled_high_cost_controls ?? 0)} 个开启`} />
        <InfoBlock label="风险状态" value={String(controlSummary.risk_level || "controlled")} />
        <InfoBlock label="本月预算剩余" value={`$${String(controlSummary.budget_remaining_usd ?? 0)}`} />
      </section>
      <section className="vkpi-card vkpi-action-card">
        <CardHeader title="08:00 同步策略" />
        <InfoBlock label="时间" value={`${String(syncPolicy.daily_sync_time || "08:00")} ${String(syncPolicy.timezone || "Asia/Shanghai")}`} />
        <InfoBlock label="每人候选" value={`${String(syncPolicy.candidate_limit_per_staff || 100)} 条`} />
        <InfoBlock label="候选限制" value={syncPolicy.only_uncontacted_kols ? "只推未联系 KOL" : "未限制"} />
      </section>
      <section className="vkpi-card vkpi-action-card">
        <CardHeader title="YouTube KPI 预留" />
        <InfoBlock label="预留槽" value={youtubeKpi.reserved ? "已预留" : "未预留"} />
        <InfoBlock label="平台抓取" value={youtubeKpi.platform_enabled ? "开启" : "关闭"} />
        <InfoBlock label="API 状态" value={String(youtubeKpi.last_test_status || "not_configured")} />
      </section>
      <section className="vkpi-card vkpi-action-card">
        <CardHeader title="AI 周报" />
        <InfoBlock label="Claude API" value={claudeConfigured ? claudeStatus : "未配置"} />
        <InfoBlock label="失败处理" value="自动 fallback 模板" />
        <InfoBlock label="记录方式" value="写入 LLM 调用审计" />
      </section>
    </>
  );
}
