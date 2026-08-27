import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { languageOriginCounts, languageOriginSummaryLabel, resolveLanguageProvenance } from "./LanguageProvenance";
import { LanguageProvenanceCell, LanguageProvenanceDetail } from "./LanguageProvenanceChip";
import { StrictQualifiedList } from "./SmartKolInputPanel.LocalQualifiedList";
import { localQualifiedSummary } from "./SmartKolInputPanel.LocalQualified";
import { SMART_KOL_LANGUAGE_OPTIONS } from "./SmartKolInputPanel.QualityFilters";

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

describe("language provenance: 自报 / 推断 / 未知 三态可追", () => {
  it("平台自报值照常显示，并且说得出是他自己填的", () => {
    const provenance = resolveLanguageProvenance([
      { language: { values: ["en"], source: "vkpi_kol_profiles.language", passed: true } },
      { language: "en" },
    ]);
    expect(provenance.origin).toBe("self_reported");
    expect(provenance.displayLabel).toBe("EN");
    expect(provenance.originLabel).toBe("自报");
    expect(provenance.basisLabel).toBe("");
    expect(provenance.title).toContain("自己填的");
  });

  it("来源串带推断口径时判为推断，并说出依据是个人简介", () => {
    const provenance = resolveLanguageProvenance([
      { language: { values: ["ja"], source: "content_inference_v1", evidence_fields: ["bio"] } },
    ]);
    expect(provenance.origin).toBe("inferred");
    expect(provenance.originLabel).toBe("推断");
    expect(provenance.basisLabel).toBe("个人简介");
    expect(provenance.title).toContain("平台资料上没写语言");
    expect(provenance.title).toContain("个人简介");
  });

  it("既有的公开内容检出口径同样判为推断，依据是作品标题", () => {
    const provenance = resolveLanguageProvenance([
      {
        facet_evidence: {
          language: {
            value: "ko",
            source: "provider_public_content_language_v1",
            evidence_fields: ["sample_title", "title"],
          },
        },
      },
    ]);
    expect(provenance.origin).toBe("inferred");
    expect(provenance.basisLabel).toBe("作品标题");
  });

  it("简介与标题都用到时两样都说出来", () => {
    const provenance = resolveLanguageProvenance([
      { language_source: "inferred_from_public_text", language: "de", language_evidence_fields: ["bio", "sample_title"] },
    ]);
    expect(provenance.origin).toBe("inferred");
    expect(provenance.basisLabel).toBe("个人简介和作品标题");
  });

  it("布尔标记也算数：inferred=true 时绝不冒充自报", () => {
    const provenance = resolveLanguageProvenance([
      { language: { values: ["fr"], source: "vkpi_kol_profiles.language", inferred: true } },
    ]);
    expect(provenance.origin).toBe("inferred");
  });

  it("只在推断形状的键上有值时判为推断", () => {
    const provenance = resolveLanguageProvenance([{ inferred_language: "es" }]);
    expect(provenance.origin).toBe("inferred");
    expect(provenance.displayLabel).toBe("ES");
  });

  it("拿不到语言时显示「未知」，不留空；话只说到「我们这里没有」为止", () => {
    const provenance = resolveLanguageProvenance([{ language: { values: [], source: "unknown" } }, {}]);
    expect(provenance.origin).toBe("unknown");
    expect(provenance.displayLabel).toBe("未知");
    expect(provenance.title).toContain("我们这里没有");
    // 「推不出来」是一句我们**查不到**的事实声明:推断列有没有被读到、推断有没有跑过,
    // 门面无从知道。不知道的事就不许写在墙上。
    // 说得出口的只有「我们这里没有拿到」这种关于收到了什么的陈述。
    expect(provenance.title).toContain("没有拿到");
    ["没有足够的文字", "推不出", "推断不出", "无法推断", "试过"].forEach((claim) => {
      expect(provenance.title).not.toContain(claim);
      expect(provenance.noteLabel).not.toContain(claim);
    });
  });

  it("条目自己的 source 是「这条结果哪来的」，不许当语言来源读", () => {
    const provenance = resolveLanguageProvenance([
      { language: "en", source: "platform_discovery_strict", origin: "content_inference_v1" },
    ]);
    expect(provenance.origin).toBe("self_reported");
  });

  it("多语言与地区后缀都归一到可读文案", () => {
    const provenance = resolveLanguageProvenance([{ language: { values: ["zh-CN", "en_US", "zh"] } }]);
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

// 服务端 profile_recall_language_gate.language_gate_evidence() 的真实出参形状。
function gateEvidence(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    values: [], targets: [], filter_requested: false, invalid_targets: [], passed: true,
    source: "vkpi_kol_profiles.language",
    origin: "unknown",
    inferred: false,
    self_reported_values: [],
    inferred_values: [],
    ...overrides,
  };
}

describe("对齐服务端硬闸给的三态明牌", () => {
  it("origin=self_reported 照实显示为自报", () => {
    const provenance = resolveLanguageProvenance([
      {
        language: gateEvidence({
          values: ["en"], origin: "self_reported", self_reported_values: ["en"],
          source: "vkpi_kol_profiles.language",
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
          values: ["zh"], origin: "self_reported",
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

  it("池行有自报值时,推断列不顶替它", () => {
    const provenance = resolveLanguageProvenance([{ language: "en", language_inferred: "ja" }]);
    expect(provenance.origin).toBe("self_reported");
    expect(provenance.displayLabel).toBe("EN");
    expect(provenance.divergenceLabel).toBe("他自己填的是英语，照他发的东西推断出来的是日语。");
  });

  it("language_inferred 当布尔标记用时也认，不当语言代码读", () => {
    const provenance = resolveLanguageProvenance([{ language: "en", language_inferred: true }]);
    expect(provenance.origin).toBe("inferred");
    expect(provenance.displayLabel).toBe("EN");
  });
});

describe("language provenance 统计条", () => {
  it("全是自报时不占位，出现推断或未知才显示", () => {
    const selfOnly = [resolveLanguageProvenance([{ language: "en" }])];
    expect(languageOriginSummaryLabel(languageOriginCounts(selfOnly))).toBe("");
    const mixed = [
      resolveLanguageProvenance([{ language: "en" }]),
      resolveLanguageProvenance([{ inferred_language: "ja" }]),
      resolveLanguageProvenance([{}]),
    ];
    expect(languageOriginCounts(mixed)).toEqual({ selfReported: 1, inferred: 1, unknown: 1 });
    expect(languageOriginSummaryLabel(languageOriginCounts(mixed))).toBe("语言 · 自报 1 · 推断 1 · 未知 1");
  });
});

describe("language provenance 门面件", () => {
  it("自报不挂角标，推断挂「推断」角标", () => {
    const { rerender } = render(
      <LanguageProvenanceCell provenance={resolveLanguageProvenance([{ language: "en" }])} testId="cell" />,
    );
    expect(screen.getByTestId("cell").textContent).toBe("EN");
    rerender(
      <LanguageProvenanceCell
        provenance={resolveLanguageProvenance([{ language_source: "content_inference_v1", language: "en" }])}
        testId="cell"
      />,
    );
    expect(screen.getByTestId("cell").textContent).toBe("EN推断");
  });

  it("详情页把依据直接写出来", () => {
    render(
      <LanguageProvenanceDetail
        provenance={resolveLanguageProvenance([
          { language_source: "content_inference_v1", language: "ja", language_evidence_fields: ["bio"] },
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
//
// 这一组钉的是本功能的立身之本:自报 / 推断 / 未知 三态泾渭分明。
// 判不出就是「未知」——不显示任何具体值,不标「自报」,也不拼出「他自己填的是……」。
const PLACEHOLDER_VALUES = [
  "Unknown", "unknown", "UNKNOWN", "N/A", "n/a", "None", "null", "NA",
  "unspecified", "not_specified", "NOT SPECIFIED", "not-specified",
  "undetermined", "unavailable", "no data", "other", "auto", "default", "?",
  "und", "zxx", "mis", "mul", "未知", "无", "未填写", "不详",
  // 光剩一根横杠的空位占位符。"\u2014" 是长破折号「—」——**这块门面自己原来用的那个**。
  "-", "\u2014", "\u2013", "\u2012", "\u2015", "\u2212", "\uFF0D", "--", "___",
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

  it("服务端硬闸把占位词当自报值传上来时，门面不跟着认", () => {
    const provenance = resolveLanguageProvenance([
      {
        language: gateEvidence({
          values: ["unknown"], origin: "self_reported", self_reported_values: ["unknown"],
          source: "vkpi_kol_profiles.language",
        }),
      },
    ]);
    expect(provenance.origin).toBe("unknown");
    expect(provenance.displayLabel).toBe("未知");
    expect(provenance.title).not.toContain("自己填的");
  });

  it("占位词与真值混在一起时只留真值，占位词不上墙", () => {
    const provenance = resolveLanguageProvenance([{ language: { values: ["Unknown", "en", "N/A"] } }]);
    expect(provenance.origin).toBe("self_reported");
    expect(provenance.codes).toEqual(["en"]);
    expect(provenance.displayLabel).toBe("EN");
  });

  it("真语言不会被误当占位词吃掉（挪威语 no / 荷兰语 nl）", () => {
    expect(resolveLanguageProvenance([{ language: "no" }]).origin).toBe("self_reported");
    expect(resolveLanguageProvenance([{ language: "no" }]).displayLabel).toBe("NO");
    expect(resolveLanguageProvenance([{ language: "nl" }]).nameLabel).toBe("荷兰语");
  });

  it("筛选面板里能勾的语言，一个都不许被当成占位词", () => {
    SMART_KOL_LANGUAGE_OPTIONS.forEach((option) => {
      expect(resolveLanguageProvenance([{ language: option.value }]).origin).toBe("self_reported");
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

  it("硬闸证据里 self_reported_values 是占位词时，分歧那句话也不许出现", () => {
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

  it("他真填了值、且与我们推断的不一样时，照旧如实说出分歧", () => {
    const provenance = resolveLanguageProvenance([{ language: "en", language_inferred: "ja" }]);
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
          { language: "Unknown", language_inferred: "ko", language_inferred_source: "video_titles" },
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

  it("门面禁内部术语:三态的文案里都不出现检测器 / 置信度 / 哨兵之类的说法", () => {
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
      qualification_evidence: strictProof({ language: { passed: true, values: ["en"], source: "vkpi_kol_profiles.language" } }),
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
        language: { passed: true, values: ["ja"], source: "content_inference_v1", evidence_fields: ["bio"] },
        activity: { passed: false, known: false, deferred: true, status: "activity_unknown_pending_fetch" },
      }),
      source_fields: { server_rank: 2 },
    },
    {
      kol_pool_id: 13,
      handle: "no_language_at_all",
      platform: "youtube",
      followers: 5000,
      qualification_evidence: strictProof({ language: { passed: true, values: [], source: "unknown" } }),
      source_fields: { server_rank: 3 },
    },
    {
      // 两个「未知」撞在同一行:语言是占位词判不出,活跃度也从没抓到过。
      // 两句话必须各占各的格子,谁也不许替谁把话说满。
      kol_pool_id: 14,
      handle: "placeholder_language_and_activity_unknown",
      platform: "youtube",
      followers: 6000,
      language: "Unknown",
      qualification_evidence: strictProof({
        passed: false,
        deferred: true,
        deferred_reason: "latest_video_unknown",
        language: { passed: true, values: ["Unknown"], origin: "self_reported", self_reported_values: ["Unknown"], source: "vkpi_kol_profiles.language" },
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
    // 活跃度未知说的是「从没抓到过」,语言说的是「推断」,两句话不互相顶替。
    expect(screen.getAllByText("从没抓到过").length).toBe(2);
    expect(screen.getByTestId(`local-language-${inferredRow.identity}`).textContent).toBe("JA推断");
  });

  it("语言未知与活跃度未知同时落在一行时，各占各的格子、互不挤压", () => {
    const summary = localQualifiedSummary(rowsResult);
    render(<StrictQualifiedList summary={summary} />);
    const doubleUnknown = summary.rows.find((row) => row.item.kol_pool_id === 14)!;
    // 活跃度那一格照旧说「从没抓到过」;语言那一格独立地说「未知」。
    expect(doubleUnknown.activityUnknown).toBe(true);
    expect(doubleUnknown.language.origin).toBe("unknown");
    const languageCell = screen.getByTestId(`local-language-${doubleUnknown.identity}`);
    expect(languageCell.textContent).toBe("未知");
    // 占位词既不上墙,也没被误标成「自报」。
    expect(languageCell.textContent).not.toContain("Unknown");
    expect(languageCell.getAttribute("title") || "").not.toContain("自己填的");
    // 「从没抓到过」是活跃度那一格的话,没有跑到语言格里来。
    expect(languageCell.textContent).not.toContain("从没抓到过");
  });

  it("语言判不出来的行显示「未知」，不是空格子", () => {
    const summary = localQualifiedSummary(rowsResult);
    render(<StrictQualifiedList summary={summary} />);
    const blankRow = summary.rows.find((row) => row.item.kol_pool_id === 13)!;
    expect(screen.getByTestId(`local-language-${blankRow.identity}`).textContent).toBe("未知");
  });

  it("统计条如实报出三态人数", () => {
    const summary = localQualifiedSummary(rowsResult);
    render(<StrictQualifiedList summary={summary} />);
    expect(screen.getByTestId("local-language-origin-stat").textContent).toBe("语言 · 自报 1 · 推断 1 · 未知 2");
  });
});
