import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { resolveLanguageProvenance } from "./LanguageProvenance";
import { LanguageProvenanceCell, LanguageProvenanceDetail } from "./LanguageProvenanceChip";

// 服务端 profile_recall_language_gate.language_gate_evidence() 的真实出参形状。
// 与 LanguageProvenance.test.tsx 里那份同形:这一份专门用来**把它标错**。
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

// ── H7:门面是第二道防线,不是服务端的传声筒 ──────────────────────────────────
//
// 服务端的三态明牌会标错(H6:归属判定不读来源列,别处投影来的值会被贴上「自报」标签)。
// 明牌一旦标错,门面若照单全收,就替这个人伪造了一句他从没说过的话。
// 这一组钉死:明牌与手上的证据打架时,门面**绝不跟着错**。
describe("服务端明牌标错时，门面按证据兜底", () => {
  // 「这是他自己填的」这句**声明**只有两种合法长相:自报档的整句、以及转述分歧的那半句。
  // 别处一律不许冒出来。注意「不**是**他自己填的」是澄清不是声明,所以不能拿裸串去匹配。
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
  it("值明明取自推断那一格，明牌却写「自报」——以证据为准，标推断", () => {
    const provenance = resolveLanguageProvenance([
      {
        language: gateEvidence({
          // 自报那一格是占位词(等于他没填),值是推断列顶上来的,明牌却说是他自己填的。
          values: ["Unknown"], origin: "self_reported",
          self_reported_values: ["Unknown"], inferred_values: ["ko"],
          source: "vkpi_kol_profiles.language",
          evidence_fields: ["video_titles"],
        }),
      },
    ]);
    expect(provenance.origin).toBe("inferred");
    expect(provenance.originLabel).toBe("推断");
    expect(provenance.displayLabel).toBe("KO");
    expect(provenance.basisLabel).toBe("作品标题");
    expect(provenance.selfReportedCodes).toEqual([]);
    expectNoSelfReportClaim(provenance);
  });

  it("池行同样:自报格是占位词、值来自推断列时，明牌说自报也不认", () => {
    const provenance = resolveLanguageProvenance([
      {
        language: "N/A",
        language_origin: "self_reported",
        language_inferred: "ja",
        language_inferred_source: "bio",
      },
    ]);
    expect(provenance.origin).toBe("inferred");
    expect(provenance.basisLabel).toBe("个人简介");
    expectNoSelfReportClaim(provenance);
  });

  it("明牌说自报、记录里却明写 inferred=true —— 两边对不上就落未知，不显示值", () => {
    const provenance = resolveLanguageProvenance([
      {
        language: gateEvidence({
          values: ["en"], origin: "self_reported", inferred: true,
          self_reported_values: ["en"], source: "vkpi_kol_profiles.language",
        }),
      },
    ]);
    expect(provenance.origin).toBe("unknown");
    expect(provenance.displayLabel).toBe("未知");
    expect(provenance.originLabel).toBe("未知");
    // 判不出就是未知:一个具体语言值都不许漏出去。
    expect(provenance.codes).toEqual([]);
    expect(provenance.selfReportedCodes).toEqual([]);
    expect(provenance.inferredCodes).toEqual([]);
    expect(provenance.nameLabel).toBe("");
    expectNoSelfReportClaim(provenance);
    expect(provenance.title).not.toContain("英语");
    // 未知的理由如实说:说法不一致所以不显示 —— 不谎称我们没有这个值,
    // 也不替系统声称「推不出来」。
    expect(provenance.title).toContain("说法不一致");
    ["没有足够的文字", "推不出", "推断不出"].forEach((claim) => {
      expect(provenance.title).not.toContain(claim);
    });
    expect(provenance.noteLabel).toBe("两份记录对不上，暂不显示");
  });

  it("明牌说自报、来源串却是推断口径 —— 同样落未知", () => {
    const provenance = resolveLanguageProvenance([
      { language: "en", language_origin: "self_reported", language_source: "content_inference_v1" },
    ]);
    expect(provenance.origin).toBe("unknown");
    expectNoSelfReportClaim(provenance);
  });

  it("明牌说推断照收 —— 这一头是安全方向，不会凭空伪造一句自报声明", () => {
    const provenance = resolveLanguageProvenance([
      {
        language: gateEvidence({
          values: ["en"], origin: "inferred", inferred: true,
          inferred_values: ["en"], source: "vkpi_kol_pool.language_inferred",
          evidence_fields: ["bio"],
        }),
      },
    ]);
    expect(provenance.origin).toBe("inferred");
    expect(provenance.displayLabel).toBe("EN");
  });

  it("明牌与证据一致时照常放行，不误伤正常的自报行", () => {
    const provenance = resolveLanguageProvenance([
      {
        language: gateEvidence({
          values: ["en"], origin: "self_reported", inferred: false,
          self_reported_values: ["en"], source: "vkpi_kol_profiles.language",
        }),
      },
      { language: "en" },
    ]);
    expect(provenance.origin).toBe("self_reported");
    expect(provenance.displayLabel).toBe("EN");
    expect(provenance.noteLabel).toBe("他自己填的");
  });

  it("明牌说自报、旁挂推断值与之不同(真分歧)时不算打架，照旧如实说出分歧", () => {
    // 这里的推断值只是**旁挂**,显示的那个值仍然取自自报格 —— 没有证据说这个值来自推断,
    // 不构成冲突。分歧照说,值照显示。
    const provenance = resolveLanguageProvenance([
      {
        language: gateEvidence({
          values: ["zh"], origin: "self_reported",
          self_reported_values: ["zh"], inferred_values: ["en"],
          source: "vkpi_kol_profiles.language",
        }),
      },
    ]);
    expect(provenance.origin).toBe("self_reported");
    expect(provenance.divergenceLabel).toBe("他自己填的是中文，照他发的东西推断出来的是英语。");
  });

  it("门面件渲染:明牌标错落未知时，抽屉里不出现值、也不出现「他自己填的」", () => {
    render(
      <LanguageProvenanceDetail
        provenance={resolveLanguageProvenance([
          {
            language: gateEvidence({
              values: ["en"], origin: "self_reported", inferred: true,
              self_reported_values: ["en"], source: "vkpi_kol_profiles.language",
            }),
          },
        ])}
        testId="detail-conflict"
      />,
    );
    const rendered = screen.getByTestId("detail-conflict").textContent || "";
    expect(rendered).toContain("未知");
    expect(rendered).toContain("两份记录对不上");
    expect(rendered).not.toContain("他在平台资料里自己填的");
    expect(rendered).not.toContain("英语");
    expect(rendered).not.toContain("EN");
  });
});

// ── 破折号占位符:U+2014「—」恰是这块门面自己原来用的空位符 ────────────────────
describe("光剩一根横杠的占位符不是语言", () => {
  const DASHES: Array<[string, string]> = [
    ["长破折号 —", "\u2014"], ["短破折号 –", "\u2013"], ["连接号 ‐", "\u2010"],
    ["全角减号 －", "\uFF0D"], ["数学减号 −", "\u2212"], ["ASCII 连字符 -", "-"],
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
      { language: value, language_inferred: "ko", language_inferred_source: "video_titles" },
    ]);
    expect(provenance.origin).toBe("inferred");
    expect(provenance.displayLabel).toBe("KO");
    expect(provenance.selfReportedCodes).toEqual([]);
    expect(provenance.divergenceLabel).toBe("");
  });

  it("带破折号的地区后缀切完仍是占位词（未知—US）", () => {
    expect(resolveLanguageProvenance([{ language: "\u672A\u77E5\u2014US" }]).origin).toBe("unknown");
    expect(resolveLanguageProvenance([{ language: "unknown\u2014US" }]).origin).toBe("unknown");
  });

  it("破折号切分不误伤真语言代码（zh–CN 用的是短破折号）", () => {
    const provenance = resolveLanguageProvenance([{ language: "zh\u2013CN" }]);
    expect(provenance.origin).toBe("self_reported");
    expect(provenance.codes).toEqual(["zh"]);
    expect(provenance.nameLabel).toBe("中文");
  });

  it("列表格渲染:横杠占位符进来时显示「未知」，横杠本身不上墙", () => {
    render(
      <LanguageProvenanceCell
        provenance={resolveLanguageProvenance([{ language: "\u2014" }])}
        testId="cell-dash"
      />,
    );
    expect(screen.getByTestId("cell-dash").textContent).toBe("未知");
  });
});
