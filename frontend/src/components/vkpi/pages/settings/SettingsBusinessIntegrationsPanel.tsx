import React from "react";
import {
  getBusinessIntegrationsStatus,
  type BusinessIntegrationCard,
  type BusinessIntegrationOperatorStatus,
  type BusinessIntegrationState,
} from "../../../../services/vkpi/settings-api";
import { ShopifyCompatibilityConnectWizard } from "./ShopifyCompatibilityConnectWizard";

const STATE_LABEL: Record<BusinessIntegrationState, string> = {
  connected: "已验证",
  pending: "待配置",
  not_configured: "待配置",
  error: "异常",
};

const OPERATOR_STATE_LABEL: Record<BusinessIntegrationOperatorStatus, string> = {
  verified: "已验证",
  awaiting_authorization: "待授权",
  awaiting_configuration: "待配置",
  error: "异常",
};

function operatorStateLabel(card: BusinessIntegrationCard): string {
  if (card.operator_status && OPERATOR_STATE_LABEL[card.operator_status]) {
    return OPERATOR_STATE_LABEL[card.operator_status];
  }
  if (card.key === "shopify" && card.state === "not_configured") return "待授权";
  return STATE_LABEL[card.state] || card.state;
}

const QUALITY_LABEL: Record<string, string> = {
  empty: "无真实数据",
  unverified: "数据未核验",
  partial: "部分证据",
  real: "真实证据",
};

const ACTION_LABEL: Record<string, string> = {
  shopify: "打开 Shopify 授权接入",
  dealers: "打开 Dealers",
  inventory: "打开库存管理",
  costs: "打开 SKU 成本",
  attribution: "打开销售归因",
  r2: "查看 R2 安全诊断",
  outcomes: "打开人工结果复盘",
};

const EVIDENCE_FIELDS: Record<string, Array<[string, string]>> = {
  shopify: [["orders", "订单"], ["native_webhook_snapshots", "HMAC 快照"], ["successful_sync_runs", "成功同步"]],
  dealers: [
    ["address_complete", "地址"],
    ["contact_complete", "联系方式"],
    ["website_complete", "官网"],
    ["map_visible", "地图可见"],
  ],
  inventory: [["rows", "记录"], ["verified_non_sample", "已核验"]],
  costs: [["active_reference_rows", "参考成本"], ["verified_rows", "已核验"]],
  attribution: [["shopify_orders", "订单"], ["confirmed_rows", "确认归因"]],
  r2: [["required_env_configured", "密钥项"], ["historical_cached_assets", "历史缓存"]],
  outcomes: [["evidence_backed_finalized_outcomes", "人工结果"], ["verified_actual_evals", "Actual 评估"]],
};

function numberText(value: unknown): string {
  const n = Number(value);
  return Number.isFinite(n) ? n.toLocaleString("en-US") : "—";
}

export function SettingsBusinessIntegrationsPanel({
  apiToken,
  onOpenArea,
  onOpenCostModule,
}: {
  apiToken?: string;
  onOpenArea?: (area: "shopify" | "dealers" | "events" | "gtmCommand") => void;
  onOpenCostModule?: () => void;
}) {
  const [cards, setCards] = React.useState<BusinessIntegrationCard[]>([]);
  const [generatedAt, setGeneratedAt] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  const [expandedDiagnostic, setExpandedDiagnostic] = React.useState("");

  const load = React.useCallback(async (throwOnError = false) => {
    if (!apiToken) {
      setError("缺少 API token，无法读取真实业务接口状态。");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const payload = await getBusinessIntegrationsStatus(apiToken);
      setCards(Array.isArray(payload?.integrations) ? payload.integrations : []);
      setGeneratedAt(String(payload?.generated_at || ""));
    } catch (err) {
      setError(err instanceof Error ? err.message : "真实业务接口状态读取失败");
      if (throwOnError) throw err;
    } finally {
      setLoading(false);
    }
  }, [apiToken]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const openCard = (key: string) => {
    if (key === "r2" || key === "shopify") {
      setExpandedDiagnostic((current) => current === key ? "" : key);
      return;
    }
    if (key === "costs") {
      onOpenCostModule?.();
      return;
    }
    if (key === "dealers") {
      onOpenArea?.(key);
      return;
    }
    if (key === "inventory") {
      onOpenArea?.("events");
      return;
    }
    if (key === "attribution") onOpenArea?.("shopify");
    if (key === "outcomes") onOpenArea?.("gtmCommand");
  };

  return (
    <div className="vkpi-settings-rules">
      <div className="vkpi-settings-check-row vkpi-settings-check-row--panel">
        <span className="vkpi-settings-hint">
          状态只认真实成功证据；保存凭据、公开页面、目录 SKU、历史缓存都不会自动变成“已连接”。
        </span>
        <button className="vkpi-mini-button" type="button" onClick={() => void load()} disabled={loading || !apiToken}>
          {loading ? "读取中…" : "刷新真实状态"}
        </button>
      </div>
      {error ? <div className="vkpi-inline-message is-error">{error}</div> : null}
      <div className="vkpi-settings-card-grid">
        {cards.map((card) => {
          const metrics = EVIDENCE_FIELDS[card.key] || [];
          const actionAvailable = card.key === "r2" || card.key === "shopify" || (card.key === "costs" ? Boolean(onOpenCostModule) : Boolean(onOpenArea));
          return (
            <article className={`vkpi-settings-toggle-card ${card.state === "connected" ? "is-on" : "is-off"}`} key={card.key}>
              <header>
                <strong>{card.title}</strong>
                <span>{operatorStateLabel(card)}</span>
              </header>
              <p>{card.summary}</p>
              <div className="vkpi-settings-meta-grid">
                <small>数据质量<b>{QUALITY_LABEL[card.data_quality] || card.data_quality}</b></small>
                {metrics.map(([field, label]) => (
                  <small key={field}>{label}<b>{numberText(card.evidence?.[field])}</b></small>
                ))}
              </div>
              <p className="vkpi-settings-hint is-muted">下一步：{card.next_action}</p>
              {actionAvailable ? (
                <button
                  className="vkpi-mini-button"
                  type="button"
                  onClick={() => openCard(card.key)}
                  aria-expanded={card.key === "r2" || card.key === "shopify" ? expandedDiagnostic === card.key : undefined}
                  aria-controls={card.key === "r2" ? "vkpi-r2-safe-diagnostic" : card.key === "shopify" ? "vkpi-shopify-compatibility-connect" : undefined}
                >
                  {ACTION_LABEL[card.key] || "打开入口"}
                </button>
              ) : null}
              {card.key === "shopify" && expandedDiagnostic === "shopify" ? (
                <ShopifyCompatibilityConnectWizard
                  apiToken={apiToken}
                  onOpenDataPage={onOpenArea ? () => onOpenArea("shopify") : undefined}
                  onRefreshStatus={() => load(true)}
                />
              ) : null}
              {card.key === "r2" && expandedDiagnostic === "r2" ? (
                <div id="vkpi-r2-safe-diagnostic" role="region" aria-label="R2 安全诊断与配置说明" className="vkpi-inline-message">
                  <strong>R2 安全诊断（只读）</strong>
                  <p>当前仍以“{operatorStateLabel(card)}”为准；此页面不会保存或回显 Access Key、Secret 或 endpoint。</p>
                  <p>生产配置由部署密钥管理完成。授权到位后先运行一次上传 → 回读 → SHA 校验 canary，成功证据入账后再刷新本卡，才允许显示“已验证连接”。</p>
                  <small className="text-muted">当前证据：密钥项 {numberText(card.evidence?.required_env_configured)} · 历史缓存 {numberText(card.evidence?.historical_cached_assets)}。历史文件不等于当前连接可用。</small>
                </div>
              ) : null}
              <em className="text-muted" style={{ fontSize: 10, lineHeight: 1.45 }}>来源：{card.source}</em>
            </article>
          );
        })}
      </div>
      {!loading && !error && cards.length === 0 ? <div className="vkpi-empty-state">暂无可用状态。</div> : null}
      <p className="vkpi-settings-hint is-muted">
        口径：descriptive_only · 不回显任何密钥{generatedAt ? ` · 生成 ${generatedAt}` : ""}
      </p>
    </div>
  );
}
