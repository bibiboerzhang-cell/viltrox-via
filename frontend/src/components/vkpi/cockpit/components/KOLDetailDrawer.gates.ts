export function videoAnalysisGateMessage(payload: any): string {
  const gate = String(payload?.provider_gate_reason || payload?.reason || payload?.status || "").trim();
  const readiness = String(payload?.model_readiness_status || "").trim();
  if (/model_binding_blocked|readiness_not_production_ready|probe_evidence_missing|evaluation_evidence_missing/i.test(gate)) {
    return `精确视频模型尚未通过生产就绪，本次未入队${readiness ? `（${readiness}）` : ""}。`;
  }
  if (/budget_guard_blocked|budget_denied|budget/i.test(gate)) {
    return "预算授权尚未放行，本次未入队且未产生模型费用。";
  }
  if (/operator_disabled|provider_disabled|ai_disabled/i.test(gate)) {
    return "AI 视频分析当前未启用，本次未入队；基础视频证据仍保留。";
  }
  return `视频深析未入队${gate ? `（${gate}）` : ""}。`;
}
