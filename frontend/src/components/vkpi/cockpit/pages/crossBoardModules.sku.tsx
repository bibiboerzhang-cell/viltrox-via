import React from "react";
import { getSku360Profile, listSku360Options } from "../../../../services/vkpi/sku360-api";
import { EmptyLine, ErrorCard, LoadingLine } from "./MarketVoicePage.modules";
import { CatalogBody, MODULE_SOURCES } from "./Sku360BoardPage.modules";
import { asRow, type Row } from "./Sku360BoardPage.charts";
import { XbCard, useXbFetch, xbNoToken } from "./crossBoardModules.shell";

const BOARD_LABEL = "SKU 360°";
const SKU_KEY = "vkpi:sku360-sku";
const source = MODULE_SOURCES.catalog;

interface CatalogPayload {
  sku: string;
  product: Row | null;
  contextSource: "current" | "catalog" | "none";
}

function currentSku() {
  try {
    return String(window.sessionStorage.getItem(SKU_KEY) || "").trim();
  } catch {
    return "";
  }
}

async function fetchCatalog(token: string): Promise<CatalogPayload> {
  let sku = currentSku();
  let contextSource: CatalogPayload["contextSource"] = sku ? "current" : "none";
  if (!sku) {
    const options = await listSku360Options(token, "", 1);
    sku = String(options[0]?.sku || "").trim();
    contextSource = sku ? "catalog" : "none";
  }
  if (!sku) return { sku: "", product: null, contextSource };
  const profile = await getSku360Profile(token, sku);
  return { sku, product: asRow(profile?.product), contextSource };
}

export function SkuCatalogXbCard({ apiToken, onOpenBoard }: { apiToken: string; onOpenBoard: () => void }) {
  const remote = useXbFetch(apiToken, fetchCatalog);
  let body: React.ReactNode;
  if (!apiToken) body = xbNoToken(BOARD_LABEL);
  else if (remote.error) body = <ErrorCard title="SKU 档案读取失败" text={remote.error} />;
  else if (!remote.data) body = <LoadingLine text="产品目录读取中…" />;
  else if (!remote.data.sku || !remote.data.product) body = <EmptyLine text="产品目录当前无可用 SKU，档案不摆假数据。" />;
  else body = <CatalogBody product={remote.data.product} />;
  const contextText = remote.data?.contextSource === "current" ? "当前 SKU" : remote.data?.contextSource === "catalog" ? "目录首项" : "无上下文";
  return (
    <XbCard
      title="产品档案"
      cnt={remote.data?.sku || undefined}
      srcLabel={source.label}
      srcRows={[...source.rows, ["Dashboard 选择", `优先最近打开的 SKU；缺失时只读目录首项（当前：${contextText}）`]]}
      boardLabel={BOARD_LABEL}
      onOpenBoard={onOpenBoard}
    >
      {body}
    </XbCard>
  );
}
