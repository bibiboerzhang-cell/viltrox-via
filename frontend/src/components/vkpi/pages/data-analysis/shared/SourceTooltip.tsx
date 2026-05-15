export type SourceStatus = 'real' | 'fallback' | 'local' | 'missing' | 'beta';

interface SourceTooltipProps {
  source: string;
  detail?: string;
  capturedAt?: string;
  drilldown?: string;
  status?: SourceStatus;
}

const STATUS_LABEL: Record<SourceStatus, string> = {
  real: '真实数据',
  fallback: '回退字段',
  local: '本地聚合',
  missing: '等待同步',
  beta: 'Beta 口径',
};

export function SourceTooltip({
  source,
  detail,
  capturedAt,
  drilldown,
  status = 'real',
}: SourceTooltipProps) {
  const tooltip = [
    STATUS_LABEL[status],
    `来源: ${source}`,
    detail,
    capturedAt ? `更新时间: ${capturedAt}` : '',
    drilldown ? `下钻: ${drilldown}` : '',
  ].filter(Boolean).join('\n');

  return (
    <span className={`da-source-tooltip da-source-tooltip--${status}`}>
      <button
        type="button"
        className="da-source-tooltip__button"
        aria-label={tooltip}
        title={tooltip}
        onClick={(event) => event.stopPropagation()}
      >
        i
      </button>
      <span className="da-source-tooltip__panel" role="tooltip">
        <strong>{STATUS_LABEL[status]}</strong>
        <span>来源: {source}</span>
        {detail ? <span>{detail}</span> : null}
        {capturedAt ? <span>更新时间: {capturedAt}</span> : null}
        {drilldown ? <span>下钻: {drilldown}</span> : null}
      </span>
    </span>
  );
}
