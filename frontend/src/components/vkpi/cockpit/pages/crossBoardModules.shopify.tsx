import React from "react";
import { getShopifyGmv, type ShopifyGmvSummary } from "../../../../services/vkpi/shopifyBoard-api";
import { centsToUsd } from "../../../../services/vkpi/projectsBoard-api";
import { EmptyLine, ErrorCard, LoadingLine } from "./MarketVoicePage.modules";
import { MODULE_SOURCES } from "./ShopifyBoardPage.modules";
import { XbCard, useXbFetch, xbNoToken } from "./crossBoardModules.shell";

const BOARD_LABEL = "Shopify";
const source = MODULE_SOURCES.kpiS;
const fetchGmv = (token: string) => getShopifyGmv(token);

function money(cents: unknown, currency: string) {
  const value = centsToUsd(cents);
  if (value === null) return "—";
  return `${currency || "USD"} ${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function GmvBody({ data }: { data: ShopifyGmvSummary }) {
  const sourceLabel = data.gmv_source === "vkpi_shopify_orders.total_price_cents[ingested]"
    ? "订单台账"
    : data.gmv_source === "vkpi_sales_attributions.revenue_cents[shopify,confirmed+refund,net]"
      ? "归因台账净额"
      : "等待数据";
  const currency = String(data.currency || "USD");
  const orderCount = Number(data.order_count) || 0;
  const gmv = centsToUsd(data.gmv_cents);
  if (data.gmv_source === "awaiting_data" && orderCount === 0 && (gmv === null || gmv === 0)) {
    return <EmptyLine text="订单台账与归因台账当前均无行，GMV 不摆假数。" />;
  }
  const rows = [
    ["对账 GMV", money(data.gmv_cents, currency)],
    ["订单数", orderCount.toLocaleString()],
    ["当前口径", sourceLabel],
    ["订单台账", money(data.order_ledger_gmv_cents, currency)],
    ["归因净额", money(data.attribution_gmv_cents, currency)],
  ];
  return (
    <div>
      {rows.map(([label, value]) => (
        <div key={label} className="flex items-center justify-between gap-3 border-b border-line py-2 text-[11.5px] last:border-0">
          <span className="text-muted">{label}</span>
          <span className="text-right font-mono text-ink-2">{value}</span>
        </div>
      ))}
    </div>
  );
}

export function ShopifyGmvXbCard({ apiToken, onOpenBoard }: { apiToken: string; onOpenBoard: () => void }) {
  const remote = useXbFetch(apiToken, fetchGmv);
  let body: React.ReactNode;
  if (!apiToken) body = xbNoToken(BOARD_LABEL);
  else if (remote.error) body = <ErrorCard title="shopify/gmv 读取失败" text={remote.error} />;
  else if (!remote.data) body = <LoadingLine text="GMV 对账中…" />;
  else body = <GmvBody data={remote.data} />;
  return (
    <XbCard
      title="GMV 对账"
      cnt={remote.data && Number(remote.data.order_count) > 0 ? `${Number(remote.data.order_count)} 单` : undefined}
      srcLabel={source.label}
      srcRows={source.rows}
      boardLabel={BOARD_LABEL}
      onOpenBoard={onOpenBoard}
    >
      {body}
    </XbCard>
  );
}
