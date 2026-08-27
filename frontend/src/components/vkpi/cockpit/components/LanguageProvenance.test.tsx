import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { languageOriginCounts, languageOriginSummaryLabel, resolveLanguageProvenance } from "./LanguageProvenance";
import { LanguageProvenanceCell, LanguageProvenanceDetail } from "./LanguageProvenanceChip";
import { StrictQualifiedList } from "./SmartKolInputPanel.LocalQualifiedList";
import { localQualifiedSummary } from "./SmartKolInputPanel.LocalQualified";
import { SMART_KOL_LANGUAGE_OPTIONS } from "./SmartKolInputPanel.QualityFilters";

// 服务端 profile_recall_language_gate.language_gate_evidence() 的真实出参形状。
// 归属由它裁,门面只渲染 —— 所以本文件里凡是要看「档」的用例,一律从这个形状出发。
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

function strictProof(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schema: "smart_local_gate_evidence_v2",
    passed: true,
    account_quality: { passed: true }, followers: { passed: true }, activity: { passed: true },
    market: { passed: true }, language: { passed: true }, profile_type: { passed: true },
    platform: { passed: true }, relevance: { passed: true },
    ...overrides,
  };
}

function result(items: any[]): any {
  return {
    method: "vector_recall",
    query: {},
    ratio: { creator_quota: 30, reviewer_quota: 0, policy: "soft", mixed_policy: "dominant", dedupe: true },
    items,
    buckets: { creator: items, reviewer: [] },
    diagnostics: {},
  };
}

describe("裁决怎么说,门面就怎么显示", () => {
  it("裁决说自报:值照常显示，并且说得出是他自己填的", () => {
    const provenance = resolveLanguageProvenance([
      {
        language: gateEvidence({
          values: ["en"], origin: "self_reported", self_reported: true, self_reported_values: ["en"],
        }),
      },
      { language: "en" },
    ]);
    expect(provenance.origin).toBe("self_reported");
    expect(provenance.displayLabel).toBe("EN");
    expect(provenance.originLabel).toBe("自报");
    expect(provenance.basisLabel).toBe("");
    expect(provenance.title).toContain("自己填的");
    expect(provenance.hasServerVerdict).toBe(true);
  });

  it("裁决说推断:说出依据是个人简介", () => {
    const provenance = resolveLanguageProvenance([
      {
        language: gateEvidence({
          values: ["ja"], origin: "inferred", inferred: true, inferred_values: ["ja"],
          source: "vkpi_kol_pool.language_inferred",
          basis: "bio", evidence_fields: ["bio"],
        }),
      },
    ]);
    expect(provenance.origin).toBe("inferred");
    expect(provenance.originLabel).toBe("推断");
    expect(provenance.basisLabel).toBe("个人简介");
    expect(provenance.title).toContain("平台资料上没写语言");
    expect(provenance.title).toContain("个人简介");
  });

  it("裁决落在 facet 证据块上时同样照收，依据是作品标题", () => {
    const provenance = resolveLanguageProvenance([
      {
        facet_evidence: {
          language: gateEvidence({
            values: ["ko"], origin: "inferred", inferred: true, inferred_values: ["ko"],
            source: "provider_public_content_language_v1",
            evidence_fields: ["sample_title", "title"],
          }),
        },
      },
    ]);
    expect(provenance.origin).toBe("inferred");
    expect(provenance.basisLabel).toBe("作品标题");
  });

  it("简介与标题都用到时两样都说出来", () => {
    const provenance = resolveLanguageProvenance([
      {
        language_evidence: gateEvidence({
          values: ["de"], origin: "inferred", inferred: true, inferred_values: ["de"],
          evidence_fields: ["bio", "sample_title"],
        }),
      },
    ]);
    expect(provenance.origin).toBe("inferred");
    expect(provenance.basisLabel).toBe("个人简介和作品标题");
  });

  it("落库列 language_inferred 是我们那一列,没有裁决时也算得上推断", () => {
    const provenance = resolveLanguageProvenance([
      { inferred_language: "es", inferred_language_confidence: "high" },
    ]);
    expect(provenance.origin).toBe("inferred");
    expect(provenance.displayLabel).toBe("ES");
    expect(provenance.hasServerVerdict).toBe(false);
  });

  // 第五种形态在**没有裁决**这条路上也得成立:后端在同一行上判「未知(试过、没敢用)」的
  // 那一票,不许在 KOL 池 / 详情抽屉里被升格成「推断」。
  it("没有裁决时,把握度没过门槛的推断值算未知,不算推断,值也不上墙", () => {
    const provenance = resolveLanguageProvenance([
      {
        language: null,
        language_inferred: "ko",
        language_inferred_source: "video_titles",
        language_inferred_confidence: "low",
      },
    ]);
    expect(provenance.origin).toBe("unknown");
    expect(provenance.displayLabel).toBe("未知");
    expect(provenance.inferenceWithheld).toBe(true);
    expect(provenance.noteLabel).toBe("试着判断过，但把握不够，没当结论");
    expect(provenance.codes).toEqual([]);
    expect(provenance.inferredCodes).toEqual([]);
    expect(provenance.title).not.toContain("KO");
    expect(provenance.title).not.toContain("韩语");
  });

  it("没有裁决、连把握度都读不出来时同样不放行 —— 证不出达标就是没达标", () => {
    const provenance = resolveLanguageProvenance([
      { language: null, language_inferred: "ko", language_inferred_source: "video_titles" },
    ]);
    expect(provenance.origin).toBe("unknown");
    expect(provenance.inferenceWithheld).toBe(true);
    expect(provenance.codes).toEqual([]);
  });

  it("自报列有值时,门槛下的那一票不改变这一格显示什么", () => {
    const provenance = resolveLanguageProvenance([
      { language: "en", language_inferred: "ja", language_inferred_confidence: "low" },
    ]);
    expect(provenance.origin).toBe("projected");
    expect(provenance.displayLabel).toBe("EN");
    expect(provenance.inferredCodes).toEqual([]);
  });

  it("拿不到语言时显示「未知」，不留空；话只说到「我们这里没有」为止", () => {
    const provenance = resolveLanguageProvenance([{ language: { values: [], source: "unknown" } }, {}]);
    expect(provenance.origin).toBe("unknown");
    expect(provenance.displayLabel).toBe("未知");
    expect(provenance.title).toContain("我们这里没有");
    // 「推不出来」是一句我们**查不到**的事实声明:推断有没有跑过,这一档无从知道。
    // 只有服务端亲口说了它试过(旁挂了一票),才许说「试过」——见「没过门槛」那一组。
    expect(provenance.title).toContain("没有拿到");
    ["没有足够的文字", "推不出", "推断不出", "无法推断", "试着判断"].forEach((claim) => {
      expect(provenance.title).not.toContain(claim);
      expect(provenance.noteLabel).not.toContain(claim);
    });
  });

  it("多语言与地区后缀都归一到可读文案", () => {
    const provenance = resolveLanguageProvenance([
      { language: gateEvidence({ values: ["zh-CN", "en_US", "zh"], origin: "projected" }) },
    ]);
    expect(provenance.codes).toEqual(["zh", "en"]);
    expect(provenance.displayLabel).toBe("ZH/EN");
    expect(provenance.nameLabel).toBe("中文、英语");
  });

  it("平台存的是整词而不是代码时也不喊大写", () => {
    const provenance = resolveLanguageProvenance([{ language: "English" }]);
    expect(provenance.displayLabel).toBe("English");
    expect(provenance.nameLabel).toBe("English");
  });

  it("嵌套对象不会被当成语言代码", () => {
    const provenance = resolveLanguageProvenance([{ language: { values: [{ code: "en" }] } }]);
    expect(provenance.origin).toBe("unknown");
  });

  it("语言名表与筛选面板同一套口径", () => {
    SMART_KOL_LANGUAGE_OPTIONS.forEach((option) => {
      expect(resolveLanguageProvenance([{ language: option.value }]).nameLabel).toBe(option.label);
    });
  });
});

// ── 拆推导:没有裁决时,降级路径没有通向「自报」的出口 ─────────────────────────
//
// 这一组钉的是三轮复核都没根治的那句假话:门面手上有个值、又没有任何一份材料说得出
// 是谁填的,于是默认说成「他自己填的」。现在这条路被拆了 —— 有值说「来源不明」,
// 没值说「未知」,两个出口,一个都不通向「自报」。
describe("没有裁决时绝不落「自报」", () => {
  it("裸的 language 列:说得出「资料里有」,说不出「他自己填的」", () => {
    const provenance = resolveLanguageProvenance([{ language: "en" }]);
    expect(provenance.hasServerVerdict).toBe(false);
    expect(provenance.origin).toBe("projected");
    expect(provenance.originLabel).toBe("来源不明");
    expect(provenance.displayLabel).toBe("EN");
    expect(provenance.selfReportedCodes).toEqual([]);
    expect(provenance.divergenceLabel).toBe("");
    expect(provenance.title).not.toContain("他在平台资料里自己填的");
  });

  it("行上的 source / origin 这类记号不是裁决,读了就是门面自己在判", () => {
    const provenance = resolveLanguageProvenance([
      { language: "en", source: "platform_discovery_strict", origin: "content_inference_v1" },
    ]);
    expect(provenance.origin).toBe("projected");
    expect(provenance.hasServerVerdict).toBe(false);
    expect(provenance.title).not.toContain("他在平台资料里自己填的");
  });

  it("自报列与推断列同时有值、又没有裁决:显示自报列那个值,但不认领那句声明", () => {
    const provenance = resolveLanguageProvenance([{ language: "en", language_inferred: "ja" }]);
    expect(provenance.origin).toBe("projected");
    expect(provenance.displayLabel).toBe("EN");
    expect(provenance.selfReportedCodes).toEqual([]);
    // 「他自己填的是……」是替他转述一句话,没人说过就一个字都不许出现。
    expect(provenance.divergenceLabel).toBe("");
    expect(provenance.title).not.toContain("他自己填的是");
  });

  it("language_inferred 当布尔标记用时不当语言值读,也不因此判成推断", () => {
    const provenance = resolveLanguageProvenance([{ language: "en", language_inferred: true }]);
    expect(provenance.origin).toBe("projected");
    expect(provenance.displayLabel).toBe("EN");
  });

  it("筛选面板里能勾的语言,没有裁决时一个都不许被说成「自报」", () => {
    SMART_KOL_LANGUAGE_OPTIONS.forEach((option) => {
      expect(resolveLanguageProvenance([{ language: option.value }]).origin).not.toBe("self_reported");
    });
  });
});

describe("对齐服务端裁决的四档明牌", () => {
  it("origin=self_reported 照实显示为自报", () => {
    const provenance = resolveLanguageProvenance([
      {
        language: gateEvidence({
          values: ["en"], origin: "self_reported", self_reported: true, self_reported_values: ["en"],
        }),
      },
    ]);
    expect(provenance.origin).toBe("self_reported");
    expect(provenance.divergenceLabel).toBe("");
  });

  it("origin=inferred 时带出「推断」与依据字段名", () => {
    const provenance = resolveLanguageProvenance([
      {
        language: gateEvidence({
          values: ["ja"], origin: "inferred", inferred: true, inferred_values: ["ja"],
          source: "vkpi_kol_pool.language_inferred",
          inference_method: "kol_content_langdetect_vote_v1",
          inference_basis: "bio+video_titles",
          basis: "bio+video_titles",
          evidence_fields: ["bio", "video_titles"],
        }),
      },
    ]);
    expect(provenance.origin).toBe("inferred");
    expect(provenance.basisLabel).toBe("个人简介和作品标题");
    expect(provenance.inferredCodes).toEqual(["ja"]);
  });

  it("origin=unknown 且没有值时是「未知」", () => {
    const provenance = resolveLanguageProvenance([{ language: gateEvidence() }]);
    expect(provenance.origin).toBe("unknown");
    expect(provenance.displayLabel).toBe("未知");
  });

  it("他填的和我们推断的对不上时如实说出分歧", () => {
    const provenance = resolveLanguageProvenance([
      {
        language: gateEvidence({
          values: ["zh"], origin: "self_reported", self_reported: true,
          self_reported_values: ["zh"], inferred_values: ["en"],
        }),
      },
    ]);
    expect(provenance.origin).toBe("self_reported");
    expect(provenance.divergenceLabel).toBe("他自己填的是中文，照他发的东西推断出来的是英语。");
    expect(provenance.title).toContain("他自己填的是中文");
  });

  it("池行的落库列:language_inferred 是值、language_inferred_source 是依据", () => {
    const provenance = resolveLanguageProvenance([
      {
        language: null,
        language_inferred: "ko",
        language_inferred_source: "video_titles",
        language_inferred_method: "kol_content_langdetect_vote_v1",
        language_inferred_confidence: "high",
      },
    ]);
    expect(provenance.origin).toBe("inferred");
    expect(provenance.displayLabel).toBe("KO");
    expect(provenance.basisLabel).toBe("作品标题");
    // 门面禁术语:版本号 / 置信度一律不上界面。
    expect(provenance.title).not.toContain("langdetect");
    expect(provenance.title).not.toContain("置信");
  });
});

describe("language provenance 统计条", () => {
  const verdict = (overrides: Record<string, unknown>) =>
    resolveLanguageProvenance([{ language: gateEvidence(overrides) }]);

  it("全是自报时不占位，出现推断或未知才显示", () => {
    const selfOnly = [verdict({ values: ["en"], origin: "self_reported", self_reported: true })];
    expect(languageOriginSummaryLabel(languageOriginCounts(selfOnly))).toBe("");
    const mixed = [
      verdict({ values: ["en"], origin: "self_reported", self_reported: true }),
      verdict({ values: ["ja"], origin: "inferred", inferred: true, inferred_values: ["ja"] }),
      verdict({}),
    ];
    expect(languageOriginCounts(mixed)).toEqual({ selfReported: 1, inferred: 1, projected: 0, unknown: 1 });
    expect(languageOriginSummaryLabel(languageOriginCounts(mixed))).toBe("语言 · 自报 1 · 推断 1 · 未知 1");
  });

  it("「来源不明」这一档也要数进去 —— 少列一档，那几个人就被顺手当成自报了", () => {
    const four = [
      verdict({ values: ["en"], origin: "self_reported", self_reported: true }),
      verdict({ values: ["ja"], origin: "inferred", inferred: true, inferred_values: ["ja"] }),
      verdict({ values: ["ko"], origin: "projected", projected_values: ["ko"] }),
      verdict({}),
    ];
    const counts = languageOriginCounts(four);
    expect(counts).toEqual({ selfReported: 1, inferred: 1, projected: 1, unknown: 1 });
    // 四个数字加起来等于人数 —— 这一栏不许有「没被数进去的人」。
    expect(counts.selfReported + counts.inferred + counts.projected + counts.unknown).toBe(four.length);
    expect(languageOriginSummaryLabel(counts)).toBe("语言 · 自报 1 · 推断 1 · 来源不明 1 · 未知 1");
  });
});

describe("language provenance 门面件", () => {
  it("自报不挂角标，推断挂「推断」角标", () => {
    const { rerender } = render(
      <LanguageProvenanceCell
        provenance={resolveLanguageProvenance([
          { language: gateEvidence({ values: ["en"], origin: "self_reported", self_reported: true }) },
        ])}
        testId="cell"
      />,
    );
    expect(screen.getByTestId("cell").textContent).toBe("EN");
    rerender(
      <LanguageProvenanceCell
        provenance={resolveLanguageProvenance([
          { language: gateEvidence({ values: ["en"], origin: "inferred", inferred: true, inferred_values: ["en"] }) },
        ])}
        testId="cell"
      />,
    );
    expect(screen.getByTestId("cell").textContent).toBe("EN推断");
  });

  it("详情页把依据直接写出来", () => {
    render(
      <LanguageProvenanceDetail
        provenance={resolveLanguageProvenance([
          {
            language: gateEvidence({
              values: ["ja"], origin: "inferred", inferred: true, inferred_values: ["ja"],
              evidence_fields: ["bio"],
            }),
          },
        ])}
        testId="detail"
      />,
    );
    expect(screen.getByTestId("detail").textContent).toContain("日语");
    expect(screen.getByTestId("detail").textContent).toContain("推断 · 依据个人简介");
  });

  it("详情页判不出来时显示「未知」而不是「—」，并且不替他说他填了什么", () => {
    render(<LanguageProvenanceDetail provenance={resolveLanguageProvenance([{}])} testId="detail" />);
    const rendered = screen.getByTestId("detail").textContent || "";
    expect(rendered).toContain("未知");
    expect(rendered).toContain("我们这里没有语言信息");
    expect(rendered).not.toContain("他自己填的");
    // 详情页那行小字同样不许替系统声称「我们试过、推不出来」。
    ["没有足够的文字", "推不出", "推断不出"].forEach((claim) => expect(rendered).not.toContain(claim));
  });
});

// ── H4:平台把「没填」写成占位词时,门面绝不许把它当成一句自报声明 ──────────────
const PLACEHOLDER_VALUES = [
  "Unknown", "unknown", "UNKNOWN", "N/A", "n/a", "None", "null", "NA",
  "unspecified", "not_specified", "NOT SPECIFIED", "not-specified",
  "undetermined", "unavailable", "no data", "other", "auto", "default", "?",
  "und", "zxx", "mis", "mul", "未知", "无", "未填写", "不详",
  // 光剩一根横杠的空位占位符。"—" 是长破折号「—」——**这块门面自己原来用的那个**。
  "-", "—", "–", "‒", "―", "−", "－", "--", "___",
];

describe("占位词不是语言，更不是一句自报声明", () => {
  it.each(PLACEHOLDER_VALUES)("平台把语言写成「%s」时判为未知，不冒充自报", (value) => {
    const provenance = resolveLanguageProvenance([{ language: value }]);
    expect(provenance.origin).toBe("unknown");
    expect(provenance.displayLabel).toBe("未知");
    expect(provenance.originLabel).toBe("未知");
    expect(provenance.codes).toEqual([]);
    expect(provenance.selfReportedCodes).toEqual([]);
    expect(provenance.nameLabel).toBe("");
    expect(provenance.title).not.toContain("自己填的");
  });

  it("占位词带地区后缀（Unknown-US）切完仍是占位词，照样判未知", () => {
    const provenance = resolveLanguageProvenance([{ language: "Unknown-US" }]);
    expect(provenance.origin).toBe("unknown");
    expect(provenance.displayLabel).toBe("未知");
  });

  it("裁决把占位词当自报值传上来时，门面不跟着认", () => {
    const provenance = resolveLanguageProvenance([
      {
        language: gateEvidence({
          values: ["unknown"], origin: "self_reported", self_reported: true,
          self_reported_values: ["unknown"],
        }),
      },
    ]);
    expect(provenance.origin).toBe("unknown");
    expect(provenance.displayLabel).toBe("未知");
    expect(provenance.title).not.toContain("自己填的");
  });

  it("占位词与真值混在一起时只留真值，占位词不上墙", () => {
    const provenance = resolveLanguageProvenance([
      {
        language: gateEvidence({
          values: ["Unknown", "en", "N/A"], origin: "self_reported", self_reported: true,
          self_reported_values: ["Unknown", "en", "N/A"],
        }),
      },
    ]);
    expect(provenance.origin).toBe("self_reported");
    expect(provenance.codes).toEqual(["en"]);
    expect(provenance.displayLabel).toBe("EN");
  });

  it("真语言不会被误当占位词吃掉（挪威语 no / 荷兰语 nl）", () => {
    expect(resolveLanguageProvenance([{ language: "no" }]).displayLabel).toBe("NO");
    expect(resolveLanguageProvenance([{ language: "no" }]).origin).toBe("projected");
    expect(resolveLanguageProvenance([{ language: "nl" }]).nameLabel).toBe("荷兰语");
  });

  it("筛选面板里能勾的语言，一个都不许被当成占位词", () => {
    SMART_KOL_LANGUAGE_OPTIONS.forEach((option) => {
      expect(resolveLanguageProvenance([{ language: option.value }]).origin).not.toBe("unknown");
    });
  });
});

describe("没有自报值就不许说「他自己填的是……」", () => {
  it.each(PLACEHOLDER_VALUES)("自报格是「%s」而我们推断出韩语时，只说推断，不转述他的话", (value) => {
    const provenance = resolveLanguageProvenance([
      {
        language: value,
        language_inferred: "ko",
        language_inferred_source: "video_titles",
        language_inferred_confidence: "high",
      },
    ]);
    expect(provenance.origin).toBe("inferred");
    expect(provenance.displayLabel).toBe("KO");
    expect(provenance.basisLabel).toBe("作品标题");
    expect(provenance.selfReportedCodes).toEqual([]);
    expect(provenance.divergenceLabel).toBe("");
    expect(provenance.title).not.toContain("他自己填的是");
  });

  it("裁决里 self_reported_values 是占位词时，分歧那句话也不许出现", () => {
    const provenance = resolveLanguageProvenance([
      {
        language: gateEvidence({
          values: ["ko"], origin: "inferred", inferred: true,
          self_reported_values: ["Unknown"], inferred_values: ["ko"],
          source: "vkpi_kol_pool.language_inferred",
          basis: "video_titles", evidence_fields: ["video_titles"],
        }),
      },
    ]);
    expect(provenance.origin).toBe("inferred");
    expect(provenance.divergenceLabel).toBe("");
    expect(provenance.title).not.toContain("他自己填的是");
  });

  it("判不出的那一档，分歧那句话一律清空", () => {
    const provenance = resolveLanguageProvenance([
      { language: gateEvidence({ self_reported_values: ["Unknown"], inferred_values: ["N/A"] }) },
    ]);
    expect(provenance.origin).toBe("unknown");
    expect(provenance.divergenceLabel).toBe("");
  });

  it("裁决说他真填了、且与我们推断的不一样时，照旧如实说出分歧", () => {
    const provenance = resolveLanguageProvenance([
      {
        language: gateEvidence({
          values: ["en"], origin: "self_reported", self_reported: true,
          self_reported_values: ["en"], inferred_values: ["ja"],
        }),
      },
    ]);
    expect(provenance.divergenceLabel).toBe("他自己填的是英语，照他发的东西推断出来的是日语。");
  });

  it("门面件渲染:占位词进来时抽屉里绝不出现「他自己填的」字样", () => {
    const { rerender } = render(
      <LanguageProvenanceDetail
        provenance={resolveLanguageProvenance([{ language: "Unknown" }])}
        testId="detail"
      />,
    );
    let rendered = screen.getByTestId("detail").textContent || "";
    expect(rendered).toContain("未知");
    expect(rendered).not.toContain("他自己填的");
    expect(rendered).not.toContain("Unknown");

    rerender(
      <LanguageProvenanceDetail
        provenance={resolveLanguageProvenance([
          {
            language: "Unknown",
            language_inferred: "ko",
            language_inferred_source: "video_titles",
            language_inferred_confidence: "high",
          },
        ])}
        testId="detail"
      />,
    );
    rendered = screen.getByTestId("detail").textContent || "";
    expect(rendered).toContain("韩语");
    expect(rendered).toContain("推断 · 依据作品标题");
    expect(rendered).not.toContain("他自己填的");
    expect(rendered).not.toContain("Unknown");
  });

  it("列表格渲染:占位词进来时显示「未知」，不显示 Unknown，也不挂角标", () => {
    render(
      <LanguageProvenanceCell
        provenance={resolveLanguageProvenance([{ language: "Unknown" }])}
        testId="cell-placeholder"
      />,
    );
    expect(screen.getByTestId("cell-placeholder").textContent).toBe("未知");
  });

  it("门面禁内部术语:各档的文案里都不出现检测器 / 置信度 / 哨兵之类的说法", () => {
    const samples = [
      resolveLanguageProvenance([{ language: "en" }]),
      resolveLanguageProvenance([
        { language: "Unknown", language_inferred: "ko", language_inferred_source: "video_titles", language_inferred_confidence: "low" },
      ]),
      resolveLanguageProvenance([{ language: "N/A" }]),
    ];
    samples.forEach((provenance) => {
      const wording = [
        provenance.title, provenance.originLabel, provenance.displayLabel,
        provenance.divergenceLabel, provenance.noteLabel,
      ].join(" ");
      ["langdetect", "置信", "哨兵", "检测器", "sentinel", "confidence", "detector"].forEach((banned) => {
        expect(wording.toLowerCase()).not.toContain(banned.toLowerCase());
      });
    });
  });
});

describe("语言标注与活跃度未知标注互不打架", () => {
  const rowsResult = result([
    {
      kol_pool_id: 11,
      handle: "self_reported",
      platform: "youtube",
      followers: 12000,
      qualification_evidence: strictProof({
        language: {
          passed: true, values: ["en"], origin: "self_reported", self_reported: true,
          self_reported_values: ["en"], source: "vkpi_kol_profiles.language",
        },
      }),
      source_fields: { server_rank: 1 },
    },
    {
      kol_pool_id: 12,
      handle: "inferred_and_activity_unknown",
      platform: "youtube",
      followers: 8000,
      qualification_evidence: strictProof({
        passed: false,
        deferred: true,
        deferred_reason: "latest_video_unknown",
        language: {
          passed: true, values: ["ja"], origin: "inferred", inferred: true, inferred_values: ["ja"],
          source: "provider_public_content_language_v1", evidence_fields: ["bio"],
        },
        activity: { passed: false, known: false, deferred: true, status: "activity_unknown_pending_fetch" },
      }),
      source_fields: { server_rank: 2 },
    },
    {
      kol_pool_id: 13,
      handle: "no_language_at_all",
      platform: "youtube",
      followers: 5000,
      qualification_evidence: strictProof({
        language: { passed: true, values: [], origin: "unknown", self_reported: false, source: "" },
      }),
      source_fields: { server_rank: 3 },
    },
    {
      // 两个「未知」撞在同一行:语言是占位词判不出,活跃度也从没抓到过。
      kol_pool_id: 14,
      handle: "placeholder_language_and_activity_unknown",
      platform: "youtube",
      followers: 6000,
      language: "Unknown",
      qualification_evidence: strictProof({
        passed: false,
        deferred: true,
        deferred_reason: "latest_video_unknown",
        language: {
          passed: true, values: ["Unknown"], origin: "self_reported", self_reported: true,
          self_reported_values: ["Unknown"], source: "vkpi_kol_profiles.language",
        },
        activity: { passed: false, known: false, deferred: true, status: "activity_unknown_pending_fetch" },
      }),
      source_fields: { server_rank: 4 },
    },
  ]);

  it("同一行的两种标注落在不同格子，文案各说各的", () => {
    const summary = localQualifiedSummary(rowsResult);
    render(<StrictQualifiedList summary={summary} />);
    const inferredRow = summary.rows.find((row) => row.item.kol_pool_id === 12)!;
    expect(inferredRow.activityUnknown).toBe(true);
    expect(inferredRow.language.origin).toBe("inferred");
    expect(screen.getAllByText("从没抓到过").length).toBe(2);
    expect(screen.getByTestId(`local-language-${inferredRow.identity}`).textContent).toBe("JA推断");
  });

  it("语言未知与活跃度未知同时落在一行时，各占各的格子、互不挤压", () => {
    const summary = localQualifiedSummary(rowsResult);
    render(<StrictQualifiedList summary={summary} />);
    const doubleUnknown = summary.rows.find((row) => row.item.kol_pool_id === 14)!;
    expect(doubleUnknown.activityUnknown).toBe(true);
    expect(doubleUnknown.language.origin).toBe("unknown");
    const languageCell = screen.getByTestId(`local-language-${doubleUnknown.identity}`);
    expect(languageCell.textContent).toBe("未知");
    expect(languageCell.textContent).not.toContain("Unknown");
    expect(languageCell.getAttribute("title") || "").not.toContain("自己填的");
    expect(languageCell.textContent).not.toContain("从没抓到过");
  });

  it("语言判不出来的行显示「未知」，不是空格子", () => {
    const summary = localQualifiedSummary(rowsResult);
    render(<StrictQualifiedList summary={summary} />);
    const blankRow = summary.rows.find((row) => row.item.kol_pool_id === 13)!;
    expect(screen.getByTestId(`local-language-${blankRow.identity}`).textContent).toBe("未知");
  });

  it("统计条如实报出各档人数", () => {
    const summary = localQualifiedSummary(rowsResult);
    render(<StrictQualifiedList summary={summary} />);
    expect(screen.getByTestId("local-language-origin-stat").textContent).toBe("语言 · 自报 1 · 推断 1 · 未知 2");
  });
});
