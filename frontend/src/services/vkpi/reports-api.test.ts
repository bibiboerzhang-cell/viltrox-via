import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.hoisted(() => vi.fn());

vi.mock('../http', () => ({
  ApiResponseError: class ApiResponseError extends Error {
    status: number;
    payload: unknown;

    constructor(response: Response, payload: unknown) {
      super(String((payload as { detail?: string })?.detail || `${response.status} ${response.statusText}`));
      this.status = response.status;
      this.payload = payload;
    }
  },
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  buildApiUrl: (path: string) => path,
  jsonBody: (payload: unknown) => JSON.stringify(payload),
}));

import {
  VKPI_REPORT_SECTION_KEYS,
  archiveVkpiReport,
  buildVkpiReportPayload,
  downloadVkpiFile,
  generateVkpiReport,
  listVkpiReports,
  reportApiErrorMessage,
  reportModelPolicyLabel,
  restoreVkpiReport,
} from './reports-api';

const config = {
  period: 'weekly' as const,
  date: '2026-07-13',
  language: 'en' as const,
  sections: ['kpiOverview', 'summary'] as const,
  format: 'markdown' as const,
  scope: 'self' as const,
  staffId: '7',
};

beforeEach(() => {
  apiFetch.mockReset();
  apiFetch.mockResolvedValue({});
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('reports API request contract', () => {
  it('submits period/date/language/sections/format/scope and derives an inclusive date range', () => {
    expect(buildVkpiReportPayload(config)).toEqual({
      report_type: 'weekly',
      period: 'weekly',
      period_days: 7,
      date: '2026-07-13',
      date_from: '2026-07-07',
      date_to: '2026-07-13',
      language: 'en',
      sections: ['kpiOverview', 'summary'],
      format: 'markdown',
      scope: 'self',
      staff_id: 7,
    });
  });

  it('calls the real generator and keeps a real zero distinct from unknown data', async () => {
    apiFetch.mockResolvedValueOnce({
      report_run_id: 42,
      report_uid: 'weekly-42',
      status: 'ready',
      download_url: '/api/admin/vkpi/reports/files/42/download?format=pdf',
      context: {
        data_status: 'partial',
        period_start: '2026-07-07T00:00:00Z',
        period_end: '2026-07-13T00:00:00Z',
        report_spec: { report_type: 'weekly' },
        summary_text: 'Server summary',
        model_policy: {
          mode: 'deterministic_descriptive',
          provider_calls_allowed: false,
          deterministic_only: true,
          claim_level: 'descriptive_only',
        },
        kpis: [
          { key: 'views', label: 'Views', value: '0', raw_value: 0, data_status: 'real' },
          { key: 'gmv', label: 'GMV', value: null, raw_value: null, data_status: 'awaiting_source' },
        ],
      },
    });

    const result = await generateVkpiReport('token', config);

    expect(apiFetch).toHaveBeenCalledWith(
      '/api/admin/vkpi/reports/weekly/generate',
      expect.objectContaining({ method: 'POST', timeoutMs: 120_000 }),
      'token',
    );
    expect(JSON.parse(apiFetch.mock.calls[0][1].body)).toEqual(buildVkpiReportPayload(config));
    expect(result).toMatchObject({
      reportId: 42,
      reportUid: 'weekly-42',
      reportType: 'weekly',
      periodStart: '2026-07-07T00:00:00Z',
      periodEnd: '2026-07-13T00:00:00Z',
      summary: 'Server summary',
      claimLevel: 'descriptive_only',
    });
    expect(result.modelPolicy).toEqual(expect.objectContaining({
      mode: 'deterministic_descriptive',
      provider_calls_allowed: false,
    }));
    expect(reportModelPolicyLabel(result.modelPolicy, result.claimLevel))
      .toBe('确定性描述模式（未调用模型）');
    expect(result.metrics[0]).toMatchObject({ rawValue: 0, value: '0', dataStatus: 'real' });
    expect(result.metrics[1]).toMatchObject({ rawValue: null, dataStatus: 'awaiting_source' });
  });

  it('loads current/archived history and calls soft archive/restore endpoints', async () => {
    apiFetch.mockResolvedValueOnce({
      reports: [{
        id: 9,
        report_uid: 'weekly-9',
        report_type: 'weekly',
        status: 'archived',
        summary_text: 'Saved summary',
        data_status: 'awaiting_source',
        archived_at: '2026-07-13T12:00:00Z',
      }],
      count: 1,
      archived: true,
    });

    const result = await listVkpiReports('token', true);
    await archiveVkpiReport('token', 9);
    await restoreVkpiReport('token', 9);

    expect(apiFetch.mock.calls[0][0]).toBe('/api/admin/vkpi/reports?archived=true&limit=50');
    expect(result.reports[0]).toMatchObject({ id: 9, reportUid: 'weekly-9', dataStatus: 'awaiting_source' });
    expect(result.reports[0].modelPolicy).toBeNull();
    expect(reportModelPolicyLabel(result.reports[0].modelPolicy, result.reports[0].claimLevel))
      .toBe('策略未披露');
    expect(apiFetch.mock.calls[1]).toEqual([
      '/api/admin/vkpi/reports/9',
      { method: 'DELETE', body: JSON.stringify({ reason: 'user_archived' }) },
      'token',
    ]);
    expect(apiFetch.mock.calls[2]).toEqual([
      '/api/admin/vkpi/reports/9/restore',
      { method: 'POST' },
      'token',
    ]);
  });
});

describe('reports API download and errors', () => {
  it('downloads through an authenticated request instead of opening a bare URL', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(new Blob(['pdf']), {
      status: 200,
      headers: { 'Content-Disposition': 'attachment; filename="weekly-42.pdf"' },
    }));
    const createObjectURL = vi.fn(() => 'blob:report');
    const revokeObjectURL = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);

    const filename = await downloadVkpiFile('secret', '/api/admin/vkpi/reports/files/42/download?format=pdf', 'fallback.pdf');

    expect(filename).toBe('weekly-42.pdf');
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer secret');
    expect(init.credentials).toBe('include');
    expect(createObjectURL).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:report');
  });

  it('does not send the bearer token to a foreign download origin', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    await expect(downloadVkpiFile('secret', 'https://files.example.test/report.pdf', 'report.pdf'))
      .rejects.toThrow('已停止发送登录凭证');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('uses explicit permission and server-failure messages', () => {
    expect(reportApiErrorMessage({ status: 403 }, '加载失败')).toContain('权限不足');
    expect(reportApiErrorMessage({ status: 500 }, '报告生成失败')).toBe('报告生成失败，系统未产出可用结果。');
    expect(VKPI_REPORT_SECTION_KEYS).toHaveLength(6);
  });
});
