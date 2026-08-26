/**
 * 「自动放宽」门面契约(2026-08-26)。
 *
 * 用户裁令逐条钉死:
 * - 显示「本来能出 N 人,自动放宽了 X 之后能出 M 人」;
 * - 说清放宽的是什么、为什么(未知≠不符合);
 * - 一键改回去;
 * - 松不动就如实说「库里就是没有人」,不许假装;
 * - 降级时如实说是规则推荐;
 * - 门面文案不许出现任何内部词。
 */
import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  AUTO_RELAX_SCHEMA,
  autoRelaxFromResponse,
  deriveAutoRelaxView,
  followerText,
  useAutoRelaxControl,
  type AutoRelaxPayload,
} from "./SmartKolInputPanel.AutoRelax";
import { AutoRelaxNotice } from "./SmartKolInputPanel.AutoRelaxNotice";
import {
  EMPTY_KOL_SEARCH_FILTERS,
  KolSearchPolicyPanel,
} from "./SmartKolInputPanel.SearchPolicy";

function payload(overrides: Partial<AutoRelaxPayload> = {}): AutoRelaxPayload {
  return {
    schema: AUTO_RELAX_SCHEMA,
    status: "relaxed",
    target: 30,
    baseline_count: 6,
    final_count: 49,
    advice_source: "model",
    protected_untouched: ["gear_content", "freshness_days"],
    applied: [
      {
        key: "languages",
        action: "include_unknown",
        count_before: 6,
        count_after: 49,
        gained: 43,
        gained_are_unknown_only: true,
      },
    ],
    ...overrides,
  };
}

/** 门面禁术语:内部键名、模式名、厂商/模型名一个都不许漏到界面上。 */
const FORBIDDEN_WORDS = [
  "include_unknown",
  "tri_state",
  "三态",
  "硬筛",
  "召回",
  "rule_v0",
  "fallback",
  "LLM",
  "llm",
  "GPT",
  "Gemini",
  "Claude",
  "大模型",
  "languages",
  "countries",
  "followers_min",
  "verticals",
  "schema",
  "auto_relax",
  "unknown",
  "mismatch",
  "pool",
];

function assertNoJargon(text: string) {
  for (const word of FORBIDDEN_WORDS) {
    expect(text).not.toContain(word);
  }
}

describe("自动放宽的话怎么说", () => {
  it("放宽成功时说清本来多少人、放宽了什么、现在多少人", () => {
    const view = deriveAutoRelaxView(payload());
    expect(view).not.toBeNull();
    expect(view!.tone).toBe("relaxed");
    expect(view!.headline).toBe("本来只能出 6 人，自动放宽了「内容语言」之后能出 49 人。");
    expect(view!.lines[0].text).toBe("放宽「内容语言」：多出 43 人。他们只是没填内容语言，不是明确不符合的人。");
    expect(view!.restoreLabel).toBe("改回我的条件");
  });

  it("放宽的每一条都说得出「为什么」,而且合格线明说不会动", () => {
    const view = deriveAutoRelaxView(
      payload({
        applied: [
          { key: "languages", action: "include_unknown", count_before: 6, count_after: 49, gained: 43, gained_are_unknown_only: true },
          { key: "verticals", action: "drop", count_before: 49, count_after: 75, gained: 26, gained_are_unknown_only: false },
          { key: "followers_min", action: "lower", count_before: 75, count_after: 160, gained: 85, gained_are_unknown_only: false, from_value: 50000, to_value: 30000 },
        ],
      }),
    );
    const texts = view!.lines.map((line) => line.text);
    expect(texts[1]).toContain("不再限制「内容垂类」");
    expect(texts[1]).toContain("系统替你推断的");
    expect(texts[2]).toBe("粉丝下限从 5 万 降到 3 万：多出 85 人。");
    expect(view!.protectedNote).toBe("器材内容证据、内容新鲜度属于合格线，任何时候都不会被自动放宽。");
  });

  it("松不动就如实说库里就是没有人,不假装", () => {
    const view = deriveAutoRelaxView(
      payload({ status: "short", baseline_count: 0, final_count: 0, applied: [] }),
    );
    expect(view!.tone).toBe("short");
    expect(view!.headline).toBe("这个条件下库里就是没有人。你选的条件系统一格都没动。");
    expect(view!.restoreLabel).toBeNull();
  });

  it("放宽到底还是不够时,既承认松过也承认还是不够", () => {
    const view = deriveAutoRelaxView(payload({ status: "short", baseline_count: 3, final_count: 11 }));
    expect(view!.headline).toBe("自动放宽了「内容语言」，也只能出 11 人——这个条件下库里就这么多人。");
    expect(view!.restoreLabel).toBe("改回我的条件");
  });

  it("条件本来就够用时不占版面", () => {
    expect(deriveAutoRelaxView(payload({ status: "not_needed" }))).toBeNull();
  });

  it("操作员关掉自动放宽后,界面如实显示现在按他的条件搜", () => {
    const view = deriveAutoRelaxView(payload({ status: "disabled" }));
    expect(view!.headline).toBe("已按你原来的条件搜索，没有自动放宽。");
    expect(view!.restoreLabel).toBe("恢复自动放宽");
  });

  it("估不出人数时说的是真实发生的事:没估出来所以没放宽,不是「条件按原样执行了」", () => {
    const view = deriveAutoRelaxView(payload({ status: "unavailable", baseline_count: null, final_count: null }));
    // 旧文案「条件按原样执行了」与实际不符:系统加的条件可能仍在生效,
    // 真实行为只是「估不出 → 直接不放宽」。
    expect(view!.headline).toBe("这次没能预先估算能出多少人，所以一格都没有自动放宽。");
    expect(view!.headline).not.toContain("按原样执行");
    expect(view!.lines).toEqual([]);
  });

  it("降级到规则时如实说,不冒充读懂了描述", () => {
    expect(deriveAutoRelaxView(payload({ advice_source: "rules" }))!.sourceNote).toBe(
      "这次的搜索条件是按固定规则给的，没能读懂你的描述。",
    );
    expect(deriveAutoRelaxView(payload({ advice_source: "model" }))!.sourceNote).toBe(
      "这次的搜索条件是系统读你的描述后给的。",
    );
  });

  it("认不出的台账一律不显示,绝不猜", () => {
    expect(deriveAutoRelaxView(null)).toBeNull();
    expect(deriveAutoRelaxView({ schema: "something_else", status: "relaxed" })).toBeNull();
    expect(deriveAutoRelaxView("relaxed")).toBeNull();
  });

  it("产量口径原样透到界面:说清这只是库内人数", () => {
    const view = deriveAutoRelaxView(
      payload({ scope_note: "这是库内可选人数；联网还能补多少人不在此列。", pool_total: 2036 }),
    );
    expect(view!.scopeNote).toBe("这是库内可选人数；联网还能补多少人不在此列。");
    render(<AutoRelaxNotice view={view} onRestore={vi.fn()} />);
    expect(screen.getByText(/联网还能补多少人不在此列/)).toBeTruthy();
  });

  it("粉丝数说人话", () => {
    expect(followerText(50000)).toBe("5 万");
    expect(followerText(35000)).toBe("3.5 万");
    expect(followerText(3000)).toBe("3000");
    expect(followerText(0)).toBe("不限");
    expect(followerText(null)).toBe("不限");
  });

  it("从搜索响应的任意一层都能挖出台账", () => {
    expect(autoRelaxFromResponse({ auto_relax: { status: "relaxed" } })).toEqual({ status: "relaxed" });
    expect(autoRelaxFromResponse({ result: { auto_relax: { status: "short" } } })).toEqual({ status: "short" });
    expect(autoRelaxFromResponse({ result: {} })).toBeNull();
    expect(autoRelaxFromResponse(undefined)).toBeNull();
  });
});

describe("自动放宽在界面上的样子", () => {
  it("在筛选面板上显示,并且折叠状态下也看得见", () => {
    render(
      <KolSearchPolicyPanel
        open={false}
        onToggleOpen={vi.fn()}
        strategy="balanced"
        onStrategyChange={vi.fn()}
        platforms={["youtube"]}
        onPlatformsChange={vi.fn()}
        languages={[]}
        onLanguagesChange={vi.fn()}
        filters={EMPTY_KOL_SEARCH_FILTERS}
        onFiltersChange={vi.fn()}
        autoRelax={deriveAutoRelaxView(payload())}
        onAutoRelaxRestore={vi.fn()}
      />,
    );
    expect(screen.getByTestId("kol-search-auto-relax-notice")).toBeTruthy();
    expect(screen.getByText(/本来只能出 6 人/)).toBeTruthy();
  });

  it("一键改回去按得动", () => {
    const onRestore = vi.fn();
    render(<AutoRelaxNotice view={deriveAutoRelaxView(payload())} onRestore={onRestore} />);
    fireEvent.click(screen.getByRole("button", { name: /改回我的条件/ }));
    expect(onRestore).toHaveBeenCalledTimes(1);
  });

  it("搜索进行中时还原按钮禁用,避免重复排队", () => {
    render(<AutoRelaxNotice view={deriveAutoRelaxView(payload())} onRestore={vi.fn()} busy />);
    expect((screen.getByRole("button", { name: /改回我的条件/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("没有台账就整块不渲染", () => {
    const { container } = render(<AutoRelaxNotice view={null} onRestore={vi.fn()} />);
    expect(container.textContent).toBe("");
  });

  it("界面文案一个内部词都没有", () => {
    const cases: AutoRelaxPayload[] = [
      payload(),
      payload({ status: "short", baseline_count: 0, final_count: 0, applied: [] }),
      payload({ status: "short", baseline_count: 3, final_count: 11 }),
      payload({ status: "disabled" }),
      payload({ status: "unavailable" }),
      payload({ advice_source: "rules" }),
      payload({ scope_note: "这是库内可选人数；联网还能补多少人不在此列。" }),
      payload({
        applied: [
          { key: "countries", action: "include_unknown", count_before: 1, count_after: 9, gained: 8, gained_are_unknown_only: true },
          { key: "platforms", action: "drop", count_before: 9, count_after: 20, gained: 11, gained_are_unknown_only: false },
          { key: "followers_min", action: "lower", count_before: 20, count_after: 44, gained: 24, gained_are_unknown_only: false, from_value: 100000, to_value: 30000 },
        ],
      }),
    ];
    for (const item of cases) {
      const { container, unmount } = render(
        <AutoRelaxNotice view={deriveAutoRelaxView(item)} onRestore={vi.fn()} />,
      );
      assertNoJargon(container.textContent || "");
      unmount();
    }
  });
});

/**
 * 2026-08-26 复核纠偏:**加筛选与松筛选必须同等可见**。
 * 上一版系统能悄悄替操作员加上他从没说过的条件,而界面只播报松绑 ——
 * 比不松绑更严重,因为他根本不知道自己被加了条件。下面这组把「加了什么 / 为什么加 /
 * 怎么去掉」与「还原在任何状态下都能用」逐条钉死。
 */
const ADDED_COUNTRY = {
  key: "countries",
  values: ["us"],
  reason: "你没点名国家，这是默认的主力市场",
  removable: true,
};
const ADDED_FOLLOWERS = {
  key: "followers_min",
  value: 50000,
  reason: "你没说体量，这是默认的粉丝下限",
  removable: true,
};

describe("系统替你加的条件,和松掉的条件一样看得见", () => {
  it("加了什么、为什么加、能不能去掉,三样都说", () => {
    const view = deriveAutoRelaxView(payload({ added: [ADDED_COUNTRY, ADDED_FOLLOWERS] }));
    expect(view!.addedHeadline).toBe("系统按你的描述替你加了 2 项你没说过的条件，每一条都能单独去掉。");
    expect(view!.addedLines[0].text).toBe("系统替你加了「国家 / 地区」：US。这一条你没说过。");
    expect(view!.addedLines[0].reason).toBe("你没点名国家，这是默认的主力市场");
    expect(view!.addedLines[1].text).toBe("系统替你加了「粉丝下限」：5 万。这一条你没说过。");
    expect(view!.addedLines.every((line) => line.removable)).toBe(true);
    expect(view!.removeLabel).toBe("去掉这条");
  });

  it("说不出为什么加时如实说说不出,不替它编一个理由", () => {
    const view = deriveAutoRelaxView(payload({ added: [{ key: "verticals", values: ["lifestyle"], reason: "" }] }));
    expect(view!.addedLines[0].text).toBe("系统替你加了「内容垂类」：生活方式。这一条你没说过。");
    expect(view!.addedLines[0].reason).toBe("系统没能说清为什么加这一条。");
  });

  it("一格都没松、条件本来就够用时,加的条件照样要显示", () => {
    const view = deriveAutoRelaxView(payload({ status: "not_needed", applied: [], added: [ADDED_COUNTRY] }));
    expect(view).not.toBeNull();
    expect(view!.headline).toBe("这次的条件够用，没有自动放宽。");
    expect(view!.addedLines).toHaveLength(1);
    expect(view!.restoreLabel).toBe("改回我的条件");
  });

  it("关掉自动放宽但加项还在时,绝不说「已按你原来的条件搜索」", () => {
    const view = deriveAutoRelaxView(payload({ status: "disabled", added: [ADDED_COUNTRY] }));
    expect(view!.headline).toBe("这次没有自动放宽；但系统仍替你加了下面这些你没说过的条件。");
    expect(view!.addedLines).toHaveLength(1);
  });

  it("估不出人数且加项仍生效时,如实说加项还在生效", () => {
    const view = deriveAutoRelaxView(payload({ status: "unavailable", added: [ADDED_COUNTRY] }));
    expect(view!.headline).toBe(
      "这次没能预先估算能出多少人，所以一格都没有自动放宽；系统替你加的条件仍在生效。",
    );
  });

  it("去掉之后如实说去掉了哪几条,并且能一键回到系统建议", () => {
    const view = deriveAutoRelaxView(
      payload({ status: "disabled", applied: [], added: [], added_dropped: [ADDED_COUNTRY, ADDED_FOLLOWERS] }),
    );
    expect(view!.headline).toBe("已按你自己的条件搜索：系统加的条件都去掉了，也没有做任何放宽。");
    expect(view!.droppedNote).toBe("已按你的要求去掉系统加的「国家 / 地区」、「粉丝下限」，这次没有用它们筛。");
    expect(view!.restoreLabel).toBe("恢复系统建议");
  });

  it("只加没松也给得出还原入口:任何状态下都能改回我的条件", () => {
    for (const status of ["relaxed", "short", "not_needed", "unavailable"] as const) {
      const view = deriveAutoRelaxView(payload({ status, applied: [], added: [ADDED_COUNTRY] }));
      expect(view!.restoreLabel, status).toBe("改回我的条件");
    }
  });

  it("加项在界面上摆得出来,「去掉这条」按得动,按过的置灰", () => {
    const onRemove = vi.fn();
    const view = deriveAutoRelaxView(payload({ added: [ADDED_COUNTRY, ADDED_FOLLOWERS] }));
    const { rerender } = render(
      <AutoRelaxNotice view={view} onRestore={vi.fn()} onRemoveAdded={onRemove} removedKeys={[]} />,
    );
    expect(screen.getByTestId("kol-search-auto-added")).toBeTruthy();
    const buttons = screen.getAllByRole("button", { name: /去掉这条/ });
    expect(buttons).toHaveLength(2);
    fireEvent.click(buttons[0]);
    expect(onRemove).toHaveBeenCalledWith("countries");
    rerender(
      <AutoRelaxNotice view={view} onRestore={vi.fn()} onRemoveAdded={onRemove} removedKeys={["countries"]} />,
    );
    expect((screen.getAllByRole("button", { name: /去掉这条/ })[0] as HTMLButtonElement).disabled).toBe(true);
  });

  it("加项的每一句话也一个内部词都没有", () => {
    const cases: AutoRelaxPayload[] = [
      payload({ added: [ADDED_COUNTRY, ADDED_FOLLOWERS] }),
      payload({ status: "not_needed", applied: [], added: [{ key: "languages", values: ["en"], reason: "你没点名语言，这是按你的描述推断的" }] }),
      payload({ status: "disabled", applied: [], added: [], added_dropped: [ADDED_COUNTRY] }),
      payload({ status: "unavailable", added: [ADDED_COUNTRY] }),
    ];
    for (const item of cases) {
      const { container, unmount } = render(
        <AutoRelaxNotice view={deriveAutoRelaxView(item)} onRestore={vi.fn()} onRemoveAdded={vi.fn()} />,
      );
      assertNoJargon(container.textContent || "");
      unmount();
    }
  });

  it("筛选面板上也摆得出加项,折叠状态下照样看得见", () => {
    render(
      <KolSearchPolicyPanel
        open={false}
        onToggleOpen={vi.fn()}
        strategy="balanced"
        onStrategyChange={vi.fn()}
        platforms={["youtube"]}
        onPlatformsChange={vi.fn()}
        languages={[]}
        onLanguagesChange={vi.fn()}
        filters={EMPTY_KOL_SEARCH_FILTERS}
        onFiltersChange={vi.fn()}
        autoRelax={deriveAutoRelaxView(payload({ added: [ADDED_COUNTRY] }))}
        onAutoRelaxRestore={vi.fn()}
        onAutoRelaxRemoveAdded={vi.fn()}
      />,
    );
    expect(screen.getByTestId("kol-search-auto-added")).toBeTruthy();
    expect(screen.getByRole("button", { name: /去掉这条/ })).toBeTruthy();
  });
});

describe("「改回我的条件」真的回得去", () => {
  it("按下去两个开关一起关:既不放宽,也不采纳系统加的条件", () => {
    const { result } = renderHook(() => useAutoRelaxControl({ auto_relax: payload() }));
    expect(result.current.requestParams()).toEqual({
      autoRelax: true,
      autoFilters: true,
      droppedAutoFilters: [],
    });
    act(() => result.current.toggleOptOut());
    // 只关放宽是回不去的:系统推断出来的条件照样会被硬加上去。两个都得关。
    expect(result.current.requestParams()).toEqual({
      autoRelax: false,
      autoFilters: false,
      droppedAutoFilters: [],
    });
    expect(result.current.optOut).toBe(true);
  });

  it("同一次事件里读到的就是新值(不然还原只是个假动作)", () => {
    const { result } = renderHook(() => useAutoRelaxControl(null));
    let seen: boolean | null = null;
    act(() => {
      result.current.toggleOptOut();
      seen = result.current.requestParams().autoRelax;
    });
    expect(seen).toBe(false);
  });

  it("逐条去掉的项进请求体,重复点不会堆两次", () => {
    const { result } = renderHook(() => useAutoRelaxControl(null));
    act(() => result.current.removeAdded("countries"));
    act(() => result.current.removeAdded("countries"));
    act(() => result.current.removeAdded("followers_min"));
    expect(result.current.requestParams().droppedAutoFilters).toEqual(["countries", "followers_min"]);
    expect(result.current.droppedKeys).toEqual(["countries", "followers_min"]);
  });

  it("恢复系统建议时,逐条点掉的那几项也一并恢复", () => {
    const { result } = renderHook(() => useAutoRelaxControl(null));
    act(() => result.current.removeAdded("countries"));
    act(() => result.current.toggleOptOut());
    act(() => result.current.toggleOptOut());
    expect(result.current.requestParams()).toEqual({
      autoRelax: true,
      autoFilters: true,
      droppedAutoFilters: [],
    });
  });
});
