import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';

const apiFetchMock = vi.fn();
vi.mock('../../../services/http', () => ({
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
}));

import { LlmProductionReadinessCard } from './SettingsPage';

beforeEach(() => {
  apiFetchMock.mockReset();
});

describe('LLM 生产就绪只读卡片', () => {
  it('仅读取系统模型审计，区分候选、生产闸门和任务绑定', async () => {
    apiFetchMock.mockResolvedValue({
      readiness_audit: {
        candidate_count: 7,
        configured_count: 6,
        probed_count: 2,
        evaluated_count: 2,
        production_ready_count: 2,
        blocked_count: 5,
        attestation_trust_roots: {
          exact_probe: { configured: true, declared_key_count: 1, valid_key_count: 1 },
          evaluation: { configured: true, declared_key_count: 1, valid_key_count: 1 },
          distinct_key_ids: true,
          distinct_public_keys: true,
          ready_to_verify_signed_evidence: true,
          runtime_can_extend_trust_roots: false,
          release_review_required: true,
          failure_reasons: [],
        },
        evidence_source: {
          source: 'VKPI_MODEL_READINESS_EVIDENCE_JSON',
          parsed: true,
          binding_count: 7,
          secret_values_exposed: false,
        },
      },
      task_model_readiness: {
        via_chat: {
          binding: 'openai/gpt-5.4-mini',
          state: 'configured',
          configured: true,
          probed: true,
          evaluated: true,
          production_ready: true,
          probe: { attestation_verified: true },
          evaluation: { attestation_verified: true, sample_count: 30, success_rate: 1, structured_valid_rate: 1, factual_valid_rate: 1, source_valid_rate: 1, safety_valid_rate: 1, latency_ms: { p95: 1200 } },
        },
        kol_audience_analysis: {
          binding: 'google/gemini-3.5-flash',
          state: 'configured',
          configured: true,
          probed: false,
          evaluated: false,
          production_ready: false,
          runtime_gate: { failure_reasons: ['probe_evidence_missing', 'evaluation_evidence_missing'] },
          probe: { attestation_verified: false },
          evaluation: { attestation_verified: false, sample_count: 0, latency_ms: { p95: null } },
          thresholds: { minimum_eval_samples: 30, maximum_p95_latency_ms: 15000 },
        },
        audit_video_analysis: {
          binding: 'google/gemini-3.5-flash',
          state: 'configured',
          configured: true,
          production_ready: true,
        },
      },
      available_models_semantics: 'registered_candidates_only_not_verified_availability',
    });

    render(<LlmProductionReadinessCard apiToken="admin-token" />);

    expect(await screen.findByText('已有 2 个精确模型绑定通过当前证据闸门')).toBeInTheDocument();
    expect(within(screen.getByText('候选注册').parentElement as HTMLElement).getByText('7')).toBeInTheDocument();
    expect(within(screen.getByText('环境凭据已配置').parentElement as HTMLElement).getByText('6/7')).toBeInTheDocument();
    expect(within(screen.getByText('精确模型探针').parentElement as HTMLElement).getByText('2/7')).toBeInTheDocument();
    expect(within(screen.getByText('真实评测').parentElement as HTMLElement).getByText('2/7')).toBeInTheDocument();
    expect(within(screen.getByText('生产闸门通过').parentElement as HTMLElement).getByText('2')).toBeInTheDocument();
    expect(within(screen.getByText('生产闸门阻断').parentElement as HTMLElement).getByText('5')).toBeInTheDocument();
    expect(screen.getByText('2 通过 / 1 阻断')).toBeInTheDocument();
    expect(screen.getByText('已注册 / 已配置不等于可用；', { exact: false })).toBeInTheDocument();
    expect(screen.getByText('本卡片只读，不会调用外部模型；', { exact: false })).toBeInTheDocument();
    expect(screen.getAllByText('环境凭据已配置', { exact: false })).toHaveLength(2);
    expect(screen.getByText('证据来源：VKPI_MODEL_READINESS_EVIDENCE_JSON · 已解析 7 个绑定')).toBeInTheDocument();
    expect(within(screen.getByTestId('llm-trust-root-status')).getByText(/已具备校验独立签名证据/)).toBeInTheDocument();
    expect(screen.getByText('逐任务真实状态（2/3 通过）')).toBeInTheDocument();
    expect(within(screen.getByTestId('llm-task-kol_audience_analysis')).getByText('KOL 受众分析')).toBeInTheDocument();
    expect(within(screen.getByTestId('llm-task-kol_audience_analysis')).getByText(/缺少该精确模型的实时探针证据/)).toBeInTheDocument();
    expect(screen.getByText(/当前模型清单仅代表候选注册/)).toBeInTheDocument();

    expect(apiFetchMock).toHaveBeenCalledTimes(1);
    expect(apiFetchMock).toHaveBeenCalledWith(
      '/api/admin/system/models',
      expect.objectContaining({ timeoutMs: 15000, signal: expect.any(AbortSignal) }),
      'admin-token',
    );
  });

  it('信任根未发布时直接显示真正阻断原因', async () => {
    apiFetchMock.mockResolvedValue({
      readiness_audit: {
        candidate_count: 18,
        configured_count: 6,
        probed_count: 0,
        evaluated_count: 0,
        production_ready_count: 0,
        blocked_count: 18,
        attestation_trust_roots: {
          exact_probe: { configured: false, declared_key_count: 0, valid_key_count: 0 },
          evaluation: { configured: false, declared_key_count: 0, valid_key_count: 0 },
          distinct_key_ids: true,
          distinct_public_keys: true,
          ready_to_verify_signed_evidence: false,
          runtime_can_extend_trust_roots: false,
          release_review_required: true,
          failure_reasons: ['probe_trust_root_missing', 'evaluation_trust_root_missing'],
        },
      },
      task_model_readiness: {},
    });

    render(<LlmProductionReadinessCard apiToken="admin-token" />);

    const status = await screen.findByTestId('llm-trust-root-status');
    expect(within(status).getByText(/必须由发布审核提供两套不同公钥/)).toBeInTheDocument();
    expect(within(status).getByText(/probe_trust_root_missing/)).toBeInTheDocument();
    expect(within(status).getByText(/evaluation_trust_root_missing/)).toBeInTheDocument();
  });

  it('接口不可访问时明确显示不可核验，不伪造就绪数据', async () => {
    apiFetchMock.mockRejectedValue(new Error('403 Forbidden'));

    render(<LlmProductionReadinessCard apiToken="expired-token" />);

    expect(await screen.findByText('不可核验：403 Forbidden')).toBeInTheDocument();
    expect(screen.queryByText('候选注册')).not.toBeInTheDocument();
    expect(screen.queryByText('生产闸门通过')).not.toBeInTheDocument();
  });

  it('无管理员会话时不发请求，保留 AI-off 基础流程说明', async () => {
    render(<LlmProductionReadinessCard />);

    expect(screen.getByText('不可核验：缺少管理员会话。')).toBeInTheDocument();
    expect(screen.getByText('AI 未就绪或关闭时，基础数据流程继续可用。', { exact: false })).toBeInTheDocument();
    await waitFor(() => expect(apiFetchMock).not.toHaveBeenCalled());
  });
});
