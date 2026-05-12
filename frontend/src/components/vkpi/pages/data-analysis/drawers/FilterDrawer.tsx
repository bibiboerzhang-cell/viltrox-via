import { useEffect, useMemo } from 'react';
import type { ChartKey, KpiKey, Row } from '../utils/types';
import { accountId, accountName, rowString } from '../utils/rowAccessors';
import { normalizePlatform, platformDisplay } from '../utils/platformHelpers';
import {
  CHART_GROUP_LABELS,
  CHART_OPTIONS,
  KPI_GROUP_LABELS,
  KPI_OPTIONS,
} from '../utils/kpiOptions';

interface FilterDrawerProps {
  isOpen: boolean;
  accounts: Row[];
  selectedProfileIds: string[];
  selectedKpis: KpiKey[];
  selectedCharts: ChartKey[];
  includeBenchmark: boolean;
  onClose: () => void;
  onProfileToggle: (id: string) => void;
  onKpiToggle: (key: KpiKey) => void;
  onChartToggle: (key: ChartKey) => void;
  onBenchmarkToggle: () => void;
  onUpdate: () => void;
}

export function FilterDrawer({
  isOpen,
  accounts,
  selectedProfileIds,
  selectedKpis,
  selectedCharts,
  includeBenchmark,
  onClose,
  onProfileToggle,
  onKpiToggle,
  onChartToggle,
  onBenchmarkToggle,
  onUpdate,
}: FilterDrawerProps) {
  const grouped = useMemo(() => {
    const map = new Map<string, Row[]>();
    for (const account of accounts) {
      const platform = normalizePlatform(rowString(account, ['platform']));
      const items = map.get(platform) || [];
      items.push(account);
      map.set(platform, items);
    }
    return Array.from(map.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [accounts]);
  const groupedKpis = useMemo(() => KPI_OPTIONS.reduce((acc, option) => {
    const items = acc.get(option.group) || [];
    items.push(option);
    acc.set(option.group, items);
    return acc;
  }, new Map<(typeof KPI_OPTIONS)[number]['group'], typeof KPI_OPTIONS>()), []);
  const groupedCharts = useMemo(() => CHART_OPTIONS.reduce((acc, option) => {
    const items = acc.get(option.group) || [];
    items.push(option);
    acc.set(option.group, items);
    return acc;
  }, new Map<(typeof CHART_OPTIONS)[number]['group'], typeof CHART_OPTIONS>()), []);

  useEffect(() => {
    if (!isOpen) return undefined;
    const listener = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', listener);
    return () => window.removeEventListener('keydown', listener);
  }, [isOpen, onClose]);

  return (
    <aside className={`da-filter-drawer${isOpen ? ' da-filter-drawer--open' : ''}`} aria-hidden={!isOpen}>
      <header className="da-filter-drawer__header">
        <div>
          <span>Viltrox 数据筛选</span>
          <h3>Customize dashboard</h3>
        </div>
        <button type="button" onClick={onClose} aria-label="关闭筛选">×</button>
      </header>
      <div className="da-filter-drawer__body">
        <section className="da-filter-group da-filter-group--intro">
          <h4 className="da-filter-group__title">Profiles</h4>
          <p className="da-filter-empty">选择参与本页聚合的账号。未选择任何账号时默认展示全部。</p>
        </section>
        {grouped.length ? grouped.map(([platform, items]) => (
          <section className="da-filter-group" key={platform}>
            <h4 className="da-filter-group__title">{platformDisplay(platform)}</h4>
            {items.map((account) => {
              const id = accountId(account);
              return (
                <label className="da-filter-checkbox" key={id}>
                  <input
                    type="checkbox"
                    checked={selectedProfileIds.includes(id)}
                    onChange={() => onProfileToggle(id)}
                  />
                  <span className="da-filter-checkbox__label">{accountName(account)}</span>
                </label>
              );
            })}
          </section>
        )) : (
          <section className="da-filter-group">
            <h4 className="da-filter-group__title">Profiles:</h4>
            <p className="da-filter-empty">添加首个账号后启用平台级筛选。</p>
          </section>
        )}
        <section className="da-filter-group">
          <h4 className="da-filter-group__title">Select KPIs</h4>
          {Array.from(groupedKpis.entries()).map(([group, items]) => (
            <details className="da-filter-accordion" open={['count', 'engagement', 'reach'].includes(group)} key={group}>
              <summary>{KPI_GROUP_LABELS[group]}</summary>
              {items.map((option) => (
                <label className="da-filter-checkbox" key={option.key}>
                  <input
                    type="checkbox"
                    checked={selectedKpis.includes(option.key)}
                    onChange={() => onKpiToggle(option.key)}
                  />
                  <span className="da-filter-checkbox__label">{option.label}</span>
                </label>
              ))}
            </details>
          ))}
          <label
            className="da-filter-checkbox"
            style={{ marginTop: 8, paddingTop: 8, borderTop: '1px solid var(--da-border-soft)' }}
          >
            <input type="checkbox" checked={includeBenchmark} onChange={onBenchmarkToggle} />
            <span className="da-filter-checkbox__label">Benchmark Average</span>
          </label>
        </section>
        <section className="da-filter-group">
          <h4 className="da-filter-group__title">Select Charts</h4>
          {Array.from(groupedCharts.entries()).map(([group, items]) => (
            <details className="da-filter-accordion" open key={group}>
              <summary>{CHART_GROUP_LABELS[group]}</summary>
              {items.map((option) => (
                <label className="da-filter-checkbox" key={option.key}>
                  <input
                    type="checkbox"
                    checked={selectedCharts.includes(option.key)}
                    onChange={() => onChartToggle(option.key)}
                  />
                  <span className="da-filter-checkbox__label">{option.label}</span>
                </label>
              ))}
            </details>
          ))}
        </section>
      </div>
      <footer className="da-filter-drawer__footer">
        <button className="da-filter-update-button" type="button" onClick={onUpdate}>
          应用筛选
        </button>
      </footer>
    </aside>
  );
}
