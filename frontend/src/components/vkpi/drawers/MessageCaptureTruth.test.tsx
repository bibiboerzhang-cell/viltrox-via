import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ProjectEvidenceForms } from './ProjectEvidenceForms';
import { ProjectDetailDrawer } from './ProjectDetailDrawer';
import { messageRecordDirectionLabel } from './messageRecordPresentation';
import { KolStageTimeline } from '../pages/projects/KolStageTimeline';
import type { VkpiProjectRow } from '../vkpiTypes';

describe('manual communication remains a record, not a transport receipt', () => {
  it.each([
    ['outbound', '我方沟通（手工记录）'], [' SENT ', '我方沟通（手工记录）'],
    ['inbound', '对方回复（手工记录）'], ['reply', '对方回复（手工记录）'],
    ['internal_note', '内部备注'], ['draft', '草稿记录'],
    [undefined, '沟通记录（方向待确认）'], ['provider_verified', '沟通记录（方向待确认）'],
  ])('does not treat direction %s as verification', (direction, label) => {
    expect(messageRecordDirectionLabel(direction)).toBe(label);
  });

  it.each(['outbound', 'inbound'])('saves %s manually without claiming to send', async (direction) => {
    const save = vi.fn().mockResolvedValue(undefined);
    render(<ProjectEvidenceForms projectId="71" onAddMessage={save} />);
    expect(screen.getByText(/仅保存人工沟通记录，不会发送消息/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('方向（人工记录）'), { target: { value: direction } });
    fireEvent.change(screen.getByPlaceholderText('粘贴沟通要点，例如报价、回复、交付承诺。'), { target: { value: '合成沟通留档' } });
    fireEvent.click(screen.getByRole('button', { name: '保存沟通记录' }));
    await waitFor(() => expect(save).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
      source: 'manual', direction, body: '合成沟通留档',
    })));
    expect(save.mock.calls[0][0]).not.toHaveProperty('communication_truth');
    expect(save.mock.calls[0][0]).not.toHaveProperty('sent');
    await waitFor(() => expect(screen.getByPlaceholderText('粘贴沟通要点，例如报价、回复、交付承诺。')).toHaveValue(''));
    expect(screen.queryByText('已发送')).not.toBeInTheDocument();
  });

  it.each(['outbound', 'inbound', 'internal_note'])('keeps raw %s and client claims unverified in the drawer', (direction) => {
    render(<ProjectDetailDrawer
      detail={{ project: { id: 71, project_name: '合成项目' }, messages: [{
        id: 91, source: 'email', direction, body: '合成记录内容', evidence_url: 'https://example.test/evidence',
        communication_truth: { sent: true, replied: true, transport_status: 'delivered' },
        metadata_json: { provider_verified: true },
      }] } as never}
      viewMode="manager" onClose={() => undefined}
    />);
    const record = screen.getByText('合成记录内容').closest('article');
    expect(record).toHaveTextContent(messageRecordDirectionLabel(direction));
    expect(record).toHaveTextContent('收发未核验');
    expect(record).not.toHaveTextContent('投递成功');
  });

  it('manual stage changes retain their workflow, without becoming message proof', () => {
    const row: VkpiProjectRow = {
      id: '81', kolName: '合成人选', kolHandle: '@fixture', platform: 'YouTube', campaign: '合成活动',
      stage: 'replied', latestMessageAt: '', latestMessageSource: 'Manual note', views: 0,
      clicks: null, orders: null, gmv: null, cost: null, roi: null, ownerName: '', updatedAt: '',
    };
    const move = vi.fn();
    render(<KolStageTimeline row={row} evidenceCount={0} movingRowId="" onMoveRowStage={move}
      onOpenScreenshotModal={vi.fn()} onOpenStageActionModal={vi.fn()}
      tracking={{ courier: '', no: '', status: '', last: '', delivered: false }} />);
    expect(screen.getAllByText('阶段由人工维护，不代表消息已发送或回复已核验。').length).toBeGreaterThan(0);
    expect(screen.queryByText('阶段证据随真实项目行同步。')).not.toBeInTheDocument();
    expect(screen.getByText('回复记录')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^推进到「.*」$/ }));
    expect(move).toHaveBeenCalledWith(row);
  });
});
