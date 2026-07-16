import { Component, type ErrorInfo, type ReactNode } from 'react';
import { formatLargeNum } from '../projectDeliverableStyle';
import type { VkpiProjectRow } from '../../../vkpiTypes';
import type { VkpiProjectVideoAnalysisCacheItem } from '../../../../../services/vkpi/projects-api';
import {
  analysisScoreColor,
  asRecord,
  compactText,
  finalV1Payload,
  finalV1QaPayload,
  firstText,
  layerValue,
  normaliseRiskFlags,
  normaliseScore,
  qaBoolean,
  qaCheckTags,
  qaIssueItems,
  qaScoreCorrectionText,
  qaStatusClass,
  qaStatusLabel,
  retrospectiveRowInitial,
  retrospectiveVideoUrl,
  textFrom,
} from './CampaignRetrospectiveTab.helpers';

export class RetrospectiveCardErrorBoundary extends Component<{ children: ReactNode; label: string }, { hasError: boolean }> {
  state = { hasError: false };

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error: Error, _info: ErrorInfo) {
    console.warn('final_v1 analysis card render failed', this.props.label, error);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="rounded-lg border border-rose-400/20 bg-rose-500/[0.04] p-3 text-[10.5px] text-rose-200">
          final_v1 卡片渲染异常：{this.props.label}。该条结果仍保留在缓存中，等待字段 normalizer 补齐。
        </div>
      );
    }
    return this.props.children;
  }
}

export function ProjectVideoAnalysisCard({
  row,
  item,
  qaItem,
}: {
  row: VkpiProjectRow;
  item: VkpiProjectVideoAnalysisCacheItem;
  qaItem?: VkpiProjectVideoAnalysisCacheItem | null;
}) {
  const ready = item.state === 'ready' && item.entry;
  const displayName = row.kolName || item.kol_name || item.handle || 'Unknown';
  const videoUrl = item.content_url || retrospectiveVideoUrl(row);
  const views = item.view_count ?? row.views ?? 0;
  const likes = item.like_count ?? row.likes ?? 0;
  const comments = item.comment_count ?? row.comments ?? 0;
  const payload = ready ? finalV1Payload(item.entry) : {};
  const layer2 = asRecord(payload.layer2_viewer_emotion);
  const layer3 = asRecord(payload.layer3_three_values);
  const layer6 = asRecord(payload.layer6_flags_and_scores);
  const scores = asRecord(layer6.scores);
  const contentScore = normaliseScore(scores.content_quality_score);
  const marketingScore = normaliseScore(scores.marketing_value_score ?? layer6.marketing_value_score);
  const viewerHeart = normaliseScore(layer2.viewer_heart_score ?? layer2.heart_movement_score, scores.viewer_heart_score);
  const channelValue = layerValue(layer3, 'channel_value', scores.channel_value_score);
  const assetValue = layerValue(layer3, 'asset_value', scores.asset_reuse_score);
  const productProof = layerValue(layer3, 'product_proof_value', scores.product_proof_score);
  const viewerReaction = firstText(layer2.one_sentence_viewer_reaction, layer2.one_sentence_viewer_feeling);
  const dislike = firstText(layer2.dislike_or_resistance, layer2.annoyance_or_ad_fatigue);
  const trigger = firstText(layer2.purchase_or_interest_trigger, layer2.desire_to_click_or_buy);
  const keyHook = textFrom(layer6.key_hook);
  const riskFlagTags = normaliseRiskFlags(layer6.risk_flags);
  const verdict = textFrom(layer6.final_verdict) || marketingScore.rationale || keyHook;
  const activeJobStatus = String(item.active_job?.status || '').toLowerCase();
  const analysisIsActive = ['queued', 'running', 'retrying', 'processing'].includes(activeJobStatus);
  const qaReady = qaItem?.state === 'ready' && Boolean(qaItem.entry);
  const qaActiveStatus = String(qaItem?.active_job?.status || '').toLowerCase();
  const qaIsActive = ['queued', 'running', 'retrying', 'processing'].includes(qaActiveStatus);
  const qaVerificationText = qaReady
    ? '已核验'
    : qaItem?.state === 'unsupported'
      ? '分析完成·QA仅支持YouTube'
      : qaItem?.state === 'failed'
        ? '分析完成·QA未完成'
        : qaIsActive
          ? '分析完成·QA待核验'
          : '分析完成·QA未请求';
  const qaPayload = qaReady ? finalV1QaPayload(qaItem?.entry) : {};
  const qaHasPayload = Object.keys(qaPayload).length > 0;
  const qaResultRecord = asRecord(qaItem?.entry?.result);
  const qaPass = qaBoolean(qaPayload.qa_pass ?? qaResultRecord.qa_pass);
  const qaBadgeText = qaPass === false ? '需复核' : qaPass === true ? '通过' : '未定';
  const qaSummary = textFrom(qaPayload.summary);
  const qaConfidence = Number(qaPayload.confidence);
  const qaChecks = qaCheckTags(qaPayload.checks);
  const qaIssues = qaIssueItems(qaPayload.issues);
  const qaCorrection = qaScoreCorrectionText(qaPayload.score_correction);
  const qaAction = textFrom(qaPayload.recommended_review_action);
  const fullLayers = [
    ['layer1 画面', payload.layer1_visual_content],
    ['layer2 心动', payload.layer2_viewer_emotion],
    ['layer3 价值', payload.layer3_three_values],
    ['layer4 归因', payload.layer4_attribution],
    ['layer5 建议', payload.layer5_recommendations],
    ['layer6 评分', payload.layer6_flags_and_scores],
  ];

  if (!ready) {
    const pendingLabel = analysisIsActive
      ? activeJobStatus === 'queued' || activeJobStatus === 'retrying' ? '排队中' : '分析中'
      : item.state === 'failed'
        ? '分析未完成'
        : item.state === 'unsupported'
          ? '暂不支持'
          : item.state === 'pending'
            ? '状态待确认'
            : '尚未请求';
    const pendingBody = analysisIsActive
      ? `Worker 已有 ${activeJobStatus} 任务，完成写入缓存后这里会自动亮起。`
      : item.state === 'failed'
        ? `最近任务未产出可用缓存${item.terminal_reason ? `：${item.terminal_reason}` : '。'}`
        : item.state === 'unsupported'
          ? '当前视频平台不支持这项分析。'
          : item.state === 'pending'
            ? '旧接口未返回活动任务证据；不会自动轮询，请点“刷新状态”确认。'
            : '当前没有 queued/running 分析任务；“刷新状态”只读取现状，不会新建任务。';
    return (
      <div className="rounded-lg border border-white/[0.05] bg-white/[0.012] p-3">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="text-[11px] font-semibold text-slate-300 truncate">{displayName}</div>
            <div className="text-[9.5px] text-slate-500 truncate">{item.platform || row.platform} · evidence #{item.evidence_id || '-'}</div>
          </div>
          <span className={`px-2 py-1 rounded text-[10px] shrink-0 ${analysisIsActive ? 'bg-amber-500/10 text-amber-200' : 'bg-white/[0.05] text-slate-400'}`}>{pendingLabel}</span>
        </div>
        <div className="mt-2 text-[10.5px] text-slate-500">{pendingBody}</div>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-cyan-400/20 bg-cyan-400/[0.035] p-3 space-y-3">
      <div className="flex items-center gap-3">
        {row.kolAvatar ? (
          <img src={row.kolAvatar} alt={displayName} className="w-8 h-8 rounded-full object-cover shrink-0" />
        ) : (
          <div className="w-8 h-8 rounded-full bg-cyan-500/15 text-cyan-200 flex items-center justify-center text-[11px] font-bold shrink-0">{retrospectiveRowInitial(row)}</div>
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-semibold text-white truncate">{displayName}</span>
            <span className={`px-1.5 py-0.5 rounded text-[9.5px] ${qaReady ? 'bg-emerald-500/15 text-emerald-300' : 'bg-amber-500/10 text-amber-200'}`}>
              {qaVerificationText}
            </span>
          </div>
          <div className="text-[9.5px] text-slate-500 truncate">{item.platform || row.platform} · 播放 {formatLargeNum(views)} · 赞 {formatLargeNum(likes)} · 评论 {formatLargeNum(comments)}</div>
        </div>
        {videoUrl ? <a href={videoUrl} target="_blank" rel="noreferrer" className="text-[10px] text-cyan-300 shrink-0">看视频</a> : null}
      </div>

      <div className="grid grid-cols-2 gap-2">
        {[
          ['内容质量', contentScore],
          ['投放价值', marketingScore],
        ].map(([label, score]) => {
          const itemScore = score as ReturnType<typeof normaliseScore>;
          return (
            <div key={label as string} className="rounded-md bg-black/30 border border-white/[0.05] px-3 py-2">
              <div className="text-[9.5px] text-slate-500 mb-1">{label as string}</div>
              <div className="text-[28px] font-bold leading-none tabular-nums" style={{ color: analysisScoreColor(itemScore.score) }}>{itemScore.score ?? '—'}</div>
            </div>
          );
        })}
      </div>
      <div className="text-[10.5px] text-slate-300 leading-relaxed">{compactText(verdict, 190)}</div>

      <div className="grid md:grid-cols-3 gap-2">
        {[
          ['渠道价值', channelValue],
          ['素材复用', assetValue],
          ['产品证明', productProof],
        ].map(([label, value]) => {
          const block = value as ReturnType<typeof layerValue>;
          return (
            <div key={label as string} className="rounded-md bg-white/[0.025] border border-white/[0.05] p-2">
              <div className="flex items-center justify-between gap-2 mb-1">
                <span className="text-[9.5px] text-slate-500">{label as string}</span>
                <span className="text-[15px] font-bold tabular-nums" style={{ color: analysisScoreColor(block.score) }}>{block.score ?? '—'}</span>
              </div>
              <div className="text-[10px] text-slate-300 leading-relaxed">{compactText(block.text, 92)}</div>
            </div>
          );
        })}
      </div>

      <div className="rounded-md bg-purple-500/[0.06] border border-purple-400/15 p-2.5">
        <div className="text-[9.5px] text-purple-300 mb-1">观众心动</div>
        <div className="italic text-[11px] text-slate-100 leading-relaxed">“{viewerReaction || '暂无一句话观众反应'}”</div>
        <div className="mt-2 flex flex-wrap gap-1.5 text-[9.5px]">
          <span className="px-1.5 py-0.5 rounded bg-white/[0.05] text-slate-300">心动 {viewerHeart.score ?? '—'}</span>
          {dislike ? <span className="px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-200">反感: {compactText(dislike, 42)}</span> : null}
          {trigger ? <span className="px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-200">种草: {compactText(trigger, 42)}</span> : null}
        </div>
      </div>

      <div className="flex flex-wrap gap-2 text-[10px]">
        {keyHook ? <span className="px-2 py-1 rounded bg-cyan-500/10 text-cyan-200">Hook: {compactText(keyHook, 90)}</span> : null}
        {riskFlagTags.map((flag, index) => (
          <span key={`${flag.label}-${index}`} className={`px-2 py-1 rounded ${flag.severity === 'high' ? 'bg-rose-500/15 text-rose-200' : 'bg-amber-500/10 text-amber-200'}`}>
            风险: {compactText(flag.label, 90)}
          </span>
        ))}
      </div>

      {qaHasPayload ? (
        <div className={`rounded-md border p-2.5 ${qaPass === false ? 'border-rose-400/20 bg-rose-500/[0.045]' : 'border-emerald-400/15 bg-emerald-500/[0.035]'}`}>
          <div className="flex items-center justify-between gap-2 mb-2">
            <div className="flex items-center gap-2 min-w-0">
              <span className={`px-2 py-1 rounded text-[9.5px] font-medium ${qaPass === false ? 'bg-rose-500/15 text-rose-200' : 'bg-emerald-500/15 text-emerald-200'}`}>
                关键帧 QA {qaBadgeText}
              </span>
              {Number.isFinite(qaConfidence) ? <span className="text-[9.5px] text-slate-500">置信 {Math.round(qaConfidence * 100)}%</span> : null}
            </div>
            {qaAction ? <span className="text-[9.5px] text-slate-500 shrink-0">{qaAction.replace(/_/g, ' ')}</span> : null}
          </div>
          {qaSummary ? <div className="text-[10.5px] text-slate-200 leading-relaxed mb-2">{compactText(qaSummary, 180)}</div> : null}
          {qaChecks.length ? (
            <div className="flex flex-wrap gap-1.5 mb-2">
              {qaChecks.map((check) => (
                <span key={check.key} className={`px-2 py-1 rounded border text-[9.5px] ${qaStatusClass(check.status)}`} title={check.detail || undefined}>
                  {check.label}: {qaStatusLabel(check.status)}
                </span>
              ))}
            </div>
          ) : null}
          {qaIssues.length ? (
            <div className="space-y-1.5 mb-2">
              {qaIssues.slice(0, 3).map((issue) => (
                <div key={issue.key} className="rounded bg-black/20 border border-white/[0.05] px-2 py-1.5 text-[10px] text-slate-300">
                  <span className="text-amber-200">{issue.label}</span>
                  {issue.evidence ? <span> · {compactText(issue.evidence, 110)}</span> : null}
                  {issue.correction ? <span className="text-cyan-200"> · {compactText(issue.correction, 90)}</span> : null}
                </div>
              ))}
            </div>
          ) : null}
          {qaCorrection ? <div className="text-[10px] text-slate-400">纠偏建议: {compactText(qaCorrection, 190)}</div> : null}
        </div>
      ) : null}

      <details className="rounded-md border border-white/[0.05] bg-black/20">
        <summary className="cursor-pointer px-3 py-2 text-[10.5px] text-cyan-200">展开完整6层</summary>
        <div className="p-3 grid gap-2">
          {fullLayers.map(([label, layer]) => (
            <div key={label as string}>
              <div className="text-[9.5px] text-slate-500 mb-1">{label as string}</div>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-black/30 p-2 text-[10px] leading-relaxed text-slate-300">{JSON.stringify(layer || {}, null, 2)}</pre>
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}
