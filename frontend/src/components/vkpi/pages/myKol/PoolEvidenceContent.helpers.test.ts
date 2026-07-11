import { describe, it, expect } from 'vitest';
import {
  analysisTerminalReceipt,
  commentsTerminalReceipt,
  findQueueJob,
  missingJobIdReceipt,
  waitJobTerminal,
} from './PoolEvidenceContent.helpers';

// 评论链修复冒烟(2026-07-11)三条红线:
// ①gone ≠ done:轮询封顶回执绝不写 ✓、不穿成功绿
// ②跨源不错认:ledger 同号任务的终态不能算到 apify job 头上
// ③失败必红:blocked 回执 tone 必须是 error

describe('findQueueJob 跨源匹配', () => {
  it('同 id 的 ledger 行不被错认,只认 source=apify_jobs', () => {
    const snapshot = {
      active: [{ id: '7', source: 'ledger', status: 'done' }],
      recent: [{ id: '7', source: 'apify_jobs', status: 'running' }],
    };
    const item = findQueueJob(snapshot, 7);
    expect(item).toBeTruthy();
    expect(item!.source).toBe('apify_jobs');
    expect(item!.status).toBe('running');
  });

  it('只有异源同号行时视为找不到(宁可 gone 不可错认)', () => {
    const snapshot = { active: [{ id: '9', source: 'llm_calls', status: 'done' }], recent: [] };
    expect(findQueueJob(snapshot, 9)).toBeUndefined();
  });
});

describe('waitJobTerminal 终态轮询', () => {
  const notCancelled = { current: false };

  it('ledger 同号 done 不触发假终态,轮询封顶后按 gone 收场', async () => {
    const terminal = await waitJobTerminal('t', 7, notCancelled, {
      intervalMs: 0,
      maxTries: 4,
      fetchQueue: async () => ({
        active: [{ id: '7', source: 'ledger', status: 'done' }],
        recent: [],
      }),
    });
    expect(terminal).toEqual({ state: 'gone' });
  });

  it('apify_jobs done 才算 done', async () => {
    const terminal = await waitJobTerminal('t', 7, notCancelled, {
      intervalMs: 0,
      maxTries: 4,
      fetchQueue: async () => ({
        active: [],
        recent: [{ id: '7', source: 'apify_jobs', status: 'done' }],
      }),
    });
    expect(terminal).toEqual({ state: 'done' });
  });

  it('failed/blocked 带原因返回 blocked', async () => {
    const terminal = await waitJobTerminal('t', 7, notCancelled, {
      intervalMs: 0,
      maxTries: 4,
      fetchQueue: async () => ({
        active: [{ id: '7', source: 'apify_jobs', status: 'failed', error: 'budget_hard_stop' }],
        recent: [],
      }),
    });
    expect(terminal).toEqual({ state: 'blocked', error: 'budget_hard_stop' });
  });
});

describe('终态回执文案(gone 不写 ✓ / 失败必红)', () => {
  it('评论采集 gone:无 ✓、非成功 tone、明说仍在后台', () => {
    const receipt = commentsTerminalReceipt({ state: 'gone' });
    expect(receipt.text).not.toContain('✓');
    expect(receipt.text).toContain('仍在后台排队/执行');
    expect(receipt.tone).not.toBe('ok');
    expect(receipt.refresh).toBe(false);
  });

  it('账号分析 gone:无 ✓、非成功 tone', () => {
    const receipt = analysisTerminalReceipt({ state: 'gone' });
    expect(receipt.text).not.toContain('✓');
    expect(receipt.text).toContain('仍在后台排队/执行');
    expect(receipt.tone).not.toBe('ok');
    expect(receipt.refresh).toBe(false);
  });

  it('done 才有 ✓ 与刷新', () => {
    expect(commentsTerminalReceipt({ state: 'done' })).toMatchObject({ tone: 'ok', refresh: true });
    expect(commentsTerminalReceipt({ state: 'done' }).text).toContain('✓');
    expect(analysisTerminalReceipt({ state: 'done' })).toMatchObject({ tone: 'ok', refresh: true });
  });

  it('blocked 回执 tone=error 且带原因', () => {
    const receipt = commentsTerminalReceipt({ state: 'blocked', error: 'quota' });
    expect(receipt.tone).toBe('error');
    expect(receipt.text).toContain('quota');
    expect(analysisTerminalReceipt({ state: 'blocked' }).tone).toBe('error');
  });

  it('入队无 job_id 的逃生回执:非成功 tone,指路泳道', () => {
    const receipt = missingJobIdReceipt('评论采集');
    expect(receipt.tone).toBe('info');
    expect(receipt.text).toContain('未拿到任务号');
    expect(receipt.text).toContain('泳道');
  });
});
