import { describe, expect, it } from "vitest";
import {
  dedupeCandidates,
  eventCandidate,
  filterCandidatesByTerm,
  globalSearchCandidates,
  kolCandidate,
  matchNavItems,
  navCandidate,
  parseAskQuery,
  projectCandidate,
  resolveEmptyKind,
  skuCandidate,
  suggestionCandidate,
  type AskCandidate,
} from "./askGrammar";

const NAV = [
  { key: "dashboard", label: "Dashboard" },
  { key: "my-kol", label: "MY KOL" },
  { key: "kol-pool", label: "KOL Pool" },
  { key: "kolProfile", label: "KOL 档案" },
  { key: "projects", label: "Projects" },
  { key: "events", label: "Events" },
  { key: "dealers", label: "Dealers" },
  { key: "sku360", label: "SKU 360°" },
  { key: "dataQuery", label: "问数", ops: true },
  { key: "reports", label: "Reports", v2: true },
];

const ZH: Record<string, string> = { Dashboard: "仪表盘", Dealers: "经销商", "MY KOL": "我的 KOL", Projects: "项目", Events: "活动" };
const tZh = (text: string) => ZH[text] ?? text;

describe("parseAskQuery 前缀表", () => {
  it.each([
    ["@alice", "kol", "alice"],
    ["＠alice", "kol", "alice"],
    ["#26mm EVO", "project", "26mm EVO"],
    ["＃上市", "project", "上市"],
    ["$75", "sku", "75"],
    ["＄85mm pro", "sku", "85mm pro"],
    ["/dealers", "nav", "dealers"],
    ["／问数", "nav", "问数"],
    ["  @  bob ", "kol", "bob"],
    ["@", "kol", ""],
    ["alice", null, "alice"],
    ["", null, ""],
    ["email@x.com", null, "email@x.com"],
  ] as const)("%j → prefix=%s term=%j", (raw, prefix, term) => {
    expect(parseAskQuery(raw)).toEqual({ raw, prefix, term });
  });
});

describe("matchNavItems 板块别名表", () => {
  it.each([
    ["经销商地图", "dealers"],
    ["dealer", "dealers"],
    ["地图", "dealers"],
    ["问数", "dataQuery"],
    ["data q&a", "dataQuery"],
    ["我的 KOL", "my-kol"],
    ["mykol", "my-kol"],
    ["档案", "kolProfile"],
    ["项目", "projects"],
    ["活动", "events"],
    ["镜头", "sku360"],
    ["仪表盘", "dashboard"],
    ["首页", "dashboard"],
    ["dash", "dashboard"],
  ])("%j → 首位 %s", (term, key) => {
    const matches = matchNavItems(term, NAV, tZh);
    expect(matches[0]?.item.key).toBe(key);
  });

  it("空词返回全部可达板块并排除 v2 占位;无命中返回空", () => {
    const all = matchNavItems("", NAV, tZh, 50);
    expect(all.map((match) => match.item.key)).not.toContain("reports");
    expect(all).toHaveLength(NAV.length - 1);
    expect(matchNavItems("zzz", NAV, tZh)).toEqual([]);
  });

  it("相等 < 前缀 < 子串 排序", () => {
    const tiers = matchNavItems("kol", NAV, tZh).map((match) => match.tier);
    expect(tiers).toEqual([...tiers].sort((a, b) => a - b));
    expect(matchNavItems("KOL Pool", NAV, tZh)[0]).toMatchObject({ item: { key: "kol-pool" }, tier: 0 });
  });
});

describe("候选契约(P1 只有 navigate / open_entity / ask)", () => {
  it.each([
    ["nav", navCandidate({ key: "dealers", label: "Dealers" }, tZh), { kind: "nav", id: "nav:dealers", label: "经销商", action: { type: "navigate", route: "dealers" } }],
    ["kol", kolCandidate({ id: 9, platform: "YouTube", handle: "@alice", display_name: "Alice", avatar_url: null, followers: 1 }), { kind: "kol", id: "kol:9", label: "Alice", action: { type: "open_entity", entity: { type: "kol", id: 9 }, route: "kol-pool" } }],
    ["project", projectCandidate({ id: 33, project_uid: "P-33", project_name: "26mm EVO", stage: "planning", stage_status: null, platform: null }, tZh), { kind: "project", id: "project:33", label: "26mm EVO", detail: "planning", action: { type: "open_entity", entity: { type: "project", id: 33 }, route: "projects" } }],
    ["event", eventCandidate({ id: "evt-1", title: "Expo", status: null, start_date: "2026-09-01", end_date: null }, tZh), { kind: "event", id: "event:evt-1", label: "Expo", detail: "2026-09-01", action: { type: "open_entity", entity: { type: "event", id: "evt-1" }, route: "events" } }],
    ["sku", skuCandidate({ sku: "AF-85MM-F14-PRO-FE", display_name: "AF 85mm F1.4 Pro", lens_key: "af85mmf14pro" }, tZh), { kind: "sku", id: "sku:AF-85MM-F14-PRO-FE", label: "AF 85mm F1.4 Pro", detail: "AF-85MM-F14-PRO-FE", action: { type: "open_entity", entity: { type: "sku", id: "AF-85MM-F14-PRO-FE" }, route: "sku360" } }],
    ["sku family", skuCandidate({ sku: "", display_name: "AF 75mm F1.8 EVO", lens_key: "af75mmf18evo" }, tZh), { kind: "sku", id: "sku:af75mmf18evo", action: { type: "navigate", route: "sku360", params: { search: "AF 75mm F1.8 EVO" } } }],
    ["suggestion", suggestionCandidate("目前 KOL 数量是多少？", 0), { kind: "suggestion", label: "目前 KOL 数量是多少？", action: { type: "ask", query: "目前 KOL 数量是多少？" } }],
  ])("%s 候选形状", (_name, candidate, expected) => {
    expect(candidate).toMatchObject(expected);
    expect(["navigate", "open_entity", "ask"]).toContain(candidate.action.type);
  });

  it("globalSearchCandidates 按前缀切分:@ 只留 KOL,# 留项目+活动,无前缀全留", () => {
    const result = {
      kols: [{ id: 1, platform: "YouTube", handle: "@a", display_name: "A", avatar_url: null, followers: 1 }],
      projects: [{ id: 2, project_uid: "P", project_name: "Proj", stage: null, stage_status: null, platform: null }],
      events: [{ id: "e", title: "Evt", status: null, start_date: null, end_date: null }],
    };
    expect(globalSearchCandidates(result, "kol", tZh).map((c) => c.kind)).toEqual(["kol"]);
    expect(globalSearchCandidates(result, "project", tZh).map((c) => c.kind)).toEqual(["project", "event"]);
    expect(globalSearchCandidates(result, null, tZh).map((c) => c.kind)).toEqual(["kol", "project", "event"]);
  });
});

describe("诚实空态三态", () => {
  it.each([
    [["ready", "ready"], 0, "none"],
    [[], 0, "none"],
    [["blocked", "blocked"], 0, "scope"],
    [["blocked", "ready"], 0, "scope"],
    [["error", "ready"], 0, "unavailable"],
    [["absent"], 0, "unavailable"],
    [["degraded"], 0, "unavailable"],
    [["blocked", "error"], 0, "unavailable"],
    [["error"], 3, null],
  ] as const)("states=%j count=%d → %s", (states, count, expected) => {
    expect(resolveEmptyKind(states, count)).toBe(expected);
  });
});

describe("本地过滤与去重", () => {
  const pool: AskCandidate[] = [
    { kind: "kol", id: "kol:1", label: "Alice Wong", detail: "YouTube · @alice", action: { type: "navigate", route: "kol-pool" } },
    { kind: "kol", id: "kol:2", label: "Bob", detail: "TikTok · @bob", action: { type: "navigate", route: "kol-pool" } },
    { kind: "kol", id: "kol:3", label: "Malice", detail: "", action: { type: "navigate", route: "kol-pool" } },
  ];
  it("filterCandidatesByTerm 前缀优先于子串,空词不返回", () => {
    expect(filterCandidatesByTerm(pool, "alice").map((c) => c.id)).toEqual(["kol:1", "kol:3"]);
    expect(filterCandidatesByTerm(pool, "@bob").map((c) => c.id)).toEqual(["kol:2"]);
    expect(filterCandidatesByTerm(pool, "")).toEqual([]);
  });
  it("dedupeCandidates 同 id 首次胜出", () => {
    expect(dedupeCandidates([pool.slice(0, 2), pool]).map((c) => c.id)).toEqual(["kol:1", "kol:2", "kol:3"]);
  });
});
