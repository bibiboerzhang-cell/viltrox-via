import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.hoisted(() => vi.fn());
const recordPredictionActual = vi.hoisted(() => vi.fn());

vi.mock("../../../../services/http", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
}));
vi.mock("../../../../services/vkpi/prediction-ledger-api", () => ({
  recordPredictionActual: (...args: unknown[]) => recordPredictionActual(...args),
}));

import { PredictionLedgerPanel } from "./PredictionLedgerPanel";

const summary = {
  status: "ok",
  generated_at: "2026-08-11T08:00:00Z",
  groups: [],
  totals: { groups: 0, judged_total: 0, pending_total: 0, groups_with_sample: 0 },
};

beforeEach(() => {
  window.localStorage.clear();
  apiFetch.mockReset();
  recordPredictionActual.mockReset();
  apiFetch.mockResolvedValue(summary);
});

describe("PredictionLedgerPanel actual-from-outcome", () => {
  it("客户端不收 actual 数字；只提交已终结 outcome 的证据绑定，并在刷新后保留回执", async () => {
    recordPredictionActual.mockResolvedValue({ ok: true, id: 81, deduped: false });
    render(<PredictionLedgerPanel apiToken="tok" />);

    fireEvent.click(await screen.findByRole("button", { name: "人工对答案（经理）" }));
    fireEvent.click(screen.getByRole("button", { name: "从结果证据写入 actual" }));
    expect(screen.getByText(/请填写预测 run/)).toBeInTheDocument();
    expect(recordPredictionActual).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("预测 run ID"), { target: { value: "RUN / 42" } });
    fireEvent.change(screen.getByLabelText("Outcome ID"), { target: { value: "31" } });
    fireEvent.change(screen.getByLabelText("证据窗口"), { target: { value: "window_14d" } });
    fireEvent.change(screen.getByLabelText("指标路径"), { target: { value: "metrics bad path" } });
    fireEvent.click(screen.getByRole("button", { name: "从结果证据写入 actual" }));
    expect(recordPredictionActual).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("指标路径"), { target: { value: "metrics.views_median" } });
    fireEvent.change(screen.getByLabelText("对答案备注"), { target: { value: "项目复盘已终结" } });
    fireEvent.click(screen.getByRole("button", { name: "从结果证据写入 actual" }));

    await waitFor(() => expect(recordPredictionActual).toHaveBeenCalledTimes(1));
    const [token, runId, payload] = recordPredictionActual.mock.calls[0];
    expect(token).toBe("tok");
    expect(runId).toBe("RUN / 42");
    expect(payload).toMatchObject({
      outcome_id: 31,
      evidence_field: "window_14d",
      metric_path: "metrics.views_median",
      notes: "项目复盘已终结",
    });
    expect(payload).not.toHaveProperty("actual_value");
    expect(payload).not.toHaveProperty("prev_actual");
    expect(payload.correlation_id).toMatch(/^prediction-actual-RUN---42-[A-Za-z0-9.-]+$/);
    expect(payload.correlation_id.length).toBeLessThanOrEqual(160);
    expect(await screen.findByText("已记录真实结果 · eval #81")).toBeInTheDocument();
    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(2));
    expect(screen.getByLabelText("预测 run ID")).toHaveValue("RUN / 42");
  });

  it("后端拒绝证据绑定时显示真实原因，不伪造成功", async () => {
    recordPredictionActual.mockRejectedValue(new Error("actual_horizon_mismatch"));
    render(<PredictionLedgerPanel apiToken="tok" />);
    fireEvent.click(await screen.findByRole("button", { name: "人工对答案（经理）" }));
    fireEvent.change(screen.getByLabelText("预测 run ID"), { target: { value: "run-28d" } });
    fireEvent.change(screen.getByLabelText("Outcome ID"), { target: { value: "31" } });
    fireEvent.change(screen.getByLabelText("指标路径"), { target: { value: "metrics.views" } });
    fireEvent.click(screen.getByRole("button", { name: "从结果证据写入 actual" }));
    expect(await screen.findByText("actual_horizon_mismatch")).toBeInTheDocument();
    expect(apiFetch).toHaveBeenCalledTimes(1);
  });

  it("响应丢失后用同一 correlation 重试，成功确认后才为下一次提交换新值", async () => {
    recordPredictionActual
      .mockRejectedValueOnce(new Error("response lost"))
      .mockResolvedValueOnce({ ok: true, id: 82, deduped: true })
      .mockResolvedValueOnce({ ok: true, id: 82, deduped: true });
    render(<PredictionLedgerPanel apiToken="tok" />);
    fireEvent.click(await screen.findByRole("button", { name: "人工对答案（经理）" }));
    fireEvent.change(screen.getByLabelText("预测 run ID"), { target: { value: "run-retry-28d" } });
    fireEvent.change(screen.getByLabelText("Outcome ID"), { target: { value: "82" } });
    fireEvent.change(screen.getByLabelText("指标路径"), { target: { value: "metrics.views" } });

    fireEvent.click(screen.getByRole("button", { name: "从结果证据写入 actual" }));
    expect(await screen.findByText("response lost")).toBeInTheDocument();
    const firstCorrelation = recordPredictionActual.mock.calls[0][2].correlation_id;

    fireEvent.click(screen.getByRole("button", { name: "从结果证据写入 actual" }));
    expect(await screen.findByText("已记录真实结果 · eval #82（幂等复用）")).toBeInTheDocument();
    expect(recordPredictionActual.mock.calls[1][2].correlation_id).toBe(firstCorrelation);

    fireEvent.click(screen.getByRole("button", { name: "从结果证据写入 actual" }));
    await waitFor(() => expect(recordPredictionActual).toHaveBeenCalledTimes(3));
    expect(recordPredictionActual.mock.calls[2][2].correlation_id).not.toBe(firstCorrelation);
  });

  it("预测、outcome 或证据合同字段变化时更换 correlation", async () => {
    recordPredictionActual.mockRejectedValue(new Error("retryable"));
    render(<PredictionLedgerPanel apiToken="tok" />);
    fireEvent.click(await screen.findByRole("button", { name: "人工对答案（经理）" }));
    fireEvent.change(screen.getByLabelText("预测 run ID"), { target: { value: "run-contract" } });
    fireEvent.change(screen.getByLabelText("Outcome ID"), { target: { value: "91" } });
    fireEvent.change(screen.getByLabelText("指标路径"), { target: { value: "metrics.views" } });
    fireEvent.click(screen.getByRole("button", { name: "从结果证据写入 actual" }));
    expect(await screen.findByText("retryable")).toBeInTheDocument();
    const firstCorrelation = recordPredictionActual.mock.calls[0][2].correlation_id;

    fireEvent.change(screen.getByLabelText("指标路径"), { target: { value: "metrics.engagement" } });
    fireEvent.click(screen.getByRole("button", { name: "从结果证据写入 actual" }));
    await waitFor(() => expect(recordPredictionActual).toHaveBeenCalledTimes(2));
    expect(recordPredictionActual.mock.calls[1][2].correlation_id).not.toBe(firstCorrelation);
  });
});
