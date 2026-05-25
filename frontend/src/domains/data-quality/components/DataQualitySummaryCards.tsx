import type { DataQualityResponse } from '../types';

interface DataQualitySummaryCardsProps {
  apiTokenAvailable: boolean;
  loading: boolean;
  quality: DataQualityResponse | null;
  onRefresh: () => void;
}

function toneClass(active: boolean) {
  return active ? 'vkpi-info-block--warn' : 'vkpi-info-block--good';
}

function SummaryBlock({ label, value, tone = '' }: { label: string; value: string; tone?: string }) {
  return (
    <div className={`vkpi-info-block ${tone}`.trim()}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function DataQualitySummaryCards({
  apiTokenAvailable,
  loading,
  quality,
  onRefresh,
}: DataQualitySummaryCardsProps) {
  const summary = quality?.summary || {};
  const totalCount = Number(quality?.total_count || 0);

  return (
    <section className="vkpi-card-grid vkpi-card-grid--forms">
      <section className="vkpi-card vkpi-action-card">
        <div className="vkpi-card-header">
          <h2>检查状态</h2>
        </div>
        <SummaryBlock label="问题总数" value={String(totalCount)} tone={toneClass(totalCount > 0)} />
        <SummaryBlock label="高优先级" value={String(summary.high || 0)} tone={toneClass(Boolean(summary.high))} />
        <SummaryBlock label="中优先级" value={String(summary.medium || 0)} />
        <button
          className="vkpi-button vkpi-button--primary"
          type="button"
          disabled={loading || !apiTokenAvailable}
          onClick={onRefresh}
        >
          {loading ? '正在检查' : '重新检查'}
        </button>
      </section>
      <section className="vkpi-card vkpi-action-card">
        <div className="vkpi-card-header">
          <h2>检查口径</h2>
        </div>
        <p className="vkpi-summary-text">只读取真实业务表，不生成假问题；问题用于复核，不会阻塞当前业务操作。</p>
        <SummaryBlock label="最近检查" value={quality?.generated_at || '-'} />
      </section>
    </section>
  );
}
