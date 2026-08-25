export interface HumanizedLlmReason {
  message: string;
  code: string;
}

const REASON_COPY: ReadonlyArray<[string, string]> = [
  ["probe_trust_root_missing", "精确探针的发布审核信任根尚未配置。"],
  ["evaluation_trust_root_missing", "真实评测的独立发布审核信任根尚未配置。"],
  ["probe_trust_root_duplicate_public_keys", "精确探针信任根包含重复公钥，需重新审核。"],
  ["evaluation_trust_root_duplicate_public_keys", "真实评测信任根包含重复公钥，需重新审核。"],
  ["attestation_key_ids_must_differ", "探针与评测必须使用不同签名标识。"],
  ["attestation_public_keys_must_differ", "探针与评测必须使用不同签名公钥。"],
  ["probe_evidence_missing", "缺少该精确模型的实时探针证据。"],
  ["evaluation_evidence_missing", "缺少该精确模型的真实评测证据。"],
  ["evaluation_sample_count_below_minimum", "真实评测样本不足，尚未达到生产门槛。"],
  ["evaluation_p95_latency_above_maximum", "真实评测 P95 延迟超过生产门槛。"],
  ["evaluation_success_rate_below_minimum", "真实评测成功率未达到生产门槛。"],
  ["evaluation_structured_valid_rate_below_minimum", "结构化结果有效率未达到生产门槛。"],
  ["evaluation_factual_valid_rate_below_minimum", "事实有效率未达到生产门槛。"],
  ["evaluation_source_valid_rate_below_minimum", "来源有效率未达到生产门槛。"],
  ["evaluation_safety_valid_rate_below_minimum", "安全有效率未达到生产门槛。"],
  ["probe_attestation_unverified", "实时探针签名未通过独立信任根校验。"],
  ["pricing_unknown", "该精确模型尚无已审核价格目录。"],
  ["binding_not_in_registered_catalog", "任务绑定不在已审核候选目录中。"],
  ["advisor_budget_not_authorized", "模型预算尚未授权，本轮不会调用外部模型。"],
  ["budget_guard_blocked", "模型预算尚未授权，本轮不会调用外部模型。"],
  ["budget_check_failed", "模型预算状态无法完成校验，本轮已安全阻止外部调用。"],
  ["budget_blocked", "模型预算策略阻止了本轮外部调用。"],
  ["advisor_exact_model_not_production_ready", "指定模型尚未通过生产就绪校验，本轮不会调用外部模型。"],
  ["model_binding_blocked", "指定模型尚未通过生产就绪校验，AI 分析未启动。"],
  ["runtime_not_checked", "指定模型尚未完成运行时验证，AI 分析未启动。"],
  ["readiness_not_production_ready", "指定模型尚未通过生产就绪校验，AI 分析未启动。"],
  ["readiness_check_failed", "模型生产就绪证据暂时无法核验，AI 分析未启动。"],
  ["advisor_provider_not_connected", "外部模型通道尚未连接；会话仍可安全留存并使用私有本地上下文。"],
  ["provider_not_connected", "外部模型通道尚未连接，AI 分析未启动。"],
  ["provider_not_configured", "外部模型通道尚未配置，AI 分析未启动。"],
  ["provider_429", "外部模型正在限流，本任务可稍后重试。"],
  ["provider_5xx", "外部模型服务暂时异常，本任务可稍后重试。"],
  ["provider_http_error", "外部模型拒绝了本次请求，请检查模型权限或请求契约。"],
  ["provider_unavailable", "外部模型未能完成本次请求，本任务可按策略重试。"],
  ["provider_error", "外部模型未能完成本次请求，本任务可按策略重试。"],
  ["transport_error", "外部模型连接失败，本任务可稍后重试。"],
  ["provider_exception", "模型通道执行异常，本任务可按策略重试。"],
  ["provider_blocked", "模型通道当前被运行策略阻断，本轮未发起外部调用。"],
  ["all_providers_failed", "所有已授权模型通道均未完成本次请求。"],
  ["fleet_breaker_open", "模型通道熔断保护已开启，本轮未发起外部调用。"],
  ["parse_failure", "模型返回内容无法解析，未写入正式结果。"],
  ["validation_failure", "模型返回内容未通过业务校验，未写入正式结果。"],
  ["invalid_response", "模型返回内容不符合结果契约，未写入正式分析。"],
  ["schema_failure", "模型返回内容未通过结构校验，未写入正式分析。"],
  ["model_mismatch", "服务实际返回的模型与授权模型不一致，结果已拒绝。"],
  ["operator_disabled", "外部模型调用已由运营开关关闭，本轮未启动。"],
  ["timeout", "模型请求超时，本任务可稍后重试。"],
  ["advisor_schema_unavailable", "顾问会话存储尚未就绪，请先完成所需数据库迁移。"],
  ["advisor_persistence_unavailable", "顾问会话暂时无法留存，请稍后重试。"],
  ["provider_not_ready", "外部模型服务尚未就绪，AI 分析未启动。"],
];

function looksLikeMachineReason(value: string): boolean {
  return value.includes("_") && /^[a-z0-9_.:\-\s]+$/i.test(value);
}

export function humanizeLlmReason(value: unknown, fallback = "AI 服务暂时不可用，请稍后重试。"): HumanizedLlmReason {
  const raw = String(value ?? "").trim();
  if (!raw) return { message: fallback, code: "" };
  const normalized = raw.toLowerCase();
  const matched = REASON_COPY.find(([code]) => normalized.includes(code));
  if (matched) return { message: matched[1], code: matched[0] };
  if (looksLikeMachineReason(raw)) return { message: fallback, code: raw.slice(0, 160) };
  return { message: raw, code: "" };
}

export function llmErrorValue(error: unknown): unknown {
  if (error && typeof error === "object") {
    const detail = (error as { detail?: unknown }).detail;
    if (detail && typeof detail === "object") {
      const reason = (detail as { reason?: unknown }).reason;
      if (reason) return reason;
      const message = (detail as { message?: unknown }).message;
      if (message) return message;
    }
    if (typeof detail === "string" && detail) return detail;
    const message = (error as { message?: unknown }).message;
    if (message) return message;
  }
  return error;
}
