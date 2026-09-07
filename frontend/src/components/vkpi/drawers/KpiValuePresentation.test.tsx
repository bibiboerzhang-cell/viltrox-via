import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { kpiValueLabel, workloadValueLabel } from './kpiValuePresentation';
import { StaffProfileDrawer } from './StaffProfileDrawer';
import { KolProfileDrawer } from './KolProfileDrawer';

describe('KPI projections never coerce unknown to zero or old credit', () => {
  it.each([null, undefined, '', ' ', true, false, NaN, Infinity, {}, 'unknown'])('keeps %s unknown', (value) => {
    expect(kpiValueLabel(value)).toBe('待核验');
  });
  it.each([0, '0', 2.5, '2.5'])('keeps an eligible finite value %s', (value) => {
    expect(kpiValueLabel(value)).not.toBe('待核验');
  });
  it('cannot revive blocked numeric historical evidence', () => {
    expect(kpiValueLabel(99, false)).toBe('待核验');
    expect(workloadValueLabel({ workload_score: null, kpi_credit: 99 })).toBe('待核验');
    expect(workloadValueLabel({ workload_score: 0, kpi_credit: 99 })).toBe('0');
  });

  it('renders staff current values unknown while leaving finite zero alone', () => {
    render(<StaffProfileDrawer member={{ id: '1', name: '合成员工', email: '', role: '', active: true, vkpiPermission: '' }}
      profile={{ staff: {}, summary: { workload_score: null, kpi_credit: 999 }, projects: [], claims: [],
        links: [], attributions: [], costs: [], channels: [], audit_events: [],
        kpi_ledger: [{ id: 1, metric_key: '合成待核记录', metric_value: null, recorded_metric_value: 999 }],
        kpi_breakdown: { grouped: [{ metric_key: '合成零值', total_value: 0 },
          { metric_key: '合成通信汇总', total_value: 999, aggregation_eligible: false }] },
      }} onSelectProject={() => {}} onClose={() => {}} />);
    expect(screen.getByText('合成待核记录').closest('article')).toHaveTextContent('待核验');
    expect(screen.getByText('合成通信汇总').closest('article')).toHaveTextContent('待核验');
    expect(screen.getByText('合成零值').closest('article')).toHaveTextContent('0');
    expect(screen.queryByText('999')).not.toBeInTheDocument();
    expect(screen.getByText(/历史账本保留/)).toBeInTheDocument();
  });

  it('renders KOL manual messages and projected KPI values without success claims', () => {
    render(<KolProfileDrawer fallbackKol={{ id: '9', name: '合成人选', handle: '@synthetic', platform: 'YouTube',
      subscribersLabel: '-', videosLabel: '-', engagementLabel: '-', recentContent: [] } as never}
      profile={{ kol: {}, claim_history: [], projects: [], links: [], sales_attributions: [], costs: [], posts: [],
        messages: [{ id: 1, body: '合成消息', direction: 'inbound', source: 'provider' }],
        kpi_ledger: [{ id: 2, metric_key: '合成旧积分', metric_value: null, recorded_metric_value: 88 }],
        kpi_summary: [{ metric_key: '合成旧总分', total_value: null, recorded_total_value: 88 }],
      }} viewMode="manager" onClose={() => {}} />);
    const record = screen.getByText('合成消息').closest('article')!;
    expect(within(record).getByText(/收发未核验/)).toBeInTheDocument();
    expect(screen.getByText('合成旧积分').closest('article')).toHaveTextContent('数值 待核验');
    expect(screen.getByText('合成旧总分').closest('article')).toHaveTextContent('累计 待核验');
    expect(screen.queryByText('88')).not.toBeInTheDocument();
  });
});
