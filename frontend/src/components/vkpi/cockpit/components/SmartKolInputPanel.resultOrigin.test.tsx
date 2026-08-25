// 来源标注(库内 / 新发现 / 你提供的)的口径契约 + 门面渲染。
// 这里是「口径唯一真源」的看门测试:改 RESULT_ORIGIN_BY_ITEM_TYPE 或后端写入端,必须同步改这个文件。
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RecallMiniItem } from "./SmartKolInputPanel.Sections";
import { ResultOriginSummaryBar } from "./SmartKolInputPanel.TextResult";
import {
  RESULT_ORIGIN_BY_ITEM_TYPE,
  discoveryItemsFromSession,
  recallResultFromSession,
  resultOriginBadge,
  resultOriginCounts,
  resultOriginOf,
  sessionResultOriginCounts,
  summaryResultOriginCounts,
  withLocalRecallOrigin,
} from "./SmartKolInputPanel.sessionProjection";

/** 线上真实写入形状(2026-08-25 prod 实测):每种 item_type 的 payload 关键键。 */
const PROD_SHAPES = {
  recall_candidate: {
    item_type: "recall_candidate",
    kol_pool_id: 501,
    source_url: "https://youtube.com/@local_one",
    payload: { handle: "local_one", platform: "youtube", display_name: "Local One" },
  },
  existing_kol: {
    item_type: "existing_kol",
    kol_pool_id: 502,
    source_url: "https://youtube.com/@already_ours",
    // 陷阱:线上 427/427 条 existing_kol 的 payload.source 也是 platform_discovery。
    payload: { source: "platform_discovery", handle: "already_ours", platform: "youtube", historical_match: { kol_pool_id: 502 } },
  },
  new_creator: {
    item_type: "new_creator",
    source_url: "https://youtube.com/@brand_new",
    payload: { source: "platform_discovery", handle: "brand_new", platform: "youtube" },
  },
  online_qualified_candidate: {
    item_type: "online_qualified_candidate",
    kol_pool_id: 504,
    payload: { source: "platform_discovery_strict", origin_lane: "online", handle: "net_new", platform: "youtube" },
  },
  url_profile: {
    item_type: "url_profile",
    kol_pool_id: 505,
    source_url: "https://youtube.com/@pasted",
    payload: { url_type: "profile", handle: "pasted", platform: "youtube", in_pool: true },
  },
  url_video: {
    item_type: "url_video",
    source_url: "https://youtube.com/watch?v=abc",
    payload: { url_type: "video", platform: "youtube" },
  },
  // 线上 4 条:贴进来但没认出平台的链接,后端记成 item_type='unknown',payload 仍带 url_type。
  unknown_url: {
    item_type: "unknown",
    source_url: "https://www.youtube.com/results?search_query=epic+1.33",
    payload: { url_type: "unknown", handle: null, platform: "youtube" },
  },
} as const;

describe("来源口径:五种条目类型各自判到哪一档", () => {
  it.each([
    ["recall_candidate", "local", "库内"],
    ["existing_kol", "local", "库内"],
    ["new_creator", "online", "新发现"],
    ["online_qualified_candidate", "online", "新发现"],
    ["url_profile", "provided", "你提供的"],
    ["url_video", "provided", "你提供的"],
  ] as const)("%s → %s(%s)", (shape, kind, label) => {
    const item = PROD_SHAPES[shape as keyof typeof PROD_SHAPES];
    expect(resultOriginOf(item).kind).toBe(kind);
    expect(resultOriginBadge(item)?.label).toBe(label);
  });

  it("贴进来但没认出平台的链接(item_type=unknown + url_type)仍算「你提供的」", () => {
    expect(resultOriginOf(PROD_SHAPES.unknown_url).kind).toBe("provided");
  });

  it("existing_kol 的 payload.source=platform_discovery 不许被读成「新发现」", () => {
    // 线上 427 条 existing_kol 全带这个 source;拿 source 判来源会把库内老人全标成新发现。
    expect(PROD_SHAPES.existing_kol.payload.source).toBe("platform_discovery");
    expect(resultOriginOf(PROD_SHAPES.existing_kol).kind).toBe("local");
  });

  it("判不出来源就是 unknown,而且拿不到徽标", () => {
    const mystery = { item_type: "some_future_type", payload: { handle: "who", platform: "youtube" } };
    expect(resultOriginOf(mystery).kind).toBe("unknown");
    expect(resultOriginOf(mystery).basis).toBe("none");
    expect(resultOriginBadge(mystery)).toBeNull();
  });
});

describe("来源字段缺失时的回退,与显式字段口径必须一致", () => {
  it("有明确来源字段就用字段,basis=field", () => {
    const item = { item_type: "new_creator", result_origin: "local", payload: { source: "platform_discovery" } };
    expect(resultOriginOf(item)).toEqual({ kind: "local", basis: "field" });
  });

  it("字段名换成 origin / source_origin / origin_lane 都读得到", () => {
    expect(resultOriginOf({ origin: "local_pool" }).kind).toBe("local");
    expect(resultOriginOf({ source_origin: "platform_discovery" }).kind).toBe("online");
    expect(resultOriginOf({ payload: { origin_lane: "online" } }).kind).toBe("online");
    expect(resultOriginOf({ source_fields: { result_origin: "operator_url" } }).kind).toBe("provided");
  });

  it("字段值不认识时不当判据,继续按条目类型回退(不误标)", () => {
    const item = { item_type: "recall_candidate", result_origin: "some_new_lane_we_do_not_know" };
    expect(resultOriginOf(item)).toEqual({ kind: "local", basis: "inferred" });
  });

  // 口径对齐看门测试:同一条数据,「后端给了字段」和「后端没给、前端回退」必须落到同一档。
  it.each(Object.keys(RESULT_ORIGIN_BY_ITEM_TYPE))("%s:给字段和不给字段,判出来必须一样", (itemType) => {
    const expected = RESULT_ORIGIN_BY_ITEM_TYPE[itemType];
    const withoutField = resultOriginOf({ item_type: itemType });
    const withField = resultOriginOf({ item_type: itemType, result_origin: expected });
    expect(withoutField.kind).toBe(expected);
    expect(withoutField.basis).toBe("inferred");
    expect(withField).toEqual({ kind: expected, basis: "field" });
  });

  it("旧数据只写过中文 type_label 时,沿用门面已经说过的话", () => {
    expect(resultOriginOf({ type_label: "库内已有" }).kind).toBe("local");
    expect(resultOriginOf({ type_label: "全网发现" }).kind).toBe("online");
    expect(resultOriginOf({ type_label: "联网净新增" }).kind).toBe("online");
    // 「创作者 / 测评号」是角色标签,不是来源,不许被当成来源判据。
    expect(resultOriginOf({ type_label: "创作者" }).kind).toBe("unknown");
    expect(resultOriginOf({ type_label: "测评号" }).kind).toBe("unknown");
  });

  it("本地召回接口返回的行补「库内」,已有明确来源的行不动", () => {
    const [plain, explicit] = withLocalRecallOrigin([
      { handle: "no_origin" },
      { handle: "already_online", result_origin: "online" },
    ]);
    expect(resultOriginOf(plain).kind).toBe("local");
    expect(resultOriginOf(explicit).kind).toBe("online");
  });
});

describe("投影:所有类型都拿得到来源标注", () => {
  const session = {
    id: 9001,
    items: [
      PROD_SHAPES.recall_candidate,
      PROD_SHAPES.existing_kol,
      PROD_SHAPES.new_creator,
      PROD_SHAPES.online_qualified_candidate,
      PROD_SHAPES.url_profile,
    ],
    result_summary: {},
  } as never;

  it("recall_candidate 投影出来带「库内」(过去这一类一条都没标)", () => {
    const projected = recallResultFromSession(session).items;
    expect(projected).toHaveLength(1);
    expect(resultOriginBadge(projected[0])?.label).toBe("库内");
  });

  it("发现墙两类分别拿到「库内」和「新发现」", () => {
    const discovery = discoveryItemsFromSession(session);
    const labels = discovery.map((item) => resultOriginBadge(item)?.label);
    expect(labels).toEqual(["库内", "新发现"]);
    // 类型闸只管谁上发现墙:本地召回 / 贴链接结果不许被灌进来。
    expect(discovery).toHaveLength(2);
  });

  it("整场搜索的分布覆盖五种类型,不再只数发现墙那两类", () => {
    expect(sessionResultOriginCounts(session)).toMatchObject({
      total: 5, local: 2, online: 2, provided: 1, unknown: 0, basis: "session",
    });
  });

  it("投影出来的发现项直接摆到卡片上,徽标就是投影里那一档", () => {
    const [existing, fresh] = discoveryItemsFromSession(session);
    const { unmount } = render(<RecallMiniItem index={1} item={existing as never} />);
    expect(screen.getByTestId("candidate-origin-badge").getAttribute("data-origin")).toBe("local");
    unmount();
    render(<RecallMiniItem index={2} item={fresh as never} />);
    expect(screen.getByTestId("candidate-origin-badge").getAttribute("data-origin")).toBe("online");
  });

  // 线上实测锚点(2026-08-25 prod,vkpi_kol_search_session_items 全表 3939 行):
  // 这套口径把每一行都判到了归属,unknown=0;显式字段与类型回退零分歧。
  it("线上全表按这套口径都判得出来源,没有漏网的", () => {
    const PROD_ITEM_TYPE_ROWS: Record<string, number> = {
      recall_candidate: 1401, existing_kol: 427, new_creator: 1100,
      online_qualified_candidate: 9, url_profile: 952, url_video: 46,
    };
    const tally = { local: 0, online: 0, provided: 0, unknown: 0 };
    Object.entries(PROD_ITEM_TYPE_ROWS).forEach(([itemType, rows]) => {
      tally[resultOriginOf({ item_type: itemType }).kind] += rows;
    });
    tally[resultOriginOf(PROD_SHAPES.unknown_url).kind] += 4; // item_type='unknown' 但带 url_type 的 4 行
    expect(tally).toEqual({ local: 1828, online: 1109, provided: 1002, unknown: 0 });
    expect(tally.local + tally.online + tally.provided + tally.unknown).toBe(3939);
  });
});

describe("顶部分布数字", () => {
  it("同一个人出现在多段列表里只计一次,且「已经在库里」这个事实优先", () => {
    const recall = [{ handle: "@Shared", platform: "YouTube", result_origin: "local" }];
    const discovery = [{ handle: "shared", platform: "youtube", result_origin: "online" }];
    const counts = resultOriginCounts(recall, discovery);
    expect(counts).toMatchObject({ total: 1, local: 1, online: 0, basis: "displayed" });
  });

  it("数字与三段列表实际条数对得上", () => {
    const recall = [{ handle: "a", platform: "youtube" }, { handle: "b", platform: "youtube" }];
    const online = [{ handle: "c", platform: "youtube", source_fields: { origin_lane: "online" } }];
    const discovery = [{ handle: "d", platform: "tiktok", item_type: "new_creator" }];
    const counts = resultOriginCounts(withLocalRecallOrigin(recall), online, discovery);
    expect(counts.total).toBe(recall.length + online.length + discovery.length);
    expect(counts.local).toBe(2);
    expect(counts.online).toBe(2);
    expect(counts.unknown).toBe(0);
  });

  it("判不出来源的行进「来源待标注」,不摊进库内或新发现", () => {
    const counts = resultOriginCounts([{ handle: "ghost", platform: "youtube" }]);
    expect(counts).toMatchObject({ total: 1, local: 0, online: 0, provided: 0, unknown: 1 });
  });

  it("服务端算好的分布优先,并标明是全量口径", () => {
    const counts = summaryResultOriginCounts({ result_origin_counts: { local: 12, online: 7, provided: 1, total: 20 } });
    expect(counts).toEqual({ total: 20, local: 12, online: 7, provided: 1, unknown: 0, basis: "summary" });
  });

  it("服务端分布缺 local/online 就当没有,不半信半疑地拼数", () => {
    expect(summaryResultOriginCounts({ result_origin_counts: { provided: 3 } })).toBeNull();
    expect(summaryResultOriginCounts({})).toBeNull();
  });
});

// 写端与读端的对接契约。上面那些用例只证明「读端自己自洽」,证明不了「读端读得懂写端写的东西」。
// 隔离库 3806 行真实数据逐条对拍时,分歧全出在这一段缺失的三件事上:
//   1) 写端落库字面量 online_new 不在读端取值表里 -> 写端判好的结论被丢掉,退回按类型猜;
//   2) 写端的键是 origin_breakdown、数字嵌在 counts 里,读端两样都不认 -> 顶部永远退回前端现数;
//   3) unlabeled(还没回填的老行)被读成 0 -> 回填跑没跑过,在门面上看不出来。
describe("写读同源:后端落库形状必须被读端认下来", () => {
  // 写端唯一真源:migrations/301 的 CHECK 与 search_sessions_item_origin.ITEM_ORIGIN_VALUES。
  const BACKEND_ORIGIN_VALUES = {
    local_pool: "local",
    online_new: "online",
    operator_url: "provided",
  } as const;

  it("后端落库的三个确定取值,读端一个不漏,而且认的是字段不是猜", () => {
    Object.entries(BACKEND_ORIGIN_VALUES).forEach(([backendValue, expectedKind]) => {
      // 故意用读端类型表里没有的 item_type:还能判对,就只可能是读到了后端写的字段。
      expect(resultOriginOf({ item_type: "some_future_lane", payload: { origin: backendValue } }))
        .toEqual({ kind: expectedKind, basis: "field" });
    });
  });

  it("后端第四个取值 unknown 意思是「它也没判出来」,读端要继续回退而不是就地认命", () => {
    expect(resultOriginOf({ item_type: "url_profile", payload: { origin: "unknown" } }))
      .toEqual({ kind: "provided", basis: "inferred" });
  });

  it("读端认得后端真正写的 origin_breakdown(键名 + 嵌套 counts + 后端字面量)", () => {
    // search_sessions_items._update_session 每次持久化会话汇总都会重算并落这个形状。
    const counts = summaryResultOriginCounts({
      kind: "kol_recall",
      origin_breakdown: {
        schema: "session_item_origin_v1",
        total: 3806,
        counts: { local_pool: 1708, online_new: 1100, operator_url: 998, unknown: 0, unlabeled: 0 },
        labels: { local_pool: "库内已有" },
        by_item_type: { recall_candidate: { local_pool: 1281 } },
      },
    });
    expect(counts).toEqual({ total: 3806, local: 1708, online: 1100, provided: 998, unknown: 0, basis: "summary" });
  });

  it("回填还没跑时照实报「全都还没标注」,不许报成 0", () => {
    const counts = summaryResultOriginCounts({
      origin_breakdown: { total: 3806, counts: { local_pool: 0, online_new: 0, operator_url: 0, unknown: 0, unlabeled: 3806 } },
    });
    expect(counts).toMatchObject({ total: 3806, unknown: 3806, local: 0, online: 0 });
  });

  it("unknown(判不出)与 unlabeled(还没回填)相加进「待标注」,不二选一", () => {
    const counts = summaryResultOriginCounts({
      origin_breakdown: { total: 30, counts: { local_pool: 10, online_new: 8, operator_url: 5, unknown: 3, unlabeled: 4 } },
    });
    expect(counts).toMatchObject({ total: 30, local: 10, online: 8, provided: 5, unknown: 7 });
  });
});

describe("门面渲染", () => {
  it("卡片按来源摆徽标", () => {
    const { unmount } = render(
      <RecallMiniItem index={1} item={{ handle: "new_one", platform: "youtube", result_origin: "online" } as never} />,
    );
    const badge = screen.getByTestId("candidate-origin-badge");
    expect(badge.textContent).toBe("新发现");
    expect(badge.getAttribute("data-origin")).toBe("online");
    unmount();

    render(<RecallMiniItem index={1} item={{ handle: "old_one", platform: "youtube", result_origin: "local" } as never} />);
    expect(screen.getByTestId("candidate-origin-badge").textContent).toBe("库内");
  });

  it("判不出来源的卡片什么都不摆,不许出现「未知」", () => {
    render(<RecallMiniItem index={1} item={{ handle: "ghost", platform: "youtube" } as never} />);
    expect(screen.queryByTestId("candidate-origin-badge")).toBeNull();
    expect(screen.queryByText("未知")).toBeNull();
    expect(screen.queryByText(/来源/)).toBeNull();
  });

  it("顶部分布把库内 / 新发现摆出来,并说明用的是哪一种口径", () => {
    render(<ResultOriginSummaryBar counts={{ total: 9, local: 6, online: 3, provided: 0, unknown: 0, basis: "displayed" }} />);
    expect(screen.getByTestId("result-origin-total").textContent).toBe("本次 9 人");
    expect(screen.getByTestId("result-origin-count-local").textContent).toContain("库内 6");
    expect(screen.getByTestId("result-origin-count-online").textContent).toContain("新发现 3");
    expect(screen.queryByTestId("result-origin-count-provided")).toBeNull();
    expect(screen.queryByTestId("result-origin-count-unknown")).toBeNull();
    expect(screen.getByTestId("result-origin-summary").textContent).toContain("本页已显示的结果");
  });

  it("库内为 0 也照实摆 0,不藏", () => {
    render(<ResultOriginSummaryBar counts={{ total: 4, local: 0, online: 4, provided: 0, unknown: 0, basis: "session" }} />);
    expect(screen.getByTestId("result-origin-count-local").textContent).toContain("库内 0");
    expect(screen.getByTestId("result-origin-summary").textContent).toContain("本次搜索全部结果");
  });

  it("一条结果都没有就不摆这一行", () => {
    const { container } = render(
      <ResultOriginSummaryBar counts={{ total: 0, local: 0, online: 0, provided: 0, unknown: 0, basis: "displayed" }} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("有来源待标注的行就照实报出来", () => {
    render(<ResultOriginSummaryBar counts={{ total: 5, local: 2, online: 1, provided: 0, unknown: 2, basis: "displayed" }} />);
    expect(screen.getByTestId("result-origin-count-unknown").textContent).toContain("来源待标注 2");
  });
});
