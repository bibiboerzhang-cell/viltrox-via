// 拆自 PoolEvidenceContent.tsx(评论链缺陷修复,2026-07-11):
// job 终态轮询 + 回执文案纯函数。拆出的动机是可测性——
// 「gone 不写 ✓ / 失败必红 / 跨源不错认」三条红线在此处有冒烟测试直击。

export type JobTerminal = { state: 'done' | 'blocked' | 'gone'; error?: string };

export type FlowTone = 'ok' | 'error' | 'info';
export type FlowReceipt = { text: string; tone: FlowTone };
export type TerminalReceipt = FlowReceipt & { refresh: boolean };

type QueueItem = Record<string, unknown>;
export type QueueSnapshot = { active?: QueueItem[]; recent?: QueueItem[] };

// 队列聚合三源(apify_jobs / job_execution_ledger / vkpi_llm_calls),id 各自独立、
// 序号会重叠。本页入队拿到的 job_id 是 apify_jobs 行——不带 source 匹配会把
// ledger 同号任务的终态错认到本 job 头上(假完成/假失败都可能)。
export function findQueueJob(snapshot: QueueSnapshot, jobId: number): QueueItem | undefined {
  const all = [...(snapshot.active || []), ...(snapshot.recent || [])];
  return all.find((task) => String(task.id) === String(jobId) && String(task.source || '') === 'apify_jobs');
}

// 轮询 apify_jobs 终态(经 task-queue/compact):done/blocked 为准;
// 轮询封顶(默认 6s×30=3min)或任务滑出 recent 窗口(10min)时返回 gone——
// gone ≠ 完成(慢任务大多 >3min 才出终态),回执文案必须按「仍在后台」处理。
export async function waitJobTerminal(
  apiToken: string,
  jobId: number,
  cancelled: { current: boolean },
  {
    intervalMs = 6000,
    maxTries = 30,
    fetchQueue,
  }: { intervalMs?: number; maxTries?: number; fetchQueue?: () => Promise<QueueSnapshot> } = {},
): Promise<JobTerminal | null> {
  const loadQueue = fetchQueue || (async () => {
    const { getTaskQueueCompact } = await import('../../../../services/vkpi/tasks-api');
    return (await getTaskQueueCompact(apiToken, { limit: 50, recentMinutes: 10 })) as unknown as QueueSnapshot;
  });
  let seen = false;
  for (let i = 0; i < maxTries; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
    if (cancelled.current) return null;
    try {
      const resp = await loadQueue();
      const item = findQueueJob(resp, jobId);
      if (!item) {
        if (seen) return { state: 'gone' };
        if (i >= 2) return { state: 'gone' };
        continue;
      }
      seen = true;
      const status = String(item.status || '');
      if (status === 'done') return { state: 'done' };
      if (status === 'blocked' || status === 'failed') return { state: 'blocked', error: String(item.error || status).slice(0, 140) };
    } catch { /* 单次轮询失败不终止 */ }
  }
  return { state: 'gone' };
}

// 账号分析回执:done 才有 ✓ 与刷新;gone 只承认「仍在后台」,绝不假成功。
export function analysisTerminalReceipt(terminal: JobTerminal): TerminalReceipt {
  if (terminal.state === 'blocked') {
    return { text: `账号分析被拦:${terminal.error || '原因见泳道'}`, tone: 'error', refresh: false };
  }
  if (terminal.state === 'done') {
    return { text: '✓ 账号分析完成——列表已刷新(视频/缩略图/R2 状态以最新为准)。', tone: 'ok', refresh: true };
  }
  return { text: '账号分析仍在后台排队/执行(超出本页轮询窗口)——完成后到泳道或重开本页查看。', tone: 'info', refresh: false };
}

// 评论采集回执:同上,gone 不写 ✓。
export function commentsTerminalReceipt(terminal: JobTerminal): TerminalReceipt {
  if (terminal.state === 'blocked') {
    return { text: `评论采集被拦:${terminal.error || '原因见泳道'}`, tone: 'error', refresh: false };
  }
  if (terminal.state === 'done') {
    return { text: '✓ 评论采集完成——点视频卡「评论明细」即见正文。', tone: 'ok', refresh: true };
  }
  return { text: '评论采集仍在后台排队/执行(超出本页轮询窗口)——完成后到泳道或重开本页查看。', tone: 'info', refresh: false };
}

// 入队响应缺 job_id 的逃生回执:不静默、不挂死在「进行中」,明示去泳道看进度。
export function missingJobIdReceipt(flowLabel: string): FlowReceipt {
  return { text: `${flowLabel}已入队但未拿到任务号——进度请到泳道查看,完成后重开本页刷新。`, tone: 'info' };
}
