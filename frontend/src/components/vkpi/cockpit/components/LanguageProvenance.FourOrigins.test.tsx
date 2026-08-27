import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { kolLanguageProvenance, resolveLanguageProvenance } from "./LanguageProvenance";
import { LanguageProvenanceCell, LanguageProvenanceDetail } from "./LanguageProvenanceChip";

// ── 四档 + 第五种形态:门面不许升格,也不许兜底成「自报」 ───────────────────────
//
// 后端 profile_recall_language_gate 判出四档:
//   self_reported  他在平台资料里自己填的
//   inferred       我们从他发的内容判断出来的
//   projected      资料里有这个值,但证不出是他自己说的
//   unknown        没有可用的值
//
// 还有一种**不是第四档也不是第五档**的形态:后端试着判断过,但它自己觉得把握不够,
// 把那一票旁挂在 `inferred_values` 上、同时判 `origin=unknown` / `values=[]`。
// 门面可以说「试着判断过、把握不够」,但**不许算成推断档**,也不许计进推断数。
//
// 这一份逐句钉死这五种形态的**界面原话**。

// 服务端 language_gate_evidence() 的真实出参形状,含四档明牌与两个布尔明牌。
function gateEvidence(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    values: [], targets: [], filter_requested: false, invalid_targets: [], passed: true,
    source: "vkpi_kol_profiles.language",
    origin: "unknown",
    inferred: false,
    self_reported: false,
    self_reported_values: [],
    inferred_values: [],
    projected_values: [],
    origin_source: "",
    ...overrides,
  };
}

const SELF_REPORTED = [
  {
    language: gateEvidence({
      values: ["en"], origin: "self_reported", self_reported: true,
      self_reported_values: ["en"], source: "vkpi_kol_profiles.language",
      origin_source: "platform_profile",
    }),
  },
  { language: "en" },
];
const INFERRED = [
  {
    language: gateEvidence({
      values: ["ja"], origin: "inferred", inferred: true, inferred_values: ["ja"],
      source: "vkpi_kol_pool.language_inferred",
      basis: "bio+video_titles", evidence_fields: ["bio", "video_titles"],
    }),
  },
];
// 资料里有 ko,但没有任何一份材料说得出这是谁填的。
const PROJECTED = [
  {
    language: gateEvidence({
      values: ["ko"], origin: "projected", projected_values: ["ko"],
      source: "platform_content_metadata", origin_source: "platform_content_metadata",
    }),
  },
];
const NOTHING = [{ language: gateEvidence() }, {}];
// 第五种形态:后端试过、旁挂了一票 en,但它判 unknown / values=[] —— 它自己没敢用。
const WITHHELD = [
  {
    language: gateEvidence({
      values: [], origin: "unknown", inferred_values: ["en"], source: "",
    }),
  },
];

// 「这是他自己填的」这句**声明**只有两种合法长相:自报档的整句,以及转述分歧的那半句。
// 「看不出是不是他自己填的」是澄清,不是声明 —— 不能拿裸串去匹配。
const SELF_REPORT_CLAIMS = ["他在平台资料里自己填的", "他自己填的是"];

describe("五种形态,五句话:门面不许把后四种说成「自报」", () => {
  it("纯自报:说得出「他自己填的」,不挂角标", () => {
    const provenance = resolveLanguageProvenance(SELF_REPORTED);
    expect(provenance.origin).toBe("self_reported");
    expect(provenance.originLabel).toBe("自报");
    expect(provenance.displayLabel).toBe("EN");
    expect(provenance.noteLabel).toBe("他自己填的");
    expect(provenance.title).toBe("英语 · 他在平台资料里自己填的。");
  });

  it("纯推断:说「不是他自己填的」,并说得出依据", () => {
    const provenance = resolveLanguageProvenance(INFERRED);
    expect(provenance.origin).toBe("inferred");
    expect(provenance.originLabel).toBe("推断");
    expect(provenance.displayLabel).toBe("JA");
    expect(provenance.noteLabel).toBe("推断 · 依据个人简介和作品标题");
    expect(provenance.title).toBe(
      "日语 · 平台资料上没写语言，这是照他自己发的个人简介和作品标题推断出来的，不是他自己填的。",
    );
  });

  it("来源不明:值照显示，但一个字都不说是谁填的", () => {
    const provenance = resolveLanguageProvenance(PROJECTED);
    expect(provenance.origin).toBe("projected");
    expect(provenance.originLabel).toBe("来源不明");
    expect(provenance.displayLabel).toBe("KO");
    expect(provenance.noteLabel).toBe("资料里有这个值，但看不出是不是他自己填的");
    expect(provenance.title).toBe("韩语 · 资料里有这个值，但我们看不出是不是他自己填的。");
    expect(provenance.codes).toEqual(["ko"]);
    expect(provenance.selfReportedCodes).toEqual([]);
    expect(provenance.divergenceLabel).toBe("");
  });

  it("什么都没有:只说「我们这里没有」,不声称我们试过判断", () => {
    const provenance = resolveLanguageProvenance(NOTHING);
    expect(provenance.origin).toBe("unknown");
    expect(provenance.originLabel).toBe("未知");
    expect(provenance.displayLabel).toBe("未知");
    expect(provenance.inferenceWithheld).toBe(false);
    expect(provenance.noteLabel).toBe("我们这里没有语言信息");
    expect(provenance.title).toBe(
      "我们这里没有这个人的语言：平台资料里没有可用的值，也没有拿到推断出来的值。",
    );
  });

  it("试过但没把握:说得出「试着判断过」,但它是「未知」,不是「推断」", () => {
    const provenance = resolveLanguageProvenance(WITHHELD);
    expect(provenance.origin).toBe("unknown");
    expect(provenance.inferenceWithheld).toBe(true);
    expect(provenance.originLabel).toBe("未知");
    expect(provenance.displayLabel).toBe("未知");
    expect(provenance.noteLabel).toBe("试着判断过，但把握不够，没当结论");
    expect(provenance.title).toBe(
      "我们这里没有这个人自己填的语言：照他发的东西试着判断过，但把握不够，没有把它当成结论。",
    );
    // **不许升格**:那一票既不上墙,也不进任何一组码。
    expect(provenance.codes).toEqual([]);
    expect(provenance.inferredCodes).toEqual([]);
    expect(provenance.nameLabel).toBe("");
  });

  it("五句话里,「他自己填的」只许出现在自报那一句", () => {
    [INFERRED, PROJECTED, NOTHING, WITHHELD].map(resolveLanguageProvenance).forEach((provenance) => {
      expect(provenance.originLabel).not.toBe("自报");
      expect(provenance.noteLabel).not.toBe("他自己填的");
      SELF_REPORT_CLAIMS.forEach((claim) => expect(provenance.title).not.toContain(claim));
      expect(provenance.divergenceLabel).toBe("");
    });
  });

  it("界面上只有四档:自报无角标，推断 / 来源不明各有角标，两种未知都不挂角标", () => {
    const cases: Array<[string, unknown[], string]> = [
      ["self", SELF_REPORTED, "EN"],
      ["inferred", INFERRED, "JA推断"],
      ["projected", PROJECTED, "KO来源不明"],
      ["nothing", NOTHING, "未知"],
      // 「试过但没把握」绝不许挂上「推断」角标 —— 挂了就是升格。
      ["withheld", WITHHELD, "未知"],
    ];
    cases.forEach(([name, records, expected]) => {
      const { unmount } = render(
        <LanguageProvenanceCell provenance={resolveLanguageProvenance(records)} testId={`cell-${name}`} />,
      );
      expect(screen.getByTestId(`cell-${name}`).textContent).toBe(expected);
      unmount();
    });
  });

  it("抽屉里同样:后四种形态一句自报声明都不出现", () => {
    const cases: Array<[string, unknown[]]> = [
      ["inferred", INFERRED], ["projected", PROJECTED],
      ["nothing", NOTHING], ["withheld", WITHHELD],
    ];
    cases.forEach(([name, records]) => {
      const { unmount } = render(
        <LanguageProvenanceDetail provenance={resolveLanguageProvenance(records)} testId={`detail-${name}`} />,
      );
      const rendered = screen.getByTestId(`detail-${name}`).textContent || "";
      SELF_REPORT_CLAIMS.forEach((claim) => expect(rendered).not.toContain(claim));
      unmount();
    });
  });

  it("门面禁内部术语:五句话里不出现 projected / origin / 置信度这类内部词", () => {
    [SELF_REPORTED, INFERRED, PROJECTED, NOTHING, WITHHELD]
      .map(resolveLanguageProvenance)
      .forEach((provenance) => {
        const wording = [
          provenance.title, provenance.originLabel, provenance.displayLabel,
          provenance.noteLabel, provenance.divergenceLabel, provenance.basisLabel,
        ].join(" ").toLowerCase();
        [
          "projected", "provenance", "origin", "self_reported", "inferred", "unknown",
          "置信", "哨兵", "检测器", "confidence", "sentinel", "langdetect", "门槛",
        ].forEach((banned) => expect(wording).not.toContain(banned.toLowerCase()));
      });
  });
});

// ── 红线 3:没过门槛的推断值不许计进「推断」那一格 ─────────────────────────────
describe("没过门槛的推断值不计入推断档", () => {
  it("旁挂一票、裁决判 unknown 时,归属就是 unknown —— 统计条按未知算", () => {
    const provenance = resolveLanguageProvenance(WITHHELD);
    expect(provenance.origin).toBe("unknown");
    expect(provenance.origin).not.toBe("inferred");
  });

  it("后端明写 inference_below_floor 时同样只说「试过但没把握」", () => {
    const provenance = resolveLanguageProvenance([
      {
        language: gateEvidence({
          values: [], origin: "unknown", inferred_values: [], inference_below_floor: "medium",
        }),
      },
    ]);
    expect(provenance.origin).toBe("unknown");
    expect(provenance.inferenceWithheld).toBe(true);
    expect(provenance.noteLabel).toBe("试着判断过，但把握不够，没当结论");
    // 门槛本身是后端的事,门面一个字都不提。
    expect(provenance.title).not.toContain("medium");
    expect(provenance.title).not.toContain("门槛");
  });

  it("裁决真判了推断时才算推断 —— 两者不许混", () => {
    const admitted = resolveLanguageProvenance(INFERRED);
    expect(admitted.origin).toBe("inferred");
    expect(admitted.inferenceWithheld).toBe(false);
  });
});

// ── 红线 1:没有裁决时一律不落「自报」 ─────────────────────────────────────────
describe("后端没给裁决时落未知 / 来源不明,绝不落自报", () => {
  const NO_VERDICT_RECORDS: Array<[string, Record<string, unknown>]> = [
    ["裸的 language 列", { language: "en" }],
    ["带来源串的行", { language: "en", language_source: "vkpi_kol_profiles.language" }],
    ["带 language_origin 记号的行", { language: "en", language_origin: "self_reported" }],
    ["旧形状的证据块(没有 origin / self_reported)", { language: { values: ["en"], source: "vkpi_kol_profiles.language" } }],
    ["facet 证据块的裸值", { facet_evidence: { language: { value: "en", source: "platform_profile" } } }],
    ["会话回放里只剩一个值", { content_language: "en" }],
  ];

  it.each(NO_VERDICT_RECORDS)("%s:落「来源不明」,不落「自报」", (_name, record) => {
    const provenance = resolveLanguageProvenance([record]);
    expect(provenance.hasServerVerdict).toBe(false);
    expect(provenance.origin).not.toBe("self_reported");
    expect(provenance.originLabel).not.toBe("自报");
    expect(provenance.selfReportedCodes).toEqual([]);
    SELF_REPORT_CLAIMS.forEach((claim) => expect(provenance.title).not.toContain(claim));
  });

  it("什么都没有时落「未知」,而且不声称我们试过", () => {
    const provenance = resolveLanguageProvenance([{}, { language: null }]);
    expect(provenance.origin).toBe("unknown");
    expect(provenance.inferenceWithheld).toBe(false);
    expect(provenance.noteLabel).toBe("我们这里没有语言信息");
  });

  it("降级路径上「推断」只由我们那一列给出,不靠猜来源串", () => {
    // 那一列**连同它的把握度**一起读:够门槛才是推断。
    expect(resolveLanguageProvenance([
      { language_inferred: "ko", language_inferred_confidence: "high" },
    ]).origin).toBe("inferred");
    // 来源串长得再像推断,也只是一句记号 —— 不是裁决,不作数。
    expect(resolveLanguageProvenance([{ language: "ko", language_source: "content_inference_v1" }]).origin)
      .toBe("projected");
  });

  // 第五种形态**不是裁决路径独有的**:没有裁决时读的是落库列,那就必须连
  // `language_inferred_confidence` 一起读,否则后端「没敢用」的一票会在这条路上被升格。
  it("降级路径上,把握度没过门槛的那一票走「未知」那一支,不挂「推断」角标", () => {
    const provenance = resolveLanguageProvenance([
      { language_inferred: "ko", language_inferred_confidence: "low" },
    ]);
    expect(provenance.origin).toBe("unknown");
    expect(provenance.inferenceWithheld).toBe(true);
    expect(provenance.inferredCodes).toEqual([]);

    render(<LanguageProvenanceCell provenance={provenance} testId="cell-withheld-degraded" />);
    expect(screen.getByTestId("cell-withheld-degraded").textContent).toBe("未知");

    render(<LanguageProvenanceDetail provenance={provenance} testId="detail-withheld-degraded" />);
    const rendered = screen.getByTestId("detail-withheld-degraded").textContent || "";
    expect(rendered).toContain("试着判断过，但把握不够，没当结论");
    expect(rendered).not.toContain("推断");
    expect(rendered).not.toContain("韩语");
  });
});

// ── 红线 2:抓取器原始载荷不是我们的列 ────────────────────────────────────────
describe("provider 原始载荷至多「来源不明」", () => {
  it("载荷里的 language 不算自报,也不算我们推断的", () => {
    const provenance = resolveLanguageProvenance({ local: [{}], provider: [{ language: "ja" }] });
    expect(provenance.origin).toBe("projected");
    expect(provenance.originLabel).toBe("来源不明");
    expect(provenance.codes).toEqual(["ja"]);
    expect(provenance.selfReportedCodes).toEqual([]);
    expect(provenance.inferredCodes).toEqual([]);
  });

  it("载荷里叫 language_inferred 的键也不算推断 —— 那是 provider 的列,不是我们的", () => {
    const provenance = resolveLanguageProvenance({ local: [{}], provider: [{ language_inferred: "ja" }] });
    expect(provenance.origin).toBe("projected");
    expect(provenance.origin).not.toBe("inferred");
  });

  it("载荷里带 origin 明牌也不当裁决读 —— 裁决只从我们自己的记录里读", () => {
    const provenance = resolveLanguageProvenance({
      local: [{}],
      provider: [{ language: gateEvidence({ values: ["ja"], origin: "self_reported", self_reported: true }) }],
    });
    expect(provenance.hasServerVerdict).toBe(false);
    expect(provenance.origin).toBe("projected");
    expect(provenance.selfReportedCodes).toEqual([]);
  });

  it("本地那一路说得出话时,载荷根本不参与", () => {
    const provenance = resolveLanguageProvenance({
      local: [{ language: gateEvidence({ values: ["en"], origin: "self_reported", self_reported: true }) }],
      provider: [{ language: "ja" }],
    });
    expect(provenance.origin).toBe("self_reported");
    expect(provenance.codes).toEqual(["en"]);
  });

  it("载荷里的占位词不许被抬成一个值", () => {
    const provenance = resolveLanguageProvenance({ local: [{}], provider: [{ language: "Unknown" }] });
    expect(provenance.origin).toBe("unknown");
    expect(provenance.codes).toEqual([]);
  });

  it("KOL 抽屉的取数口径把两路写死在一处", () => {
    const provenance = kolLanguageProvenance({}, [{ language: "ja" }, { language: "ko" }]);
    expect(provenance.origin).toBe("projected");
    expect(provenance.codes).toEqual(["ja"]);
    expect(provenance.selfReportedCodes).toEqual([]);
  });
});
