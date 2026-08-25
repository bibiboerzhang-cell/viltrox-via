import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { VkpiProjectVideoAnalysisCacheItem } from '../../../../../services/vkpi/projects-api';
import type { VkpiProjectRow } from '../../../vkpiTypes';
import { buildAnalysisItemLookup, qaItemForAnalysis } from './CampaignRetrospectiveTab.helpers';
import { ProjectVideoAnalysisCard } from './CampaignRetrospectiveTab.Sections';

const row = {
  id: '7',
  kolName: 'Creator',
  platform: 'YouTube',
  views: 100,
  likes: 10,
  comments: 2,
} as VkpiProjectRow;

function item(method: string, state: VkpiProjectVideoAnalysisCacheItem['state']): VkpiProjectVideoAnalysisCacheItem {
  return {
    evidence_id: 11,
    content_url: 'https://example.com/video/11',
    state,
    active_job: state === 'queued' ? { id: 91, status: 'queued' } : null,
    entry: state === 'ready' ? {
      target_type: 'content_evidence',
      target_id: '11',
      derive_method: method,
      status: 'ready',
      result: method.includes('keyframe_qa') ? { qa_pass: true } : {},
    } : null,
  };
}

describe('ProjectVideoAnalysisCard progressive state', () => {
  it.each(['queued', 'running', 'failed', 'quality_incomplete', 'unsupported', 'not_requested'] as const)(
    '通过真实 QA lookup 保留 %s 状态',
    (state) => {
      const analysisItem = item('video_analysis_final_v1', 'ready');
      const qaItem = item('video_analysis_final_v1_keyframe_qa', state);
      const qaLookup = buildAnalysisItemLookup([qaItem]);

      expect(qaItemForAnalysis(analysisItem, qaLookup)).toBe(qaItem);
    },
  );

  it('通过真实 QA lookup 保留 ready entry', () => {
    const analysisItem = item('video_analysis_final_v1', 'ready');
    const qaItem = item('video_analysis_final_v1_keyframe_qa', 'ready');
    const qaLookup = buildAnalysisItemLookup([qaItem]);

    expect(qaItemForAnalysis(analysisItem, qaLookup)).toBe(qaItem);
  });

  it('final_v1 已完成且 QA 有 active_job 时诚实标记待核验', () => {
    render(
      <ProjectVideoAnalysisCard
        row={row}
        item={item('video_analysis_final_v1', 'ready')}
        qaItem={item('video_analysis_final_v1_keyframe_qa', 'queued')}
      />,
    );
    expect(screen.getByText('分析完成·QA待核验')).toBeInTheDocument();
  });

  it('非 YouTube 的 QA 不伪装成队列中', () => {
    render(
      <ProjectVideoAnalysisCard
        row={row}
        item={item('video_analysis_final_v1', 'ready')}
        qaItem={item('video_analysis_final_v1_keyframe_qa', 'unsupported')}
      />,
    );
    expect(screen.getByText('分析完成·QA仅支持YouTube')).toBeInTheDocument();
  });

  it('QA ready 后才标记已核验', () => {
    render(
      <ProjectVideoAnalysisCard
        row={row}
        item={item('video_analysis_final_v1', 'ready')}
        qaItem={item('video_analysis_final_v1_keyframe_qa', 'ready')}
      />,
    );
    expect(screen.getByText('已核验')).toBeInTheDocument();
  });

  it('quality_incomplete 是独立终态，不渲染缓存 payload', () => {
    const qualityItem = item('video_analysis_final_v1', 'quality_incomplete');
    qualityItem.entry = {
      target_type: 'content_evidence',
      target_id: '11',
      derive_method: 'video_analysis_final_v1',
      status: 'quality_incomplete',
      result: { verdict: 'DO NOT RENDER' },
    };
    qualityItem.terminal_reason = 'final_v1_quality_incomplete';

    render(<ProjectVideoAnalysisCard row={row} item={qualityItem} />);

    expect(screen.getByText('结果质量未通过')).toBeInTheDocument();
    expect(screen.getByText(/待重试或人工复核/)).toBeInTheDocument();
    expect(screen.queryByText('DO NOT RENDER')).not.toBeInTheDocument();
  });

  it('legacy_unverified 显示历史待核验，不渲染 payload', () => {
    const legacyItem = item('video_analysis_final_v1', 'legacy_unverified');
    legacyItem.entry = {
      target_type: 'video', target_id: '11', derive_method: 'video_analysis_final_v1',
      status: 'ready', result: { verdict: 'LEGACY PAYLOAD' },
    };
    render(<ProjectVideoAnalysisCard row={row} item={legacyItem} />);
    expect(screen.getByText('历史结果待核验')).toBeInTheDocument();
    expect(screen.queryByText('LEGACY PAYLOAD')).not.toBeInTheDocument();
  });
});
