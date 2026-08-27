import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { resolveLanguageProvenance } from "./LanguageProvenance";
import { LanguageProvenanceCell, LanguageProvenanceDetail } from "./LanguageProvenanceChip";

// 服务端 profile_recall_language_gate.language_gate_evidence() 的真实出参形状。
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

// ── 裁决与手上的原料对不上时:**裁决说了算** ─────────────────────────────────
//
// 前三轮的门面把自己当「第二道防线」:拿手上的原料去复核服务端的明牌,不一致就自己裁。
// 那条路每补一层就多一处能说错话的地方,而且它自己也会错 —— 它凭的同样是原料。
//
// 本波把它拆了。规矩变成一句话:**裁决在,就照裁决渲染,不拿原料翻案。**
// 唯一还留着的一道守卫不是「复核」,而是读裁决自己的第二个字段:`origin` 说自报、
// 同一块里的布尔明牌 `self_reported` 却写着 false —— 裁决自相矛盾时按更保守的那句走。
describe("裁决是唯一真源:门面不再拿原料翻案", () => {
  const SELF_REPORT_CLAIMS = ["他在平台资料里自己填的", "他自己填的是"];
  function expectNoSelfReportClaim(provenance: {
    title: string; noteLabel: string; divergenceLabel: string;
  }): void {
    SELF_REPORT_CLAIMS.forEach((claim) => {
      expect(provenance.title).not.toContain(claim);
      expect(provenance.noteLabel).not.toContain(claim);
      expect(provenance.divergenceLabel).not.toContain(claim);
    });
    expect(provenance.noteLabel).not.toBe("他自己填的");
  }

  it("裁决说自报、旁边还挂着别的推断值 —— 照裁决渲染,不自己改判", () => {
    const provenance = resolveLanguageProvenance([
      {
        language: gateEvidence({
          values: ["en"], origin: "self_reported", self_reported: true, inferred: true,
          self_reported_values: ["en"], inferred_values: ["en"],
        }),
      },
    ]);
    // 老门面在这里会因为 `inferred: true` 这个旁证与明牌顶牛而落「未知」,值也藏起来。
    // 那是拿原料给裁决判错 —— 现在不做了。
    expect(provenance.origin).toBe("self_reported");
    expect(provenance.displayLabel).toBe("EN");
    expect(provenance.hasServerVerdict).toBe(true);
  });

  it("裁决说自报、平铺行上却写着推断口径的记号 —— 记号不是裁决,照裁决渲染", () => {
    const provenance = resolveLanguageProvenance([
      { language: gateEvidence({ values: ["en"], origin: "self_reported", self_reported: true }) },
      { language: "en", language_source: "content_inference_v1", language_origin: "inferred" },
    ]);
    expect(provenance.origin).toBe("self_reported");
    expect(provenance.displayLabel).toBe("EN");
  });

  it("裁决 origin 说自报、布尔明牌却说不是 —— 裁决自相矛盾,按保守的那句落「来源不明」", () => {
    const provenance = resolveLanguageProvenance([
      {
        language: gateEvidence({
          values: ["en"], origin: "self_reported", self_reported: false, self_reported_values: ["en"],
        }),
      },
    ]);
    expect(provenance.origin).toBe("projected");
    expect(provenance.originLabel).toBe("来源不明");
    expect(provenance.selfReportedCodes).toEqual([]);
    expect(provenance.divergenceLabel).toBe("");
    expectNoSelfReportClaim(provenance);
  });

  it("认不出的档位(拼写漂移 / 将来新档)落「来源不明」,绝不退化成「自报」", () => {
    const provenance = resolveLanguageProvenance([
      { language: gateEvidence({ values: ["en"], origin: "projected_v2" }) },
    ]);
    expect(provenance.origin).toBe("projected");
    expect(provenance.originLabel).toBe("来源不明");
    expectNoSelfReportClaim(provenance);
  });

  it("裁决说未知,别的层还躺着一个值 —— 也是未知:不许拿原料给裁决打补丁", () => {
    const provenance = resolveLanguageProvenance([
      { language: gateEvidence({ values: [], origin: "unknown" }) },
      { language: "en", language_inferred: "ja" },
    ]);
    expect(provenance.origin).toBe("unknown");
    expect(provenance.displayLabel).toBe("未知");
    expect(provenance.codes).toEqual([]);
    expect(provenance.inferredCodes).toEqual([]);
    expectNoSelfReportClaim(provenance);
  });

  it("裁决说推断时,平铺行的 language 不参与,也不拼出「他自己填的是……」", () => {
    const provenance = resolveLanguageProvenance([
      {
        language: gateEvidence({
          values: ["ja"], origin: "inferred", inferred: true, inferred_values: ["ja"],
          evidence_fields: ["bio"],
        }),
      },
      { language: "en" },
    ]);
    expect(provenance.origin).toBe("inferred");
    expect(provenance.displayLabel).toBe("JA");
    expect(provenance.selfReportedCodes).toEqual([]);
    expectNoSelfReportClaim(provenance);
  });

  it("裁决说来源不明时,平铺行的 language 同样不许把它抬回自报", () => {
    const provenance = resolveLanguageProvenance([
      { language: gateEvidence({ values: ["en"], origin: "projected", projected_values: ["en"] }) },
      { language: "en", language_inferred: "ja" },
    ]);
    expect(provenance.origin).toBe("projected");
    expect(provenance.selfReportedCodes).toEqual([]);
    expect(provenance.divergenceLabel).toBe("");
    expectNoSelfReportClaim(provenance);
  });

  it("第一张裁决说了算:后面几层再有别的裁决也不参与合议", () => {
    const provenance = resolveLanguageProvenance([
      { language: gateEvidence({ values: ["en"], origin: "self_reported", self_reported: true }) },
      { language: gateEvidence({ values: ["ja"], origin: "inferred", inferred: true, inferred_values: ["ja"] }) },
    ]);
    expect(provenance.origin).toBe("self_reported");
    expect(provenance.displayLabel).toBe("EN");
  });

  it("门面件渲染:裁决自相矛盾落「来源不明」时，抽屉里不出现「他自己填的」", () => {
    render(
      <LanguageProvenanceDetail
        provenance={resolveLanguageProvenance([
          {
            language: gateEvidence({
              values: ["en"], origin: "self_reported", self_reported: false, self_reported_values: ["en"],
            }),
          },
        ])}
        testId="detail-conflict"
      />,
    );
    const rendered = screen.getByTestId("detail-conflict").textContent || "";
    expect(rendered).toContain("来源不明");
    expect(rendered).toContain("看不出是不是他自己填的");
    expect(rendered).not.toContain("他在平台资料里自己填的");
  });
});

// ── 破折号占位符:U+2014「—」恰是这块门面自己原来用的空位符 ────────────────────
describe("光剩一根横杠的占位符不是语言", () => {
  const DASHES: Array<[string, string]> = [
    ["长破折号 —", "—"], ["短破折号 –", "–"], ["连接号 ‐", "‐"],
    ["全角减号 －", "－"], ["数学减号 −", "−"], ["ASCII 连字符 -", "-"],
    ["两根横杠 --", "--"], ["下划线 ___", "___"],
  ];

  it.each(DASHES)("平台把语言写成「%s」时判为未知，不冒充自报", (_name, value) => {
    const provenance = resolveLanguageProvenance([{ language: value }]);
    expect(provenance.origin).toBe("unknown");
    expect(provenance.displayLabel).toBe("未知");
    expect(provenance.codes).toEqual([]);
    expect(provenance.title).not.toContain("自己填的");
  });

  it.each(DASHES)("自报格是「%s」而我们推断出韩语时，只说推断，不转述他的话", (_name, value) => {
    const provenance = resolveLanguageProvenance([
      // 迁移 305 的推断车道四列是一起落的:把握度跟着值走,缺了它这一票根本不算数
      // (见 LanguageProvenance.meetsConfidenceFloor)。这里要试的是占位符那一格,
      // 所以喂一行**过得了门槛**的真实推断。
      {
        language: value,
        language_inferred: "ko",
        language_inferred_source: "video_titles",
        language_inferred_confidence: "high",
      },
    ]);
    expect(provenance.origin).toBe("inferred");
    expect(provenance.displayLabel).toBe("KO");
    expect(provenance.selfReportedCodes).toEqual([]);
    expect(provenance.divergenceLabel).toBe("");
  });

  it("带破折号的地区后缀切完仍是占位词（未知—US）", () => {
    expect(resolveLanguageProvenance([{ language: "未知—US" }]).origin).toBe("unknown");
    expect(resolveLanguageProvenance([{ language: "unknown—US" }]).origin).toBe("unknown");
  });

  it("破折号切分不误伤真语言代码（zh–CN 用的是短破折号）", () => {
    const provenance = resolveLanguageProvenance([
      { language: gateEvidence({ values: ["zh–CN"], origin: "self_reported", self_reported: true }) },
    ]);
    expect(provenance.origin).toBe("self_reported");
    expect(provenance.codes).toEqual(["zh"]);
    expect(provenance.nameLabel).toBe("中文");
  });

  it("列表格渲染:横杠占位符进来时显示「未知」，横杠本身不上墙", () => {
    render(
      <LanguageProvenanceCell
        provenance={resolveLanguageProvenance([{ language: "—" }])}
        testId="cell-dash"
      />,
    );
    expect(screen.getByTestId("cell-dash").textContent).toBe("未知");
  });
});
