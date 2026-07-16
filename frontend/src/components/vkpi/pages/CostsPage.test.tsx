import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { emptyDashboardData } from "../data/emptyDashboardData";
import { CostsPage } from "./CostsPage";

const data = {
  ...emptyDashboardData,
  costs: [{
    id: "19",
    projectId: "7",
    costType: "shipping",
    amount: 12.5,
    currency: "USD",
    status: "pending",
    incurredAt: "2026-07-15T12:00:00Z",
  }],
};

describe("CostsPage authorization evidence", () => {
  it("keeps all lifecycle writes disabled until explicit human evidence is present", async () => {
    const onUpdateCost = vi.fn().mockResolvedValue(undefined);
    const onApproveCost = vi.fn().mockResolvedValue(undefined);
    const onVoidCost = vi.fn().mockResolvedValue(undefined);
    render(
      <CostsPage
        data={data}
        viewMode="manager"
        onUpdateCost={onUpdateCost}
        onApproveCost={onApproveCost}
        onVoidCost={onVoidCost}
        onOpenEvidence={() => undefined}
      />,
    );

    expect(screen.getByRole("button", { name: "更新成本" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "审核" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "作废" })).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText("备注 / 审核理由 / 作废原因"), { target: { value: "财务已核对物流发票" } });
    fireEvent.change(screen.getByLabelText("成本变更授权回执"), { target: { value: "FIN-APPROVAL-19" } });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "审核" }));

    await waitFor(() => expect(onApproveCost).toHaveBeenCalledWith(
      "19",
      "财务已核对物流发票",
      {
        authorizationRef: "FIN-APPROVAL-19",
        reason: "财务已核对物流发票",
        confirmedByHuman: true,
      },
    ));
  });
});
