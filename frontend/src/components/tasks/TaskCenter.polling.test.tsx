import React from 'react';
import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

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

import { TaskCenterProvider, useTaskCenter } from './TaskCenter';

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function setVisibility(value: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    value,
  });
  Object.defineProperty(document, 'hidden', {
    configurable: true,
    value: value === 'hidden',
  });
}

function TaskSnapshot() {
  const { tasks } = useTaskCenter();
  return (
    <output data-testid="task-snapshot">
      {tasks.map((task) => `${task.task_id}:${task.status}`).join(',')}
    </output>
  );
}

function WatcherProbe() {
  const { waitForTask } = useTaskCenter();
  const [status, setStatus] = React.useState('等待');
  return (
    <>
      <button
        type="button"
        onClick={() => waitForTask('watched-task', { onDone: () => setStatus('完成') })}
      >
        监听任务
      </button>
      <output data-testid="watcher-status">{status}</output>
    </>
  );
}

function renderProvider(apiToken: string) {
  return render(
    <TaskCenterProvider apiToken={apiToken}>
      <TaskSnapshot />
    </TaskCenterProvider>,
  );
}

async function flushMicrotasks() {
  await act(async () => {
    await Promise.resolve();
  });
}

describe('TaskCenterProvider polling lifecycle', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setVisibility('visible');
    taskMocks.listTasks.mockReset();
    taskMocks.getTaskRealtimeStatus.mockReset();
    taskMocks.cancelTask.mockReset();
    taskMocks.retryTask.mockReset();
    taskMocks.prepareSseStream.mockReset();
    taskMocks.listTasks.mockResolvedValue([]);
    taskMocks.getTaskRealtimeStatus.mockResolvedValue({
      sse_available: false,
      job_queue_present: true,
      task_event_subscription_available: false,
      polling_fallback_required: true,
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllTimers();
    vi.useRealTimers();
    setVisibility('visible');
  });

  it('waits for the current request to settle before starting the idle delay', async () => {
    const first = deferred<unknown[]>();
    taskMocks.listTasks
      .mockReturnValueOnce(first.promise)
      .mockResolvedValue([]);

    renderProvider('token-a');
    await flushMicrotasks();
    expect(taskMocks.listTasks).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(20_000);
    });
    expect(taskMocks.listTasks).toHaveBeenCalledTimes(1);

    await act(async () => {
      first.resolve([]);
      await first.promise;
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(59_999);
    });
    expect(taskMocks.listTasks).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(taskMocks.listTasks).toHaveBeenCalledTimes(2);
  });

  it('keeps three-second progress while active then backs off after terminal state', async () => {
    taskMocks.listTasks
      .mockResolvedValueOnce([{
        task_id: 'active-task',
        task_type: 'vkpi_ai_analysis',
        status: 'running',
        created_at: '2026-08-04T00:00:00Z',
      }])
      .mockResolvedValueOnce([{
        task_id: 'active-task',
        task_type: 'vkpi_ai_analysis',
        status: 'done',
        created_at: '2026-08-04T00:00:00Z',
        finished_at: '2026-08-04T00:00:03Z',
      }])
      .mockResolvedValue([]);

    renderProvider('token-a');
    await flushMicrotasks();
    expect(taskMocks.listTasks).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_999);
    });
    expect(taskMocks.listTasks).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(taskMocks.listTasks).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(59_999);
    });
    expect(taskMocks.listTasks).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(taskMocks.listTasks).toHaveBeenCalledTimes(3);
  });

  it('retries a failed idle refresh after five seconds instead of waiting a minute', async () => {
    taskMocks.listTasks
      .mockRejectedValueOnce(new Error('network unavailable'))
      .mockResolvedValue([]);

    renderProvider('token-a');
    await flushMicrotasks();
    expect(taskMocks.listTasks).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_999);
    });
    expect(taskMocks.listTasks).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(taskMocks.listTasks).toHaveBeenCalledTimes(2);
  });

  it('wakes an idle cycle immediately when a task watcher is registered', async () => {
    render(
      <TaskCenterProvider apiToken="token-a">
        <WatcherProbe />
      </TaskCenterProvider>,
    );
    await flushMicrotasks();
    expect(taskMocks.listTasks).toHaveBeenCalledTimes(1);

    await act(async () => {
      screen.getByRole('button', { name: '监听任务' }).click();
      await Promise.resolve();
    });
    expect(taskMocks.listTasks).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_999);
    });
    expect(taskMocks.listTasks).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(taskMocks.listTasks).toHaveBeenCalledTimes(3);
  });

  it('does not carry a task watcher into a different login token', async () => {
    taskMocks.listTasks
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([{
        task_id: 'watched-task',
        task_type: 'vkpi_ai_analysis',
        status: 'done',
        created_at: '2026-08-04T00:00:00Z',
        finished_at: '2026-08-04T00:00:01Z',
      }]);

    const view = render(
      <TaskCenterProvider apiToken="token-a">
        <WatcherProbe />
      </TaskCenterProvider>,
    );
    await flushMicrotasks();

    await act(async () => {
      screen.getByRole('button', { name: '监听任务' }).click();
      await Promise.resolve();
    });
    expect(taskMocks.listTasks).toHaveBeenCalledTimes(2);

    view.rerender(
      <TaskCenterProvider apiToken="token-b">
        <WatcherProbe />
      </TaskCenterProvider>,
    );
    await flushMicrotasks();

    expect(taskMocks.listTasks).toHaveBeenCalledTimes(3);
    expect(screen.getByTestId('watcher-status')).toHaveTextContent('等待');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });
    expect(taskMocks.listTasks).toHaveBeenCalledTimes(3);
  });

  it('pauses and aborts while hidden, then performs exactly one immediate refresh when visible', async () => {
    const observedSignals: AbortSignal[] = [];
    taskMocks.listTasks.mockImplementation(
      (_token: string, _filters: unknown, request: { signal: AbortSignal }) => new Promise((_, reject) => {
        observedSignals.push(request.signal);
        request.signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
      }),
    );

    renderProvider('token-a');
    await flushMicrotasks();
    expect(taskMocks.listTasks).toHaveBeenCalledTimes(1);
    expect(observedSignals[0].aborted).toBe(false);

    setVisibility('hidden');
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
      await Promise.resolve();
    });
    expect(observedSignals[0].aborted).toBe(true);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(taskMocks.listTasks).toHaveBeenCalledTimes(1);

    setVisibility('visible');
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
      await Promise.resolve();
    });
    expect(taskMocks.listTasks).toHaveBeenCalledTimes(2);
    expect(observedSignals[1].aborted).toBe(false);
  });

  it('aborts the old token request and ignores its late response', async () => {
    const oldRequest = deferred<unknown[]>();
    const newRequest = deferred<unknown[]>();
    taskMocks.listTasks.mockImplementation((token: string) => (
      token === 'token-a' ? oldRequest.promise : newRequest.promise
    ));

    const view = renderProvider('token-a');
    await flushMicrotasks();
    const oldSignal = taskMocks.listTasks.mock.calls[0][2].signal as AbortSignal;

    view.rerender(
      <TaskCenterProvider apiToken="token-b">
        <TaskSnapshot />
      </TaskCenterProvider>,
    );
    await flushMicrotasks();
    expect(oldSignal.aborted).toBe(true);
    expect(taskMocks.listTasks).toHaveBeenCalledTimes(2);

    await act(async () => {
      newRequest.resolve([{
        task_id: 'new-task',
        task_type: 'vkpi_ai_analysis',
        status: 'running',
        created_at: '2026-08-04T00:00:00Z',
      }]);
      await newRequest.promise;
    });
    expect(screen.getByTestId('task-snapshot')).toHaveTextContent('new-task:running');

    await act(async () => {
      oldRequest.resolve([{
        task_id: 'old-task',
        task_type: 'vkpi_ai_analysis',
        status: 'done',
        created_at: '2026-08-03T00:00:00Z',
      }]);
      await oldRequest.promise;
    });
    expect(screen.getByTestId('task-snapshot')).toHaveTextContent('new-task:running');
    expect(screen.getByTestId('task-snapshot')).not.toHaveTextContent('old-task');
  });

  it('aborts the active task request on unmount', async () => {
    const request = deferred<unknown[]>();
    taskMocks.listTasks.mockReturnValue(request.promise);

    const view = renderProvider('token-a');
    await flushMicrotasks();
    const signal = taskMocks.listTasks.mock.calls[0][2].signal as AbortSignal;
    expect(signal.aborted).toBe(false);

    view.unmount();
    expect(signal.aborted).toBe(true);

    await act(async () => {
      request.resolve([]);
      await request.promise;
    });
  });
});
