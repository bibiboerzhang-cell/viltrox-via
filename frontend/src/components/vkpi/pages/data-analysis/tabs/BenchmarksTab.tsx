import { useState } from 'react';
import type { BenchmarkTab as BenchTab, KpiKey, Row } from '../utils/types';
import { BENCHMARK_TABS } from '../utils/types';
import { KPI_OPTIONS } from '../utils/kpiOptions';
import { accountId, accountName, rowString } from '../utils/rowAccessors';
import { normalizePlatform, platformClass, platformDisplay, platformInitial } from '../utils/platformHelpers';
import { averageNumbers, formatMetric, metricForAccount } from '../utils/metricHelpers';
import { DaCard } from '../shared/DaCard';

interface BenchmarksTabProps {
  accounts: Row[];
  visibleAccounts: Row[];
  crossPlatform: Row[];
  posts: Row[];
  selectedKpis: KpiKey[];
  includeBenchmark: boolean;
  onOpenAccount: (account: Row) => void;
  onOpenFilter: () => void;
}

export function BenchmarksTab({
  accounts: _accounts,
  visibleAccounts,
  crossPlatform,
  posts,
  selectedKpis,
  includeBenchmark,
  onOpenAccount,
  onOpenFilter,
}: BenchmarksTabProps) {
  const [activeBenchmarkTab, setActiveBenchmarkTab] = useState<BenchTab>('Cross-platform');

  // 按平台筛选: 如果不是 Cross-platform / Brands,只显示该平台账号
  const filteredAccounts = (() => {
    if (activeBenchmarkTab === 'Cross-platform') return visibleAccounts;
    if (activeBenchmarkTab === 'Brands') return visibleAccounts;
    const tabPlatform = activeBenchmarkTab.toLowerCase();
    return visibleAccounts.filter((acc) => {
      const p = normalizePlatform(rowString(acc, ['platform']));
      if (activeBenchmarkTab === '小红书') return p === 'xhs';
      if (activeBenchmarkTab === 'Twitter') return p === 'x';
      return p === tabPlatform;
    });
  })();

  return (
    <DaCard
      title="Cross-Platform Benchmarks"
      eyebrow="跨平台对比"
      wide
      side={
        <button
          className="da-black-button da-black-button--small"
          type="button"
          onClick={onOpenFilter}
        >» Choose KPIs</button>
      }
    >
      <div className="da-benchmark-tabs">
        {BENCHMARK_TABS.map((tab) => (
          <button
            key={tab}
            className={activeBenchmarkTab === tab ? 'is-active' : ''}
            type="button"
            onClick={() => setActiveBenchmarkTab(tab)}
          >{tab}</button>
        ))}
      </div>
      <div className="da-table-wrap">
        <table className="da-table">
          <thead>
            <tr>
              <th>Profile</th>
              <th>Platform</th>
              {selectedKpis.map((key) => (
                <th key={key}>{KPI_OPTIONS.find((option) => option.key === key)?.label || key}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filteredAccounts.length ? filteredAccounts.map((account) => (
              <tr key={accountId(account)}>
                <td>
                  <button
                    className="da-profile-cell"
                    type="button"
                    onClick={() => onOpenAccount(account)}
                  >
                    <span className={`da-profile-cell__avatar ${platformClass(rowString(account, ['platform']))}`}>
                      {platformInitial(rowString(account, ['platform']))}
                    </span>
                    {accountName(account)}
                  </button>
                </td>
                <td>{platformDisplay(rowString(account, ['platform']))}</td>
                {selectedKpis.map((key) => (
                  <td key={key}>{formatMetric(metricForAccount(account, crossPlatform, posts, key), key)}</td>
                ))}
              </tr>
            )) : (
              <tr>
                <td className="da-table-empty" colSpan={selectedKpis.length + 2}>
                  暂无 {activeBenchmarkTab} 账号。Add a profile 后再展示 Benchmark 表格。
                </td>
              </tr>
            )}
            {includeBenchmark && filteredAccounts.length ? (
              <tr className="da-table__benchmark-row">
                <td>Benchmark Average</td>
                <td>{activeBenchmarkTab}</td>
                {selectedKpis.map((key) => (
                  <td key={key}>
                    {formatMetric(
                      averageNumbers(filteredAccounts.map((account) => metricForAccount(account, crossPlatform, posts, key))),
                      key,
                    )}
                  </td>
                ))}
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </DaCard>
  );
}
