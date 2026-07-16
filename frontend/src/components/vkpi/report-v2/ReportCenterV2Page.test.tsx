import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { VkpiDashboardData } from '../vkpiTypes';

const reportApi = vi.hoisted(() => ({
  archiveVkpiReport: vi.fn(),
  createVkpiReportExport: vi.fn(),
  downloadVkpiFile: vi.fn(),
  generateVkpiReport: vi.fn(),
  listVkpiReports: vi.fn(),
  restoreVkpiReport: vi.fn(),
}));

vi.mock('../../../services/vkpi/reports-api', () => ({
  VKPI_REPORT_SECTION_KEYS: ['kpiOverview', 'attribution', 'projects', 'ledger', 'risks', 'summary'],
  ...reportApi,
  reportApiErrorStatus: (error: { status?: number }) => error?.status || null,
  reportApiErrorMessage: (error: { status?: number }, fallback: string) => {
    if (error?.status === 403) return '权限不足：你不能执行此报告操作。';
    if (error?.status && error.status >= 500) return `${fallback}，系统未产出可用结果。`;
    return fallback;
  },
  reportModelPolicyLabel: (policy: { mode?: string } | null | undefined) =>
    policy?.mode === 'deterministic_descriptive'
      ? '确定性描述模式（未调用模型）'
      : '策略未披露',
  vkpiReportDownloadPath: (id: number, format: string) => `/api/admin/vkpi/reports/files/${id}/download?format=${format}`,
}));

vi.mock('../v2-shell/V2ShellTopbar', () => ({ V2ShellTopbar: () => null }));

import { ReportCenterV2Page } from './ReportCenterV2Page';

const activeReport = {
  id: 11,
  reportUid: 'weekly-11',
  reportType: 'weekly',
  periodStart: '2026-07-01T00:00:00Z',
  periodEnd: '2026-07-07T00:00:00Z',
  scopeType: 'all',
  scopeId: null,
  triggeredAt: '2026-07-07T12:00:00Z',
  status: 'ready',
  summary: '历史服务端摘要',
  dataStatus: 'real',
  schemaVersion: 'report.v1',
  archivedAt: '',
  archiveReason: '',
  modelPolicy: null,
  claimLevel: '',
};

const archivedReport = {
  ...activeReport,
  id: 12,
  reportUid: 'weekly-12',
  status: 'archived',
  summary: '已归档服务端摘要',
  archivedAt: '2026-07-08T12:00:00Z',
};

const dashboardData = {
  rangeLabel: '最近 7 天',
  windowDays: 7,
  dataStatus: 'partial',
  dataNotice: '部分数据源待接入',
  metrics: [
    { key: 'gmv', label: 'GMV', value: '$0', deltaLabel: '来源 0 条', deltaDirection: 'flat' },
    { key: 'roi', label: 'ROI', value: '0', deltaLabel: '未生成快照', deltaDirection: 'flat' },
    { key: 'views', label: '播放量', value: '0', deltaLabel: '未生成快照', deltaDirection: 'flat' },
    { key: 'published_content', label: '内容', value: '0', deltaLabel: '来源 0 条', deltaDirection: 'flat' },
  ],
  revenueTrend: [],
  funnel: [],
  staffLeaderboard: [],
  productRoi: [],
  platformShare: [],
  contentTypePerformance: [],
  alerts: [],
  weeklySummary: '',
  exportReport: { id: 'none', title: '', generatedAt: '', status: 'Generating' },
  projects: [],
  links: [],
  attributions: [],
  unmatchedAttributions: [],
  costs: [],
  evidence: {},
  staffMembers: [],
  kpiLedger: [],
  productCosts: [],
  productLaunches: [],
  kolOptions: [],
  selectedKol: {},
  scopes: {},
} as unknown as VkpiDashboardData;

function renderPage() {
  return render(
    <ReportCenterV2Page
      apiToken="token"
      data={dashboardData}
      viewMode="manager"
      onSelectProject={vi.fn()}
      onOpenEvidence={vi.fn()}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  reportApi.listVkpiReports.mockImplementation(async (_token: string, archived: boolean) => ({
    reports: archived ? [archivedReport] : [activeReport],
    count: 1,
    archived,
  }));
  reportApi.generateVkpiReport.mockResolvedValue({
    reportId: 42,
    reportUid: 'monthly-42',
    reportType: 'monthly',
    periodStart: '2026-06-13T00:00:00Z',
    periodEnd: '2026-07-12T00:00:00Z',
    status: 'ready',
    downloadUrl: '/api/admin/vkpi/reports/files/42/download?format=pdf',
    summary: '新生成的服务端摘要',
    dataStatus: 'partial',
    metrics: [
      { key: 'sales', label: '销售额', value: '$0', rawValue: 0, dataStatus: 'real', note: '真实零值' },
      { key: 'views', label: '播放量', value: null, rawValue: null, dataStatus: 'awaiting_source', note: '等待来源' },
    ],
    modelPolicy: { mode: 'deterministic_descriptive', deterministic_only: true },
    claimLevel: 'descriptive_only',
  });
  reportApi.createVkpiReportExport.mockResolvedValue({ status: 'ready', downloadUrl: '/api/admin/vkpi/exports/7/download' });
  reportApi.downloadVkpiFile.mockResolvedValue('report.pdf');
  reportApi.archiveVkpiReport.mockResolvedValue(undefined);
  reportApi.restoreVkpiReport.mockResolvedValue(undefined);
});

describe('ReportCenterV2Page real report generation', () => {
  it('submits every builder control and renders only the server result as report content', async () => {
    renderPage();

    expect(screen.getByText('$0', { selector: '.report-v2-metrics strong' })).toBeInTheDocument();
    expect(screen.getAllByText('待数据', { selector: '.report-v2-metrics strong' })).not.toHaveLength(0);
    expect(await screen.findByText(/weekly-11/)).toBeInTheDocument();
    expect(screen.getAllByText('策略未披露')).not.toHaveLength(0);

    fireEvent.click(screen.getByRole('button', { name: '月报' }));
    fireEvent.change(screen.getByLabelText('报告截止日期'), { target: { value: '2026-07-12' } });
    fireEvent.click(screen.getByRole('button', { name: 'English' }));
    fireEvent.click(screen.getByRole('button', { name: 'Markdown' }));
    fireEvent.click(screen.getByRole('button', { name: '仅本人' }));
    fireEvent.click(screen.getByRole('button', { name: /Risks/ }));
    fireEvent.click(screen.getByRole('button', { name: '生成报告' }));

    await waitFor(() => expect(reportApi.generateVkpiReport).toHaveBeenCalledWith('token', {
      period: 'monthly',
      date: '2026-07-12',
      language: 'en',
      sections: ['kpiOverview', 'attribution', 'projects', 'ledger', 'summary'],
      format: 'markdown',
      scope: 'self',
    }));
    expect(await screen.findByText('新生成的服务端摘要')).toBeInTheDocument();
    const serverMetrics = document.querySelector('.report-v2-server-metrics');
    expect(serverMetrics).not.toBeNull();
    expect(within(serverMetrics as HTMLElement).getByText('$0')).toBeInTheDocument();
    expect(within(serverMetrics as HTMLElement).getByText('待数据')).toBeInTheDocument();
    expect(screen.getAllByText(/确定性描述模式（未调用模型）/)).not.toHaveLength(0);
  });

  it('shows an honest failed state when generation returns a server error', async () => {
    reportApi.generateVkpiReport.mockRejectedValueOnce({ status: 500 });
    renderPage();

    fireEvent.click(screen.getByRole('button', { name: '生成报告' }));

    expect(await screen.findByText('报告生成失败，系统未产出可用结果。')).toHaveAttribute('role', 'alert');
  });
});

describe('ReportCenterV2Page report history', () => {
  it('refreshes, reopens, downloads, archives, views archived reports, and restores', async () => {
    renderPage();

    expect(await screen.findByText(/weekly-11/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '刷新报告历史' }));
    await waitFor(() => expect(reportApi.listVkpiReports).toHaveBeenCalledWith('token', false));

    fireEvent.click(screen.getByRole('button', { name: '重开' }));
    expect(screen.getByText('服务端报告')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '下载' }));
    await waitFor(() => expect(reportApi.downloadVkpiFile).toHaveBeenCalledWith(
      'token',
      '/api/admin/vkpi/reports/files/11/download?format=pdf',
      'weekly-11.pdf',
    ));

    fireEvent.click(screen.getByRole('button', { name: '归档' }));
    fireEvent.click(screen.getByRole('button', { name: '确认归档' }));
    await waitFor(() => expect(reportApi.archiveVkpiReport).toHaveBeenCalledWith('token', 11));

    fireEvent.click(screen.getByRole('button', { name: '已归档' }));
    expect(await screen.findByText(/weekly-12/)).toBeInTheDocument();
    expect(reportApi.listVkpiReports).toHaveBeenCalledWith('token', true);

    fireEvent.click(screen.getByRole('button', { name: '恢复' }));
    await waitFor(() => expect(reportApi.restoreVkpiReport).toHaveBeenCalledWith('token', 12));
  });

  it('does not disguise a history permission failure as an empty list', async () => {
    reportApi.listVkpiReports.mockRejectedValueOnce({ status: 403 });
    renderPage();

    expect(await screen.findByText('权限不足：你不能执行此报告操作。')).toHaveAttribute('role', 'alert');
    expect(screen.queryByText('尚未生成真实报告')).not.toBeInTheDocument();
  });
});
