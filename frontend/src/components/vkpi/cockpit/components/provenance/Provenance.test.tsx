// 溯源三组件测试(vitest + @testing-library,hermetic 不打后端):
// SrcChip 渲染 label + 来源卡键值行 + onOpen 点击;ProvChain 外链 target=_blank / rec 节点回调;
// RecordPreview 渲染标题与 rows。写法仿 MetricCard.test.tsx。
import React from "react";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SrcChip } from "./SrcChip";
import { ProvChain, type ProvStep } from "./ProvChain";
import { RecordPreview } from "./RecordPreview";

afterEach(cleanup);

describe("SrcChip 来源 chip", () => {
  it("渲染 label 与来源卡键值行", () => {
    const { container } = render(
      <SrcChip
        label="market_insights · 5m"
        rows={[
          ["反馈", "vkpi_market_feedback"],
          ["情绪", "sentiment_v2 · LLM"],
        ]}
      />,
    );
    const chip = container.querySelector(".vkpi-prov-src");
    expect(chip).not.toBeNull();
    expect(chip?.textContent).toContain("market_insights · 5m");
    const rows = container.querySelectorAll(".vkpi-prov-st-r");
    expect(rows.length).toBe(2);
    expect(rows[0].textContent).toContain("反馈");
    expect(rows[0].querySelector("b")?.textContent).toBe("vkpi_market_feedback");
  });

  it("点击调用 onOpen(打开模块溯源弹窗)", () => {
    const onOpen = vi.fn();
    const { container } = render(<SrcChip label="staff · 实时" rows={[]} onOpen={onOpen} />);
    const chip = container.querySelector(".vkpi-prov-src");
    expect(chip).not.toBeNull();
    fireEvent.click(chip as Element);
    expect(onOpen).toHaveBeenCalledTimes(1);
  });
});

describe("ProvChain 溯源链", () => {
  const steps: ProvStep[] = [
    { label: "原帖", href: "https://www.reddit.com/r/videography/comments/vk8575" },
    { label: "vkpi_market_feedback #1021", rec: "feedback", rid: "1021" },
  ];

  it("外链节点 target=_blank + rel noopener + ↗ 后缀", () => {
    const { container } = render(<ProvChain steps={steps} />);
    const ext = container.querySelector("a.vkpi-prov-pchip--ext");
    expect(ext).not.toBeNull();
    expect(ext?.getAttribute("target")).toBe("_blank");
    expect(ext?.getAttribute("rel")).toContain("noopener");
    expect(ext?.textContent).toContain("原帖 ↗");
  });

  it("rec 节点点击回调 onRecord(rec, rid)", () => {
    const onRecord = vi.fn();
    const { getByText } = render(<ProvChain steps={steps} onRecord={onRecord} />);
    fireEvent.click(getByText("vkpi_market_feedback #1021"));
    expect(onRecord).toHaveBeenCalledTimes(1);
    expect(onRecord).toHaveBeenCalledWith("feedback", "1021");
  });

  it("节点间 → 分隔,链下渲染 provnote 小注", () => {
    const { container } = render(<ProvChain steps={steps} />);
    expect(container.querySelectorAll(".vkpi-prov-arrow").length).toBe(1);
    expect(container.querySelector(".vkpi-prov-arrow")?.textContent).toBe("→");
    expect(container.querySelector(".vkpi-prov-note")).not.toBeNull();
  });
});

describe("RecordPreview 库记录预览", () => {
  it("渲染标题与键值行(值 mono 右对齐列)", () => {
    const { container } = render(
      <RecordPreview
        title="库记录预览 · 点其他节点切换"
        rows={[
          ["表", "vkpi_market_feedback"],
          ["id", "#1021"],
          ["captured_at", "2026-07-08 11:25 UTC"],
        ]}
      />,
    );
    expect(container.querySelector(".vkpi-prov-rec-cap")?.textContent).toContain("库记录预览");
    const rows = container.querySelectorAll(".vkpi-prov-rr");
    expect(rows.length).toBe(3);
    expect(rows[1].querySelector("b")?.textContent).toBe("#1021");
    expect(rows[2].textContent).toContain("captured_at");
  });

  it("title 缺省时使用默认小标题", () => {
    const { container } = render(<RecordPreview rows={[["表", "apify_jobs"]]} />);
    expect(container.querySelector(".vkpi-prov-rec-cap")?.textContent).not.toBe("");
    expect(container.querySelector(".vkpi-prov-rr b")?.textContent).toBe("apify_jobs");
  });
});
