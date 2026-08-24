import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// 「数据关注」一键流程冒烟(D 车道 2026-08-23):
//   ① runDataWatchAction:tracking 成功回执 / sku_required 回落不假登记 / 未知状态如实报错 /
//      错误码人话映射 / 只读与迟到响应双闸;
//   ② VideoTrackActions 的「数据关注」按钮:props 直连(内容墙)与 DataWatchContext 回落
//      (详情弹窗经 libdetail 不改签名)两条接线都要点亮;忙态/禁用原因如实。
const dataWatchMock = vi.hoisted(() => vi.fn());
const confirmDetectedMock = vi.hoisted(() => vi.fn());
vi.mock("../../../../services/vkpi/myKolBoard-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../services/vkpi/myKolBoard-api")>();
  return { ...actual, dataWatchMyKolVideo: (...args: unknown[]) => dataWatchMock(...args) };
});
vi.mock("../../../../services/vkpi/myKolDataWatchConfirmation-api", () => ({
  confirmDetectedMyKolVideoSku: (...args: unknown[]) => confirmDetectedMock(...args),
}));

import type { VkpiKolPoolVideoRow } from "../../../../services/vkpi/myKolBoard-api";
import {
  DataWatchContext,
  dataWatchErrorText,
  dataWatchSuccessText,
  runDataWatchAction,
  SKU_PLAY_CHANGED_EVENT,
  skuRequiredHintText,
  WALL_SKU_REQUIRED_HINT,
  type RunDataWatchDeps,
} from "./MyKolBoardPage.data-watch";
import { VideoTrackActions } from "./MyKolBoardPage.video-tasks";

const video = (extra: Partial<VkpiKolPoolVideoRow> = {}): VkpiKolPoolVideoRow => ({
  evidence_id: 901,
  kol_pool_id: 101,
  media_kind: "video",
  title: "Video 901",
  content_url: "https://www.youtube.com/watch?v=video901",
  ...extra,
});

function makeDeps(overrides: Partial<RunDataWatchDeps> = {}) {
  const busy = new Set<number>();
  const deps: RunDataWatchDeps = {
    apiToken: "tok",
    kolPoolId: 101,
    isBusy: (id) => busy.has(id),
    setBusy: vi.fn((id: number, on: boolean) => { if (on) busy.add(id); else busy.delete(id); }),
    setReceipt: vi.fn(),
    onSkuRequired: vi.fn(),
    onTracked: vi.fn(),
    ...overrides,
  };
  return { deps, busy };
}

beforeEach(() => {
  dataWatchMock.mockReset();
  confirmDetectedMock.mockReset();
});

describe("runDataWatchAction(一键数据关注流程)", () => {
  it("tracking 成功:回执如实列 SKU + 指向单品播放数据模块,并触发任务态重读", async () => {
    dataWatchMock.mockResolvedValue({
      status: "tracking",
      skus: ["AF-85-F14", "AF-35-F18"],
      sku_source: "auto",
      sku_provenance: { relation_type: "detected", source: "title_alias_v1", confidence: 0.6, requires_human_confirmation: true },
      refresh: "queued",
    });
    const { deps, busy } = makeDeps();
    await runDataWatchAction(video(), deps);

    expect(dataWatchMock).toHaveBeenCalledWith("tok", 101, 901);
    const last = (deps.setReceipt as ReturnType<typeof vi.fn>).mock.calls.at(-1)?.[0];
    expect(last).toEqual({ text: "已登记数据关注(SKU AF-85-F14 / AF-35-F18)；SKU 来自标题唯一命中，已标为系统检测、待人工确认——系统定时抓取播放/点赞,结果见『单品播放数据』模块。", tone: "info" });
    expect(deps.onTracked).toHaveBeenCalledTimes(1);
    expect(busy.size).toBe(0);
  });

  it("唯一系统检测回执要求员工二次确认，不自动写 confirmed", async () => {
    dataWatchMock.mockResolvedValue({
      status: "tracking",
      skus: ["AF-85-F14"],
      sku_source: "auto",
      sku_provenance: {
        relation_type: "detected",
        source: "final_v1_lens_evidence_v2",
        confidence: 0.85,
        requires_human_confirmation: true,
        modalities: ["visual", "voice"],
        evidence_excerpt: "AF 85 shown in frame",
      },
      refresh: "queued",
    });
    const onDetectedConfirmationRequired = vi.fn();
    const { deps } = makeDeps({ onDetectedConfirmationRequired });
    const row = video();

    await runDataWatchAction(row, deps);

    expect(onDetectedConfirmationRequired).toHaveBeenCalledWith(row, [{
      sku_code: "AF-85-F14",
      sku_name: "AF-85-F14",
      match_source: "final_v1_lens_evidence_v2",
      modalities: ["visual", "voice"],
      evidence_excerpt: "AF 85 shown in frame",
    }]);
    expect(confirmDetectedMock).not.toHaveBeenCalled();
  });

  it("员工确认唯一系统检测:走独立确认意图,不冒充 manual 提交", async () => {
    confirmDetectedMock.mockResolvedValue({
      status: "tracking",
      skus: ["AF-85-F14"],
      sku_source: "confirmation",
      sku_provenance: {
        relation_type: "confirmed",
        source: "human_confirmed_detected_v1",
        confidence: 1,
        requires_human_confirmation: false,
      },
      refresh: "already_queued",
    });
    const { deps } = makeDeps();

    await runDataWatchAction(video(), deps, ["AF-85-F14"], "confirm_detected");

    expect(confirmDetectedMock).toHaveBeenCalledWith("tok", 101, 901, ["AF-85-F14"]);
    expect(dataWatchMock).not.toHaveBeenCalled();
    expect((deps.setReceipt as ReturnType<typeof vi.fn>).mock.calls.at(-1)?.[0].text)
      .toContain("系统检测 SKU 已由你显式确认");
    expect(deps.onTracked).toHaveBeenCalledTimes(1);
  });

  it("sku_required 后员工显式选择:二次 POST 原样传 SKU，成功后通知单品读模型重读", async () => {
    dataWatchMock.mockResolvedValue({
      status: "tracking",
      skus: ["AF-85-F14"],
      sku_source: "manual",
      sku_provenance: { relation_type: "manual", source: "my_kol_video_tracking", confidence: 1, requires_human_confirmation: false },
      refresh: "queued",
    });
    const changed = vi.fn();
    window.addEventListener(SKU_PLAY_CHANGED_EVENT, changed);
    const { deps } = makeDeps();
    await runDataWatchAction(video(), deps, ["AF-85-F14"]);

    expect(dataWatchMock).toHaveBeenCalledWith("tok", 101, 901, ["AF-85-F14"]);
    expect(changed).toHaveBeenCalledTimes(1);
    expect((changed.mock.calls[0][0] as CustomEvent).detail).toEqual({
      evidenceId: 901,
      skus: ["AF-85-F14"],
    });
    expect(deps.onTracked).toHaveBeenCalledTimes(1);
    window.removeEventListener(SKU_PLAY_CHANGED_EVENT, changed);
  });

  it("refresh=already_queued 如实注明「已在队列」,绝不当新排队", () => {
    expect(dataWatchSuccessText({ status: "tracking", skus: ["AF-85-F14"], refresh: "already_queued" }))
      .toBe("已登记数据关注(SKU AF-85-F14)——系统定时抓取播放/点赞,结果见『单品播放数据』模块(指标刷新已在队列中)。");
  });

  it("结构化深析唯一命中回执显示画面/字幕/口播来源，不冒充员工确认", () => {
    expect(dataWatchSuccessText({
      status: "tracking",
      skus: ["AF-85-F14"],
      sku_source: "auto",
      sku_provenance: {
        relation_type: "detected",
        source: "final_v1_lens_evidence_v2",
        confidence: 0.85,
        requires_human_confirmation: true,
        modalities: ["visual", "text", "voice"],
      },
      refresh: "queued",
    })).toContain("SKU 来自已有视频深析（画面/字幕·文字/口播）唯一命中");
  });

  it("sku_required:只回落 onSkuRequired(携候选),不写成功回执不触发重读", async () => {
    dataWatchMock.mockResolvedValue({ status: "sku_required", candidates: [{ sku_code: "AF-85-F14", sku_name: "AF 85mm" }] });
    const { deps } = makeDeps();
    const row = video();
    await runDataWatchAction(row, deps);

    expect(deps.onSkuRequired).toHaveBeenCalledWith(row, [{ sku_code: "AF-85-F14", sku_name: "AF 85mm" }]);
    // setReceipt 只被开场清空调用过(null),没有任何“已登记”假回执
    expect((deps.setReceipt as ReturnType<typeof vi.fn>).mock.calls).toEqual([[null]]);
    expect(deps.onTracked).not.toHaveBeenCalled();
  });

  it("未知状态如实报「未获服务端确认」", async () => {
    dataWatchMock.mockResolvedValue({ status: "weird" });
    const { deps } = makeDeps();
    await runDataWatchAction(video(), deps);
    expect((deps.setReceipt as ReturnType<typeof vi.fn>).mock.calls.at(-1)?.[0]).toEqual({ text: "数据关注未获服务端确认:weird", tone: "error" });
  });

  it("错误码映射成人话(共享只读 / 未采集 / 平台不支持)", async () => {
    dataWatchMock.mockRejectedValue(Object.assign(new Error("x"), { detail: "my_kol_video_write_forbidden", status: 403 }));
    const { deps } = makeDeps();
    await runDataWatchAction(video(), deps);
    expect((deps.setReceipt as ReturnType<typeof vi.fn>).mock.calls.at(-1)?.[0]).toEqual({ text: "共享 KOL 仅可查看,数据关注请由收藏负责人发起。", tone: "error" });
    expect(dataWatchErrorText({ detail: "new_video_target_resolution_required" })).toContain("请先账号补采/深爬");
    expect(dataWatchErrorText({ detail: "video_metric_platform_unsupported" })).toBe("该平台暂不支持播放追踪。");
  });

  it("只读 / 缺 evidence id / 已在提交中:零请求", async () => {
    const { deps } = makeDeps({ readOnly: true });
    await runDataWatchAction(video(), deps);
    const { deps: noEid } = makeDeps();
    await runDataWatchAction(video({ evidence_id: undefined, id: undefined }), noEid);
    const { deps: busyDeps } = makeDeps({ isBusy: () => true });
    await runDataWatchAction(video(), busyDeps);
    expect(dataWatchMock).not.toHaveBeenCalled();
  });

  it("迟到响应被丢弃(切换 KOL 后不回写回执)", async () => {
    dataWatchMock.mockResolvedValue({ status: "tracking", skus: ["AF-85-F14"] });
    const { deps } = makeDeps({ isCurrent: () => false });
    await runDataWatchAction(video(), deps);
    // 开场清空发生在 isCurrent 判定之前;成功回执被丢弃
    expect((deps.setReceipt as ReturnType<typeof vi.fn>).mock.calls).toEqual([[null]]);
    expect(deps.onTracked).not.toHaveBeenCalled();
  });

  it("sku_required 提示文案:详情带候选,墙上引导去详情且不冒充完成", () => {
    expect(skuRequiredHintText([{ sku_code: "AF-85-F14" }, { sku_code: "" }]))
      .toBe("未能自动识别产品——请补一个 SKU 后用『追踪并排队刷新』提交(候选:AF-85-F14)。");
    expect(skuRequiredHintText()).toBe("未能自动识别产品——请补一个 SKU 后用『追踪并排队刷新』提交。");
    expect(WALL_SKU_REQUIRED_HINT).toContain("未选择不会登记");
    expect(WALL_SKU_REQUIRED_HINT).toContain("内容墙选择对应 SKU");
  });
});

describe("VideoTrackActions 数据关注按钮接线", () => {
  it("props 直连(内容墙):点击回传整行;忙态标「关注提交中…」", () => {
    const onDataWatch = vi.fn();
    const row = video();
    const { rerender } = render(<VideoTrackActions video={row} onDataWatch={onDataWatch} />);
    fireEvent.click(screen.getByRole("button", { name: "数据关注" }));
    expect(onDataWatch).toHaveBeenCalledWith(row);
    rerender(<VideoTrackActions video={row} onDataWatch={onDataWatch} dataWatchBusy />);
    expect(screen.getByRole("button", { name: "关注提交中…" })).toBeDisabled();
  });

  it("DataWatchContext 回落(详情弹窗):无 props 也点亮按钮,忙态取 busyEvidence", () => {
    const onDataWatch = vi.fn();
    const row = video();
    render(
      <DataWatchContext.Provider value={{ onDataWatch, busyEvidence: new Set<number>() }}>
        <VideoTrackActions video={row} onTrack={vi.fn()} onLinkSku={vi.fn()} />
      </DataWatchContext.Provider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "数据关注" }));
    expect(onDataWatch).toHaveBeenCalledWith(row);
  });

  it("共享只读禁用并如实给原因;无 URL / 图文轮播同禁", () => {
    render(
      <VideoTrackActions video={video()} onDataWatch={vi.fn()} readOnly readOnlyHint="共享 KOL 仅可查看" />,
    );
    const btn = screen.getByRole("button", { name: "数据关注" });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("title", "共享 KOL 仅可查看");
    render(<VideoTrackActions video={video({ content_url: "" })} onDataWatch={vi.fn()} />);
    render(<VideoTrackActions video={video({ media_kind: "image" })} onDataWatch={vi.fn()} />);
    const all = screen.getAllByRole("button", { name: "数据关注" });
    expect(all.filter((el) => (el as HTMLButtonElement).disabled).length).toBe(3);
  });
});
