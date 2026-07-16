import React from "react";

import {
  connectShopifyClientCredentials,
  probeShopifyConnection,
  registerShopifyWebhooks,
  saveShopifyConnectionCredentials,
} from "../../../../services/vkpi/settings-api";

type ShopifyPhaseKey = "authorization" | "probe" | "webhooks" | "commit";
type ShopifyPhaseState = "idle" | "running" | "success" | "error";

interface ShopifyPhaseReceipt {
  state: ShopifyPhaseState;
  detail: string;
}

const EMPTY_PHASES: Record<ShopifyPhaseKey, ShopifyPhaseReceipt> = {
  authorization: { state: "idle", detail: "等待 Token 授权" },
  probe: { state: "idle", detail: "等待真实 Admin API 探测" },
  webhooks: { state: "idle", detail: "等待注册订单与退款 Webhook" },
  commit: { state: "idle", detail: "等待原子启用新配置" },
};

const PHASE_LABEL: Record<ShopifyPhaseKey, string> = {
  authorization: "1. Token 授权",
  probe: "2. 真实探测",
  webhooks: "3. Webhook 注册",
  commit: "4. 原子启用",
};

const PHASE_STATE_LABEL: Record<ShopifyPhaseState, string> = {
  idle: "待执行",
  running: "执行中",
  success: "已通过",
  error: "失败",
};

const SAFE_REASON: Record<string, string> = {
  not_configured: "凭据未完整保存",
  client_credentials_not_configured: "Client Credentials 未完整保存",
  client_credentials_missing: "Client ID 或 Client Secret 缺失",
  invalid_shop_domain: "Shopify 店铺域名无效",
  provider_rejected_credentials: "Shopify 拒绝了当前凭据",
  provider_token_payload_invalid: "Shopify Token 响应不完整",
  token_refresh_internal_error: "Token 刷新内部失败",
  credential_persist_failed: "新凭据验证通过，但未能安全替换当前配置",
  provider_graphql_error: "Shopify 返回了 GraphQL 错误",
  provider_unreachable: "当前无法连接 Shopify",
  provider_backoff_active: "Shopify 暂时不可达，服务端正在受控退避",
  refresh_in_progress: "另一进程正在刷新 Token，请稍后重试",
  provider_probe_payload_invalid: "Shopify 店铺身份返回不完整",
  public_base_url_missing: "生产 Webhook 公网地址尚未配置",
  provider_webhook_error: "Webhook 注册未全部成功",
  provider_webhook_rejected: "Shopify 拒绝了部分 Webhook",
};

const SHOPIFY_DOMAIN_RE = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.myshopify\.com$/;

export function ShopifyCompatibilityConnectWizard({
  apiToken,
  onOpenDataPage,
  onRefreshStatus,
}: {
  apiToken?: string;
  onOpenDataPage?: () => void;
  onRefreshStatus: () => Promise<void>;
}) {
  const [shopDomain, setShopDomain] = React.useState("");
  const [clientId, setClientId] = React.useState("");
  const [clientSecret, setClientSecret] = React.useState("");
  const [accessToken, setAccessToken] = React.useState("");
  const [webhookSecret, setWebhookSecret] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  const [completionMode, setCompletionMode] = React.useState<"" | "formal" | "legacy">("");
  const [phases, setPhases] = React.useState(EMPTY_PHASES);

  const updatePhase = (key: ShopifyPhaseKey, state: ShopifyPhaseState, detail: string) => {
    setPhases((current) => ({ ...current, [key]: { state, detail } }));
  };

  const validateDomain = () => {
    const normalizedDomain = shopDomain.trim().toLowerCase();
    if (!SHOPIFY_DOMAIN_RE.test(normalizedDomain)) {
      setError("店铺域名必须是纯 hostname，例如 demo.myshopify.com；不要填写协议、路径、端口或账号信息。");
      return "";
    }
    return normalizedDomain;
  };

  const runLegacyVerification = async (
    authorize: () => Promise<{ ok?: boolean; reason?: string }>,
  ) => {
    setBusy(true);
    setError("");
    setCompletionMode("");
    setPhases(EMPTY_PHASES);
    let currentPhase: ShopifyPhaseKey = "authorization";
    let publicFailure = "Shopify 接入失败，请按阶段提示复核配置。";

    try {
      updatePhase("authorization", "running", "正在服务端换取 Token，不会回显密钥");
      const authorized = await authorize();
      if (authorized?.ok !== true) {
        const safeReason = SAFE_REASON[String(authorized?.reason || "")] || "Token 授权未通过";
        publicFailure = `${safeReason}；未进入 Shopify 探测阶段。`;
        throw new Error("authorization_failed");
      }
      setClientSecret("");
      setAccessToken("");
      setWebhookSecret("");
      updatePhase("authorization", "success", "Token 已换取并加密保存；密钥输入已清空");

      currentPhase = "probe";
      updatePhase("probe", "running", "正在请求 Shopify 返回真实店铺身份");
      const probe = await probeShopifyConnection(apiToken!);
      if (probe?.ok !== true || probe?.status !== "connected") {
        const safeReason = SAFE_REASON[String(probe?.reason || "")] || "真实探测未通过";
        publicFailure = `${safeReason}；不会继续注册 Webhook。`;
        throw new Error("probe_failed");
      }
      updatePhase("probe", "success", "Shopify 已返回有效店铺身份");

      currentPhase = "webhooks";
      updatePhase("webhooks", "running", "正在注册订单、更新与退款 Webhook");
      const registered = await registerShopifyWebhooks(apiToken!);
      if (registered?.ok !== true) {
        const safeReason = SAFE_REASON[String(registered?.reason || "")] || "Webhook 注册未全部成功";
        publicFailure = `${safeReason}；当前不会标记业务就绪。`;
        throw new Error("webhooks_failed");
      }
      updatePhase("webhooks", "success", "订单、更新与退款 Webhook 已注册");
      updatePhase("commit", "idle", "兼容模式为独立三步，不提供候选配置原子切换");
      setCompletionMode("legacy");
      try {
        await onRefreshStatus();
      } catch {
        setError("兼容模式三阶段已完成，但状态卡刷新失败；请手动刷新状态。");
      }
    } catch {
      updatePhase(currentPhase, "error", publicFailure);
      setError(publicFailure);
    } finally {
      setBusy(false);
    }
  };

  const connectFormal = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!apiToken) return setError("缺少 API token，无法配置 Shopify。");
    const domain = validateDomain();
    if (!domain) return;
    if (!clientId.trim() || !clientSecret.trim()) {
      return setError("正式接入需要 Client ID 与 Client Secret。");
    }
    const submittedSecret = clientSecret.trim();
    setClientSecret("");
    setBusy(true);
    setError("");
    setCompletionMode("");
    setPhases({
      authorization: { state: "running", detail: "后端正在换取候选 Token" },
      probe: { state: "running", detail: "候选 Token 成功后探测店铺身份" },
      webhooks: { state: "running", detail: "身份通过后注册全部 Webhook" },
      commit: { state: "running", detail: "前三步全部成功后才替换当前配置" },
    });
    try {
      const result = await connectShopifyClientCredentials(apiToken, {
        shop_domain: domain,
        client_id: clientId.trim(),
        client_secret: submittedSecret,
      });
      const receipt = result?.phases || {};
      const successDetail: Record<ShopifyPhaseKey, string> = {
        authorization: "候选 Token 已验证",
        probe: "Shopify 已返回有效店铺身份",
        webhooks: "全部必需 Webhook 已注册",
        commit: "新配置已原子启用",
      };
      let failedPhase: ShopifyPhaseKey | "" = "";
      (Object.keys(PHASE_LABEL) as ShopifyPhaseKey[]).forEach((phase) => {
        const stage = receipt[phase];
        const stageStatus = String(stage?.status || "pending");
        if (stageStatus === "error") failedPhase = phase;
        const safeReason = SAFE_REASON[String(stage?.reason || "")];
        updatePhase(
          phase,
          stageStatus === "success" ? "success" : stageStatus === "error" ? "error" : "idle",
          safeReason || (stageStatus === "success" ? successDetail[phase] : "未执行；旧配置保持不变"),
        );
      });
      if (result?.ok !== true || result?.status !== "connected") {
        const reason = SAFE_REASON[String(result?.reason || "")] || "正式接入未全部通过";
        setError(`${reason}；旧配置保持不变${failedPhase ? `（失败阶段：${PHASE_LABEL[failedPhase]}）` : ""}。`);
        return;
      }
      setCompletionMode("formal");
      try {
        await onRefreshStatus();
      } catch {
        setError("新配置已原子启用，但状态卡刷新失败；请手动刷新状态。");
      }
    } catch {
      setError("Shopify 正式接入请求失败；旧配置保持不变。");
    } finally {
      setBusy(false);
    }
  };

  const connectLegacy = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!apiToken) return setError("缺少 API token，无法保存 Shopify 凭据。");
    const domain = validateDomain();
    if (!domain) return;
    if (!accessToken.trim() || !webhookSecret.trim()) {
      return setError("兼容接入需要 Admin API Access Token 与 Webhook Signing Secret。");
    }
    const submittedToken = accessToken.trim();
    const submittedWebhookSecret = webhookSecret.trim();
    setAccessToken("");
    setWebhookSecret("");
    await runLegacyVerification(() => saveShopifyConnectionCredentials(apiToken, {
      shop_domain: domain,
      access_token: submittedToken,
      webhook_secret: submittedWebhookSecret,
    }));
  };

  return (
    <div id="vkpi-shopify-compatibility-connect" role="region" aria-label="Shopify 授权接入向导" className="vkpi-inline-message">
      <strong>Shopify 组织应用接入 · 候选验证后原子启用</strong>
      <p>默认使用 Client Credentials：后端依次完成 Token、店铺身份、全部 Webhook，只有四阶段全部成功才替换 last-known-good；任何失败都保留旧配置。</p>
      <form className="vkpi-settings-control-form" onSubmit={connectFormal}>
        <div className="vkpi-settings-inline-fields">
          <label style={{ gridColumn: "1 / -1" }}>店铺域名<input value={shopDomain} onChange={(event) => setShopDomain(event.target.value)} placeholder="demo.myshopify.com" autoComplete="off" spellCheck={false} disabled={busy} /></label>
          <label>Client ID<input value={clientId} onChange={(event) => setClientId(event.target.value)} autoComplete="off" spellCheck={false} disabled={busy} /></label>
          <label>Client Secret<input type="password" value={clientSecret} onChange={(event) => setClientSecret(event.target.value)} autoComplete="new-password" spellCheck={false} disabled={busy} /></label>
        </div>
        <button className="vkpi-mini-button" type="submit" disabled={busy || !apiToken}>{busy ? "验证中…" : "一步验证并原子启用"}</button>
      </form>

      <details>
        <summary>高级：旧 Access Token 兼容模式</summary>
        <p>仅供既有长期 Access Token 迁移；这是独立三阶段兼容流程，不保证候选配置原子切换。</p>
        <form className="vkpi-settings-control-form" onSubmit={connectLegacy}>
          <div className="vkpi-settings-inline-fields">
            <label>Admin API Access Token<input type="password" value={accessToken} onChange={(event) => setAccessToken(event.target.value)} autoComplete="new-password" spellCheck={false} disabled={busy} /></label>
            <label>Webhook Signing Secret<input type="password" value={webhookSecret} onChange={(event) => setWebhookSecret(event.target.value)} autoComplete="new-password" spellCheck={false} disabled={busy} /></label>
          </div>
          <button className="vkpi-mini-button" type="submit" disabled={busy || !apiToken}>{busy ? "验证中…" : "使用旧 Token 验证"}</button>
        </form>
      </details>

      <div className="vkpi-detail-stack" aria-label="Shopify 接入阶段" aria-live="polite">
        {(Object.keys(PHASE_LABEL) as ShopifyPhaseKey[]).map((phase) => (
          <div className="vkpi-info-row" data-state={phases[phase].state} key={phase}>
            <span>{PHASE_LABEL[phase]} · {PHASE_STATE_LABEL[phases[phase].state]}</span>
            <strong>{phases[phase].detail}</strong>
          </div>
        ))}
      </div>
      {error ? <div className="vkpi-inline-message is-error">{error}</div> : null}
      {completionMode === "formal" ? <div className="vkpi-inline-message">四阶段技术接入已原子完成；仍需真实订单或成功同步证据，当前不会仅凭配置标记业务就绪。</div> : null}
      {completionMode === "legacy" ? <div className="vkpi-inline-message">兼容模式三阶段验证已完成；该路径不是候选配置原子切换，仍建议迁移到正式 Client Credentials。</div> : null}
      {onOpenDataPage ? <button className="vkpi-mini-button" type="button" onClick={onOpenDataPage}>打开 Shopify 数据页</button> : null}
    </div>
  );
}
