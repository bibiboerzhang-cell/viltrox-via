import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { KolPoolV2Page } from './KolPoolV2Page';

const kolApi = vi.hoisted(() => ({
  getKolPoolEvidenceSummary: vi.fn(),
  getKolPoolIntelligenceCard: vi.fn(),
  getKolPoolItem: vi.fn(),
  getKolPoolSummary: vi.fn(),
  listKolPool: vi.fn(),
  promoteKolPoolToMain: vi.fn(),
}));

vi.mock('../../../domains/kol', () => kolApi);
vi.mock('../v2-shell/V2ShellTopbar', () => ({ V2ShellTopbar: () => null }));

const recent = new Date().toISOString();
const old = new Date(Date.now() - 30 * 86_400_000).toISOString();
const items = [
  {
    id: 1,
    handle: '@alpha',
    display_name: 'Alpha Creator',
    avatar_url: 'https://example.test/alpha.jpg',
    platform: 'YouTube',
    country: 'US',
    created_at: recent,
    followers: 1000,
    avg_views: 500,
    engagement_rate: 5,
    viltrox_fit_score: 82,
    trend_score: 75,
    trend_topic: 'cinema lens',
    freshness: { needs_refresh: false },
  },
  {
    id: 2,
    handle: '@beta',
    display_name: 'Beta Creator',
    platform: 'Instagram',
    country: 'CA',
    created_at: old,
    followers: 800,
    avg_views: null,
    engagement_rate: null,
    viltrox_fit_score: 41,
    trend_score: 20,
    freshness: { needs_refresh: true },
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  kolApi.listKolPool.mockResolvedValue({ items });
  kolApi.getKolPoolSummary.mockResolvedValue({});
  kolApi.getKolPoolItem.mockResolvedValue({ item: items[0] });
  kolApi.getKolPoolEvidenceSummary.mockResolvedValue({ summaries: [] });
  kolApi.getKolPoolIntelligenceCard.mockResolvedValue({ evidence_index: [] });
  kolApi.promoteKolPoolToMain.mockResolvedValue({ item: { ...items[0], linked_main_kol_id: 9 } });
});

describe('KolPoolV2Page button truth', () => {
  it('未接入的模式与 Reach 明确禁用，并解释原因', () => {
    render(<KolPoolV2Page />);

    expect(screen.getByRole('button', { name: /平衡/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /精准/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /探索/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /月度估算 Reach/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: '读取最新' })).toBeDisabled();
    expect(screen.getByRole('button', { name: /月度估算 Reach/ })).toHaveAttribute('title', expect.stringContaining('尚未接入'));
  });

  it('新发现与高 Trend 指标卡会真实筛选，Pool 总数会恢复全部', async () => {
    render(<KolPoolV2Page apiToken="token" />);

    expect(await screen.findByText('Alpha Creator')).toBeInTheDocument();
    expect(screen.getByText('Beta Creator')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^新发现/ }));
    expect(screen.getByText('近 7 天新发现')).toBeInTheDocument();
    expect(screen.getByText('Alpha Creator')).toBeInTheDocument();
    expect(screen.queryByText('Beta Creator')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^Pool 总数/ }));
    expect(screen.getByText('Beta Creator')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^待补全/ }));
    expect(screen.getByText('Beta Creator')).toBeInTheDocument();
    expect(screen.queryByText('Alpha Creator')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^Pool 总数/ }));
    fireEvent.click(screen.getByRole('button', { name: /^本周高 Trend/ }));
    expect(screen.getByText('本周高 Trend', { selector: '.kol-pool-v2-table-head b' })).toBeInTheDocument();
    expect(screen.getByText('Alpha Creator')).toBeInTheDocument();
    expect(screen.queryByText('Beta Creator')).not.toBeInTheDocument();

    await waitFor(() => expect(kolApi.listKolPool).toHaveBeenCalledWith('token', expect.objectContaining({ sortBy: 'fit' })));
  });
});
