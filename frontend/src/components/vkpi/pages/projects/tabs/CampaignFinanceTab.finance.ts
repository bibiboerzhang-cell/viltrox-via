import type { ExpenseLine } from '../../../../../domains/projects';
import type { VkpiProjectRow } from '../../../vkpiTypes';
import { objectValue, rowProductSent } from '../ProjectDetailTabs.shared';

export type FinanceState = 'confirmed' | 'pending' | 'estimate' | 'unknown';
type CostType = 'contract' | 'shipping' | 'product' | 'other';
interface Entry {
  amount: number | null;
  currency: string | null;
  state: FinanceState;
  type: CostType;
}
export interface FinanceAmount {
  amount: number | null;
  currency: string | null;
  mixedCurrency: boolean;
  states: FinanceState[];
  count: number;
}
export const FINANCE_STATE_LABELS: Record<FinanceState, string> = {
  confirmed: '已确认成本', pending: '待审核', estimate: '费用估算', unknown: '状态待核',
};

function numberValue(value: unknown): number | null {
  if ((typeof value !== 'number' && typeof value !== 'string') || String(value).trim() === '') return null;
  const amount = Number(value);
  return Number.isFinite(amount) && amount >= 0 ? amount : null;
}

function costType(row: Record<string, unknown>): CostType {
  const type = String(row.cost_type || '').toLowerCase();
  if (['cash_fee', 'contract', 'creator_fee'].includes(type)) return 'contract';
  if (['product', 'sample'].includes(type)) return 'product';
  return type === 'shipping' ? 'shipping' : 'other';
}

function isVoid(row: Record<string, unknown>) {
  return String(row.status || '').toLowerCase() === 'void';
}

function ledgerEntry(row: Record<string, unknown>): Entry {
  const status = String(row.status || '').toLowerCase();
  // Existing server contract: actual + approval timestamp confirms COST, not payment/signature.
  const approved = status === 'actual' && typeof row.approved_at === 'string' && row.approved_at.trim() !== '';
  const projectionAllowsApproval = row.business_truth_status == null || row.business_truth_status === 'approved_actual';
  const state: FinanceState = approved && projectionAllowsApproval ? 'confirmed'
    : status === 'pending' ? 'pending' : ['estimate', 'estimated'].includes(status) ? 'estimate' : 'unknown';
  const cents = numberValue(row.amount_cents);
  const amount = row.amount_cents != null ? (cents === null ? null : cents / 100) : numberValue(row.amount_usd);
  const currency = typeof row.currency === 'string' && /^[A-Za-z]{3}$/.test(row.currency.trim())
    ? row.currency.trim().toUpperCase() : row.amount_cents == null && row.amount_usd != null ? 'USD' : null;
  return { amount, currency, state, type: costType(row) };
}

function matchesRow(cost: Record<string, unknown>, row: VkpiProjectRow): boolean {
  const metadata = objectValue(cost.metadata_json || cost.metadata);
  const assignment = String(row.assignmentId || '');
  const linkedAssignment = String(metadata.assignment_id || '');
  // An explicit different assignment must not leak a cost through a shared KOL ID.
  if (linkedAssignment) return Boolean(assignment && linkedAssignment === assignment);
  const ref = String(cost.source_ref || '');
  if (/^assignment_(?:contract|cash_fee|shipping|product):/.test(ref)) {
    return Boolean(assignment && ref.split(':')[1] === assignment);
  }
  return Boolean(row.kolPoolId && String(metadata.kol_pool_id || '') === String(row.kolPoolId));
}

function sumEntries(entries: Entry[]): FinanceAmount {
  const currencies = new Set(entries.map((entry) => entry.currency));
  const mixedCurrency = currencies.size > 1;
  const complete = entries.length > 0 && !mixedCurrency && entries.every((entry) => entry.amount !== null);
  return {
    amount: complete ? Math.round(entries.reduce((sum, entry) => sum + entry.amount!, 0) * 100) / 100 : null,
    currency: currencies.size === 1 ? entries[0].currency : null,
    mixedCurrency, states: [...new Set(entries.map((entry) => entry.state))], count: entries.length,
  };
}

function catalogEstimate(products: string[], unitCosts: Record<string, number>): Entry | null {
  if (!products.length) return null;
  const values = products.map((product) => numberValue(unitCosts[product] ?? unitCosts[product.toLowerCase()]));
  return {
    amount: values.every((value) => value !== null) ? values.reduce<number>((sum, value) => sum + value!, 0) : null,
    // This prop has no currency/verification fields; do not manufacture them.
    currency: null, type: 'product', state: 'estimate',
  };
}

function expenseReference(row: VkpiProjectRow, expense?: ExpenseLine): number | null {
  // buildExpenseLines uses amount=0/status=missing for null row.cost. That is NOT observed zero.
  return numberValue(row.cost) ?? (expense?.status === 'recorded' ? numberValue(expense.amount) : null);
}

export function buildCampaignFinance(
  rows: VkpiProjectRow[], expenseLines: ExpenseLine[], costRows: Array<Record<string, unknown>>,
  productUnitCosts: Record<string, number> = {},
) {
  const expenses = new Map(expenseLines.map((line) => [line.id, line]));
  const estimates: Entry[] = [];
  const rowCosts = rows.map((row) => {
    const linked = costRows.filter((cost) => matchesRow(cost, row));
    const entries = linked.filter((cost) => !isVoid(cost)).map(ledgerEntry);
    const productSent = rowProductSent(row);
    if (!linked.some((cost) => costType(cost) === 'product')) {
      const estimate = catalogEstimate(productSent, productUnitCosts);
      if (estimate) { entries.push(estimate); estimates.push(estimate); }
    }
    const reference = expenseReference(row, expenses.get(row.id));
    if (!linked.some((cost) => costType(cost) === 'contract') && reference !== null) {
      // This unallocated remainder is only an estimate, never a confirmed creator fee.
      // No currency is supplied by ExpenseLine/row.cost; don't subtract known foreign currencies.
      const deductions = entries.filter((entry) => entry.type !== 'contract');
      const safe = deductions.every((entry) => entry.amount !== null && entry.currency === null);
      const estimate: Entry = {
        amount: safe ? Math.max(reference - deductions.reduce((sum, entry) => sum + entry.amount!, 0), 0) : null,
        currency: null, type: 'contract', state: 'estimate',
      };
      entries.push(estimate); estimates.push(estimate);
    }
    return {
      row, productSent,
      contract: sumEntries(entries.filter((entry) => entry.type === 'contract')),
      shipping: sumEntries(entries.filter((entry) => entry.type === 'shipping')),
      product: sumEntries(entries.filter((entry) => entry.type === 'product')),
      total: sumEntries(entries),
      voidCount: linked.filter(isVoid).length,
    };
  });
  const entries = [...costRows.filter((cost) => !isVoid(cost)).map(ledgerEntry), ...estimates];
  const totals = Object.fromEntries((['confirmed', 'pending', 'estimate', 'unknown'] as const)
    .map((state) => [state, sumEntries(entries.filter((entry) => entry.state === state))])) as Record<FinanceState, FinanceAmount>;
  return { rowCosts, totals, voidCount: costRows.filter(isVoid).length };
}
