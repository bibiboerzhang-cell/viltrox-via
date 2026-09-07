import { Sparkles } from 'lucide-react';
import { formatMoneyShort } from '../projectDeliverableStyle';
import type { VkpiProjectRow } from '../../../vkpiTypes';
import type { ExpenseLine } from '../../../../../domains/projects';
import { buildCampaignFinance, FINANCE_STATE_LABELS, type FinanceAmount } from './CampaignFinanceTab.finance';

function amountLabel(value: FinanceAmount): string {
  if (value.mixedCurrency) return '多币种，见明细';
  if (value.amount === null) return '待确认';
  if (value.currency === 'USD') return formatMoneyShort(value.amount);
  return `${value.amount} ${value.currency || '（币种待核）'}`;
}

function CostAmount({ value, estimateNote }: { value: FinanceAmount; estimateNote?: string }) {
  return <span>
    {amountLabel(value)}
    {value.states.length > 0 ? <span className="block text-[9px] text-slate-400" title={estimateNote}>
      {value.states.map((state) => FINANCE_STATE_LABELS[state]).join(' / ')}
    </span> : null}
  </span>;
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
  const { rowCosts, totals, voidCount } = buildCampaignFinance(rows, expenseLines, costRows, productUnitCosts);

  return (
    <div className="p-4 space-y-4" aria-label="项目费用">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          ['已确认成本', amountLabel(totals.confirmed), '#a855f7', '仅 actual 且有审批时间的成本记录'],
          ['待审核成本', amountLabel(totals.pending), '#06b6d4', '未计入已确认成本'],
          ['估算成本', amountLabel(totals.estimate), '#10b981', '含目录与余额估算，非确认金额'],
          ['实际支付', '未核验', '#fb923c', '当前数据不含付款回执'],
        ].map(([label, value, color, sub]) => (
          <div key={String(label)} className="rounded-lg border border-white/[0.06] bg-white/[0.015] p-3">
            <div className="text-[10px] text-slate-500 mb-1">{label}</div>
            <div className="text-[20px] font-bold tabular-nums" style={{ color: String(color) }}>{value}</div>
            <div className="text-[9.5px] text-slate-500 mt-1">{sub}</div>
          </div>
        ))}
      </div>

      <div className="rounded-lg border border-purple-500/30 bg-purple-500/5 p-3 flex items-start gap-2.5">
        <Sparkles size={13} className="text-purple-300 mt-0.5 shrink-0" />
        <div className="text-[10.5px] text-slate-300">
          费用记录不代表合同已签署或款项已支付；签约、实付未核验。状态待核 {totals.unknown.count} 条 · 已作废 {voidCount} 条（不计入金额）。
          缺失金额显示待确认，已记录的 0 保留为 0；小计是费用记录额，可能包含待审或估算。
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
              {['KOL', '合作费用 / 余额估算', '快递费', '产品 (成本)', '记录额小计', '费用状态', '操作'].map((header) => (
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
                  <CostAmount value={item.contract} estimateNote="没有合作费用记录时可按费用余额估算；余额不证明合同金额、签约或支付。" />
                </td>
                <td className="px-4 py-2.5 text-slate-300 tabular-nums">
                  <CostAmount value={item.shipping} />
                </td>
                <td className="px-4 py-2.5 tabular-nums">
                  <CostAmount value={item.product} estimateNote="目录单价仅作估算；缺少单价或币种时保持待核。" />
                  {item.productSent.length > 0 ? <span className="text-[9.5px] text-slate-500 ml-1">（{item.productSent.length} 项产品）</span> : null}
                </td>
                <td className="px-4 py-2.5 text-white font-semibold tabular-nums">
                  {amountLabel(item.total)}
                </td>
                <td className="px-4 py-2.5">
                  <span className="text-[10px] text-slate-300">
                    {item.total.states.map((state) => FINANCE_STATE_LABELS[state]).join(' / ') || (item.voidCount ? '费用已作废' : '费用待确认')}
                  </span>
                </td>
                <td className="px-4 py-2.5">
                  {onOpenCostEntry ? (
                    <button
                      type="button"
                      className="rounded-md border border-white/[0.08] bg-white/[0.03] px-2 py-1 text-[10px] font-medium text-slate-200 hover:border-purple-400/40 hover:text-white transition"
                      onClick={() => onOpenCostEntry(item.row, item.contract.count > 0 ? 'shipping' : 'cash_fee')}
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
