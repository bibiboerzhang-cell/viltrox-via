import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { I18N_EN } from "../data/i18nEn";
import { EtaHint, FailureGuidance } from "./failureGuidance";
import { I18nContext, makeT } from "./i18n";

function renderWith(ui: React.ReactElement, lang: "zh" | "en" = "zh") {
  return render(
    <I18nContext.Provider value={{ t: makeT(lang, lang === "en" ? I18N_EN : {}), lang, setLang: vi.fn() }}>
      {ui}
    </I18nContext.Provider>,
  );
}

describe("FailureGuidance · 失败可读(F3)", () => {
  afterEach(() => cleanup());

  it("没有新契约字段时什么都不渲染(旧数据不编故事)", () => {
    const { container } = renderWith(<FailureGuidance source={{ status: "failed", last_error_category: "provider_429" }} />);
    expect(container.querySelector("[data-vkpi-failure-guidance]")).toBeNull();
  });

  it("authorization:显示中文人话原因 + 「从 MY KOL 重新发起」按钮(有 onReissue 才渲染)", () => {
    const onReissue = vi.fn();
    renderWith(<FailureGuidance source={{ failure_category: "authorization", failure_reason_human: "当前账号不是该 KOL 的收藏负责人" }} onReissue={onReissue} />);
    expect(screen.getByText("当前账号不是该 KOL 的收藏负责人")).toBeTruthy();
    const button = screen.getByRole("button", { name: "从 MY KOL 重新发起" });
    fireEvent.click(button);
    expect(onReissue).toHaveBeenCalledTimes(1);

    cleanup();
    const { container } = renderWith(<FailureGuidance source={{ failure_category: "authorization", failure_reason_human: "x" }} />);
    expect(container.querySelector("button")).toBeNull();
  });

  it("budget 给预算提示且无按钮;download/provider 给「稍后自动重试」", () => {
    renderWith(<FailureGuidance source={{ failure_category: "budget", failure_reason_human: "本月预算已用完" }} onReissue={vi.fn()} />);
    expect(screen.getByText(/预算恢复后可再次发起/)).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
    cleanup();
    renderWith(<FailureGuidance source={{ failure_category: "download" }} onReissue={vi.fn()} />);
    expect(screen.getByText("稍后自动重试,无需手动操作")).toBeTruthy();
    cleanup();
    renderWith(<FailureGuidance source={{ failure_category: "provider" }} />);
    expect(screen.getByText("稍后自动重试,无需手动操作")).toBeTruthy();
  });

  it("英文 locale:原因按类别映射英文短句,提示/按钮走词典", () => {
    renderWith(<FailureGuidance source={{ failure_category: "authorization", failure_reason_human: "当前账号无权" }} onReissue={vi.fn()} />, "en");
    expect(screen.getByText(/not authorised/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Re-issue from MY KOL" })).toBeTruthy();
    expect(screen.queryByText("当前账号无权")).toBeNull();
  });
});

describe("EtaHint · ETA 新口径(F7)", () => {
  afterEach(() => cleanup());

  it("只认 eta_seconds;缺失/旧字段不渲染", () => {
    const { container } = renderWith(<EtaHint source={{ estimated_remaining_seconds: 120 }} />);
    expect(container.textContent).toBe("");
    cleanup();
    renderWith(<EtaHint source={{ eta_seconds: 150 }} />);
    expect(screen.getByText(/预计剩余/).textContent).toContain("约 3 分钟");
    cleanup();
    renderWith(<EtaHint source={{ eta_seconds: 150 }} />, "en");
    expect(screen.getByText(/Est\. remaining/).textContent).toContain("~3 min");
  });
});
