import { useEffect, useMemo } from 'react';
import type { KpiKey, Row } from '../utils/types';
import { accountId, accountName, rowString } from '../utils/rowAccessors';
import { normalizePlatform, platformDisplay } from '../utils/platformHelpers';
import { KPI_OPTIONS } from '../utils/kpiOptions';

interface FilterDrawerProps {
  isOpen: boolean;
  accounts: Row[];
  selectedProfileIds: string[];
  selectedKpis: KpiKey[];
  includeBenchmark: boolean;
  onClose: () => void;
  onProfileToggle: (id: string) => void;
  onKpiToggle: (key: KpiKey) => void;
  onBenchmarkToggle: () => void;
  onUpdate: () => void;
}

export function FilterDrawer({
  isOpen,
  accounts,
  selectedProfileIds,
  selectedKpis,
  includeBenchmark,
  onClose,
  onProfileToggle,
  onKpiToggle,
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
          <h3>Filters</h3>
        </div>
        <button type="button" onClick={onClose} aria-label="关闭筛选">×</button>
      </header>
      <div className="da-filter-drawer__body">
        {grouped.length ? grouped.map(([platform, items]) => (
          <section className="da-filter-group" key={platform}>
            <h4 className="da-filter-group__title">{platformDisplay(platform)} profiles:</h4>
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
          <h4 className="da-filter-group__title">Choose KPIs you want to see in the table</h4>
          {KPI_OPTIONS.map((option) => (
            <label className="da-filter-checkbox" key={option.key}>
              <input
                type="checkbox"
                checked={selectedKpis.includes(option.key)}
                onChange={() => onKpiToggle(option.key)}
              />
              <span className="da-filter-checkbox__label">{option.label}</span>
            </label>
          ))}
          <label
            className="da-filter-checkbox"
            style={{ marginTop: 8, paddingTop: 8, borderTop: '1px solid var(--da-border-soft)' }}
          >
            <input type="checkbox" checked={includeBenchmark} onChange={onBenchmarkToggle} />
            <span className="da-filter-checkbox__label">Benchmark Average</span>
          </label>
        </section>
      </div>
      <footer className="da-filter-drawer__footer">
        <button className="da-filter-update-button" type="button" onClick={onUpdate}>Update</button>
      </footer>
    </aside>
  );
}
