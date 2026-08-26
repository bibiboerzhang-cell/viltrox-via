/**
 * 报告深度分析入口的接线契约(2026-08-25)。
 *
 * 这是一个**花钱的动作**,所以钉的重点不是「能不能出结果」,而是「会不会在人没同意
 * 之前把钱花掉」:
 *
 *  1. 挂载即静默 —— 不自动发任何请求;
 *  2. 第一下只报价(dry_run),第二下才真跑;
 *  3. 当天缓存如实说成 0 成本,不假装是新算的;
 *  4. 额度用尽时连执行按钮都不给,而不是让人点了再失败;
 *  5. 没派活时界面上不许出现任何进行时文案。
 */
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.hoisted(() => vi.fn());
vi.mock("../../../../services/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/http")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import ReportDeepAnalysis, { quoteMessage } from "./ReportPanel.deep-analysis";

const REPORT_TEXT = [
  "报告 RPT-2026-08",
  "周期 2026-08-01 至 2026-08-31",
  "管理摘要：本月投放集中在广角镜头，转化集中在两个头部账号。",
  "合作数：18",
  "GMV：待数据",
].join("\n");

function renderPanel(reportText = REPORT_TEXT) {
  return render(
    <ReportDeepAnalysis apiToken="tok" reportText={reportText} period="monthly" language="zh" />,
  );
}

/** 取出第 n 次调用的请求体。 */
function bodyOf(callIndex: number): Record<string, unknown> {
  const init = apiFetchMock.mock.calls[callIndex]?.[1] as { body?: string } | undefined;
  return JSON.parse(init?.body ?? "{}");
}

beforeEach(() => {
  apiFetchMock.mockReset();
});

describe("报告深度分析入口", () => {
  it("挂载时不自动请求任何东西 —— 花钱的动作绝不自动武装", () => {
    renderPanel();
    expect(apiFetchMock).not.toHaveBeenCalled();
  });

  it("空闲时界面上没有任何进行时文案", () => {
    renderPanel();
    expect(screen.queryByText(/查询中/)).toBeNull();
    expect(screen.queryByText(/分析中/)).toBeNull();
    expect(screen.getByRole("button", { name: /查看本次费用/ })).toBeTruthy();
  });

  it("报告还没生成时入口保持禁用", () => {
    renderPanel("");
    expect(screen.getByRole("button", { name: /查看本次费用/ })).toHaveProperty("disabled", true);
    expect(apiFetchMock).not.toHaveBeenCalled();
  });

  it("第一下只报价:走 dry_run,并把预计费用显示出来", async () => {
    apiFetchMock.mockResolvedValueOnce({
      available: true, dry_run: true, cached: false, will_spend: true, estimated_cost_usd: 0.1,
    });
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /查看本次费用/ }));

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledTimes(1));
    expect(bodyOf(0).dry_run).toBe(true);
    await screen.findByText(/预计花费 \$0\.10/);
    // 报价之后才出现执行按钮 —— 在此之前没有任何可以花钱的控件。
    expect(screen.getByRole("button", { name: /确认并执行/ })).toBeTruthy();
  });

  it("确认之后才真跑,且这一次不带 dry_run", async () => {
    apiFetchMock
      .mockResolvedValueOnce({
        available: true, dry_run: true, cached: false, will_spend: true, estimated_cost_usd: 0.1,
      })
      .mockResolvedValueOnce({
        available: true, cached: false,
        analysis: {
          executive_summary: "投放集中度偏高。",
          highlights: ["两个账号贡献了多数转化"],
          risks: ["单点依赖"],
          recommendations: ["扩充腰部账号"],
        },
      });
    renderPanel();

    fireEvent.click(screen.getByRole("button", { name: /查看本次费用/ }));
    const confirm = await screen.findByRole("button", { name: /确认并执行/ });
    fireEvent.click(confirm);

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledTimes(2));
    expect(bodyOf(1).dry_run).toBeUndefined();
    await screen.findByText("投放集中度偏高。");
    expect(screen.getByText(/两个账号贡献了多数转化/)).toBeTruthy();
    expect(screen.getByText(/单点依赖/)).toBeTruthy();
  });

  it("当天缓存命中时如实说成 0 成本,按钮改成直接取用", async () => {
    apiFetchMock.mockResolvedValueOnce({
      available: true, dry_run: true, cached: true, will_spend: false, estimated_cost_usd: 0,
    });
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /查看本次费用/ }));

    await screen.findByText(/本次不产生费用/);
    expect(screen.getByRole("button", { name: /直接取用今天的分析/ })).toBeTruthy();
    // 不许把复用说成一次新的分析。
    expect(screen.queryByText(/预计花费/)).toBeNull();
  });

  it("额度用尽时不给执行按钮 —— 不让人点了再失败", async () => {
    apiFetchMock.mockResolvedValueOnce({
      available: false, dry_run: true, cached: false, will_spend: false,
      estimated_cost_usd: 0, reason: "budget_blocked",
    });
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /查看本次费用/ }));

    await screen.findByText(/今日额度已用完/);
    expect(screen.queryByRole("button", { name: /确认并执行/ })).toBeNull();
  });

  it("报告换了就作废旧报价,不拿上一份的价格授权这一份的花费", async () => {
    apiFetchMock.mockResolvedValueOnce({
      available: true, dry_run: true, cached: false, will_spend: true, estimated_cost_usd: 0.1,
    });
    const { rerender } = renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /查看本次费用/ }));
    await screen.findByRole("button", { name: /确认并执行/ });

    rerender(
      <ReportDeepAnalysis
        apiToken="tok"
        reportText={`${REPORT_TEXT}\n新增章节：竞品对比结果若干条。`}
        period="monthly"
        language="zh"
      />,
    );
    expect(screen.queryByRole("button", { name: /确认并执行/ })).toBeNull();
    expect(screen.queryByText(/预计花费/)).toBeNull();
  });

  it("后端返回空壳分析时不渲染空框,而是照实说这次没成", async () => {
    apiFetchMock
      .mockResolvedValueOnce({
        available: true, dry_run: true, cached: false, will_spend: true, estimated_cost_usd: 0.1,
      })
      .mockResolvedValueOnce({ available: true, cached: false, analysis: {} });
    renderPanel();
    fireEvent.click(screen.getByRole("button", { name: /查看本次费用/ }));
    fireEvent.click(await screen.findByRole("button", { name: /确认并执行/ }));

    await screen.findByRole("alert");
    expect(screen.getByRole("alert").textContent).toMatch(/没能生成分析/);
  });
});

describe("报价文案", () => {
  it("三种结果三种说法,一句都不含糊", () => {
    const base = { available: true, cached: false, willSpend: true, estimatedCostUsd: 0.1, reason: "" };
    expect(quoteMessage(base)).toMatch(/预计花费 \$0\.10/);
    expect(quoteMessage({ ...base, cached: true, willSpend: false, estimatedCostUsd: 0 }))
      .toMatch(/不产生费用/);
    expect(quoteMessage({ ...base, available: false, reason: "budget_blocked" }))
      .toMatch(/今日额度已用完/);
    expect(quoteMessage({ ...base, available: false, reason: "report_too_short" }))
      .toMatch(/太短/);
  });

  // 门面禁术语:界面文案里不许出现厂商名 / 模型名 / 内部口径词。
  it("文案不含厂商与模型名", () => {
    const banned = /claude|gemini|openai|gpt|llm|anthropic|rule_v0/i;
    const base = { available: true, cached: false, willSpend: true, estimatedCostUsd: 0.1, reason: "" };
    for (const quote of [
      base,
      { ...base, cached: true, willSpend: false, estimatedCostUsd: 0 },
      { ...base, available: false, reason: "budget_blocked" },
      { ...base, available: false, reason: "report_too_short" },
    ]) {
      expect(quoteMessage(quote)).not.toMatch(banned);
    }
  });
});
