import type {
  VkpiProductCatalogItem,
  VkpiProductCostRow,
  VkpiProductLaunchOption,
} from '../../components/vkpi/vkpiTypes';
import {
  arrayValue,
  centsToUsd,
  numberValue,
  objectValue,
  parseJsonValue,
} from '../dashboard';

type Row = Record<string, unknown>;

export function buildProductCosts(rows: Row[]): VkpiProductCostRow[] {
  return rows.map((row) => ({
    id: String(row.id || row.product_sku || ''),
    productSku: String(row.product_sku || ''),
    productName: String(row.product_name || ''),
    unitCost: centsToUsd(row.unit_cost_cents),
    currency: String(row.currency || 'USD'),
    active: row.active === true || Number(row.active ?? 1) !== 0,
    note: String(row.note || ''),
    updatedAt: String(row.updated_at || row.created_at || ''),
  })).filter((row) => row.productSku);
}

export function buildProductCatalog(rows: Row[]): VkpiProductCatalogItem[] {
  return rows.map((row) => ({
    sku: String(row.sku || row.product_sku || ''),
    categoryMain: String(row.category_main || ''),
    categoryDetail: String(row.category_detail || ''),
    modelName: String(row.model_name || row.product_name || ''),
    marketingName: String(row.marketing_name || ''),
    priceUsd: row.price_usd === null || row.price_usd === undefined || row.price_usd === '' ? null : numberValue(row.price_usd),
    status: String(row.status || ''),
    description: String(row.description || ''),
    sourceFile: String(row.source_file || ''),
    series: String(row.series || ''),
    mount: String(row.mount || ''),
    productUrl: String(row.product_url || ''),
    specs: objectValue(parseJsonValue(row.specs_json), {}),
    fitTags: arrayValue(parseJsonValue(row.fit_tags_json)).map(String),
    sourceUrl: String(row.source_url || ''),
    sourceCheckedAt: String(row.source_checked_at || ''),
    sourceConfidence: numberValue(row.source_confidence),
  })).filter((row) => row.sku);
}

export function buildProductLaunchOptions(rows: Row[]): VkpiProductLaunchOption[] {
  return rows.map((row) => ({
    id: String(row.id || row.launch_uid || row.product_sku || ''),
    productSku: String(row.product_sku || row.sku || ''),
    productName: String(row.product_name || row.name || row.product_sku || ''),
    launchName: String(row.name || row.launch_name || row.product_name || row.product_sku || ''),
    status: String(row.status || ''),
    category: String(row.category || ''),
    updatedAt: String(row.updated_at || row.created_at || ''),
  })).filter((row) => row.productSku || row.productName || row.launchName);
}
