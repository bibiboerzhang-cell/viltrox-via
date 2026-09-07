import { render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ExpenseLine } from '../../../../../domains/projects';
import type { VkpiProjectRow } from '../../../vkpiTypes';
import { CampaignFinanceTab } from './CampaignFinanceTab';
import { buildCampaignFinance } from './CampaignFinanceTab.finance';

const creator = {
  id: 'r7', assignmentId: '7', kolPoolId: '22', kolName: 'Creator', kolHandle: '@creator',
  platform: 'YouTube', stage: '已合作', cost: null,
} as VkpiProjectRow;
const expense = { id: 'r7', amount: 0, status: 'missing' } as ExpenseLine;
const cost = (patch: Record<string, unknown> = {}) => ({
  cost_type: 'cash_fee', source_ref: 'assignment_cash_fee:7', amount_cents: 10000,
  currency: 'USD', status: 'pending', ...patch,
});
const approved = { status: 'actual', approved_at: '2026-09-07T10:00:00Z', business_truth_status: 'approved_actual' };

function model(costRows: Record<string, unknown>[] = [], row = creator, line = expense, units = {}) {
  return buildCampaignFinance([row], [line], costRows, units);
}

function renderFinance(costRows: Record<string, unknown>[] = [], row = creator, line = expense, units = {}) {
  return render(<CampaignFinanceTab rows={[row]} expenseLines={[line]} costRows={costRows}
    productUnitCosts={units} onOpenShippingInfo={vi.fn()} />);
}

describe('CampaignFinance cost-state contract', () => {
  it.each([
    ['pending', {}, 'pending'],
    ['actual', {}, 'unknown'],
    ['actual', { approved_at: ' ' }, 'unknown'],
    ['actual', { approved_at: approved.approved_at }, 'confirmed'],
    ['actual', { ...approved, business_truth_status: 'reference_only' }, 'unknown'],
    ['approved', { approved_at: approved.approved_at }, 'unknown'],
    ['paid', { approved_at: approved.approved_at }, 'unknown'],
    ['estimate', {}, 'estimate'],
    ['estimated', {}, 'estimate'],
    ['', {}, 'unknown'],
  ])('真实状态 %s 和审批字段映射为 %s，不推断签约/支付', (status, fields, expected) => {
    const value = model([cost({ status, ...fields as object })]);
    expect(value.rowCosts[0].contract.states).toEqual([expected]);
    expect(value.rowCosts[0].contract.amount).toBe(100);
    expect(value.totals.confirmed.amount).toBe(expected === 'confirmed' ? 100 : null);
  });

  it('void 不进汇总，也不会由历史 expense residual 复活', () => {
    const value = model([cost({ status: 'void' })], { ...creator, cost: 100 });
    expect(value.rowCosts[0].contract).toMatchObject({ amount: null, count: 0, states: [] });
    expect(value.rowCosts[0].voidCount).toBe(1);
    expect(value.totals.estimate.count).toBe(0);
    expect(value.totals.confirmed.amount).toBeNull();
  });

  it('真实零合作费用不被非零总支出替换', () => {
    const value = model([cost({ ...approved, amount_cents: 0 })], { ...creator, cost: 500 });
    expect(value.rowCosts[0].contract).toMatchObject({ amount: 0, states: ['confirmed'] });
    expect(value.totals.estimate.count).toBe(0);
    expect(value.totals.confirmed.amount).toBe(0);
  });

  it.each([null, '', 'bad', true, Number.NaN, Number.POSITIVE_INFINITY])('缺失/非法金额 %s 不变为0或残差', (amount) => {
    const value = model([cost({ amount_cents: amount, amount_usd: null })], { ...creator, cost: 500 });
    expect(value.rowCosts[0].contract.amount).toBeNull();
    expect(value.rowCosts[0].contract.states).toEqual(['pending']);
    expect(value.totals.estimate.count).toBe(0);
  });

  it('missing expense 的默认0不是观测零；row.cost=0则保留为估算零', () => {
    expect(model().rowCosts[0].contract.amount).toBeNull();
    expect(model([], { ...creator, cost: 0 }).rowCosts[0].contract)
      .toMatchObject({ amount: 0, states: ['estimate'], currency: null });
  });

  it('有 recorded expense 时只给余额估算，不声称真实合同费', () => {
    const value = model([], creator, { ...expense, amount: 150, status: 'recorded' });
    expect(value.rowCosts[0].contract).toMatchObject({ amount: 150, states: ['estimate'], currency: null });
    expect(value.totals.confirmed.amount).toBeNull();
  });

  it('目录与费用余额只作估算，并区分缺价和真实零目录价', () => {
    const row = { ...creator, cost: 150, productName: 'Lens' };
    const known = model([], row, expense, { Lens: 50 });
    expect(known.rowCosts[0].product.amount).toBe(50);
    expect(known.rowCosts[0].contract.amount).toBe(100);
    expect(known.rowCosts[0].total.amount).toBe(150);
    expect(known.totals.estimate.amount).toBe(150);
    expect(model([], row).rowCosts[0].product.amount).toBeNull();
    expect(model([], row).rowCosts[0].contract.amount).toBeNull();
    expect(model([], row, expense, { Lens: 0 }).rowCosts[0].product.amount).toBe(0);
  });

  it('产品账本零不被目录正价替换', () => {
    const value = model([cost({ ...approved, cost_type: 'product', amount_cents: 0 })],
      { ...creator, productName: 'Lens' }, expense, { Lens: 50 });
    expect(value.rowCosts[0].product).toMatchObject({ amount: 0, states: ['confirmed'] });
    expect(value.totals.estimate.count).toBe(0);
  });

  it('不能只因 contract ID 与 assignment ID 同号关联费用', () => {
    const value = model([cost({ source_ref: 'contract:7' })]);
    expect(value.rowCosts[0].contract.amount).toBeNull();
    expect(value.totals.pending.amount).toBe(100); // 项目账本仍保留这笔未归属费用。
  });

  it('显式归属不同 assignment 不通过相同 KOL ID 混入', () => {
    const value = model([cost({ source_ref: 'contract:9', metadata: { assignment_id: 8, kol_pool_id: 22 } })]);
    expect(value.rowCosts[0].contract.count).toBe(0);
  });

  it('合同来源与归属 metadata 可以定位成本，但不形成签署证据', () => {
    const value = model([cost({ ...approved, source_ref: 'contract:9', metadata: { assignment_id: 7 } })]);
    expect(value.rowCosts[0].contract.states).toEqual(['confirmed']);
    expect(value.rowCosts[0]).not.toHaveProperty('hasContract');
  });

  it('多币种不能直接相加为美元，余额缺币种时不扣减外币', () => {
    const value = model([cost({ ...approved }), cost({ ...approved, currency: 'EUR' })]);
    expect(value.totals.confirmed).toMatchObject({ amount: null, mixedCurrency: true });
    const residual = model([cost({ cost_type: 'shipping', currency: 'EUR' })], { ...creator, cost: 200 });
    expect(residual.rowCosts[0].contract.amount).toBeNull();
  });
});

describe('CampaignFinance honest UI', () => {
  it.each([
    ['待审核', { status: 'pending' }], ['已确认成本', approved],
    ['状态待核', { status: 'actual' }], ['费用估算', { status: 'estimate' }],
  ])('有费用或已合作阶段只显示 %s，不显示已签或已付', (label, patch) => {
    renderFinance([cost(patch)]);
    const dataRow = screen.getAllByRole('row')[1];
    expect(within(dataRow).getAllByText(label).length).toBeGreaterThan(0);
    expect(screen.queryByText('已签合同')).not.toBeInTheDocument();
    expect(screen.queryByText('待签约')).not.toBeInTheDocument();
    expect(screen.queryByText('已支付')).not.toBeInTheDocument();
    expect(screen.getByText('当前数据不含付款回执')).toBeInTheDocument();
  });

  it('void 仅显示费用已作废，不显示合同状态', () => {
    renderFinance([cost({ status: 'void' })], { ...creator, cost: 500 });
    expect(screen.getByText('费用已作废')).toBeInTheDocument();
    expect(screen.queryByText('$500')).not.toBeInTheDocument();
    expect(screen.queryByText('已签合同')).not.toBeInTheDocument();
  });

  it('未签署有费用残差也仅显示估算和币种待核', () => {
    renderFinance([], { ...creator, cost: 150 });
    expect(screen.getAllByText('费用估算').length).toBeGreaterThan(0);
    expect(screen.getAllByText('150 （币种待核）').length).toBeGreaterThan(0);
    expect(screen.queryByText('已签合同')).not.toBeInTheDocument();
  });

  it('零金额真实显示 $0；缺失金额显示待确认', () => {
    const rendered = renderFinance([cost({ ...approved, amount_cents: 0 })]);
    expect(screen.getAllByText('$0').length).toBeGreaterThan(0);
    rendered.unmount();
    renderFinance();
    expect(screen.queryByText('$0')).not.toBeInTheDocument();
    expect(screen.getAllByText('待确认').length).toBeGreaterThan(0);
  });
});
