import { describe, it, expect } from "vitest";
import {
  normalizeAlerts,
  normalizeKolFunnel,
  latestCalendarDate,
  eventCoords,
  normalizeTopMovers,
} from "./normalizers";

// 源 normalizers.ts 的 `items = []` / `kolRows = []` 默认参被 TS 推断为 never[]
// (源文件无类型注解,且不在本 lane 可改范围)。测试侧用 row 数组喂入是预期用法,
// 这里把行数据声明为 Row[] 再经 Parameters<> 桥接到形参类型,既不放宽源签名也无 any。
type Row = Record<string, unknown>;
const asCalendarItems = (rows: Row[]): Parameters<typeof latestCalendarDate>[0] =>
  rows as unknown as Parameters<typeof latestCalendarDate>[0];
const asMoverRows = (rows: Row[]): Parameters<typeof normalizeTopMovers>[0] =>
  rows as unknown as Parameters<typeof normalizeTopMovers>[0];

// P2-6: cockpit normalizers 纯归一化逻辑(无网络)。本测试已在严格 tsc 下零报错。
// 选纯且无随机精确值的导出;凡涉及 Date.now()/jitter 只断言「字段存在/类型/排序/长度」。
// 红线:normalizeTopMovers 只留真分,绝不出现非真占位行。

describe("normalizeAlerts 分类/严重度/分桶", () => {
  it("feedback 识别 → category=feedback + iconColor amber", () => {
    const out = normalizeAlerts([{ target_type: "team_feedback", title: "反馈" }]);
    const alert = out.all[0];
    expect(alert.category).toBe("feedback");
    expect(alert.iconColor).toBe("#f59e0b");
    expect(alert.source).toBe("vkpi_feedback");
  });

  it("severity critical → 收敛为 high,iconColor 红", () => {
    const out = normalizeAlerts([{ severity: "critical", title: "崩" }]);
    expect(out.all[0].severity).toBe("high");
    expect(out.all[0].iconColor).toBe("#ef4444");
  });

  it("resolved_at → status=done,unread=false", () => {
    const out = normalizeAlerts([{ title: "done", resolved_at: "2026-06-01" }]);
    expect(out.all[0].status).toBe("done");
    expect(out.all[0].unread).toBe(false);
  });

  it("未 resolved → unread=true,status=todo", () => {
    const out = normalizeAlerts([{ title: "open" }]);
    expect(out.all[0].unread).toBe(true);
    expect(out.all[0].status).toBe("todo");
  });

  it("notifications 桶排除 reminder/task;reminders 桶只含它们", () => {
    const out = normalizeAlerts([
      { title: "通知", category: "notification" },
      { title: "提醒事项", category: "reminder" },
      { title: "任务", category: "task" },
    ]);
    expect(out.notifications.map((a) => a.title)).toEqual(["通知"]);
    expect(out.reminders.map((a) => a.title).sort()).toEqual(["任务", "提醒事项"]);
  });

  it("空输入 → 三桶皆空数组", () => {
    const out = normalizeAlerts([]);
    expect(out.all).toEqual([]);
    expect(out.notifications).toEqual([]);
    expect(out.reminders).toEqual([]);
  });
});

describe("normalizeKolFunnel 真伪判定", () => {
  it("全 null → isReal=false,4 个阶段 count=null", () => {
    const out = normalizeKolFunnel({});
    expect(out.isReal).toBe(false);
    expect(out.stages).toHaveLength(4);
    expect(out.stages.every((s: { count: number | null }) => s.count == null)).toBe(true);
  });

  it("任一 count 非空 → isReal=true", () => {
    const out = normalizeKolFunnel({ funnel: { favorites_total: 5 } });
    expect(out.isReal).toBe(true);
    expect(out.stages[0].count).toBe(5);
  });

  it("by_staff 归一化为对象数组", () => {
    const out = normalizeKolFunnel({ funnel: { claimed_total: 1, by_staff: [{ staff_id: 9 }] } });
    expect(out.byStaff).toHaveLength(1);
    expect(out.byStaff[0]).toMatchObject({ staff_id: 9 });
  });
});

describe("latestCalendarDate 取最近日期", () => {
  it("混合日期串取最大 YYYY-MM-DD", () => {
    const out = latestCalendarDate(asCalendarItems([
      { posted_at: "2026-05-28" },
      { published_at: "2026-06-10" },
      { created_at: "2026-04-01" },
    ]));
    expect(out).toBe("2026-06-10");
  });

  it("空输入 → null", () => {
    expect(latestCalendarDate([])).toBeNull();
  });
});

describe("eventCoords 落点逻辑", () => {
  it("显式 lat/lng 直接返回", () => {
    expect(eventCoords("US", 12.5, -34.2)).toEqual({ lat: 12.5, lng: -34.2 });
  });

  it("无经纬 + 无可识别国家 → null", () => {
    expect(eventCoords("", null, null)).toBeNull();
    expect(eventCoords("ZZ", null, null)).toBeNull();
  });

  it("有国家 → 质心 + jitter,lat/lng 为 number(不断言精确值)", () => {
    const coords = eventCoords("US", null, null, "seed");
    expect(coords).not.toBeNull();
    expect(typeof coords!.lat).toBe("number");
    expect(typeof coords!.lng).toBe("number");
  });
});

describe("normalizeTopMovers 真分排序(红线)", () => {
  it("只留 v6_fit != null,DESC 排序,≤5", () => {
    const out = normalizeTopMovers(asMoverRows([
      { id: 1, handle: "@a", v6_fit: 70, followers: 100 },
      { id: 2, handle: "@b", v6_fit: 95, followers: 200 },
      { id: 3, handle: "@c", v6_fit: null, followers: 999 },
      { id: 4, handle: "@d", v6_fit: 82 },
    ]));
    expect(out).toHaveLength(3);
    expect(out.map((m: { raw: { v6_fit: number } }) => m.raw.v6_fit)).toEqual([95, 82, 70]);
  });

  it("无 v6_fit 的行被剔除(不出现非真占位)", () => {
    const out = normalizeTopMovers(asMoverRows([
      { id: 1, handle: "@noscore", followers: 5000 },
      { id: 2, handle: "@onlyfit", v6_fit: 88 },
    ]));
    expect(out).toHaveLength(1);
    expect(out[0].handle).toBe("@onlyfit");
    // deltaFollower 必须是真分,绝不出现「待评估」占位
    expect(out[0].deltaFollower).toBe("Fit 88");
  });

  it("超过 5 条只取前 5", () => {
    const rows = Array.from({ length: 8 }, (_, i) => ({ id: i, handle: `@k${i}`, v6_fit: 10 + i }));
    expect(normalizeTopMovers(asMoverRows(rows))).toHaveLength(5);
  });

  it("空输入 → 空数组", () => {
    expect(normalizeTopMovers([])).toEqual([]);
  });
});
