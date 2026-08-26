import { describe, expect, it } from "vitest";

import {
  analysisChannelLabel,
  analysisProviderTrace,
  analysisStageFlow,
  analysisTaskBindingLabel,
  analysisTaskBindingTrace,
  analysisTaskFamily,
  analysisTerminalCopy,
} from "./analysisTaskProgress";

describe("analysisTaskProgress", () => {
  it("为 profile/video/comments/audience/fit/QA/advisor 提供各自的真实阶段文案", () => {
    expect(analysisTaskFamily({ job_type: "kol_profile_deep_crawl" })).toBe("profile");
    expect(analysisTaskFamily({ job_type: "video_analysis_final_v1" })).toBe("video");
    expect(analysisTaskFamily({ job_type: "kol_pool_comments_collect" })).toBe("comments");
    expect(analysisTaskFamily({ job_type: "kol_audience_stats_refresh" })).toBe("audience");
    expect(analysisTaskFamily({ job_type: "kol_content_fit_analysis" })).toBe("fit");
    expect(analysisTaskFamily({ job_type: "video_analysis_final_v1_keyframe_qa" })).toBe("qa");
    expect(analysisTaskFamily({ purpose: "marketing_advisor" })).toBe("advisor");
    expect(analysisTaskFamily({ source: "llm_reservations", purpose: "generic_json" })).toBe("llm");

    expect(analysisStageFlow({ job_type: "kol_audience_stats_refresh" }).map((step) => step.label))
      .toEqual(["队列", "评论样本", "受众推断", "画像落库"]);
    expect(analysisStageFlow({ job_type: "video_analysis_final_v1_keyframe_qa" }).map((step) => step.label))
      .toEqual(["队列", "媒体抽帧", "模型复核", "QA 落库"]);
  });

  it("规则回退与 provider 回退均诚实标记，不伪装为首选模型结论", () => {
    expect(analysisTerminalCopy({ status: "failed", fallback_mode: "rule_v0" })).toEqual({
      label: "规则回退",
      detail: "生产模型未形成可用结果；仅保留规则降级输出，不冒充模型结论。",
      tone: "failed",
    });
    expect(analysisTerminalCopy({ status: "done", fallback_mode: "provider_fallback" }).label)
      .toBe("回退模型后完成");
  });

  it("区分阻塞、取消、部分完成、超时与待人工排查，不统一写成运行中或失败", () => {
    expect(analysisTerminalCopy({
      status: "blocked",
      reason_code: "readiness_not_production_ready",
    })).toEqual({
      label: "已阻塞",
      detail: "指定模型尚未通过生产就绪校验，AI 分析未启动。",
      tone: "warn",
    });
    expect(analysisTerminalCopy({ status: "cancelled" }).label).toBe("已取消");
    expect(analysisTerminalCopy({ status: "partial_done" }).label).toBe("部分完成");
    expect(analysisTerminalCopy({ status: "prefilter_rejected" }).label).toBe("预筛未通过");
    expect(analysisTerminalCopy({ status: "triage" }).label).toBe("待人工排查");
    expect(analysisTerminalCopy({ status: "timeout", reason_code: "timeout" })).toEqual({
      label: "超时",
      detail: "模型请求超时，本任务可稍后重试。",
      tone: "failed",
    });
    expect(analysisTerminalCopy({ status: "retrying" }).label).toBe("等待重试");
  });

  it("将预算与 provider 失败映射为稳定人话", () => {
    expect(analysisTerminalCopy({ status: "blocked", reason_code: "budget_blocked" }).detail)
      .toBe("模型预算策略阻止了本轮外部调用。");
    expect(analysisTerminalCopy({ status: "failed", reason_code: "provider_unavailable" }).detail)
      .toBe("外部模型未能完成本次请求，本任务可按策略重试。");
  });

  it("门面只出通道角色，厂商与模型标识只留在溯源层", () => {
    expect(analysisChannelLabel({ provider: "google", model: "gemini-2.5-pro" })).toBe("主通道");
    expect(analysisChannelLabel({ provider: "google", fallback_mode: "provider_fallback" }))
      .toBe("备用通道");
    expect(analysisChannelLabel({ provider: "google", fallback_mode: "rule_v0" })).toBe("规则降级");
    expect(analysisChannelLabel({})).toBe("");
    expect(analysisChannelLabel({ provider: "google" })).not.toMatch(/google|gemini/i);

    expect(analysisProviderTrace({ provider: "google", model: "gemini-2.5-pro" }))
      .toBe("服务 google · 模型 gemini-2.5-pro");
    expect(analysisProviderTrace({})).toBe("");
  });

  it("将严格预留里的任务绑定映射成用户可读标签", () => {
    expect(analysisTaskBindingLabel({ task_binding: "kol_content_fit_analysis" }))
      .toBe("KOL 内容契合");
    // 未登记绑定不把机器码上脸,回落中性说法;原码只进溯源层。
    expect(analysisTaskBindingLabel({ task_binding: "future_reviewed_task" }))
      .toBe("其他分析任务");
    expect(analysisTaskBindingTrace({ task_binding: "future_reviewed_task" }))
      .toBe("future_reviewed_task");
    expect(analysisTaskBindingLabel({})).toBe("");
    expect(analysisTaskBindingTrace({})).toBe("");
  });
});
