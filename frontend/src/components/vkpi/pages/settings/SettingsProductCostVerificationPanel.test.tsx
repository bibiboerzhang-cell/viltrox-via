import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SettingsProductCostVerificationPanel } from "./SettingsProductCostVerificationPanel";
import { listProductCosts, verifyProductCost } from "../../../../services/vkpi/cost-api";

vi.mock("../../../../services/vkpi/cost-api", () => ({
  listProductCosts: vi.fn(),
  verifyProductCost: vi.fn(),
}));

const listMock = vi.mocked(listProductCosts);
const verifyMock = vi.mocked(verifyProductCost);

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, resolve, reject };
}

describe("SettingsProductCostVerificationPanel", () => {
  beforeEach(() => {
    listMock.mockReset().mockResolvedValue({
      product_costs: [
        {
          id: 17,
          product_sku: "AF-35-18",
          product_name: "AF 35mm F1.8",
          unit_cost_cents: 12345,
          currency: "USD",
          row_version: 4,
          updated_at: "2026-07-15T12:00:00Z",
          verification_status: "reference_unverified",
        },
        {
          id: 18,
          product_sku: "AF-85-18",
          product_name: "AF 85mm F1.8",
          unit_cost_cents: 20000,
          currency: "USD",
          row_version: 2,
          updated_at: "2026-07-15T12:05:00Z",
          verification_status: "verified",
          source_ref: "ERP-42",
        },
      ],
    });
    verifyMock.mockReset().mockResolvedValue({ verified: true });
  });

  it("keeps reference costs out of truth until full human evidence is supplied", async () => {
    render(<SettingsProductCostVerificationPanel apiToken="admin-token" />);

    expect(await screen.findByText("1/2 已核验")).toBeInTheDocument();
    const submit = screen.getByRole("button", { name: "提交带授权的核验" });
    expect(submit).toBeDisabled();

    fireEvent.change(screen.getByLabelText("成本来源引用"), { target: { value: "invoice://INV-2026-77" } });
    fireEvent.change(screen.getByLabelText("成本来源观测时间"), { target: { value: "2026-07-15T09:30" } });
    fireEvent.change(screen.getByLabelText("成本授权回执"), { target: { value: "FIN-APPROVAL-77" } });
    fireEvent.change(screen.getByLabelText("成本核验原因"), { target: { value: "财务已核对当前供应商发票" } });
    fireEvent.click(screen.getByRole("checkbox"));
    expect(submit).toBeEnabled();
    fireEvent.click(submit);

    await waitFor(() => expect(verifyMock).toHaveBeenCalledWith(
      "admin-token",
      "AF-35-18",
      {
        sourceType: "supplier_invoice",
        sourceRef: "invoice://INV-2026-77",
        // 本地 datetime-local 输入 → UTC 的真契约;绝不硬编码某台机器的时区换算结果(时区炸弹)。
        sourceObservedAt: new Date("2026-07-15T09:30").toISOString(),
        authorizationRef: "FIN-APPROVAL-77",
        reason: "财务已核对当前供应商发票",
        confirmedByHuman: true,
        expectedId: 17,
        expectedUnitCostCents: 12345,
        expectedCurrency: "USD",
        expectedRowVersion: 4,
        expectedUpdatedAt: "2026-07-15T12:00:00Z",
      },
    ));
    expect(await screen.findByText(/AF-35-18 已形成带来源与人工授权/)).toBeInTheDocument();
    expect(listMock).toHaveBeenCalledTimes(2);
  });

  it("shows the fail-closed backend reason instead of pretending verification succeeded", async () => {
    verifyMock.mockRejectedValue(new Error("409 real_business_manual_writes is disabled"));
    render(<SettingsProductCostVerificationPanel apiToken="admin-token" />);
    await screen.findByText("1/2 已核验");

    fireEvent.change(screen.getByLabelText("成本来源引用"), { target: { value: "ERP-77" } });
    fireEvent.change(screen.getByLabelText("成本来源观测时间"), { target: { value: "2026-07-15T09:30" } });
    fireEvent.change(screen.getByLabelText("成本授权回执"), { target: { value: "FIN-77" } });
    fireEvent.change(screen.getByLabelText("成本核验原因"), { target: { value: "等待受控开闸" } });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "提交带授权的核验" }));

    expect(await screen.findByText("409 real_business_manual_writes is disabled")).toBeInTheDocument();
    expect(screen.queryByText(/已形成带来源与人工授权/)).not.toBeInTheDocument();
  });

  it("does not let a delayed previous-session response replace the current token rows", async () => {
    const first = deferred<{ product_costs: Array<Record<string, unknown>> }>();
    const second = deferred<{ product_costs: Array<Record<string, unknown>> }>();
    listMock
      .mockReset()
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise);

    const { rerender } = render(<SettingsProductCostVerificationPanel apiToken="token-a" />);
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(1));
    const firstSignal = listMock.mock.calls[0]?.[1]?.signal;

    rerender(<SettingsProductCostVerificationPanel apiToken="token-b" />);
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(2));
    expect(firstSignal?.aborted).toBe(true);

    await act(async () => {
      second.resolve({
        product_costs: [{
          id: 22,
          product_sku: "TOKEN-B-SKU",
          unit_cost_cents: 2200,
          currency: "USD",
          row_version: 1,
          updated_at: "2026-07-15T12:10:00Z",
          verification_status: "reference_unverified",
        }],
      });
      await second.promise;
    });
    expect(await screen.findByText("TOKEN-B-SKU")).toBeInTheDocument();

    await act(async () => {
      first.resolve({
        product_costs: [{
          id: 11,
          product_sku: "TOKEN-A-SKU",
          unit_cost_cents: 1100,
          currency: "USD",
          row_version: 1,
          updated_at: "2026-07-15T12:00:00Z",
          verification_status: "reference_unverified",
        }],
      });
      await first.promise;
    });

    expect(screen.getByText("TOKEN-B-SKU")).toBeInTheDocument();
    expect(screen.queryByText("TOKEN-A-SKU")).not.toBeInTheDocument();
  });
});
