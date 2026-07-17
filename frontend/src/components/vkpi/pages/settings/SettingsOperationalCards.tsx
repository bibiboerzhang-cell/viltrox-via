import React, { useEffect, useState } from 'react';

import { apiFetch } from '../../../../services/http';
import { formatLocal } from '../../lib/timeLocal';

// ── C1 数据健康哨兵 ──────────────────────────────────────────
// 黄金链路日检:数量由后端返回，绿/黄/红点 + label + detail + 检查时间 + 手动运行。
// 只读展示 + 手动触发;后端 /ops/health-sentinel 落 persistent_cache,调度每日 09:30 自动跑。
interface SentinelCheck {
  key: string;
  label: string;
  status: 'ok' | 'warn' | 'fail' | string;
  detail: string;
  checked_at?: string;
}

interface SentinelResult {
  available?: boolean;
  reason?: string;
  ran_at?: string;
  trigger?: string;
  summary?: { ok?: number; warn?: number; fail?: number; total?: number };
  checks?: SentinelCheck[];
}

// 2026-07-11 样式回归修:状态点走 --ds-* 语义色 token,6 主题(3 风格×明暗)自洽,不写死 hex。
const SENTINEL_DOT_COLOR: Record<string, string> = {
  ok: 'var(--ds-good)',
  warn: 'var(--ds-warn)',
  fail: 'var(--ds-crit)',
};

export function HealthSentinelCard({ apiToken }: { apiToken?: string }) {
  const [result, setResult] = useState<SentinelResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setLoading(true);
    apiFetch<SentinelResult>('/api/admin/vkpi/ops/health-sentinel', { timeoutMs: 15000 }, apiToken)
      .then((res) => {
        if (alive) setResult(res || null);
      })
      .catch((err) => {
        if (alive) setError(err instanceof Error ? err.message : '哨兵结果读取失败');
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => { alive = false; };
  }, [apiToken]);

  const runNow = async () => {
    if (!apiToken || running) return;
    setRunning(true);
    setError('');
    try {
      // 手动跑一轮(当前全部纯 SELECT 检查,通常几秒;留足超时)。
      const res = await apiFetch<SentinelResult>(
        '/api/admin/vkpi/ops/health-sentinel/run',
        { method: 'POST', timeoutMs: 60000 },
        apiToken,
      );
      setResult({ available: true, ...res });
    } catch (err) {
      setError(err instanceof Error ? err.message : '哨兵运行失败');
    } finally {
      setRunning(false);
    }
  };

  const checks = (result?.checks || []) as SentinelCheck[];
  const summary = result?.summary || {};
  const hasData = Boolean(result?.available !== false && checks.length);
  const totalChecks = summary.total ?? checks.length;

  return (
    <section className="vkpi-card" style={{ marginBottom: 16, padding: '14px 16px' }}>
      <div className="flex items-center justify-between" style={{ gap: 12, flexWrap: 'wrap' }}>
        <div>
          <strong style={{ fontSize: 14 }}>数据健康哨兵</strong>
          <span className="text-muted" style={{ marginLeft: 10, fontSize: 12 }}>
            {hasData
              ? `${totalChecks} 项黄金链路 · 正常 ${summary.ok ?? 0} / 留意 ${summary.warn ?? 0} / 失败 ${summary.fail ?? 0} · 上次运行 ${formatLocal(result?.ran_at)}`
              : loading
                ? '读取中…'
                : '尚未运行过(每日 09:30 自动跑,或手动运行一次)'}
          </span>
        </div>
        <button type="button" className="vkpi-btn" disabled={running || !apiToken} onClick={() => void runNow()}>
          {running ? '检查中…' : '手动运行'}
        </button>
      </div>
      {error ? <div className="vkpi-inline-message is-error" style={{ marginTop: 8 }}>{error}</div> : null}
      {hasData ? (
        <div style={{ marginTop: 10, display: 'grid', gap: 4 }}>
          {checks.map((check) => (
            <div key={check.key} className="flex items-center" style={{ gap: 8, fontSize: 12, lineHeight: 1.5 }}>
              <span
                aria-label={check.status}
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  flex: '0 0 auto',
                  background: SENTINEL_DOT_COLOR[check.status] || 'var(--ds-muted)',
                }}
              />
              <span style={{ minWidth: 170, fontWeight: 500 }}>{check.label}</span>
              <span className="text-muted" style={{ flex: 1 }}>{check.detail}</span>
              <span className="text-muted" style={{ flex: '0 0 auto', fontSize: 11 }}>
                {formatLocal(check.checked_at)}
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

// ── C5 成本记账(内部口径)──────────────────────────────────
// 今日/本月 Apify+LLM 消耗 + top actor + 记账覆盖盲区提示。
// 后端 /ops/cost-ledger 只读聚合 vkpi_ai_cost_ledger;精确=usageTotalUsd(平台用量),
// 付费 actor 费按内部价目表估算(estimated);权威账单以 Apify console 为准。
interface CostBucket {
  apify_usd?: number;
  apify_calls?: number;
  llm_usd?: number;
  llm_calls?: number;
  total_usd?: number;
}

interface CostActorRow {
  actor_id?: string;
  runs?: number;
  cost_usd?: number;
}

interface CostCoverage {
  apify_runs?: number;
  unified_entries?: number;
  estimated_entries?: number;
  zero_cost_entries?: number;
  unified_ratio?: number | null;
}

interface CostOverview {
  generated_at?: string;
  today?: CostBucket;
  month?: CostBucket;
  top_actors_today?: CostActorRow[];
  top_actors_month?: CostActorRow[];
  coverage?: CostCoverage;
  note?: string;
}

const usd = (value?: number) => `$${Number(value ?? 0).toFixed(2)}`;

export function CostLedgerCard({ apiToken }: { apiToken?: string }) {
  const [overview, setOverview] = useState<CostOverview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!apiToken) return;
    let alive = true;
    setLoading(true);
    apiFetch<CostOverview>(`/api/admin/vkpi/ops/cost-ledger?tz_offset_minutes=${-new Date().getTimezoneOffset()}`, { timeoutMs: 15000 }, apiToken)
      .then((res) => {
        if (alive) setOverview(res || null);
      })
      .catch((err) => {
        if (alive) setError(err instanceof Error ? err.message : '成本记账读取失败');
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => { alive = false; };
  }, [apiToken]);

  const today = overview?.today || {};
  const month = overview?.month || {};
  const coverage = overview?.coverage || {};
  const topActors = (overview?.top_actors_month || []).slice(0, 5);
  const apifyRuns = coverage.apify_runs ?? 0;
  const unified = coverage.unified_entries ?? 0;
  const stats: Array<{ label: string; value: string; sub: string }> = [
    { label: '今日 Apify', value: usd(today.apify_usd), sub: `${today.apify_calls ?? 0} 次调用` },
    { label: '今日 LLM 成本账本', value: usd(today.llm_usd), sub: `${today.llm_calls ?? 0} 次记账` },
    { label: '本月 Apify', value: usd(month.apify_usd), sub: `${month.apify_calls ?? 0} 次调用` },
    { label: '本月 LLM 成本账本', value: usd(month.llm_usd), sub: `${month.llm_calls ?? 0} 次记账` },
  ];

  return (
    <section className="vkpi-card" style={{ marginBottom: 16, padding: '14px 16px' }}>
      <div className="flex items-center justify-between" style={{ gap: 12, flexWrap: 'wrap' }}>
        <div>
          <strong style={{ fontSize: 14 }}>成本记账(内部口径)</strong>
          <span className="text-muted" style={{ marginLeft: 10, fontSize: 12 }}>
            {overview
              ? `本月合计 ${usd(month.total_usd)} · 统计时间 ${formatLocal(overview.generated_at)}`
              : loading
                ? '读取中…'
                : '暂无记账数据'}
          </span>
        </div>
      </div>
      {error ? <div className="vkpi-inline-message is-error" style={{ marginTop: 8 }}>{error}</div> : null}
      {overview ? (
        <>
          <div style={{ marginTop: 10, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 8 }}>
            {stats.map((item) => (
              <div key={item.label} style={{ padding: '8px 10px', borderRadius: 8, background: 'color-mix(in srgb, var(--ds-text) 6%, transparent)' }}>
                <div className="text-muted" style={{ fontSize: 11 }}>{item.label}</div>
                <div style={{ fontSize: 16, fontWeight: 600 }}>{item.value}</div>
                <div className="text-muted" style={{ fontSize: 11 }}>{item.sub}</div>
              </div>
            ))}
          </div>
          {topActors.length ? (
            <div style={{ marginTop: 10, display: 'grid', gap: 4 }}>
              <div className="text-muted" style={{ fontSize: 11, fontWeight: 500 }}>本月 Top Actor</div>
              {topActors.map((actor) => (
                <div key={actor.actor_id} className="flex items-center" style={{ gap: 8, fontSize: 12, lineHeight: 1.5 }}>
                  <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {actor.actor_id}
                  </span>
                  <span className="text-muted" style={{ flex: '0 0 auto' }}>{actor.runs ?? 0} 次</span>
                  <span style={{ flex: '0 0 auto', fontWeight: 500 }}>{usd(actor.cost_usd)}</span>
                </div>
              ))}
            </div>
          ) : null}
          <div className="text-muted" style={{ marginTop: 10, fontSize: 11, lineHeight: 1.6 }}>
            记账收口覆盖(本月):{unified}/{apifyRuns} 笔 Apify run 走统一记账口
            {coverage.estimated_entries ? ` · 估算 ${coverage.estimated_entries} 笔` : ''}
            {coverage.zero_cost_entries ? ` · 零成本盲区 ${coverage.zero_cost_entries} 笔` : ''}
            。{overview.note || '内部口径,权威账单以 Apify console 为准。'}
          </div>
        </>
      ) : null}
    </section>
  );
}
