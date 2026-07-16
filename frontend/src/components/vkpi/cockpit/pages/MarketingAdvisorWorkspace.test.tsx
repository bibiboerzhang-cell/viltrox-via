import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AdvisorMemoryBody, MarketingAdvisorBody } from "./MarketingAdvisorWorkspace";
import {
  confirmAdvisorMemoryCandidate,
  createAdvisorMemoryCandidate,
  createAdvisorThread,
  getAdvisorMemory,
  getAdvisorReadiness,
  listAdvisorMessages,
  listAdvisorThreads,
  postAdvisorMessageStream,
  updateAdvisorMemorySettings,
} from "../../../../services/vkpi/marketing-advisor-api";

vi.mock("../../../../services/vkpi/marketing-advisor-api", () => ({
  getAdvisorReadiness: vi.fn(),
  listAdvisorThreads: vi.fn(),
  createAdvisorThread: vi.fn(),
  listAdvisorMessages: vi.fn(),
  postAdvisorMessageStream: vi.fn(),
  getAdvisorMemory: vi.fn(),
  updateAdvisorMemorySettings: vi.fn(),
  createAdvisorMemoryCandidate: vi.fn(),
  confirmAdvisorMemoryCandidate: vi.fn(),
  rejectAdvisorMemoryCandidate: vi.fn(),
  updateAdvisorMemoryFact: vi.fn(),
}));

const readinessMock = vi.mocked(getAdvisorReadiness);
const threadsMock = vi.mocked(listAdvisorThreads);
const createThreadMock = vi.mocked(createAdvisorThread);
const messagesMock = vi.mocked(listAdvisorMessages);
const postMessageMock = vi.mocked(postAdvisorMessageStream);
const memoryMock = vi.mocked(getAdvisorMemory);
const createCandidateMock = vi.mocked(createAdvisorMemoryCandidate);
const confirmCandidateMock = vi.mocked(confirmAdvisorMemoryCandidate);
const updateSettingsMock = vi.mocked(updateAdvisorMemorySettings);

beforeEach(() => {
  vi.clearAllMocks();
  readinessMock.mockResolvedValue({
    status: "degraded",
    provider_ready: false,
    provider_called: false,
    reason: "advisor_provider_not_connected",
    persistence_ready: true,
    action_mode: "draft_only",
    retryable: true,
    knowledge_bridge_ready: false,
    knowledge_bridge_reason: "intelligent_search_not_tenant_scoped",
  });
  threadsMock.mockResolvedValue([]);
  messagesMock.mockResolvedValue([]);
});

describe("MarketingAdvisorBody", () => {
  it("creates a private persisted thread on first send and shows the honest server response", async () => {
    const thread = { thread_uid: "advthr_1", title: "海外 KOL 建议", status: "active" };
    createThreadMock.mockResolvedValue(thread);
    postMessageMock.mockResolvedValue({
      status: "degraded",
      reason: "advisor_provider_not_connected",
      claim_status: "descriptive_only",
      messages: [
        { message_uid: "u1", thread_uid: "advthr_1", role: "user", content_text: "给我海外 KOL 建议", status: "ready", created_at: "2026-07-14T20:00:00Z" },
        { message_uid: "a1", thread_uid: "advthr_1", role: "assistant", content_text: "问题已安全保存；模型通道尚未连接。", status: "degraded", created_at: "2026-07-14T20:00:01Z" },
      ],
    });
    threadsMock.mockResolvedValueOnce([]).mockResolvedValueOnce([{ ...thread, last_message_at: "2026-07-14T20:00:01Z" }]);

    render(<MarketingAdvisorBody apiToken="token" />);
    expect(await screen.findByText("会话可留存 · 模型降级")).toBeTruthy();
    expect(screen.getByText(/现有检索还未完成组织级隔离/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("向营销顾问提问"), { target: { value: "给我海外 KOL 建议" } });
    fireEvent.click(screen.getByRole("button", { name: /发送/ }));

    expect(await screen.findByText("问题已安全保存；模型通道尚未连接。")).toBeTruthy();
    expect(createThreadMock).toHaveBeenCalledWith("token", "给我海外 KOL 建议");
    expect(postMessageMock).toHaveBeenCalledWith(
      "token",
      "advthr_1",
      "给我海外 KOL 建议",
      expect.any(String),
      false,
      expect.any(Function),
    );
    expect(screen.getByText("诚实降级")).toBeTruthy();
  });

  it("resets the busy state after a completed turn under React StrictMode", async () => {
    const thread = { thread_uid: "advthr_strict", title: "严格模式会话", status: "active" };
    threadsMock.mockResolvedValue([thread]);
    postMessageMock.mockResolvedValue({
      status: "degraded",
      messages: [
        {
          message_uid: "strict-answer",
          thread_uid: thread.thread_uid,
          role: "assistant",
          content_text: "严格模式下已完成诚实降级",
          status: "degraded",
        },
      ],
    });

    render(
      <React.StrictMode>
        <MarketingAdvisorBody apiToken="token" />
      </React.StrictMode>,
    );
    expect(await screen.findByRole("combobox", { name: "选择持久会话" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("向营销顾问提问"), { target: { value: "不要调用外部模型" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("严格模式下已完成诚实降级")).toBeTruthy();
    await waitFor(() => expect(screen.getByRole("button", { name: "发送" })).toBeTruthy());
    expect(screen.queryByRole("button", { name: "处理中" })).toBeNull();
  });

  it("does not claim full readiness when persistence is unavailable", async () => {
    readinessMock.mockResolvedValue({
      status: "degraded",
      provider_ready: true,
      persistence_ready: false,
      action_mode: "draft_only",
    });

    render(<MarketingAdvisorBody apiToken="token" />);
    expect(await screen.findByText("模型已就绪 · 会话未就绪")).toBeTruthy();
    expect(screen.queryByText("模型与持久化已就绪")).toBeNull();
  });

  it("distinguishes the usable AI-off advisor path from blocked external generation", async () => {
    readinessMock.mockResolvedValue({
      status: "degraded",
      core_status: "ready",
      external_ai_status: "blocked",
      ai_off_path_ready: true,
      external_ai_ready: false,
      provider_ready: false,
      provider_called: false,
      persistence_ready: true,
      knowledge_bridge_ready: true,
      action_mode: "draft_only",
      reason: "advisor_exact_model_not_production_ready",
    });

    render(<MarketingAdvisorBody apiToken="token" />);
    expect(await screen.findByText("会话与记忆可用 · 外部模型关闭")).toBeTruthy();
    expect(screen.getByRole("checkbox")).toBeDisabled();
  });

  it("translates model and budget blocks into user copy while retaining a secondary diagnostic code", async () => {
    readinessMock.mockResolvedValue({
      status: "degraded",
      provider_ready: false,
      provider_called: false,
      persistence_ready: true,
      action_mode: "draft_only",
      reason: "budget_guard_blocked",
    });

    render(<MarketingAdvisorBody apiToken="token" />);
    expect(await screen.findByText("模型预算尚未授权，本轮不会调用外部模型。")).toBeTruthy();
    expect(screen.getByText("诊断码：budget_guard_blocked")).toBeTruthy();
    expect(screen.getByRole("checkbox")).toBeDisabled();
  });

  it("requires an explicit per-turn checkbox before external AI is allowed", async () => {
    const thread = { thread_uid: "advthr_ai", title: "AI 会话", status: "active" };
    readinessMock.mockResolvedValue({
      status: "ready",
      provider_ready: true,
      persistence_ready: true,
      action_mode: "draft_only",
      knowledge_bridge_ready: true,
    });
    threadsMock.mockResolvedValue([thread]);
    postMessageMock.mockResolvedValue({ status: "ok", messages: [] });

    render(<MarketingAdvisorBody apiToken="token" />);
    const consent = await screen.findByRole("checkbox");
    expect(consent).not.toBeChecked();
    fireEvent.click(consent);
    fireEvent.change(screen.getByLabelText("向营销顾问提问"), { target: { value: "给我建议" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => expect(postMessageMock).toHaveBeenCalledTimes(1));
    expect(postMessageMock.mock.calls[0]?.[4]).toBe(true);
  });

  it("keeps the honest readiness visible when the conversation schema is unavailable", async () => {
    readinessMock.mockResolvedValue({
      status: "degraded",
      provider_ready: false,
      persistence_ready: false,
      action_mode: "draft_only",
      reason: "advisor_schema_unavailable",
    });
    threadsMock.mockRejectedValue(new Error("migration 250 is not applied"));

    render(<MarketingAdvisorBody apiToken="token" />);
    expect(await screen.findByText("顾问未就绪")).toBeTruthy();
    expect(screen.getByRole("alert")).toHaveTextContent("migration 250 is not applied");
    expect(screen.queryByText("就绪状态不可用")).toBeNull();
  });

  it("reuses the client request id when a failed turn is retried", async () => {
    const thread = { thread_uid: "advthr_retry", title: "重试会话", status: "active" };
    threadsMock.mockResolvedValue([thread]);
    postMessageMock
      .mockRejectedValueOnce(new Error("网络中断"))
      .mockResolvedValueOnce({
        status: "degraded",
        messages: [
          { message_uid: "u-retry", thread_uid: thread.thread_uid, role: "user", content_text: "给我建议", status: "ready" },
          { message_uid: "a-retry", thread_uid: thread.thread_uid, role: "assistant", content_text: "已安全重放", status: "degraded" },
        ],
      });

    render(<MarketingAdvisorBody apiToken="token" />);
    expect(await screen.findByRole("combobox", { name: "选择持久会话" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("向营销顾问提问"), { target: { value: "给我建议" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("网络中断");

    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByText("已安全重放")).toBeTruthy();
    expect(postMessageMock).toHaveBeenCalledTimes(2);
    expect(postMessageMock.mock.calls[1]?.[3]).toBe(postMessageMock.mock.calls[0]?.[3]);
  });

  it("does not mix a slow response into a different selected thread", async () => {
    const first = { thread_uid: "advthr_first", title: "会话 A", status: "active" };
    const second = { thread_uid: "advthr_second", title: "会话 B", status: "active" };
    threadsMock.mockResolvedValue([first, second]);
    messagesMock.mockImplementation(async (_token, uid) => uid === second.thread_uid ? [
      { message_uid: "b1", thread_uid: second.thread_uid, role: "assistant", content_text: "B 会话内容", status: "ready" },
    ] : []);
    let resolveTurn: ((value: Awaited<ReturnType<typeof postAdvisorMessageStream>>) => void) | undefined;
    postMessageMock.mockReturnValue(new Promise((resolve) => {
      resolveTurn = resolve;
    }));

    render(<MarketingAdvisorBody apiToken="token" />);
    const select = await screen.findByRole("combobox", { name: "选择持久会话" });
    fireEvent.change(screen.getByLabelText("向营销顾问提问"), { target: { value: "A 的问题" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    fireEvent.change(select, { target: { value: second.thread_uid } });
    expect(await screen.findByText("B 会话内容")).toBeTruthy();

    await act(async () => {
      resolveTurn?.({
        status: "degraded",
        messages: [
          { message_uid: "a1", thread_uid: first.thread_uid, role: "assistant", content_text: "A 的延迟回答", status: "degraded" },
        ],
      });
    });
    await waitFor(() => expect(postMessageMock).toHaveBeenCalledTimes(1));
    expect(screen.queryByText("A 的延迟回答")).toBeNull();
    expect(screen.getByText("B 会话内容")).toBeTruthy();
  });

  it("does not show an old thread's delayed error under the newly selected thread", async () => {
    const first = { thread_uid: "advthr_error_first", title: "会话 A", status: "active" };
    const second = { thread_uid: "advthr_error_second", title: "会话 B", status: "active" };
    threadsMock.mockResolvedValue([first, second]);
    let rejectTurn: ((reason?: unknown) => void) | undefined;
    postMessageMock.mockReturnValue(new Promise((_resolve, reject) => {
      rejectTurn = reject;
    }));

    render(<MarketingAdvisorBody apiToken="token" />);
    const select = await screen.findByRole("combobox", { name: "选择持久会话" });
    fireEvent.change(screen.getByLabelText("向营销顾问提问"), { target: { value: "A 的问题" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    fireEvent.change(select, { target: { value: second.thread_uid } });
    await act(async () => {
      rejectTurn?.(new Error("A 的延迟错误"));
    });

    await waitFor(() => expect(postMessageMock).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("alert")).toBeNull();
    expect((screen.getByRole("combobox", { name: "选择持久会话" }) as HTMLSelectElement).value).toBe(second.thread_uid);
  });

  it("服务端 accepted 后展示真实 staged SSE 进度，不伪装 token 流", async () => {
    const thread = { thread_uid: "advthr_stream", title: "进度会话", status: "active" };
    threadsMock.mockResolvedValue([thread]);
    let resolveTurn: ((value: Awaited<ReturnType<typeof postAdvisorMessageStream>>) => void) | undefined;
    postMessageMock.mockImplementation((_token, _uid, _content, _requestId, _allowAi, onEvent) => {
      onEvent?.({
        type: "accepted",
        payload: { status: "accepted", transport: "staged_sse_v1", provider_streaming: false },
      });
      return new Promise((resolve) => {
        resolveTurn = resolve;
      });
    });

    render(<MarketingAdvisorBody apiToken="token" />);
    expect(await screen.findByRole("combobox", { name: "选择持久会话" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("向营销顾问提问"), { target: { value: "给我分析" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByRole("status", { name: "营销顾问分析进度" })).toBeTruthy();
    expect(screen.getByText("服务端接收")).toBeTruthy();
    expect(screen.getByText("私有检索 / 模型路径")).toBeTruthy();
    expect(screen.getByText(/服务端已接收 · staged_sse_v1 · 非 token 流/)).toBeTruthy();

    await act(async () => {
      resolveTurn?.({
        status: "degraded",
        messages: [{
          message_uid: "stream-final",
          thread_uid: thread.thread_uid,
          role: "assistant",
          content_text: "已保存降级结果",
          status: "degraded",
        }],
      });
    });
    expect(await screen.findByText("已保存降级结果")).toBeTruthy();
    expect(screen.getByText(/未冒充外部模型结论/)).toBeTruthy();
  });

  it("remounts the private conversation state when the login token changes", async () => {
    const oldThread = { thread_uid: "advthr_old", title: "旧员工会话", status: "active" };
    threadsMock.mockImplementation(async (token) => token === "old-token" ? [oldThread] : []);
    messagesMock.mockImplementation(async (token) => token === "old-token" ? [
      { message_uid: "old-message", thread_uid: oldThread.thread_uid, role: "assistant", content_text: "旧员工私有内容", status: "ready" },
    ] : []);

    const { rerender } = render(<MarketingAdvisorBody apiToken="old-token" />);
    expect(await screen.findByText("旧员工私有内容")).toBeTruthy();
    rerender(<MarketingAdvisorBody apiToken="new-token" />);
    expect(screen.queryByText("旧员工私有内容")).toBeNull();
    expect(await screen.findByText(/尚无服务端会话/)).toBeTruthy();
  });
});

describe("AdvisorMemoryBody", () => {
  it("keeps a manual memory inactive until the user explicitly confirms it", async () => {
    const empty = {
      settings: { state: "active" as const, retention_days: 180, persisted: true },
      candidates: [],
      facts: [],
    };
    const pending = {
      ...empty,
      candidates: [{
        candidate_uid: "c1",
        memory_kind: "preference",
        memory_key: "manual:1",
        summary: "优先海外摄影创作者",
        status: "pending",
      }],
    };
    const confirmed = {
      ...empty,
      facts: [{
        fact_uid: "f1",
        memory_kind: "preference",
        memory_key: "manual:1",
        summary: "优先海外摄影创作者",
        status: "active",
        version: 1,
      }],
    };
    memoryMock.mockResolvedValueOnce(empty).mockResolvedValueOnce(pending).mockResolvedValueOnce(confirmed);
    createCandidateMock.mockResolvedValue(pending.candidates[0]);
    confirmCandidateMock.mockResolvedValue(confirmed.facts[0]);

    render(<AdvisorMemoryBody apiToken="token" />);
    expect(await screen.findByText("暂无已确认记忆。")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("新增个人记忆候选"), { target: { value: "优先海外摄影创作者" } });
    fireEvent.click(screen.getByRole("button", { name: "提出候选" }));
    expect(await screen.findByText("优先海外摄影创作者")).toBeTruthy();
    expect(createCandidateMock).toHaveBeenCalledWith("token", "优先海外摄影创作者");
    expect(confirmCandidateMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByLabelText("确认记忆：优先海外摄影创作者"));
    await waitFor(() => expect(confirmCandidateMock).toHaveBeenCalledWith("token", "c1"));
    expect(await screen.findByText("生效中 · v1")).toBeTruthy();
  });

  it("supports pausing the whole personal-memory namespace", async () => {
    memoryMock.mockResolvedValue({
      settings: { state: "active", retention_days: 180, persisted: true },
      candidates: [],
      facts: [],
    });
    updateSettingsMock.mockResolvedValue({ state: "paused", retention_days: 180, persisted: true });

    render(<AdvisorMemoryBody apiToken="token" />);
    expect(await screen.findByText("记忆已开启")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "暂停" }));
    await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith("token", "paused", 180));
  });

  it("labels retention as a read window and cannot confirm while paused", async () => {
    memoryMock.mockResolvedValue({
      settings: { state: "paused", retention_days: 30, persisted: true },
      candidates: [{
        candidate_uid: "c-paused",
        memory_kind: "constraint",
        memory_key: "approval.required",
        summary: "需要人工批准",
        status: "pending",
      }],
      facts: [],
      retention_policy: {
        mode: "read_window",
        retention_days: 30,
        expired_rows_returned: false,
        physical_delete_performed: false,
      },
    });

    render(<AdvisorMemoryBody apiToken="token" />);

    expect(await screen.findByText(/读取窗口 30 天/)).toBeTruthy();
    expect(screen.getByText(/未经授权不会物理删除/)).toBeTruthy();
    const confirm = screen.getByLabelText("确认记忆：需要人工批准") as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    fireEvent.click(confirm);
    expect(confirmCandidateMock).not.toHaveBeenCalled();
  });
});
