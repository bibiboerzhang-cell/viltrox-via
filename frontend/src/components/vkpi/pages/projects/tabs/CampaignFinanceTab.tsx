import { Sparkles } from 'lucide-react';
import { formatMoneyShort } from '../projectDeliverableStyle';
import type { VkpiProjectRow } from '../../../vkpiTypes';
import type { ExpenseLine } from '../../../../../domains/projects';
import { centsValue, costRowAmount, objectValue, productCost, rowProductSent } from '../ProjectDetailTabs.shared';

interface CostLedgerTotals {
  contract: number;
  shipping: number;
  product: number;
  total: number;
}

function costLedgerTotals(costRows: Array<Record<string, unknown>>): CostLedgerTotals {
  return costRows.reduce<CostLedgerTotals>((totals, row) => {
    if (String(row.status || '').toLowerCase() === 'void') return totals;
    const type = String(row.cost_type || '').toLowerCase();
    const amount = centsValue(row) / 100;
    if (type === 'shipping') totals.shipping += amount;
    else if (type === 'product' || type === 'sample') totals.product += amount;
    else if (type === 'cash_fee' || type === 'contract' || type === 'creator_fee') totals.contract += amount;
    totals.total += amount;
    return totals;
  }, { contract: 0, shipping: 0, product: 0, total: 0 });
}

export function CampaignFinanceTab({
  rows,
  expenseLines,
  costRows,
  productUnitCosts = {},
  onOpenShippingInfo,
  onOpenCostEntry,
}: {
  rows: VkpiProjectRow[];
  expenseLines: ExpenseLine[];
  costRows: Array<Record<string, unknown>>;
  productUnitCosts?: Record<string, number>;
  onOpenShippingInfo: () => void;
  onOpenCostEntry?: (row: VkpiProjectRow, type?: 'cash_fee' | 'shipping' | 'product') => void;
}) {
  const ledgerTotals = costLedgerTotals(costRows);
  const expenseById = new Map(expenseLines.map((line) => [line.id, line]));
  const rowCosts = rows.map((row) => {
    const shippingFee = costRowAmount(costRows, row, 'shipping');
    const productSent = rowProductSent(row);
    const ledgerProductCost = costRowAmount(costRows, row, 'product');
    const estimatedProductCost = productCost(productSent, productUnitCosts);
    const productCostAmount = ledgerProductCost || estimatedProductCost;
    const productCostIsEstimate = !ledgerProductCost && estimatedProductCost > 0;
    const ledgerContractFee = costRowAmount(costRows, row, 'contract');
    const expenseAmount = expenseById.get(row.id)?.amount ?? row.cost ?? 0;
    const contractFee = ledgerContractFee || Math.max(expenseAmount - shippingFee - productCostAmount, 0);
    // 残差推算的合同费要打"估"——与产品成本估算同口径,不冒充账本真值(扫描 #10)。
    const contractFeeIsEstimate = !ledgerContractFee && contractFee > 0;
    return {
      row,
      contractFee,
      contractFeeIsEstimate,
      shippingFee,
      productSent,
      productCost: productCostAmount,
      productCostIsEstimate,
      total: contractFee + shippingFee + productCostAmount,
      hasContract: contractFee > 0,
    };
  });
  const totalContract = ledgerTotals.contract || rowCosts.reduce((sum, item) => sum + item.contractFee, 0);
  const totalShipping = ledgerTotals.shipping || rowCosts.reduce((sum, item) => sum + item.shippingFee, 0);
  const totalProductCost = ledgerTotals.product || rowCosts.reduce((sum, item) => sum + item.productCost, 0);
  const totalAll = totalContract + totalShipping + totalProductCost;
  // 横幅口径:账本真值与倒推估算分开计数,不再合并冒充"已从合同提取"。
  const ledgerContractRows = rowCosts.filter((item) => item.hasContract && !item.contractFeeIsEstimate).length;
  const estimatedContractRows = rowCosts.filter((item) => item.contractFeeIsEstimate).length;
  const rowsWithProductCost = rowCosts.filter((item) => item.productCost > 0).length;
  const averageCost = totalAll / Math.max(rowCosts.filter((item) => item.contractFee + item.productCost > 0).length, 1);

  return (
    <div className="p-4 space-y-4" aria-label="项目费用">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          ['合同费用', totalContract, '#a855f7', '已合作阶段录入'],
          ['快递费', totalShipping, '#06b6d4', '已发货阶段录入'],
          // 子标签曾标"零售价"但贴的是成本数——这里只有产品成本口径,如实标注。
          ['产品成本', totalProductCost, '#10b981', '按产品成本计 · 零售价未录入'],
          ['总成本', totalAll, '#fb923c', `${rows.length} 个 KOL`],
        ].map(([label, value, color, sub]) => (
          <div key={String(label)} className="rounded-lg border border-white/[0.06] bg-white/[0.015] p-3">
            <div className="text-[10px] text-slate-500 mb-1">{label}</div>
            <div className="text-[20px] font-bold tabular-nums" style={{ color: String(color) }}>{formatMoneyShort(Number(value))}</div>
            <div className="text-[9.5px] text-slate-500 mt-1">{sub}</div>
          </div>
        ))}
      </div>

      <div className="rounded-lg border border-purple-500/30 bg-purple-500/5 p-3 flex items-start gap-2.5">
        <Sparkles size={13} className="text-purple-300 mt-0.5 shrink-0" />
        <div className="text-[10.5px] text-slate-300">
          合同费用:账本入账 {ledgerContractRows} 份 · 估算 {estimatedContractRows} 份 + {rowsWithProductCost} 个 KOL 计入产品成本 · 平均 KOL 总成本{' '}
          <span className="text-purple-300 font-semibold">{formatMoneyShort(averageCost)}</span>
        </div>
      </div>

      <div className="rounded-lg border border-white/[0.06] bg-white/[0.015] overflow-hidden">
        <div className="px-4 py-2.5 border-b border-white/[0.05] flex items-center justify-between gap-3">
          <h4 className="text-[12px] font-semibold text-white">KOL 费用明细</h4>
          <div className="flex items-center gap-2">
            <button
              type="button"
              className="rounded-lg border border-cyan-400/30 bg-cyan-500/10 px-2.5 py-1 text-[10.5px] font-semibold text-cyan-200 hover:bg-cyan-500/20 transition"
              onClick={onOpenShippingInfo}
            >
              录入快递
            </button>
            {rows[0] && onOpenCostEntry ? (
              <button
                type="button"
                className="rounded-lg border border-purple-400/30 bg-purple-500/10 px-2.5 py-1 text-[10.5px] font-semibold text-purple-200 hover:bg-purple-500/18 transition"
                onClick={() => onOpenCostEntry(rows[0], 'cash_fee')}
              >
                + 录入费用
              </button>
            ) : null}
          </div>
        </div>
        <table className="w-full text-[11px]">
          <thead>
            <tr className="text-left text-[10px] text-slate-500 border-b border-white/[0.04]">
              {['KOL', '合同费', '快递费', '产品 (成本)', '小计', '状态', '操作'].map((header) => (
                <th key={header} className="px-4 py-2 font-medium">{header}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rowCosts.map((item) => (
              <tr key={item.row.id} className="border-b border-white/[0.03] hover:bg-white/[0.012]">
                <td className="px-4 py-2.5">
                  <div className="flex items-center gap-2">
                    <div className="w-6 h-6 rounded-full flex items-center justify-center text-[9px] font-bold text-white shrink-0" style={{ background: 'linear-gradient(135deg,#a855f7,#ec4899)' }}>
                      {(item.row.kolName || item.row.kolHandle || '-').charAt(0).toUpperCase()}
                    </div>
                    <div>
                      <div className="text-white text-[11px]">{item.row.kolHandle || item.row.kolName}</div>
                      <div className="text-[9.5px] text-slate-500">{item.row.platform}</div>
                    </div>
                  </div>
                </td>
                <td className="px-4 py-2.5 text-slate-300 tabular-nums">
                  {item.hasContract ? (
                    <span>
                      {formatMoneyShort(item.contractFee)}
                      {item.contractFeeIsEstimate ? (
                        <span className="text-[9px] text-amber-300/80 ml-1" title="按总支出倒推估算(成本账本暂无该 KOL 签约费行;合同确认归档后自动入账)">估</span>
                      ) : null}
                    </span>
                  ) : <span className="text-slate-600">—</span>}
                </td>
                <td className="px-4 py-2.5 text-slate-300 tabular-nums">
                  {item.shippingFee > 0 ? formatMoneyShort(item.shippingFee) : <span className="text-slate-600">—</span>}
                </td>
                <td className="px-4 py-2.5 tabular-nums">
                  {item.productCost > 0 ? (
                    <span>
                      <span className="text-emerald-400">{formatMoneyShort(item.productCost)}</span>
                      {item.productCostIsEstimate ? (
                        <span className="text-[9px] text-amber-300/80 ml-1" title="按 SKU 成本目录单价估算(成本账本暂无该 KOL 产品成本行)">估</span>
                      ) : null}
                      <span className="text-[9.5px] text-slate-500 ml-1">({Math.max(item.productSent.length, 1)}件)</span>
                    </span>
                  ) : (
                    <span className="text-slate-600">—</span>
                  )}
                </td>
                <td className="px-4 py-2.5 text-white font-semibold tabular-nums">
                  {item.total > 0 ? formatMoneyShort(item.total) : <span className="text-slate-600 font-normal">—</span>}
                </td>
                <td className="px-4 py-2.5">
                  {item.hasContract && !item.contractFeeIsEstimate ? (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300">已签合同</span>
                  ) : item.hasContract ? (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-300/80" title="费用为倒推估算,账本暂无签约费行">费用估算</span>
                  ) : (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/[0.04] text-slate-500">待签约</span>
                  )}
                </td>
                <td className="px-4 py-2.5">
                  {onOpenCostEntry ? (
                    <button
                      type="button"
                      className="rounded-md border border-white/[0.08] bg-white/[0.03] px-2 py-1 text-[10px] font-medium text-slate-200 hover:border-purple-400/40 hover:text-white transition"
                      onClick={() => onOpenCostEntry(item.row, item.hasContract ? 'shipping' : 'cash_fee')}
                    >
                      录入费用
                    </button>
                  ) : (
                    <span className="text-slate-600">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
