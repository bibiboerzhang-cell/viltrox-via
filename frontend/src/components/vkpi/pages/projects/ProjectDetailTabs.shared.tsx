import type { VkpiProjectRow } from '../../vkpiTypes';

export function centsValue(row: Record<string, unknown>) {
  if (row.amount_cents != null) return Number(row.amount_cents || 0);
  if (row.amount_usd != null) return Math.round(Number(row.amount_usd || 0) * 100);
  return 0;
}

export function objectValue(value: unknown): Record<string, unknown> {
  if (!value) return {};
  if (typeof value === 'object' && !Array.isArray(value)) return value as Record<string, unknown>;
  if (typeof value !== 'string') return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

export function costRowAmount(costRows: Array<Record<string, unknown>>, row: VkpiProjectRow, type: 'shipping' | 'product' | 'contract') {
  const assignmentId = String(row.assignmentId || '').trim();
  const kolPoolId = String(row.kolPoolId || '').trim();
  return costRows.reduce((sum, costRow) => {
    if (String(costRow.status || '').toLowerCase() === 'void') return sum;
    const costType = String(costRow.cost_type || '').toLowerCase();
    if (type === 'shipping' && costType !== 'shipping') return sum;
    if (type === 'product' && !['product', 'sample'].includes(costType)) return sum;
    if (type === 'contract' && !['cash_fee', 'contract', 'creator_fee'].includes(costType)) return sum;
    const metadata = objectValue(costRow.metadata_json || costRow.metadata);
    const sourceRef = String(costRow.source_ref || '');
    const matchesAssignment = assignmentId && (
      sourceRef === `assignment_${type}:${assignmentId}`
      || sourceRef.endsWith(`:${assignmentId}`)
      || String(metadata.assignment_id || '') === assignmentId
    );
    const matchesPool = kolPoolId && String(metadata.kol_pool_id || '') === kolPoolId;
    return matchesAssignment || matchesPool ? sum + (centsValue(costRow) / 100) : sum;
  }, 0);
}

export function rowProductSent(row: VkpiProjectRow): string[] {
  const dynamicRow = row as unknown as Record<string, unknown>;
  const raw = dynamicRow.productSent || dynamicRow.product_sent || dynamicRow.productsSent || dynamicRow.products;
  if (Array.isArray(raw)) {
    return raw.map((item) => {
      if (typeof item === 'string') return item;
      if (item && typeof item === 'object') {
        const record = item as Record<string, unknown>;
        return String(record.name || record.productName || record.product_name || record.sku || record.productSku || '').trim();
      }
      return '';
    }).filter(Boolean);
  }
  const single = String(dynamicRow.productName || dynamicRow.product_name || dynamicRow.productSku || dynamicRow.product_sku || '').trim();
  return single ? [single] : [];
}

export function productCost(productSent: string[], unitCosts: Record<string, number> = {}) {
  if (!productSent.length) return 0;
  return productSent.reduce((sum, item) => {
    const key = String(item || '').trim();
    return sum + (Number(unitCosts[key]) || Number(unitCosts[key.toLowerCase()]) || 0);
  }, 0);
}

export function retrospectiveTextField(row: VkpiProjectRow, keys: string[]) {
  const dynamicRow = row as unknown as Record<string, unknown>;
  for (const key of keys) {
    const value = dynamicRow[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return '';
}

export function retrospectiveNumberField(row: VkpiProjectRow, keys: string[]) {
  const dynamicRow = row as unknown as Record<string, unknown>;
  for (const key of keys) {
    const value = dynamicRow[key];
    if (value == null || value === '') continue;
    const numeric = Number(value);
    if (Number.isFinite(numeric)) return numeric;
  }
  return 0;
}

export function retrospectiveVideoTitle(row: VkpiProjectRow, projectTitle: string) {
  return retrospectiveTextField(row, ['evidenceTitle', 'evidence_title', 'videoTitle', 'video_title', 'contentTitle', 'content_title', 'title'])
    || `${projectTitle} · ${row.kolHandle || row.kolName || 'KOL'}`;
}
