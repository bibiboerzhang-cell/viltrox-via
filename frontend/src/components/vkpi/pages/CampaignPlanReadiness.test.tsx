import React from "react";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { CampaignPlanReadiness } from "./CampaignPlanReadiness";

afterEach(cleanup);
const base = {
  status: "needs_evidence", executable: false, approval_status: "not_requested",
  claim_status: "descriptive_only",
  gaps: [{ code: "signals", message: "合成：缺少目标市场证据" }],
  next_steps: [{ code: "collect", title: "合成：核对已有来源", reason: "先验证资料再选择行动" }],
};

describe("campaign draft readiness guidance", () => {
  it("explains a gap and next step without adding an execution control", () => {
    render(<CampaignPlanReadiness output={{ planning_readiness: base }} />);
    expect(screen.getByRole("status")).toHaveTextContent("先补齐判断依据");
    expect(screen.getByText(base.gaps[0].message)).toBeInTheDocument();
    expect(screen.getByText(base.next_steps[0].title)).toBeInTheDocument();
    expect(screen.getByText(/不等于项目创建、批准或对外执行/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("separates missing inputs from missing evidence", () => {
    render(<CampaignPlanReadiness output={{ planning_readiness: { ...base, status: "needs_inputs" } }} />);
    expect(screen.getByRole("status")).toHaveTextContent("先补齐规划条件");
  });

  it("material-backed drafts still require human checks", () => {
    render(<CampaignPlanReadiness output={{ planning_readiness: { ...base, status: "draft_for_review", gaps: [] } }} />);
    expect(screen.getByRole("status")).toHaveTextContent("草稿可供人工审阅");
    expect(screen.getByText(/负责人核对适用范围、资源和交付条件/)).toBeInTheDocument();
  });

  it.each([
    {}, { planning_readiness: null }, { planning_readiness: { ...base, executable: true } },
    { planning_readiness: { ...base, approval_status: "approved" } },
    { planning_readiness: { ...base, claim_status: "verified" } },
    { planning_readiness: { ...base, status: "draft_for_review" } },
    { planning_readiness: { ...base, status: "ready" } },
    { planning_readiness: { ...base, gaps: [] } },
    { planning_readiness: { ...base, gaps: [null] } },
    { planning_readiness: { ...base, next_steps: [] } },
    { planning_readiness: { ...base, next_steps: [{ title: "missing code and reason" }] } },
  ])("does not infer readiness from missing or contradictory data (%#)", (output) => {
    render(<CampaignPlanReadiness output={output} />);
    const section = screen.getByRole("region", { name: "营销计划准备检查" });
    expect(within(section).getByRole("status")).toHaveTextContent("准备状态待核验");
    expect(within(section).getByText(/不要据此执行/)).toBeInTheDocument();
    expect(within(section).queryByRole("list")).not.toBeInTheDocument();
  });
});
