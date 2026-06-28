import React from "react";
import type { VkpiStaffActivationLinkResponse, VkpiStaffInviteCapabilities } from "../../../../domains/settings";
import type { VkpiProductCatalogItem } from "../../vkpiTypes";
import { CardHeader } from "../../shared/CardHeader";
import { InfoBlock } from "../../shared/InfoBlock";
import { ProviderStatusTable } from "../../tables/ProviderStatusTable";
import { getHealthTrust, type VkpiHealthTrust } from "../../../../services/vkpi/settings-api";
import { frontendBuildInfo } from "../../../../lib/buildInfo";
import { STAFF_ASSIGNABLE_PERMISSION_TEMPLATES, vkpiPermissionFromTemplate } from "./staffPermissionTemplates";

type Row = Record<string, unknown>;

function shortSha(value: string | null): string {
  const s = String(value || "").trim();
  return s ? s.slice(0, 8) : "--";
}

// F3 运行态信任卡:GET /health 顶层 trust 字段醒目化。
// ① sha_aligned 绿/红;② worker 离线红字;③ 三 sha 一栏可见 + 迁移号。
// 自取数(复用 settingsApi.getHealthTrust),挂载即拉一次。trust 不可达显「无信号」,绝不编造。
export function RuntimeTrustCard() {
  const [trust, setTrust] = React.useState<VkpiHealthTrust | null>(null);
  const [loaded, setLoaded] = React.useState(false);

  const reload = React.useCallback(async () => {
    setLoaded(false);
    const data = await getHealthTrust(frontendBuildInfo?.gitSha);
    setTrust(data);
    setLoaded(true);
  }, []);

  React.useEffect(() => {
    let alive = true;
    (async () => {
      const data = await getHealthTrust(frontendBuildInfo?.gitSha);
      if (alive) {
        setTrust(data);
        setLoaded(true);
      }
    })();
    return () => { alive = false; };
  }, []);

  const aligned = trust?.sha_aligned ?? null;
  const alignClass = aligned === true ? "is-ok" : aligned === false ? "is-error" : "";
  const alignLabel = !loaded
    ? "读取中"
    : trust == null
      ? "无信号"
      : aligned === true
        ? "版本对齐 ✓"
        : aligned === false
          ? "版本不一致 ✗"
          : "对齐待确认";

  const workerOnline = trust?.worker_online ?? null;
  const workerLabel = !loaded
    ? "读取中"
    : trust == null
      ? "无信号"
      : workerOnline === true
        ? "在线"
        : workerOnline === false
          ? "Worker 离线"
          : "状态未知";

  return (
    <section className="vkpi-card vkpi-action-card">
      <div className="vkpi-table-card__header">
        <div><h2>运行态信任</h2><span>{loaded ? (trust ? "/health trust" : "/health 不可达") : "读取中"}</span></div>
        <button className="vkpi-button" type="button" onClick={() => { void reload(); }}>刷新</button>
      </div>
      <div className={`vkpi-info-block ${alignClass}`}>
        <span>版本对齐 (sha_aligned)</span>
        <strong style={aligned === true ? { color: "#34d399" } : aligned === false ? { color: "#f43f5e" } : undefined}>{alignLabel}</strong>
      </div>
      <div className="vkpi-info-block">
        <span>Worker 状态</span>
        <strong style={workerOnline === false ? { color: "#f43f5e" } : workerOnline === true ? { color: "#34d399" } : undefined}>{workerLabel}</strong>
      </div>
      <InfoBlock label="server sha" value={shortSha(trust?.server_git_sha ?? null)} />
      <InfoBlock label="client sha" value={shortSha(trust?.client_git_sha ?? null)} />
      <InfoBlock label="worker sha" value={shortSha(trust?.worker_sha ?? null)} />
      <InfoBlock label="DB 迁移号 (max)" value={trust?.db_migration_max || "--"} />
    </section>
  );
}

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
  permissionTemplate,
  busy,
  canInvite,
  inviteMode,
  inviteCapabilities,
  inviteCapabilitiesError,
  activationLink,
  activationCopied,
  onEmailChange,
  onNameChange,
  onRoleChange,
  onPermissionChange,
  onPermissionTemplateChange,
  onCopyActivationLink,
  onSubmit,
}: {
  email: string;
  name: string;
  role: string;
  permission: "none" | "read" | "write";
  permissionTemplate: string;
  busy: boolean;
  canInvite: boolean;
  inviteMode: "email" | "manual_link";
  inviteCapabilities?: VkpiStaffInviteCapabilities | null;
  inviteCapabilitiesError?: string;
  activationLink?: VkpiStaffActivationLinkResponse | null;
  activationCopied?: boolean;
  onEmailChange: (value: string) => void;
  onNameChange: (value: string) => void;
  onRoleChange: (value: string) => void;
  onPermissionChange: (value: "none" | "read" | "write") => void;
  onPermissionTemplateChange: (value: string) => void;
  onCopyActivationLink: () => void;
  onSubmit: React.FormEventHandler;
}) {
  const allowedDomains = inviteCapabilities?.allowed_domains?.length
    ? inviteCapabilities.allowed_domains.join(" / ")
    : "viltrox.com";
  const submitLabel = inviteMode === "email" ? "发送邀请" : "生成激活链接";
  return (
    <section className="vkpi-card vkpi-action-card">
      <CardHeader title="授权账户" />
      <form className="vkpi-form-stack" onSubmit={onSubmit}>
        <input value={email} onChange={(event) => onEmailChange(event.target.value)} placeholder="成员邮箱，建议使用 @viltrox.com" />
        <input value={name} onChange={(event) => onNameChange(event.target.value)} placeholder="成员姓名 / 拼音 ID" />
        <select value={role} onChange={(event) => onRoleChange(event.target.value)}><option value="employee">成员 / 运营</option><option value="manager">管理层</option><option value="analyst">数据分析</option><option value="readonly">只读</option></select>
        <div className="vkpi-staff-template-row">
          {STAFF_ASSIGNABLE_PERMISSION_TEMPLATES.map((template) => (
            <button
              className={permissionTemplate === template.key ? "is-active" : ""}
              type="button"
              key={template.key}
              onClick={() => onPermissionTemplateChange(template.key)}
              title={template.detail}
            >
              {template.label}
            </button>
          ))}
        </div>
        <select value={permission} onChange={(event) => onPermissionChange(event.target.value as "none" | "read" | "write")}><option value="write">可操作 Viltrox Marketing</option><option value="read">只读 Viltrox Marketing</option><option value="none">无 Viltrox Marketing 权限</option></select>
        <div className="vkpi-invite-mode-row">
          <span>权限模板</span>
          <em>{STAFF_ASSIGNABLE_PERMISSION_TEMPLATES.find((item) => item.key === permissionTemplate)?.detail || `V-KPI ${vkpiPermissionFromTemplate(permissionTemplate)}`}</em>
        </div>
        <div className="vkpi-invite-mode-row">
          <span>{inviteMode === "email" ? "邮件邀请" : "激活链接"}</span>
          <em>{inviteMode === "email" ? "SMTP 已配置" : `允许域名 ${allowedDomains}`}</em>
        </div>
        {inviteCapabilitiesError ? <div className="vkpi-inline-message is-warn">{inviteCapabilitiesError}</div> : null}
        <button className="vkpi-button vkpi-button--primary" type="submit" disabled={busy || !canInvite}>{busy ? "处理中" : submitLabel}</button>
      </form>
      {activationLink?.activation_url ? (
        <div className="vkpi-activation-link-panel">
          <div>
            <strong>激活链接</strong>
            <span>{activationLink.expires_in_hours || inviteCapabilities?.token_ttl_hours || 48} 小时内有效</span>
          </div>
          <input readOnly value={activationLink.activation_url} onFocus={(event) => event.currentTarget.select()} />
          <button className="vkpi-button" type="button" onClick={onCopyActivationLink}>{activationCopied ? "已复制" : "复制链接"}</button>
        </div>
      ) : null}
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
  const selectedSpecs = selectedProduct?.specs || {};
  const specRows = [
    ["卡口", selectedProduct?.mount || selectedSpecs.lens_mount],
    ["焦段", selectedSpecs.focal_length],
    ["光圈", selectedSpecs.aperture],
    ["视角", selectedSpecs.viewing_angle],
    ["结构", selectedSpecs.lens_elements],
    ["叶片", selectedSpecs.aperture_blades],
    ["最近对焦", selectedSpecs.shooting_distance],
    ["对焦机构", selectedSpecs.focus_mechanism],
    ["对焦马达", selectedSpecs.focus_motor],
    ["对焦方式", selectedSpecs.focus_mode],
    ["放大倍率", selectedSpecs.max_magnification],
    ["尺寸", selectedSpecs.lens_size],
    ["重量", selectedSpecs.weight],
    ["滤镜", selectedSpecs.filter_size],
  ]
    .map(([label, value]) => ({ label: String(label), value: String(value || "").trim() }))
    .filter((row) => row.value);
  const sourceConfidence = Number(selectedProduct?.sourceConfidence || 0);
  return (
    <section className="vkpi-card vkpi-action-card">
      <CardHeader title="SKU 录入" />
      <form className="vkpi-form-stack" onSubmit={onSubmit}>
        <input value={costSku} onChange={(event) => onCostSkuChange(event.target.value)} placeholder="产品 SKU，例如 AF35-F1.2" />
        <input value={costProductName} onChange={(event) => onCostProductNameChange(event.target.value)} placeholder="产品名称，例如 AF 35mm F1.2" />
        <input value={unitCostUsd} onChange={(event) => onUnitCostUsdChange(event.target.value)} placeholder="镜头内部成本 USD" inputMode="decimal" />
        <input value={costNote} onChange={(event) => onCostNoteChange(event.target.value)} placeholder="备注（可选）" />
        {selectedProduct ? (
          <div className="vkpi-selected-product-detail">
            <div className="vkpi-selected-sku-price">
              <span>当前 SKU</span>
              <strong>{selectedProduct.sku}</strong>
              <em>{selectedProduct.priceUsd == null ? "未定价" : `$${selectedProduct.priceUsd.toLocaleString("en-US")}`}</em>
            </div>
            <p>{selectedProduct.modelName || selectedProduct.marketingName || selectedProduct.sku}</p>
            {specRows.length ? (
              <dl>
                {specRows.map((row) => (
                  <div key={row.label}>
                    <dt>{row.label}</dt>
                    <dd>{row.value}</dd>
                  </div>
                ))}
              </dl>
            ) : (
              <small>官网规格未解析；已保留产品链接和基础价格。</small>
            )}
            {selectedProduct.productUrl ? (
              <a href={selectedProduct.productUrl} target="_blank" rel="noreferrer">打开官网产品页</a>
            ) : null}
            {sourceConfidence > 0 && sourceConfidence < 1 ? (
              <small>官网规格未完整结构化，已保留价格、分类、产品链接和可解析字段。</small>
            ) : null}
          </div>
        ) : null}
        <button className="vkpi-button vkpi-button--primary" type="submit" disabled={busy || !canUpsert}>保存 SKU</button>
      </form>
    </section>
  );
}

const PRODUCT_GROUPS = [
  { key: "lens", title: "镜头", categories: ["Lens", "Cine Lens"] },
  { key: "lighting", title: "灯光 / 闪光灯", categories: ["Lighting", "Lighting/Flash"] },
  { key: "adapter", title: "转接 / 配件", categories: ["Adapter", "Macro Extension Tube", "Accessories", "Uv Filter"] },
  { key: "monitor", title: "监视器 / 电池", categories: ["Monitor", "Battery"] },
  { key: "other", title: "其他", categories: ["Product"] },
];

function productLabel(product: VkpiProductCatalogItem) {
  return product.marketingName || product.modelName || product.sku;
}

function productMetaLine(product: VkpiProductCatalogItem) {
  return [
    product.series,
    product.mount,
    product.categoryDetail,
  ].filter(Boolean).join(" · ") || product.status || "unknown";
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
                    <em>{priceLabel(product.priceUsd)} · {productMetaLine(product)}</em>
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
        <InfoBlock label="对话引擎 API" value={claudeConfigured ? claudeStatus : "未配置"} />
        <InfoBlock label="失败处理" value="自动 fallback 模板" />
        <InfoBlock label="记录方式" value="写入 LLM 调用审计" />
      </section>
    </>
  );
}
