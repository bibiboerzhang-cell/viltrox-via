import type { IntelligenceCardModel } from './IntelligenceCard';

interface IntelligenceEvidenceDrawerProps {
  card: IntelligenceCardModel;
  onClose: () => void;
  footerNote?: string;
}

function confidenceLabel(card: IntelligenceCardModel): string {
  if (typeof card.confidence === 'number') return `${Math.round(card.confidence * 100)}%`;
  return 'evidence';
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function text(value: unknown, fallback = '-'): string {
  const next = String(value ?? '').trim();
  return next || fallback;
}

function firstText(...values: unknown[]): string {
  for (const value of values) {
    const next = text(value, '');
    if (next) return next;
  }
  return '-';
}

export function IntelligenceEvidenceDrawer({ card, onClose, footerNote }: IntelligenceEvidenceDrawerProps) {
  const metadata = asRecord(card.metadata);
  const raw = asRecord(metadata.raw);
  const qualityGate = asRecord(metadata.quality_gate || raw.quality_gate);
  const qualityChecks = asRecord(qualityGate.checks);
  const runEvidence = card.evidence.find((item) => item.label.toLowerCase().includes('run'));
  const sourceEvidence = card.evidence.find((item) => item.source.toLowerCase().includes('source') || item.label.toLowerCase().includes('source'));
  const runId = firstText(metadata.run_uid, raw.run_uid, raw.entityId, raw.entity_id, card.entityId, runEvidence?.value);
  const provider = firstText(qualityGate.provider, raw.provider, metadata.provider);
  const model = firstText(qualityGate.model, raw.model, metadata.model);
  const qualityStatus = firstText(qualityGate.status, qualityGate.usable_for_ui === true ? 'usable' : qualityGate.usable_for_ui === false ? 'blocked' : '');
  const qualityReasons = Array.isArray(qualityGate.reasons)
    ? qualityGate.reasons.map((item) => text(item, '')).filter(Boolean)
    : [];
  const qualityCheckRows = Object.entries(qualityChecks).slice(0, 6);

  return (
    <aside className="vkpi-evidence-drawer vkpi-intelligence-evidence-drawer" role="dialog" aria-label={`${card.title} evidence`}>
      <header>
        <div>
          <span>智能证据链</span>
          <h2>{card.title}</h2>
          <small>{card.sourceLabel} · {card.freshnessLabel || '本次会话'}</small>
        </div>
        <button type="button" aria-label="关闭证据抽屉" onClick={onClose}>×</button>
      </header>

      <div className="vkpi-intelligence-evidence-summary">
        <span><b>类型</b>{card.type}</span>
        <span><b>对象</b>{card.entityType}</span>
        <span><b>优先级</b>{card.priority}</span>
        <span><b>置信度</b>{confidenceLabel(card)}</span>
      </div>

      <div className="vkpi-intelligence-evidence-context">
        <b>判断摘要</b>
        <p>{card.summary}</p>
      </div>

      <div className="vkpi-intelligence-runtime-evidence" aria-label="运行和质量闸门">
        <span><b>运行范围</b>{runId}</span>
        <span><b>来源</b>{firstText(raw.sourceLabel, raw.source_label, card.sourceLabel, sourceEvidence?.value)}</span>
        <span><b>Provider</b>{provider}</span>
        <span><b>模型 / 规则</b>{model}</span>
        <span><b>质量状态</b>{qualityStatus}</span>
        <span><b>质量说明</b>{qualityReasons.length ? qualityReasons.join(' / ') : '无阻断原因'}</span>
      </div>

      {qualityCheckRows.length ? (
        <div className="vkpi-intelligence-quality-checks" aria-label="质量检查">
          {qualityCheckRows.map(([key, value]) => (
            <span className={value ? 'is-pass' : 'is-fail'} key={key}>
              <b>{key}</b>{value ? '通过' : '未通过'}
            </span>
          ))}
        </div>
      ) : null}

      <div className="vkpi-evidence-list">
        {card.evidence.length ? card.evidence.map((item, index) => (
          <article key={`${item.label}-${item.source}-${item.value || item.url || index}`}>
            <div>
              <div>
                <strong>{item.label}</strong>
                <span>{item.source}</span>
              </div>
              <b>#{index + 1}</b>
            </div>
            <p>{item.value || '当前证据项没有额外文本。'}</p>
            {item.url ? (
              <a className="vkpi-evidence-link" href={item.url} target="_blank" rel="noreferrer">
                打开来源
              </a>
            ) : null}
          </article>
        )) : (
          <div className="vkpi-empty-state">暂无 evidence refs。该智能卡不能进入可执行建议，直到补齐证据。</div>
        )}
      </div>

      <p className="vkpi-help-text">
        {footerNote || 'Evidence Drawer v0 只展示已有证据，不生成事实；后续外部搜索、Gemini 视频分析和 LLM 总结都会写入可追溯 evidence refs。'}
      </p>
    </aside>
  );
}

export default IntelligenceEvidenceDrawer;
