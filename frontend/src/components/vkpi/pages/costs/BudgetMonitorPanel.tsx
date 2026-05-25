import React, { useEffect, useMemo, useState } from 'react';
import {
  getAiBudgetStatus,
  getAiBudgetUsageByCron,
  getAiBudgetUsageByProvider,
  updateAiBudgetScope,
} from '../../../../services/vkpi/cost-api';
import { CardHeader } from '../../shared/CardHeader';
import { InfoBlock } from '../../shared/InfoBlock';
import { currencyFormatter } from '../../shared/vkpiFormatters';
import { safeNumber } from '../../shared/vkpiDataUtils';

type Row = Record<string, unknown>;

interface BudgetMonitorPanelProps {
  apiToken?: string;
}

function money(value: unknown) {
  return currencyFormatter.format(safeNumber(value));
}

function ratioLabel(value: unknown) {
  const ratio = safeNumber(value);
  return `${Math.round(ratio * 100)}%`;
}

function budgetState(row: Row) {
  if (row.hard_stopped) return { label: 'Hard stop', className: 'is-danger' };
  if (row.warning) return { label: 'Warning', className: 'is-warning' };
  return { label: 'OK', className: 'is-ok' };
}

function usageRowsLabel(rows: Row[]) {
  return rows.length ? String(rows.length) : '0';
}

export function BudgetMonitorPanel({ apiToken }: BudgetMonitorPanelProps) {
  const [budgets, setBudgets] = useState<Row[]>([]);
  const [summary, setSummary] = useState<Row>({});
  const [providerUsage, setProviderUsage] = useState<Row[]>([]);
  const [cronUsage, setCronUsage] = useState<Row[]>([]);
  const [selectedScope, setSelectedScope] = useState('');
  const [capUsd, setCapUsd] = useState('');
  const [currentSpendUsd, setCurrentSpendUsd] = useState('');
  const [warningAt, setWarningAt] = useState('');
  const [hardStopAt, setHardStopAt] = useState('');
  const [fallbackAction, setFallbackAction] = useState('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');

  const selectedBudget = useMemo(() => budgets.find((row) => String(row.scope) === selectedScope) || null, [budgets, selectedScope]);

  const loadBudgets = async () => {
    if (!apiToken) return;
    setLoading(true);
    setMessage('');
    try {
      const [budgetResponse, providerResponse, cronResponse] = await Promise.all([
        getAiBudgetStatus(apiToken),
        getAiBudgetUsageByProvider(apiToken, { limit: 20 }).catch(() => ({ rows: [] })),
        getAiBudgetUsageByCron(apiToken, { limit: 20 }).catch(() => ({ rows: [] })),
      ]);
      const rows = budgetResponse.budgets || [];
      setBudgets(rows);
      setSummary(budgetResponse.summary || {});
      setProviderUsage(providerResponse.rows || []);
      setCronUsage(cronResponse.rows || []);
      if (!selectedScope && rows[0]) selectBudget(rows[0]);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Budget Guard 状态读取失败');
    } finally {
      setLoading(false);
    }
  };

  const selectBudget = (row: Row) => {
    setSelectedScope(String(row.scope || ''));
    setCapUsd(String(row.cap_usd ?? 0));
    setCurrentSpendUsd(String(row.current_spend ?? 0));
    setWarningAt(String(row.warning_at ?? 0.8));
    setHardStopAt(String(row.hard_stop_at ?? 1));
    setFallbackAction(String(row.fallback_action || ''));
  };

  useEffect(() => {
    void loadBudgets();
  }, [apiToken]);

  useEffect(() => {
    if (selectedBudget) selectBudget(selectedBudget);
  }, [selectedBudget?.scope]);

  const submitBudget = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!apiToken || !selectedScope) return;
    setSaving(true);
    setMessage('');
    try {
      await updateAiBudgetScope(apiToken, selectedScope, {
        cap_usd: Number(capUsd || 0),
        current_spend: Number(currentSpendUsd || 0),
        warning_at: Number(warningAt || 0.8),
        hard_stop_at: Number(hardStopAt || 1),
        fallback_action: fallbackAction,
      });
      await loadBudgets();
      setMessage(`已更新预算 scope：${selectedScope}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Budget Guard 更新失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <section className="vkpi-card-grid vkpi-card-grid--forms">
        <section className="vkpi-card vkpi-action-card">
          <CardHeader title="AI Budget Guard" />
          <div className="vkpi-result-grid">
            <InfoBlock label="Scopes" value={String(summary.scopes || budgets.length || 0)} />
            <InfoBlock label="Cap" value={money(summary.cap_usd)} />
            <InfoBlock label="Spend" value={money(summary.current_spend_usd)} />
            <InfoBlock label="Warning / Stop" value={`${String(summary.warnings || 0)} / ${String(summary.hard_stopped || 0)}`} />
          </div>
          <div className="vkpi-row-actions">
            <button className="vkpi-button" type="button" disabled={!apiToken || loading} onClick={() => void loadBudgets()}>{loading ? '读取中...' : '刷新预算'}</button>
          </div>
          {message ? <div className="vkpi-inline-message">{message}</div> : null}
        </section>
        <section className="vkpi-card vkpi-action-card">
          <CardHeader title="预算阈值编辑" />
          <form className="vkpi-form-stack" onSubmit={submitBudget}>
            <select value={selectedScope} onChange={(event) => {
              const row = budgets.find((item) => String(item.scope) === event.target.value);
              if (row) selectBudget(row);
            }}>
              <option value="">选择 scope</option>
              {budgets.map((row) => <option key={String(row.scope)} value={String(row.scope)}>{String(row.scope)}</option>)}
            </select>
            <input value={capUsd} onChange={(event) => setCapUsd(event.target.value)} placeholder="Cap USD" inputMode="decimal" />
            <input value={currentSpendUsd} onChange={(event) => setCurrentSpendUsd(event.target.value)} placeholder="Current spend USD" inputMode="decimal" />
            <input value={warningAt} onChange={(event) => setWarningAt(event.target.value)} placeholder="Warning ratio, e.g. 0.8" inputMode="decimal" />
            <input value={hardStopAt} onChange={(event) => setHardStopAt(event.target.value)} placeholder="Hard stop ratio, e.g. 1" inputMode="decimal" />
            <input value={fallbackAction} onChange={(event) => setFallbackAction(event.target.value)} placeholder="Fallback action" />
            <button className="vkpi-button vkpi-button--primary" type="submit" disabled={!apiToken || !selectedScope || saving}>{saving ? '保存中...' : '保存预算'}</button>
          </form>
        </section>
      </section>
      <section className="vkpi-card vkpi-table-card">
        <div className="vkpi-table-card__header"><div><h2>Provider / Cron 预算</h2><span>{budgets.length} 个 scope</span></div></div>
        <div className="vkpi-table-wrap">
          <table className="vkpi-table">
            <thead><tr><th>Scope</th><th>状态</th><th>Cap</th><th>Spend</th><th>剩余</th><th>使用率</th><th>Warning</th><th>Hard stop</th><th>Fallback</th><th>操作</th></tr></thead>
            <tbody>
              {budgets.length ? budgets.map((row) => {
                const state = budgetState(row);
                return (
                  <tr key={String(row.scope)}>
                    <td>{String(row.scope || '-')}</td>
                    <td><span className={`vkpi-budget-state ${state.className}`}>{state.label}</span></td>
                    <td>{money(row.cap_usd)}</td>
                    <td>{money(row.current_spend)}</td>
                    <td>{row.remaining_usd === null || row.remaining_usd === undefined ? 'uncapped' : money(row.remaining_usd)}</td>
                    <td>{ratioLabel(row.usage_ratio)}</td>
                    <td>{ratioLabel(row.warning_at)}</td>
                    <td>{ratioLabel(row.hard_stop_at)}</td>
                    <td>{String(row.fallback_action || '-')}</td>
                    <td><button className="vkpi-mini-button" type="button" onClick={() => selectBudget(row)}>编辑</button></td>
                  </tr>
                );
              }) : <tr><td className="vkpi-table-empty" colSpan={10}>{loading ? '正在读取 Budget Guard...' : '暂无预算 scope。'}</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
      <section className="vkpi-card-grid vkpi-card-grid--forms">
        <UsageTable title="Provider 使用量" label="provider" rows={providerUsage} countLabel={usageRowsLabel(providerUsage)} />
        <UsageTable title="Cron 使用量" label="cron" rows={cronUsage} countLabel={usageRowsLabel(cronUsage)} />
      </section>
    </>
  );
}

function UsageTable({ title, label, rows, countLabel }: { title: string; label: string; rows: Row[]; countLabel: string }) {
  return (
    <section className="vkpi-card vkpi-table-card">
      <div className="vkpi-table-card__header"><div><h2>{title}</h2><span>{countLabel} 行</span></div></div>
      <div className="vkpi-table-wrap">
        <table className="vkpi-table">
          <thead><tr><th>{label}</th><th>Calls</th><th>Cost</th><th>Tokens In</th><th>Tokens Out</th><th>Last seen</th></tr></thead>
          <tbody>
            {rows.length ? rows.map((row) => {
              const key = String(row.ai_provider || row.cron_task || 'unknown');
              return (
                <tr key={key}>
                  <td>{key}</td>
                  <td>{String(row.calls || 0)}</td>
                  <td>{money(row.cost_usd)}</td>
                  <td>{String(row.tokens_in || 0)}</td>
                  <td>{String(row.tokens_out || 0)}</td>
                  <td>{String(row.last_seen_at || '-')}</td>
                </tr>
              );
            }) : <tr><td className="vkpi-table-empty" colSpan={6}>暂无 AI cost ledger 记录。</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}
