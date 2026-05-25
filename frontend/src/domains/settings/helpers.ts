export interface BackendBuildInfo {
  git_sha?: string;
  git_short_sha?: string;
  git_branch?: string;
  build_time?: string;
  client_matches_server?: boolean;
  client_build_source?: string;
}

export const boolValue = (value: unknown, fallback = false) => {
  if (value === undefined || value === null) return fallback;
  if (typeof value === 'string') return ['1', 'true', 'yes', 'on', 'enabled'].includes(value.toLowerCase());
  return Boolean(value);
};

export const currentFrontendAsset = () => {
  if (typeof document === 'undefined') return '';
  const src = Array.from(document.scripts)
    .map((script) => script.src)
    .find((srcValue) => srcValue.includes('/assets/app-'));
  return src ? src.split('/').pop() || src : '';
};

export const rowEnabled = (row: Record<string, unknown>, key = 'enabled') => {
  const raw = row[key];
  return raw === true || raw === 1 || raw === '1' || String(raw).toLowerCase() === 'true';
};

export const numberValue = (value: unknown, fallback = 0) => {
  const next = Number(value ?? fallback);
  return Number.isFinite(next) ? next : fallback;
};

export const formNumber = (form: FormData, key: string, fallback = 0) => {
  const raw = form.get(key);
  return numberValue(raw === null ? fallback : String(raw), fallback);
};

export const platformBlockedReason = (row: Record<string, unknown>) => (
  rowEnabled(row, 'crawl_enabled') ? '已开启' : '已关闭'
);

export const boolLabel = (value: boolean) => (value ? '开启' : '关闭');

export const moneyLabel = (value: unknown) => `$${numberValue(value).toLocaleString('en-US')}`;

export const percentLabel = (value: unknown) => {
  const next = numberValue(value, 0);
  return `${(next * 100).toFixed(next > 0 && next < 0.01 ? 2 : 1)}%`;
};

export const timeLabel = (value: unknown) => {
  const raw = String(value || '').trim();
  if (!raw || raw === 'unknown') return '-';
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return raw;
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date);
};

export const settingChangeLine = (label: string, before: string | number, after: string | number) => (
  `${label}: ${before} -> ${after}`
);

export const confirmHighRiskSettingChange = (title: string, lines: string[]) => {
  void title;
  void lines;
  return true;
};

export const summarizeSettingChange = (prefix: string, lines: string[]) => {
  const changed = lines.filter((line) => line.includes('->')).slice(0, 4);
  return `${prefix}: ${changed.join('；') || '已写入'}`;
};
