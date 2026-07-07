import { describe, it, expect, beforeEach } from "vitest";
import { render } from "@testing-library/react";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// U2 动效基元单测:Sparkline / FreshnessDot / DeltaBadge / SkeletonBlock。
// hermetic(零请求组件,不 mock 网络);另含 reduced-motion 降级的 CSS 卫兵断言
// (jsdom 不跑 CSS,直接读 motion.css 断言 media query 存在且覆盖全部动画类)。
import { Sparkline } from "./Sparkline";
import { FreshnessDot, computeFreshness } from "./FreshnessDot";
import { DeltaBadge } from "./DeltaBadge";
import { SkeletonBlock } from "./SkeletonBlock";

const HERE = path.dirname(fileURLToPath(import.meta.url));

beforeEach(() => {
  localStorage.clear();
});

describe("Sparkline", () => {
  it("正常序列 → 渲染 svg + 入场描线 path(pathLength=1,d 无 NaN)", () => {
    const { container } = render(<Sparkline data={[1, 3, 2, 5]} title="测试趋势" />);
    const svg = container.querySelector('[data-ui="sparkline"]');
    expect(svg).toBeTruthy();
    const p = container.querySelector("path.vk-spark-path");
    expect(p).toBeTruthy();
    expect(p!.getAttribute("pathLength")).toBe("1");
    expect(p!.getAttribute("d")).not.toContain("NaN");
  });

  it("平线序列 → 不出 NaN,照常渲染(居中)", () => {
    const { container } = render(<Sparkline data={[5, 5, 5]} />);
    const p = container.querySelector("path.vk-spark-path");
    expect(p).toBeTruthy();
    expect(p!.getAttribute("d")).not.toContain("NaN");
  });

  it("null 空桶点跳过,仍可画线", () => {
    const { container } = render(<Sparkline data={[1, null, 5, 2]} />);
    expect(container.querySelector("path.vk-spark-path")).toBeTruthy();
  });

  it("有效点 <2 → 安静返回 null(绝不画假线)", () => {
    const a = render(<Sparkline data={[4]} />);
    expect(a.container.firstChild).toBeNull();
    const b = render(<Sparkline data={[null, 3]} />);
    expect(b.container.firstChild).toBeNull();
    const c = render(<Sparkline data={[]} />);
    expect(c.container.firstChild).toBeNull();
  });
});

describe("FreshnessDot", () => {
  const hoursAgo = (h: number) => new Date(Date.now() - h * 3600 * 1000).toISOString();

  it("computeFreshness 档位:≤24h 绿 / ≤72h 黄 / 更旧 stale / 无戳 unknown", () => {
    expect(computeFreshness(hoursAgo(1))).toBe("fresh");
    expect(computeFreshness(hoursAgo(48))).toBe("warn");
    expect(computeFreshness(hoursAgo(100))).toBe("stale");
    expect(computeFreshness(null)).toBe("unknown");
    expect(computeFreshness("not-a-date")).toBe("unknown");
  });

  it("fresh → 绿呼吸灯 + title 给具体时间(freshnessLabel)", () => {
    const { container } = render(<FreshnessDot ts={hoursAgo(1)} label="最新证据" />);
    const dot = container.querySelector('[data-ui="freshness-dot"]')!;
    expect(dot.getAttribute("data-state")).toBe("fresh");
    expect(dot.className).toContain("vk-breathe");
    expect(dot.getAttribute("title")).toContain("最新证据");
    expect(dot.getAttribute("title")).toContain("更新于");
  });

  it("warn / stale(danger)→ 静态黄 / 红,不呼吸", () => {
    const w = render(<FreshnessDot ts={hoursAgo(48)} />);
    const wd = w.container.querySelector('[data-ui="freshness-dot"]')!;
    expect(wd.getAttribute("data-state")).toBe("warn");
    expect(wd.className).toContain("bg-amber-400");
    expect(wd.className).not.toContain("vk-breathe");
    const s = render(<FreshnessDot ts={hoursAgo(100)} />);
    const sd = s.container.querySelector('[data-ui="freshness-dot"]')!;
    expect(sd.getAttribute("data-state")).toBe("stale");
    expect(sd.className).toContain("bg-rose-500");
  });

  it("state 显式覆盖 + staleTone=muted + 自定义 title(worker 在线灯口径)", () => {
    const { container } = render(
      <FreshnessDot state="stale" staleTone="muted" ts={hoursAgo(1)} title="离线(5 分钟内无心跳)" />,
    );
    const dot = container.querySelector('[data-ui="freshness-dot"]')!;
    expect(dot.getAttribute("data-state")).toBe("stale");
    expect(dot.className).toContain("bg-slate-600");
    expect(dot.className).not.toContain("vk-breathe");
    expect(dot.getAttribute("title")).toBe("离线(5 分钟内无心跳)");
  });

  it("无时间戳 → unknown 空心灰圈,title 诚实「—」", () => {
    const { container } = render(<FreshnessDot ts={null} />);
    const dot = container.querySelector('[data-ui="freshness-dot"]')!;
    expect(dot.getAttribute("data-state")).toBe("unknown");
    expect(dot.getAttribute("title")).toContain("—");
  });
});

describe("DeltaBadge", () => {
  it("prev 模式:升 → ↑绿;降 → ↓红;持平 → 安静缺席", () => {
    const up = render(<DeltaBadge value={5} prev={3} />);
    expect(up.container.textContent).toBe("↑2");
    expect(up.container.querySelector(".vk-delta-flash")!.className).toContain("text-emerald-300");
    const down = render(<DeltaBadge value={2} prev={5} />);
    expect(down.container.textContent).toBe("↓3");
    expect(down.container.querySelector(".vk-delta-flash")!.className).toContain("text-rose-300");
    const flat = render(<DeltaBadge value={4} prev={4} />);
    expect(flat.container.firstChild).toBeNull();
  });

  it("good=down(告警类):数字升 → 红(升不是好消息)", () => {
    const { container } = render(<DeltaBadge value={5} prev={3} good="down" />);
    expect(container.querySelector(".vk-delta-flash")!.className).toContain("text-rose-300");
  });

  it("id 模式:读 localStorage 基线,渲染 ↑,并写回本次值", () => {
    localStorage.setItem("vkpi:delta:t1", "3");
    const { container } = render(<DeltaBadge id="t1" value={5} />);
    expect(container.textContent).toBe("↑2");
    expect(container.querySelector('[data-ui="delta-badge"]')!.getAttribute("title")).toContain("3 → 5");
    expect(localStorage.getItem("vkpi:delta:t1")).toBe("5");
  });

  it("id 模式无基线:安静缺席,但把本次值记为下次基线", () => {
    const { container } = render(<DeltaBadge id="t2" value={7} />);
    expect(container.firstChild).toBeNull();
    expect(localStorage.getItem("vkpi:delta:t2")).toBe("7");
  });

  it("value 非数值/缺失 → 安静缺席,不写存储", () => {
    const { container } = render(<DeltaBadge id="t3" value={null} />);
    expect(container.firstChild).toBeNull();
    expect(localStorage.getItem("vkpi:delta:t3")).toBeNull();
  });
});

describe("SkeletonBlock", () => {
  it("单块:aria-hidden + vk-shimmer + 调用方 className", () => {
    const { container } = render(<SkeletonBlock className="h-3.5 w-3/4 rounded" />);
    const el = container.querySelector('[data-ui="skeleton"]')!;
    expect(el.getAttribute("aria-hidden")).toBe("true");
    expect(el.className).toContain("vk-shimmer");
    expect(el.className).toContain("w-3/4");
  });

  it("lines=3:三行文字形骨架(末行 2/3 宽)", () => {
    const { container } = render(<SkeletonBlock lines={3} />);
    const bars = container.querySelectorAll(".vk-shimmer");
    expect(bars.length).toBe(3);
    expect(bars[2].className).toContain("w-2/3");
  });
});

describe("reduced-motion 降级卫兵(CSS media query)", () => {
  it("motion.css 里 prefers-reduced-motion 块覆盖全部动画类并 animation:none", () => {
    const css = fs.readFileSync(path.join(HERE, "motion.css"), "utf8");
    const idx = css.indexOf("@media (prefers-reduced-motion: reduce)");
    expect(idx).toBeGreaterThan(-1);
    const tail = css.slice(idx);
    for (const cls of [".vk-spark-path", ".vk-breathe", ".vk-delta-flash", ".vk-shimmer"]) {
      expect(tail).toContain(cls);
    }
    expect(tail).toContain("animation: none");
  });

  it("入场/变化动画时长守 200-400ms 纪律", () => {
    const css = fs.readFileSync(path.join(HERE, "motion.css"), "utf8");
    expect(css).toContain("vk-spark-draw 400ms");
    expect(css).toContain("vk-delta-flash 300ms");
  });
});
