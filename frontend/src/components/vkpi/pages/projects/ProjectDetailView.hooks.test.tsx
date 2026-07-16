import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getProjectVideoAnalysisCacheMulti, type VkpiProjectVideoAnalysisCacheResponse } from '../../../../services/vkpi/projects-api';
import {
  mergeProjectVideoAnalysisCache,
  projectVideoAnalysisPollDelay,
  useProjectVideoAnalysisCache,
} from './ProjectDetailView.hooks';

vi.mock('../../../../services/vkpi/projects-api', async () => {
  const actual = await vi.importActual<typeof import('../../../../services/vkpi/projects-api')>('../../../../services/vkpi/projects-api');
  return { ...actual, getProjectVideoAnalysisCacheMulti: vi.fn() };
});

const getCacheMock = vi.mocked(getProjectVideoAnalysisCacheMulti);

function cache(
  method: string,
  state: 'ready' | 'pending' | 'queued' | 'running' | 'failed' | 'unsupported' | 'not_requested',
  result: Record<string, unknown> = {},
): VkpiProjectVideoAnalysisCacheResponse {
  const active = state === 'queued' || state === 'running';
  return {
    project_id: 7,
    derive_method: method,
    items: [{
      evidence_id: 11,
      content_url: 'https://example.com/video/11',
      title: state === 'ready' ? '完整标题' : '',
      state,
      active_job: active ? { id: 91, status: state } : null,
      entry: state === 'ready' ? {
        target_type: 'content_evidence',
        target_id: '11',
        derive_method: method,
        status: 'ready',
        result,
      } : null,
    }],
    summary: {
      evidence_count: 1,
      ready_count: state === 'ready' ? 1 : 0,
      pending_count: active ? 1 : 0,
    },
  };
}

describe('project video progressive cache', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    getCacheMock.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('按 evidence/url 合并且不会让 ready 倒退为 pending 或闪空', () => {
    const ready = cache('video_analysis_final_v1', 'ready', { verdict: 'keep me' });
    const pending = cache('video_analysis_final_v1', 'pending');
    pending.items[0].title = '';

    const merged = mergeProjectVideoAnalysisCache(ready, pending);

    expect(merged?.items).toHaveLength(1);
    expect(merged?.items[0]).toMatchObject({ state: 'ready', title: '完整标题' });
    expect(merged?.items[0].entry?.result).toEqual({ verdict: 'keep me' });
    expect(merged?.summary).toMatchObject({ evidence_count: 1, ready_count: 1, pending_count: 0 });
  });

  it('两边都有不同 evidence_id 时不因 URL 相同而串分析', () => {
    const previous = cache('video_analysis_final_v1', 'ready', { old: true });
    previous.items[0].content_url = 'https://example.com/shared';
    const incoming = cache('video_analysis_final_v1', 'queued');
    incoming.items[0].evidence_id = 12;
    incoming.items[0].content_url = 'https://example.com/shared';

    const merged = mergeProjectVideoAnalysisCache(previous, incoming);

    expect(merged?.items[0]).toMatchObject({ evidence_id: 12, state: 'queued' });
    expect(merged?.items[0].entry).toBeNull();
  });

  it('ready 更新采用完整 incoming entry，不拼接旧分析版本', () => {
    const previous = cache('video_analysis_final_v1', 'ready', { old_only: true });
    const incoming = cache('video_analysis_final_v1', 'ready', { new_only: true });
    incoming.items[0].title = '';

    const merged = mergeProjectVideoAnalysisCache(previous, incoming);

    expect(merged?.items[0].title).toBe('完整标题');
    expect(merged?.items[0].entry?.result).toEqual({ new_only: true });
  });

  it('incoming 缺席的旧 active 项会被移除，不造成永久轮询', () => {
    const previous = cache('video_analysis_final_v1', 'queued');
    const incoming = cache('video_analysis_final_v1', 'not_requested');
    incoming.items = [];
    incoming.summary = { evidence_count: 0, ready_count: 0, pending_count: 0 };

    const merged = mergeProjectVideoAnalysisCache(previous, incoming);

    expect(merged?.items).toEqual([]);
    expect(merged?.summary.pending_count).toBe(0);
  });

  it('有明确 active_job 时 2.5 秒刷新，final 与 QA 都终态后停止', async () => {
    getCacheMock
      .mockResolvedValueOnce({
        project_id: 7,
        by_method: {
          video_analysis_final_v1: cache('video_analysis_final_v1', 'queued'),
          video_analysis_final_v1_keyframe_qa: cache('video_analysis_final_v1_keyframe_qa', 'running'),
        },
      })
      .mockResolvedValueOnce({
        project_id: 7,
        by_method: {
          video_analysis_final_v1: cache('video_analysis_final_v1', 'ready', { verdict: 'done' }),
          video_analysis_final_v1_keyframe_qa: cache('video_analysis_final_v1_keyframe_qa', 'ready', { qa_pass: true }),
        },
    });

    const { result } = renderHook(() => useProjectVideoAnalysisCache('token', '7'));
    await act(async () => { await Promise.resolve(); });
    expect(getCacheMock).toHaveBeenCalledTimes(1);

    await act(async () => { await vi.advanceTimersByTimeAsync(2499); });
    expect(getCacheMock).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(getCacheMock).toHaveBeenCalledTimes(2);
    expect(result.current.videoQaCache?.summary.pending_count).toBe(0);

    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(getCacheMock).toHaveBeenCalledTimes(2);
    expect(result.current.videoAnalysisCache?.items[0].state).toBe('ready');
  });

  it('老后端 pending 但无 active_job 时不自动轮询', async () => {
    getCacheMock.mockResolvedValue({
      project_id: 7,
      by_method: {
        video_analysis_final_v1: cache('video_analysis_final_v1', 'pending'),
        video_analysis_final_v1_keyframe_qa: cache('video_analysis_final_v1_keyframe_qa', 'pending'),
      },
    });
    renderHook(() => useProjectVideoAnalysisCache('token', '7'));
    await act(async () => { await Promise.resolve(); });

    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(getCacheMock).toHaveBeenCalledTimes(1);
  });

  it('首次读取失败最多尝试三次', async () => {
    getCacheMock.mockRejectedValue(new Error('offline'));
    renderHook(() => useProjectVideoAnalysisCache('token', '7'));
    await act(async () => { await Promise.resolve(); });
    expect(getCacheMock).toHaveBeenCalledTimes(1);

    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(getCacheMock).toHaveBeenCalledTimes(3);
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(getCacheMock).toHaveBeenCalledTimes(3);
  });

  it('五分钟后从 2.5 秒退避到 10 秒', () => {
    expect(projectVideoAnalysisPollDelay(5 * 60 * 1000 - 1)).toBe(2500);
    expect(projectVideoAnalysisPollDelay(5 * 60 * 1000)).toBe(10000);
  });
});
