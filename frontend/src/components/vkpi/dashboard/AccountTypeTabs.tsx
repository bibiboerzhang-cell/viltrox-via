import type { DashboardAccountCounts, DashboardAccountType } from '../../../domains/dashboard';
import { compact, dashboardAccountTypeOptions } from '../../../domains/dashboard';

interface AccountTypeTabsProps {
  value: DashboardAccountType;
  counts?: DashboardAccountCounts;
  loading?: boolean;
  onChange: (value: DashboardAccountType) => void;
}

export function AccountTypeTabs({ value, counts = {}, loading = false, onChange }: AccountTypeTabsProps) {
  return (
    <div className={`dashboard-account-tabs${loading ? ' is-loading' : ''}`} role="tablist" aria-label="Dashboard account type">
      {dashboardAccountTypeOptions.map((item) => (
        <button
          key={item.key}
          type="button"
          role="tab"
          aria-selected={value === item.key}
          className={value === item.key ? 'is-active' : ''}
          title={item.detail}
          onClick={() => onChange(item.key)}
        >
          <span>{item.label}</span>
          <b>{compact(Number(counts[item.key] || 0))}</b>
        </button>
      ))}
    </div>
  );
}

export default AccountTypeTabs;
