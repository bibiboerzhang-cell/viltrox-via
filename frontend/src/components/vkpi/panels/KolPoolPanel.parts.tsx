import { useState } from 'react';

import { Avatar } from '../shared/Avatar';
import type { KolPoolIntelligenceCard, KolPoolItem } from './KolPoolPanel.types';
import { RefreshStateNotice } from './KolPoolPanel.listParts';
import {
  arrayRecords,
  candidatePriority,
  collectList,
  decisionProfile,
  dimensionsConfidenceRows,
  evidenceSectionLabel,
  formatConfidenceValue,
  formatNumber,
  formatPercent,
  formatScoreValue,
  getDataGaps,
  getString,
  intelligenceSectionPayload,
  memoryCardLabel,
  metricReadiness,
  parseMaybeJson,
  parseMaybeList,
  readinessLabel,
  recordValue,
  statusClass,
  statusLabel,
  stringifyValue,
} from './KolPoolPanel.utils';

export function KolPoolDetailDrawer({
  item,
  loading,
  intelligenceCard,
  intelligenceLoading,
  onClose,
  onEnrich,
  enriching,
  onPromoteToMain,
  linking,
}: {
  item: KolPoolItem;
  loading: boolean;
  intelligenceCard?: KolPoolIntelligenceCard | null;
  intelligenceLoading?: boolean;
  onClose: () => void;
  onEnrich?: () => void;
  enriching?: boolean;
  onPromoteToMain?: () => void;
  linking?: boolean;
}) {
  const raw = parseMaybeJson(item.raw_platform_data);
  const products = collectList(item.recommended_product_lines_json, raw, ['product', 'product_name', '产品', 'Product', 'sku', 'SKU']);
  const owners = collectList(undefined, raw, ['owner', '负责人', 'staff', 'assignee', 'manager', '负责员工']);
  const notes = collectList(undefined, raw, ['notes', '备注', 'comment', '合作备注', 'status', '状态']);
  const gaps = getDataGaps(item);
  const profileUrl = item.profile_url || getString(raw, ['profile_url', 'url', 'channelUrl', '主页', '主页 URL']);
  const priority = candidatePriority(item);
  const decision = decisionProfile(item);
  const readiness = metricReadiness(item);

  return (
    <aside className="vkpi-drawer vkpi-kol-pool-drawer" aria-label="KOL Pool 详情">
      <div className="vkpi-drawer__header">
        <div className="vkpi-kol-pool-drawer-title">
          <Avatar src={item.avatar_url} name={item.display_name || item.handle} size="md" />
          <div>
            <span className="vkpi-eyebrow">候选池 · {String(item.platform || 'other').toUpperCase()}</span>
            <h2>{item.display_name || item.handle || '未命名 KOL'}</h2>
            <p>@{item.handle || '—'} · {item.source_type || 'unknown'} · {item.source_ref || '无来源标记'}</p>
          </div>
        </div>
        <button className="vkpi-icon-button" type="button" onClick={onClose} aria-label="关闭">×</button>
      </div>

      {loading && <div className="vkpi-alert">正在读取完整详情…</div>}
      {intelligenceLoading && <div className="vkpi-alert">正在读取证据卡片…</div>}
      {item.refresh && <RefreshStateNotice refresh={item.refresh} />}

      <section className={`vkpi-kol-pool-decision-card ${priority.tone}`}>
        <div>
          <span className="vkpi-eyebrow">决策摘要</span>
          <strong>{priority.label}</strong>
          <p>{priority.reason}</p>
        </div>
        <div className="vkpi-kol-pool-decision-actions">
          {profileUrl && <a className="vkpi-button vkpi-button--small" href={profileUrl} target="_blank" rel="noreferrer">打开平台主页 ↗</a>}
          {onEnrich && <button className="vkpi-button vkpi-button--small vkpi-button--primary" type="button" onClick={onEnrich} disabled={enriching}>{enriching ? '补齐中…' : '补齐数据'}</button>}
        </div>
      </section>

      <section className="vkpi-kol-pool-readiness-card">
        <div>
          <span className="vkpi-eyebrow">决策优先级</span>
          <strong>{decision.label}</strong>
          <p>{decision.reason}</p>
        </div>
        <div className="vkpi-kol-pool-readiness-score">
          <span>{decision.score}</span>
          <small>readiness</small>
        </div>
      </section>

      {intelligenceCard && <KolPoolIntelligenceSection card={intelligenceCard} />}

      <section className="vkpi-detail-grid">
        <InfoTile label="粉丝" value={formatNumber(item.followers)} />
        <InfoTile label="平均播放" value={formatNumber(item.avg_views)} />
        <InfoTile label="互动率" value={formatPercent(item.engagement_rate)} />
        <InfoTile label="适配度" value={formatScoreValue(item.viltrox_fit_score)} />
        <InfoTile label="帖子/视频" value={formatNumber(item.posts_count)} />
        <InfoTile label="同步状态" value={item.sync_status || '—'} />
        <InfoTile label="邮箱" value={item.email || getString(raw, ['email', 'Email', '邮箱']) || '—'} />
      </section>

      <section className="vkpi-card vkpi-alert-detail-section">
        <h3>判断信息</h3>
        <div className="vkpi-chip-list">
          {profileUrl ? <a className="vkpi-chip" href={profileUrl} target="_blank" rel="noreferrer">打开平台主页 ↗</a> : <span className="vkpi-chip vkpi-chip--warn">缺主页 URL</span>}
          {products.length ? products.map((value) => <span key={`product-${value}`} className="vkpi-chip">产品: {value}</span>) : <span className="vkpi-chip vkpi-chip--warn">缺产品线</span>}
          {owners.length ? owners.map((value) => <span key={`owner-${value}`} className="vkpi-chip">负责: {value}</span>) : <span className="vkpi-chip vkpi-chip--warn">缺负责人</span>}
          {item.linked_main_kol_id ? <span className="vkpi-chip is-success">已链接主表 #{item.linked_main_kol_id}</span> : <span className="vkpi-chip">未链接主表</span>}
        </div>
        {item.viltrox_fit_reason && <p className="vkpi-help-text">{item.viltrox_fit_reason}</p>}
      </section>

      <section className="vkpi-card vkpi-alert-detail-section">
        <h3>可操作下一步</h3>
        <div className="vkpi-kol-pool-next-grid">
          <ActionHint done={!gaps.includes('头像') && !gaps.includes('平均播放') && !gaps.includes('互动率')} label="数据可判断" hint="头像、平均播放、互动率齐全后再决策。" />
          <ActionHint done={Boolean(item.linked_main_kol_id)} label="主表链接" hint="自动匹配已有主表；无匹配时创建主表记录。" />
          <ActionHint done={Boolean(profileUrl)} label="平台复核" hint="打开平台主页确认账号真实性。" />
          <ActionHint done={products.length > 0} label="产品匹配" hint="需要明确适配产品线，便于后续项目创建。" />
        </div>
      </section>

      <section className="vkpi-card vkpi-alert-detail-section">
        <h3>四维判断</h3>
        <div className="vkpi-kol-pool-readiness-grid">
          {readiness.map((row) => (
            <div key={row.label} className={row.ready ? 'is-ok' : 'is-missing'}>
              <strong>{row.label}</strong>
              <span>{row.value}</span>
              <small>{row.reason}</small>
            </div>
          ))}
        </div>
      </section>

      <section className="vkpi-card vkpi-alert-detail-section">
        <h3>数据缺口</h3>
        <div className="vkpi-kol-pool-gap-grid">
          {['头像', '平均播放', '互动率', '适配度'].map((label) => (
            <div key={label} className={gaps.includes(label) ? 'is-missing' : 'is-ok'}>
              <strong>{label}</strong>
              <span>{gaps.includes(label) ? '待补齐' : '已有'}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="vkpi-card vkpi-alert-detail-section">
        <h3>原始备注 / 合作记录</h3>
        {notes.length ? (
          <ul className="vkpi-kol-pool-notes">
            {notes.map((note, index) => <li key={`${note}-${index}`}>{note}</li>)}
          </ul>
        ) : (
          <p className="vkpi-help-text">当前导入记录没有可读备注。后续 P3.6 会把历史合作、负责人和产品字段做成标准字段。</p>
        )}
        <details className="vkpi-raw-details">
          <summary>查看原始导入/抓取 JSON</summary>
          <pre className="vkpi-code-block">{JSON.stringify(raw || {}, null, 2)}</pre>
        </details>
      </section>

      <div className="vkpi-kol-pool-drawer-actions">
        {onEnrich && <button className="vkpi-button vkpi-button--primary" type="button" onClick={onEnrich} disabled={enriching}>{enriching ? '真实补齐中…' : '补齐头像 / 指标'}</button>}
        {onPromoteToMain && !item.linked_main_kol_id && <button className="vkpi-button vkpi-button--primary" type="button" onClick={onPromoteToMain} disabled={linking}>{linking ? '处理中…' : '自动创建/链接主表'}</button>}
        <button className="vkpi-button" type="button" onClick={onClose}>关闭</button>
      </div>
    </aside>
  );
}

function InfoTile({ label, value }: { label: string; value: string }) {
  return <div className="vkpi-info-tile"><span>{label}</span><strong>{value}</strong></div>;
}

function ActionHint({ done, label, hint }: { done: boolean; label: string; hint: string }) {
  return (
    <div className={done ? 'is-ok' : 'is-missing'}>
      <strong>{done ? '已就绪' : '待处理'} · {label}</strong>
      <span>{hint}</span>
    </div>
  );
}

function KolPoolIntelligenceSection({ card }: { card: KolPoolIntelligenceCard }) {
  const [activeEvidence, setActiveEvidence] = useState<Record<string, unknown> | null>(null);
  const support = recordValue(card.decision_support);
  const dimensions = recordValue(card.dimensions11);
  const competitors = recordValue(card.competitors);
  const brandSignal = recordValue(card.brand_signal);
  const commentIntelligence = recordValue(card.comment_intelligence);
  const videoAnalysis = recordValue(card.video_analysis);
  const memoryCard = recordValue(card.memory_card);
  const productFit = recordValue(card.product_fit);
  const evidence = (Array.isArray(card.evidence_index) ? card.evidence_index : []).map(recordValue).filter((row) => stringifyValue(row.section));
  const readiness = stringifyValue(support.readiness || 'partial');
  const gaps = Array.isArray(support.gaps) ? support.gaps.map(stringifyValue).filter(Boolean) : [];
  const competitorSummary = recordValue(competitors.summary);
  const memoryHistory = recordValue(memoryCard.history_match);
  const memoryCompetitor = recordValue(memoryCard.competitor_memory);
  const dimensionRows = dimensionsConfidenceRows(dimensions);
  return (
    <section className="vkpi-card vkpi-alert-detail-section">
      <h3>Intelligence Card</h3>
      <div className="vkpi-kol-pool-readiness-grid">
        <div className={readiness === 'ready' ? 'is-ok' : 'is-missing'}>
          <strong>证据状态</strong>
          <span>{readinessLabel(readiness)}</span>
          <small>{gaps.length ? gaps.slice(0, 2).join(' / ') : '现有证据可读'}</small>
        </div>
        <div className={statusClass(dimensions.status)}>
          <strong>11 维</strong>
          <span>{formatScoreValue(dimensions.overall_score)}</span>
          <small>{statusLabel(dimensions.status)}</small>
        </div>
        <div className={statusClass(competitors.status)}>
          <strong>竞品</strong>
          <span>{stringifyValue(competitorSummary.risk_tier || competitors.status || '—')}</span>
          <small>{stringifyValue(competitorSummary.competitor_brand || '无高风险品牌')}</small>
        </div>
        <div className={statusClass(brandSignal.status)}>
          <strong>Brand Signal</strong>
          <span>{formatNumber(brandSignal.signal_count)}</span>
          <small>{statusLabel(brandSignal.status)}</small>
        </div>
        <div className={statusClass(commentIntelligence.status)}>
          <strong>Comment</strong>
          <span>{formatNumber(commentIntelligence.cached_comment_count || commentIntelligence.evidence_count)}</span>
          <small>{statusLabel(commentIntelligence.status)} · cap {formatNumber(recordValue(commentIntelligence.contract).cap)}</small>
        </div>
        <div className={statusClass(videoAnalysis.status)}>
          <strong>Video</strong>
          <span>{formatNumber(videoAnalysis.analyzed_count || videoAnalysis.evidence_count)}</span>
          <small>{statusLabel(videoAnalysis.status)} · rows {formatNumber(videoAnalysis.row_count)}</small>
        </div>
        <div className={statusClass(memoryCard.status)}>
          <strong>Memory</strong>
          <span>{memoryCardLabel(memoryCard)}</span>
          <small>
            合作 {formatNumber(memoryHistory.cooperation_count)} · 竞品 {stringifyValue(memoryCompetitor.risk_tier || 'opportunity')}
          </small>
        </div>
        <div className={statusClass(productFit.status)}>
          <strong>Product Fit</strong>
          <span>{formatNumber(productFit.count)}</span>
          <small>{statusLabel(productFit.status)}</small>
        </div>
      </div>
      {dimensionRows.length ? (
        <div className="vkpi-kol-pool-readiness-grid" style={{ marginTop: 12 }}>
          {dimensionRows.map((row) => (
            <div className={row.ready ? 'is-ok' : 'is-missing'} key={row.key}>
              <strong>{row.label}</strong>
              <span>{row.confidenceLabel}</span>
              <small>{row.detail}</small>
            </div>
          ))}
        </div>
      ) : null}
      <div className="vkpi-chip-list" style={{ marginTop: 12 }}>
        <span className={card.provider_calls ? 'vkpi-chip vkpi-chip--warn' : 'vkpi-chip is-success'}>Provider {card.provider_calls ? 'called' : 'off'}</span>
        <span className={card.llm_calls ? 'vkpi-chip vkpi-chip--warn' : 'vkpi-chip is-success'}>LLM {card.llm_calls ? 'called' : 'off'}</span>
        <span className={card.write_db ? 'vkpi-chip vkpi-chip--warn' : 'vkpi-chip is-success'}>Write {card.write_db ? 'on' : 'off'}</span>
      </div>
      {evidence.length ? (
        <div className="vkpi-chip-list" style={{ marginTop: 8 }}>
          {evidence.slice(0, 8).map((row, index) => (
            <button className="vkpi-chip" type="button" key={`${stringifyValue(row.section)}-${index}`} onClick={() => setActiveEvidence(row)}>
              {stringifyValue(row.label || row.section || 'evidence')} · {statusLabel(row.status)} · {formatNumber(row.evidence_count)}
            </button>
          ))}
        </div>
      ) : null}
      {activeEvidence && <KolPoolEvidenceDrawer card={card} indexRow={activeEvidence} onClose={() => setActiveEvidence(null)} />}
    </section>
  );
}

function KolPoolEvidenceDrawer({
  card,
  indexRow,
  onClose,
}: {
  card: KolPoolIntelligenceCard;
  indexRow: Record<string, unknown>;
  onClose: () => void;
}) {
  const section = stringifyValue(indexRow.section);
  const payload = intelligenceSectionPayload(card, section);
  const title = stringifyValue(indexRow.label || evidenceSectionLabel(section));
  return (
    <aside className="vkpi-evidence-drawer vkpi-kol-intelligence-evidence-drawer" role="dialog" aria-label={`${title} evidence`}>
      <header>
        <div>
          <span>Intelligence Evidence</span>
          <h2>{title}</h2>
          <small>
            {statusLabel(indexRow.status)} · {formatNumber(indexRow.evidence_count)} 条证据 · confidence {formatConfidenceValue(indexRow.confidence)}
          </small>
        </div>
        <button type="button" onClick={onClose} aria-label="关闭">×</button>
      </header>
      <div className="vkpi-evidence-list">
        <EvidenceArticle
          title="证据来源"
          meta={stringifyValue(indexRow.source || payload.source || 'stored evidence')}
          value={statusLabel(indexRow.status)}
          detail={`section=${section || 'unknown'} · provider=${card.provider_calls ? 'called' : 'off'} · llm=${card.llm_calls ? 'called' : 'off'} · write=${card.write_db ? 'on' : 'off'}`}
          badges={[
            `count ${formatNumber(indexRow.evidence_count)}`,
            `confidence ${formatConfidenceValue(indexRow.confidence)}`,
            indexRow.freshness_hours !== undefined ? `freshness ${formatNumber(indexRow.freshness_hours)}h` : '',
          ].filter(Boolean)}
        />
        <KolPoolEvidenceBody section={section} payload={payload} />
      </div>
    </aside>
  );
}

function KolPoolEvidenceBody({ section, payload }: { section: string; payload: Record<string, unknown> }) {
  if (!section) return <EvidenceArticle title="无 section" meta="evidence_index" value="unavailable" detail="当前证据索引缺少 section 字段。" />;
  if (stringifyValue(payload.status).toLowerCase() === 'skipped') {
    return <EvidenceArticle title="未启用" meta={stringifyValue(payload.reason || 'skipped')} value="skipped" detail="当前卡片没有加载这个 section 的详细证据。" />;
  }
  if (section === 'freshness') {
    return (
      <>
        <EvidenceArticle
          title="刷新层级"
          meta={stringifyValue(payload.tier_reason || payload.reason || 'vkpi_kol_refresh_tier')}
          value={stringifyValue(payload.tier || payload.status || 'unknown')}
          detail={`last_refresh=${stringifyValue(payload.last_refresh_at || '—')} · status=${stringifyValue(payload.last_refresh_status || '—')} · threshold=${stringifyValue(payload.threshold_days || '—')}d`}
          badges={[`days_old ${formatNumber(payload.days_old)}`, payload.needs_refresh ? 'stale' : 'fresh']}
        />
      </>
    );
  }
  if (section === 'dimensions11') {
    const rows = dimensionsConfidenceRows(payload);
    return (
      <>
        {rows.map((row) => (
          <EvidenceArticle
            key={row.key}
            title={row.label}
            meta="rule_engine · vkpi_kol_pool + cached posts"
            value={row.confidenceLabel}
            detail={row.detail}
            badges={[row.ready ? 'ready' : 'no evidence']}
          />
        ))}
      </>
    );
  }
  if (section === 'competitors') {
    const evidence = arrayRecords(payload.evidence);
    const relations = arrayRecords(payload.relations);
    const summary = recordValue(payload.summary);
    if (!evidence.length && !relations.length) return <EvidenceArticle title="暂无竞品证据" meta={stringifyValue(payload.source || 'competitor detector')} value={statusLabel(payload.status)} detail="没有可展示的竞品关系行。" />;
    return (
      <>
        <EvidenceArticle
          title="竞品摘要"
          meta="vkpi_competitor_relation or cached posts"
          value={stringifyValue(summary.risk_tier || 'unknown')}
          detail={`brand=${stringifyValue(summary.competitor_brand || '—')} · risk_score=${formatNumber(summary.risk_score)}`}
          badges={[`evidence ${formatNumber(payload.evidence_count || evidence.length)}`, `relations ${relations.length}`]}
        />
        {evidence.slice(0, 12).map((row, index) => (
          <EvidenceArticle
            key={`competitor-evidence-${stringifyValue(row.evidence_id || index)}`}
            title={stringifyValue(row.competitor_brand || row.brand || row.title || 'Competitor signal')}
            meta={`${stringifyValue(row.risk_tier || 'unknown')} · ${stringifyValue(row.source_table || row.source || 'competitor_signal')}`}
            value={formatConfidenceValue(row.confidence)}
            detail={stringifyValue(row.reasoning || row.title || '规则引擎命中竞品关系证据。')}
            url={stringifyValue(row.source_url || row.url)}
            badges={[
              stringifyValue(row.collaboration_depth),
              stringifyValue(row.sentiment),
              `90d ${formatNumber(row.collaboration_count_90d)}`,
              `total ${formatNumber(row.collaboration_count_total)}`,
              `risk ${formatNumber(row.risk_score)}`,
            ].filter(Boolean)}
          />
        ))}
        {!evidence.length ? relations.slice(0, 10).map((row, index) => (
          <EvidenceArticle
            key={`competitor-${index}`}
            title={stringifyValue(row.competitor_brand || row.brand || 'Competitor signal')}
            meta={stringifyValue(row.collaboration_depth || row.sentiment || 'competitor_signal')}
            value={stringifyValue(row.risk_tier || 'unknown')}
            detail={`90d=${formatNumber(row.collaboration_count_90d)} · total=${formatNumber(row.collaboration_count_total)} · risk=${formatNumber(row.risk_score)}`}
            badges={[stringifyValue(row.platform), stringifyValue(row.handle)].filter(Boolean)}
          />
        )) : null}
      </>
    );
  }
  if (section === 'brand_signal') {
    const signals = arrayRecords(payload.signals);
    if (!signals.length) return <EvidenceArticle title="暂无 Brand Signal" meta={stringifyValue(payload.source || 'cached posts')} value={statusLabel(payload.status)} detail="缓存帖子中暂未检测到 Viltrox / SKU / 竞品信号。" />;
    return (
      <>
        {signals.slice(0, 12).map((row, index) => (
          <EvidenceArticle
            key={`brand-signal-${index}`}
            title={stringifyValue(row.signal_type || row.brand || 'Brand signal')}
            meta={stringifyValue(row.brand_role || row.platform || 'brand_signal')}
            value={formatConfidenceValue(row.confidence)}
            detail={stringifyValue(row.reason || row.title || row.text || row.post_title || '缓存内容命中品牌信号。')}
            url={stringifyValue(row.source_url || row.post_url || row.url)}
            badges={[stringifyValue(row.brand), stringifyValue(row.matched_text)].filter(Boolean)}
          />
        ))}
      </>
    );
  }
  if (section === 'comment_intelligence') {
    const contract = recordValue(payload.contract);
    const counts = recordValue(payload.counts);
    const sentiment = recordValue(counts.sentiment);
    const runs = arrayRecords(payload.runs);
    const samples = arrayRecords(payload.samples);
    return (
      <>
        <EvidenceArticle
          title="评论数据契约"
          meta={stringifyValue(payload.source || 'vkpi_comments')}
          value={statusLabel(payload.status)}
          detail={`declared=${formatNumber(contract.declared)} · cached=${formatNumber(contract.cached)} · cap=${formatNumber(contract.cap)} · status=${stringifyValue(contract.status || 'unknown')}`}
          badges={[
            `runs ${formatNumber(payload.run_count)}`,
            `positive ${formatNumber(sentiment.positive)}`,
            `negative ${formatNumber(sentiment.negative)}`,
            `questions ${formatNumber(counts.questions)}`,
            `issues ${formatNumber(counts.issues)}`,
          ]}
        />
        {runs.slice(0, 8).map((row, index) => (
          <EvidenceArticle
            key={`comment-run-${stringifyValue(row.source_id || index)}`}
            title={`Run ${stringifyValue(row.source_id || row.run_uid || index + 1)}`}
            meta={stringifyValue(row.triggered_by || row.source_table || 'comment_intelligence_run')}
            value={statusLabel(row.status)}
            detail={`post=${formatNumber(row.post_id)} · fetched=${formatNumber(row.fetched_count)} · new=${formatNumber(row.new_count)} · sentiment=${formatNumber(row.sentiment_count)} · pillar=${stringifyValue(row.pillar_status || '—')}`}
            badges={[
              stringifyValue(row.finished_at || row.created_at),
              row.error_message ? 'has error' : '',
            ].filter(Boolean)}
          />
        ))}
        {samples.slice(0, 12).map((row, index) => (
          <EvidenceArticle
            key={`comment-sample-${stringifyValue(row.source_id || index)}`}
            title={stringifyValue(row.author || `Comment ${index + 1}`)}
            meta={`${stringifyValue(row.sentiment || row.rule_sentiment || 'unknown')} · ${stringifyValue(row.pillar_key || row.platform || 'comment')}`}
            value={formatConfidenceValue(row.sentiment_confidence || row.confidence)}
            detail={stringifyValue(row.text_excerpt || 'cached comment sample')}
            badges={[
              stringifyValue(row.brand_attitude),
              ...(Array.isArray(row.tags) ? row.tags.map(stringifyValue) : []),
              `likes ${formatNumber(row.likes)}`,
            ].filter(Boolean)}
          />
        ))}
        {!runs.length && !samples.length ? (
          <EvidenceArticle title="暂无评论样本" meta={stringifyValue(payload.method || 'comment_intelligence')} value={statusLabel(payload.status)} detail="当前没有匹配到已缓存评论或评论智能 run；不会伪装成已分析。" />
        ) : null}
      </>
    );
  }
  if (section === 'video_analysis') {
    const evidence = arrayRecords(payload.evidence);
    const fieldCounts = recordValue(payload.field_counts);
    return (
      <>
        <EvidenceArticle
          title="视频分析字段契约"
          meta={stringifyValue(payload.source || 'submissions.video_analysis')}
          value={statusLabel(payload.status)}
          detail={`stored_rows=${formatNumber(payload.row_count)} · analyzed=${formatNumber(payload.analyzed_count)} · fields=${Object.keys(fieldCounts).slice(0, 6).join(' / ') || 'none'}`}
          badges={[
            `evidence ${formatNumber(payload.evidence_count)}`,
            stringifyValue(payload.empty_reason),
          ].filter(Boolean)}
        />
        {evidence.slice(0, 12).map((row, index) => {
          const fields = recordValue(row.fields);
          const fieldNames = Array.isArray(row.field_names) ? row.field_names.map(stringifyValue).filter(Boolean) : [];
          const qualityScores = recordValue(fields.quality_scores);
          const title = stringifyValue(row.title || row.source_url || `Video analysis ${index + 1}`);
          const detailParts = [
            fields.target_audience ? `audience=${stringifyValue(fields.target_audience)}` : '',
            fields.production_quality ? `quality=${stringifyValue(fields.production_quality)}` : '',
            fields.marketing_potential ? `marketing=${stringifyValue(fields.marketing_potential)}` : '',
            fields.reference_value ? `reference=${stringifyValue(fields.reference_value)}` : '',
          ].filter(Boolean);
          return (
            <EvidenceArticle
              key={`video-analysis-${stringifyValue(row.evidence_id || index)}`}
              title={title}
              meta={stringifyValue(row.method || row.source_table || 'stored_video_analysis')}
              value={formatConfidenceValue(row.confidence)}
              detail={detailParts.join(' · ') || stringifyValue(row.reasoning || 'Stored video analysis evidence')}
              url={stringifyValue(row.source_url)}
              badges={[
                ...fieldNames.slice(0, 6),
                qualityScores.overall ? `overall ${formatNumber(qualityScores.overall)}` : '',
                row.provider_badge_allowed ? 'provider row stored' : '',
              ].filter(Boolean)}
            />
          );
        })}
        {!evidence.length ? (
          <EvidenceArticle title="暂无视频分析证据" meta={stringifyValue(payload.method || 'stored_video_analysis')} value={statusLabel(payload.status)} detail="没有已存储的 analyzed=true 视频分析行；不会把 Gemini preflight 或计划字段伪装成已分析结果。" />
        ) : null}
      </>
    );
  }
  if (section === 'memory_card') {
    const history = recordValue(payload.history_match);
    const excel = recordValue(payload.excel_record);
    const competitorMemory = recordValue(payload.competitor_memory);
    const posts = arrayRecords(payload.recent_posts);
    const cooperations = arrayRecords(payload.recent_cooperations);
    return (
      <>
        <EvidenceArticle
          title="历史匹配"
          meta={stringifyValue(payload.source_type || excel.source_type || 'excel_legacy')}
          value={history.matched ? 'matched' : statusLabel(history.status)}
          detail={`match=${stringifyValue(history.match_type || '—')} · cooperation=${formatNumber(history.cooperation_count)} · evidence=${formatNumber(history.evidence_count)}`}
          badges={[stringifyValue(payload.source_ref || excel.source_ref), payload.linked_main_kol_id ? `main #${payload.linked_main_kol_id}` : ''].filter(Boolean)}
        />
        <EvidenceArticle
          title="Excel 记录"
          meta={stringifyValue(excel.source_ref || 'vkpi_kol_pool')}
          value={stringifyValue(excel.source_type || 'legacy')}
          detail={`产品 ${parseMaybeList(excel.recommended_products).slice(0, 3).join(' / ') || '—'} · 关注点 ${parseMaybeList(excel.potential_concerns).slice(0, 2).join(' / ') || '—'}`}
          badges={parseMaybeList(excel.brand_collaborations).slice(0, 4)}
        />
        <EvidenceArticle
          title="竞品记忆"
          meta="competitor_signal"
          value={stringifyValue(competitorMemory.risk_tier || 'opportunity')}
          detail={`brand=${stringifyValue(competitorMemory.strongest_brand || '—')} · relations=${formatNumber(competitorMemory.relation_count)} · risk=${formatNumber(competitorMemory.risk_score)}`}
        />
        {cooperations.slice(0, 5).map((row, index) => (
          <EvidenceArticle
            key={`cooperation-${index}`}
            title={stringifyValue(row.project || row.product || '历史合作')}
            meta={stringifyValue(row.status || row.cooperation_date || 'cooperation_history')}
            value={stringifyValue(row.product || row.project || 'record')}
            detail={stringifyValue(row.content_link || row.note || row.cooperation_date || 'legacy cooperation row')}
            url={stringifyValue(row.content_link)}
          />
        ))}
        {posts.slice(0, 6).map((row, index) => (
          <EvidenceArticle
            key={`memory-post-${index}`}
            title={stringifyValue(row.title || row.id || '最近内容')}
            meta={stringifyValue(row.published_at || row.source_kind || 'platform_cache')}
            value={formatNumber(row.views)}
            detail={`likes=${formatNumber(row.likes)} · comments=${formatNumber(row.comments)}`}
            url={stringifyValue(row.post_url || row.url)}
          />
        ))}
      </>
    );
  }
  if (section === 'product_fit') {
    const evidenceRows = arrayRecords(payload.evidence);
    if (evidenceRows.length) {
      return (
        <>
          {evidenceRows.slice(0, 16).map((row, index) => {
            const source = stringifyValue(row.source || 'rule_engine');
            const specs = recordValue(row.specs);
            const official = source === 'official_catalog';
            const discovery = row.confidence_method === 'rule_v0_low_confidence';
            const title = stringifyValue(row.sku || row.model_name || row.product_family_name || row.evidence_type || `product-fit-${index + 1}`);
            const meta = official
              ? `official catalog · ${stringifyValue(row.mount || specs.lens_mount || 'mount pending')}`
              : discovery
                ? 'product-family discovery'
                : stringifyValue(row.source_table || source);
            const detailParts = [
              stringifyValue(row.reasoning),
              row.price_usd !== undefined && row.price_usd !== null ? `price $${formatNumber(row.price_usd)}` : '',
              specs.focal_length ? `focal ${stringifyValue(specs.focal_length)}` : '',
              specs.aperture ? `aperture ${stringifyValue(specs.aperture)}` : '',
            ].filter(Boolean);
            return (
              <EvidenceArticle
                key={`product-fit-evidence-${index}`}
                title={title}
                meta={meta}
                value={row.score !== undefined ? formatScoreValue(row.score) : formatConfidenceValue(row.confidence)}
                detail={detailParts.join(' · ') || 'Product Fit evidence'}
                url={stringifyValue(row.source_url)}
                badges={[
                  official ? 'official SKU evidence' : discovery ? 'low confidence discovery' : 'rule evidence',
                  stringifyValue(row.score_component),
                  stringifyValue(row.source_id),
                ].filter(Boolean)}
              />
            );
          })}
        </>
      );
    }
    const rows = arrayRecords(payload.top);
    if (!rows.length) return <EvidenceArticle title="暂无 Product Fit 证据" meta={stringifyValue(payload.method || payload.reason || 'product_fit')} value={statusLabel(payload.status)} detail="当前没有 SKU 或产品适配行；不能把空结果当作产品不适配。" />;
    return (
      <>
        {rows.slice(0, 10).map((row, index) => {
          const sku = stringifyValue(row.sku || row.product_sku || row.product_key || row.family_key || row.product_family || row.product_name || row.name || `product-${index + 1}`);
          const official = Boolean(row.sku || row.product_sku || row.catalog_product_id || row.mount || row.specs || row.price_usd);
          return (
            <EvidenceArticle
              key={`product-fit-${index}`}
              title={sku}
              meta={official ? 'official_catalog / rule_engine' : 'product-family discovery'}
              value={formatScoreValue(row.score || row.fit_score || row.total_score)}
              detail={stringifyValue(row.reason || row.match_reason || row.explanation || 'Product Fit rule evidence')}
              url={stringifyValue(row.source_url || row.product_url || row.url)}
              badges={[official ? 'official evidence' : 'low confidence discovery', stringifyValue(row.mount), stringifyValue(row.price_usd)].filter(Boolean)}
            />
          );
        })}
      </>
    );
  }
  return <EvidenceArticle title={evidenceSectionLabel(section)} meta="raw section payload" value={statusLabel(payload.status)} detail={JSON.stringify(payload).slice(0, 600)} />;
}

function EvidenceArticle({
  title,
  meta,
  value,
  detail,
  url,
  badges = [],
}: {
  title: string;
  meta: string;
  value: string;
  detail: string;
  url?: string;
  badges?: string[];
}) {
  return (
    <article>
      <div>
        <div>
          <strong>{title || '证据行'}</strong>
          <span>{meta || 'stored evidence'}</span>
        </div>
        <b>{value || '—'}</b>
      </div>
      <p>{detail || '无更多描述。'}</p>
      {badges.length ? (
        <em>{badges.map((badge) => badge.trim()).filter(Boolean).slice(0, 6).join(' · ')}</em>
      ) : null}
      {url ? <a className="vkpi-evidence-link" href={url} target="_blank" rel="noreferrer">Open original</a> : null}
    </article>
  );
}
