import React from 'react';
import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// C9(优化波 B)重渲染计数证明:轮询状态下沉到 store + selector 后,
//   · 只订阅动作(waitForTask)的消费方在任务快照变化时零重渲染;
//   · 全量消费方(useTaskCenter)只在快照真变时渲染一次,内容相同的拍子不渲染;
//   · Provider 的子树(children 元素)不因轮询重渲染。

const taskMocks = vi.hoisted(() => ({
  listTasks: vi.fn(),
  getTaskRealtimeStatus: vi.fn(),
  cancelTask: vi.fn(),
  retryTask: vi.fn(),
  prepareSseStream: vi.fn(),
}));

vi.mock('../../domains/tasks', () => ({
  TERMINAL_STATUSES: ['done', 'failed', 'cancelled', 'timeout', 'partial_done', 'prefilter_rejected'],
  buildTaskEventStreamUrl: (taskId: string) => `/tasks/${taskId}/events`,
  listTasks: taskMocks.listTasks,
  getTaskRealtimeStatus: taskMocks.getTaskRealtimeStatus,
  cancelTask: taskMocks.cancelTask,
  retryTask: taskMocks.retryTask,
}));

vi.mock('../../services/sse-api', () => ({
  prepareSseStream: taskMocks.prepareSseStream,
}));

import { TaskCenterProvider, useTaskCenter, useTaskCenterSelector } from './TaskCenter';

const renders = { action: 0, full: 0, child: 0 };

function ActionOnlyConsumer() {
  renders.action += 1;
  const waitForTask = useTaskCenterSelector((api) => api.waitForTask);
  return <output data-testid="action-only">{typeof waitForTask}</output>;
}

function FullConsumer() {
  renders.full += 1;
  const { tasks } = useTaskCenter();
  return <output data-testid="full">{tasks.map((task) => `${task.task_id}:${task.progress_pct ?? ''}`).join(',')}</output>;
}

function StaticChild() {
  renders.child += 1;
  return <output data-testid="child">static</output>;
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe('TaskCenter selector store · 轮询不击穿消费方', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    renders.action = 0;
    renders.full = 0;
    renders.child = 0;
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    Object.defineProperty(document, 'hidden', { configurable: true, value: false });
    taskMocks.listTasks.mockReset();
    taskMocks.getTaskRealtimeStatus.mockReset().mockResolvedValue({ sse_available: false });
    taskMocks.prepareSseStream.mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it('动作型消费方零重渲染;全量消费方只在快照真变时渲染;children 不因轮询重渲染', async () => {
    const running = (pct: number) => [{ task_id: 't1', task_type: 'vkpi_ai_analysis', status: 'running', progress_pct: pct }];
    taskMocks.listTasks
      .mockResolvedValueOnce(running(10))
      .mockResolvedValueOnce(running(10)) // 内容相同的一拍
      .mockResolvedValueOnce(running(40))
      .mockResolvedValue(running(40));

    render(
      <TaskCenterProvider apiToken="token-a">
        <ActionOnlyConsumer />
        <FullConsumer />
        <StaticChild />
      </TaskCenterProvider>,
    );
    await flush();
    expect(screen.getByTestId('full').textContent).toBe('t1:10');
    const afterFirst = { ...renders };

    // 第二拍:内容相同 → 指纹跳过,无人重渲染。
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });
    await flush();
    expect(renders.action).toBe(afterFirst.action);
    expect(renders.full).toBe(afterFirst.full);
    expect(renders.child).toBe(afterFirst.child);

    // 第三拍:进度 10→40 真变 → 只有全量消费方多渲染一次;动作型与静态子树纹丝不动。
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });
    await flush();
    expect(screen.getByTestId('full').textContent).toBe('t1:40');
    expect(renders.full).toBe(afterFirst.full + 1);
    expect(renders.action).toBe(afterFirst.action);
    expect(renders.child).toBe(afterFirst.child);
    expect(taskMocks.listTasks.mock.calls.length).toBeGreaterThanOrEqual(3);
  });

  it('selector 切片按 Object.is 比较;自定义 isEqual 可稳住派生数组', async () => {
    taskMocks.listTasks
      .mockResolvedValueOnce([{ task_id: 'a', task_type: 'x', status: 'running', progress_pct: 1 }])
      .mockResolvedValue([{ task_id: 'a', task_type: 'x', status: 'running', progress_pct: 2 }]);
    let idsRenders = 0;
    function ActiveIdsConsumer() {
      idsRenders += 1;
      const ids = useTaskCenterSelector(
        (api) => api.activeTasks.map((task) => task.task_id),
        (a, b) => a.join('|') === b.join('|'),
      );
      return <output data-testid="ids">{ids.join(',')}</output>;
    }
    render(
      <TaskCenterProvider apiToken="token-a">
        <ActiveIdsConsumer />
      </TaskCenterProvider>,
    );
    await flush();
    expect(screen.getByTestId('ids').textContent).toBe('a');
    const before = idsRenders;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });
    await flush();
    // 进度变了但活跃 id 集合没变 → 派生切片相等 → 不重渲染。
    expect(idsRenders).toBe(before);
  });
});
