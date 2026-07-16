import React from "react";
import { ModuleCard } from "./MarketVoicePage.modules";
import { MODULE_SOURCES } from "./ShopifyBoardPage.modules";
import { ShopifyConnectPage } from "../ShopifyConnectPage";
import { LegacyShopifyConnect } from "../../pages/ShopifyHubPage.Parts";
import { getShopifyStatus, type ShopifyProviderStatus } from "../../../../domains/attribution";
import { saveShopifyCreds } from "../../../../services/vkpi/shopifyHub-api";

// Shopify · 内嵌模块卡头收编包装族(ProjectsBoardPage.embeds / MyKolBoardPage.embeds 同一手法)。
//   手法 = 非侵入收编:**绝不改 ShopifyConnectPage / ShopifyHubPage.Parts 旧组件文件**,
//   只用包装容器的 Tailwind 任意变体选择器隐藏页级门面(标题/介绍文案)、压平页级布局;
//   功能控件(刷新钮/EnvRow/Webhook 复制/测试同步/同步历史表/凭据表单)全部保留可见。
//   ConnectEmbed     — cockpit/ShopifyConnectPage 整页零改动内嵌:连接状态 + 环境变量
//                      向导 + Webhook 注册 + 测试同步 + 同步历史(vkpi_shopify_sync_runs)。
//   LegacyCredsEmbed — 旧 Hub 页「自建 Shopify(旧·待退役)」可折叠块零改动内嵌:
//                      容器状态/处理器从旧 ShopifyHubPage 原样搬家(保存加密凭据 +
//                      状态明细),组件本体 LegacyShopifyConnect 一字不动。
//   SrcChip rows = MODULE_SOURCES 唯一注册表(真实表名与页主体对齐,零第二份)。
// 红线:绝不写 fit 分 / rule_v0;包装层颜色全 token 零写死色(旧组件内部配色不动,
//   属旧文件职责);凭据保存后清空输入绝不回显(旧逻辑原样);数据缺席=诚实缺席。

const src = (key: string) => MODULE_SOURCES[key] || { label: key, rows: [] as Array<[string, string]> };

/* ---- 收编容器选择器(Projects embeds 同套:vkpi-embed 类 + data-embed 属性抬特异性) ---- */
const EMBED = "vkpi-embed";

// ShopifyConnectPage:页根 max-w-4xl + p-6/p-8 压平进卡;页头左块(图标 + 标题 +
// 介绍文案)按「门面零介绍文案」隐藏,右侧「刷新」钮保留可用。
const CONN_TRIM = [
  "[&.vkpi-embed[data-embed]>div]:!max-w-none [&.vkpi-embed[data-embed]>div]:!p-0",
  "[&.vkpi-embed[data-embed]>div>div:first-child>div:first-child]:hidden",
].join(" ");

/* ============ 店铺连接向导(整页零改动内嵌) ============ */
export function ConnectEmbed({ apiToken, onOpenSrc }: { apiToken: string; onOpenSrc?: () => void }) {
  return (
    <ModuleCard title="店铺连接向导" srcLabel={src("connS").label} srcRows={src("connS").rows} onOpenSrc={onOpenSrc}>
      <div data-embed="conn" className={`${EMBED} ${CONN_TRIM}`}>
        <ShopifyConnectPage apiToken={apiToken} />
      </div>
    </ModuleCard>
  );
}

/* ============ 店铺凭据(旧·自建直连)—— 容器状态原样搬家,组件零改动 ============ */
// 状态/处理器 = 旧 ShopifyHubPage Region① 同款(loadStatus / onSaveCreds 逻辑逐行保留):
// 保存成功后清空密钥输入(绝不回显),随后刷新状态;错误原样透出不吞。
export function LegacyCredsEmbed({ apiToken, onOpenSrc }: { apiToken: string; onOpenSrc?: () => void }) {
  const [legacyShopifyOpen, setLegacyShopifyOpen] = React.useState(false);
  const [storeDomain, setStoreDomain] = React.useState("");
  const [accessToken, setAccessToken] = React.useState("");
  const [webhookSecret, setWebhookSecret] = React.useState("");
  const [savingCreds, setSavingCreds] = React.useState(false);
  const [credsMsg, setCredsMsg] = React.useState("");
  const [credsErr, setCredsErr] = React.useState("");
  const [status, setStatus] = React.useState<ShopifyProviderStatus | null>(null);
  const [statusLoading, setStatusLoading] = React.useState(false);
  const [statusErr, setStatusErr] = React.useState("");

  const loadStatus = React.useCallback(async () => {
    if (!apiToken) {
      setStatusErr("缺少 API token，无法读取 Shopify 连接状态。");
      return;
    }
    setStatusLoading(true);
    setStatusErr("");
    try {
      const res = await getShopifyStatus(apiToken, 10);
      setStatus(res);
    } catch (err) {
      setStatusErr(err instanceof Error ? err.message : "读取 Shopify 状态失败");
    } finally {
      setStatusLoading(false);
    }
  }, [apiToken]);

  React.useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  const onSaveCreds = React.useCallback(async () => {
    if (!apiToken) {
      setCredsErr("缺少 API token，无法保存凭据。");
      return;
    }
    setSavingCreds(true);
    setCredsErr("");
    setCredsMsg("");
    try {
      const res = await saveShopifyCreds(apiToken, { storeDomain, accessToken, webhookSecret });
      setCredsMsg(res?.message || "凭据已加密保存。");
      // 保存成功后清空密钥输入(旧逻辑原样,绝不回显)。
      setAccessToken("");
      setWebhookSecret("");
      await loadStatus();
    } catch (err) {
      setCredsErr(err instanceof Error ? err.message : "保存凭据失败");
    } finally {
      setSavingCreds(false);
    }
  }, [apiToken, storeDomain, accessToken, webhookSecret, loadStatus]);

  const configured = ["configured", "connected"].includes(String(status?.provider_status || ""));
  const env = {
    SHOPIFY_SHOP_DOMAIN: Boolean(status?.credential_fields?.shop_domain),
    SHOPIFY_ADMIN_ACCESS_TOKEN: Boolean(status?.credential_fields?.access_token),
    SHOPIFY_WEBHOOK_SECRET: Boolean(status?.credential_fields?.webhook_secret),
  };

  return (
    <ModuleCard
      title="店铺凭据(旧)"
      cnt={configured ? "已配置" : undefined}
      srcLabel={src("credsS").label}
      srcRows={src("credsS").rows}
      onOpenSrc={onOpenSrc}
    >
      <div data-embed="creds" className={EMBED}>
        <LegacyShopifyConnect
          legacyShopifyOpen={legacyShopifyOpen}
          setLegacyShopifyOpen={setLegacyShopifyOpen}
          configured={configured}
          storeDomain={storeDomain}
          setStoreDomain={setStoreDomain}
          accessToken={accessToken}
          setAccessToken={setAccessToken}
          webhookSecret={webhookSecret}
          setWebhookSecret={setWebhookSecret}
          savingCreds={savingCreds}
          credsErr={credsErr}
          credsMsg={credsMsg}
          onSaveCreds={onSaveCreds}
          statusLoading={statusLoading}
          loadStatus={loadStatus}
          statusErr={statusErr}
          status={status}
          env={env}
        />
      </div>
    </ModuleCard>
  );
}
