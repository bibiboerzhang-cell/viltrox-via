import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { buildAnalysisTrustViewModel, KOLAnalysisTrustPanel } from "./KOLAnalysisTrustPanel";

describe("KOLAnalysisTrustPanel", () => {
  it("abstains when the server reports blocking evidence gaps", () => {
    const detailBundle = {
      analysis_readiness: {
        level: "insufficient",
        status: "blocked",
        claim_status: "descriptive_only",
        abstain: true,
        key_sample_count: 3,
        evidence_coverage: {
          video_total: 12,
          deep_ready: 4,
          deep_ratio: 4 / 12,
          qa_ready: 2,
          full_video_proven: 2,
          full_video_ratio: 2 / 12,
        },
        blocking_gaps: [
          { code: "full_video_receipt_missing", severity: "high", message: "完整视频覆盖凭证不足" },
        ],
      },
    };

    render(<KOLAnalysisTrustPanel detailBundle={detailBundle} />);

    expect(screen.getByText("暂不就绪")).toBeInTheDocument();
    expect(screen.getByText("4/12 · 33%")).toBeInTheDocument();
    expect(screen.getByText("3 条")).toBeInTheDocument();
    expect(screen.getByText("2 条 · 17%")).toBeInTheDocument();
    expect(screen.getByText("仅描述性")).toBeInTheDocument();
    expect(screen.getByText("暂不建议下结论")).toBeInTheDocument();
    expect(screen.getByText(/完整视频覆盖凭证不足/)).toBeInTheDocument();
    expect(screen.getByText(/不是预测准确率/)).toBeInTheDocument();
  });

  it("keeps old detail bundles useful without inventing readiness or key samples", () => {
    render(
      <KOLAnalysisTrustPanel
        detailBundle={{
          video_analysis: { summary: { evidence_count: 5, ready_count: 3, qa_ready_count: 1 } },
        }}
      />,
    );

    expect(screen.getByText("统一口径待补")).toBeInTheDocument();
    expect(screen.getByText("3/5 · 60%")).toBeInTheDocument();
    expect(screen.getAllByText("未声明", { selector: "div" }).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/当前服务仍按旧合同返回/)).toBeInTheDocument();
    expect(screen.queryByTestId("analysis-abstain-notice")).toBeNull();
  });

  it("allows an initial judgment only when the readiness contract says evidence is ready", () => {
    const model = buildAnalysisTrustViewModel({
      detailBundle: {
        analysis_readiness: {
          level: "high",
          status: "decision_ready",
          claim_status: "decision_support",
          abstain: false,
          key_sample_count: 8,
          evidence_coverage: { video_total: 10, deep_ready: 8, deep_ratio: 0.8, full_video_proven: 6 },
          blocking_gaps: [],
        },
      },
    });

    expect(model.tone).toBe("ready");
    expect(model.abstain).toBe(false);
    render(<KOLAnalysisTrustPanel detailBundle={{ analysis_readiness: {
      level: "high",
      status: "decision_ready",
      claim_status: "decision_support",
      abstain: false,
      key_sample_count: 8,
      evidence_coverage: { video_total: 10, deep_ready: 8, deep_ratio: 0.8, full_video_proven: 6 },
      blocking_gaps: [],
    } }} />);

    expect(screen.getByText("可用于初步判断", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText("仅供决策辅助")).toBeInTheDocument();
    expect(screen.getByText(/仍需人工复核原始证据/)).toBeInTheDocument();
    expect(screen.queryByText("暂不建议下结论")).toBeNull();
  });

  it("does not turn a brand-history gap into an overall KOL abstention", () => {
    const detailBundle = {
      analysis_readiness: {
        claim_status: "descriptive_only",
        scopes: {
          overall: {
            level: "high",
            status: "decision_ready",
            recommendation_status: "recommend_with_review",
            abstain: false,
            key_sample_count: 7,
            evidence_coverage: { video_total: 9, deep_ready: 7, deep_ratio: 7 / 9 },
            blocking_gaps: [],
          },
          content_fit: { level: "high", status: "ready", abstain: false },
          brand_history: {
            level: "insufficient",
            status: "blocked",
            decision_mode: "abstain",
            blocking_gaps: [{ code: "brand_evidence_missing", message: "品牌历史证据不足" }],
          },
        },
      },
    };
    const model = buildAnalysisTrustViewModel({ detailBundle });

    expect(model.abstain).toBe(false);
    expect(model.scopes.find((scope) => scope.key === "brand_history")?.status).toBe("暂不判断");
    render(<KOLAnalysisTrustPanel detailBundle={detailBundle} />);

    expect(screen.getByText("整体 · 需复核")).toBeInTheDocument();
    expect(screen.getByText("内容契合 · 可参考")).toBeInTheDocument();
    expect(screen.getByText("品牌历史 · 暂不判断")).toBeInTheDocument();
    expect(screen.queryByText("暂不建议下结论")).toBeNull();
    expect(screen.getByText("仅描述性")).toBeInTheDocument();
  });
});
