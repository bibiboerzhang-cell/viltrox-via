import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RecallMiniItem } from "./SmartKolInputPanel.Sections";
import { SearchEvaluationStatus, SearchFilterDiagnostics } from "./SmartKolInputPanel.TextResult";

describe("KOL 候选精准度透明层", () => {
  it("只按可核验证据展示等级，并解释为何推荐", () => {
    render(
      <RecallMiniItem
        index={1}
        item={{
          kol_pool_id: 7,
          bucket: "reviewer",
          handle: "lens_reviewer",
          display_name: "Lens Reviewer",
          platform: "youtube",
          vector_score: 0.81,
          profile_type: "reviewer",
          type_label: "测评号",
          creator_type_score: 20,
          reviewer_type_score: 90,
          candidate_bucket: "core_vertical",
          match_tier: "strict",
          why_fit: "持续发布镜头测评与器材对比内容",
          used_lenses: ["Viltrox 27mm F1.2"],
          representative_evidence: [
            { title: "27mm review", content_url: "https://example.test/1", view_count: 36800, like_count: 984 },
            { title: "lens comparison", content_url: "https://example.test/2" },
          ],
        }}
      />,
    );

    expect(screen.getByText("证据置信等级 · 较完整")).toBeTruthy();
    expect(screen.getByText(/为何推荐/)).toBeTruthy();
    expect(screen.getByText(/持续发布镜头测评与器材对比内容/)).toBeTruthy();
    expect(screen.getByTestId("candidate-representative-evidence-summary")).toHaveTextContent("代表内容：37K播 · 984赞");
    expect(screen.getByTestId("candidate-representative-evidence-links").querySelectorAll("a")).toHaveLength(1);
    expect(screen.getByRole("link", { name: /27mm review/ })).toHaveAttribute("href", "https://example.test/1");
    expect(screen.queryByText(/准确率/)).toBeNull();
  });

  it("数据不足时明确仅作候选，不把缺失或 confidence 写成准确率", () => {
    render(
      <RecallMiniItem
        index={2}
        item={{
          kol_pool_id: 8,
          bucket: "creator",
          handle: "candidate_only",
          display_name: "Candidate Only",
          platform: "instagram",
          profile_type: "creator",
          type_label: "创作者",
          creator_type_score: 75,
          reviewer_type_score: 25,
          candidate_bucket: "expansion",
          match_tier: "backfill",
          unknown_fields: ["language", "gear_content"],
          source_fields: { confidence: 0.97 },
        } as any}
      />,
    );

    expect(screen.queryByText("证据置信等级 · 待补")).toBeNull();
    expect(screen.getByText("补全关键资料 · 2 项")).toBeTruthy();
    expect(screen.queryByText("缺失：内容语言、摄影器材内容")).toBeNull();
    expect(screen.getByText(/为何仅候选/)).toBeTruthy();
    expect(screen.queryByTestId("candidate-rank-signal")).toBeNull();
    expect(screen.queryByText(/准确率/)).toBeNull();
    expect(screen.queryByText(/97%/)).toBeNull();
    expect(screen.queryByText(/相关度 0/)).toBeNull();
  });

  it("第一层只展示真实存在的身份、地区和业务数值，空指标不摆占位", () => {
    render(
      <RecallMiniItem
        index={5}
        item={{
          kol_pool_id: 11,
          bucket: "creator",
          handle: "observed_creator",
          display_name: "Observed Creator",
          platform: "youtube",
          followers: 742000,
          profile_type: "creator",
          type_label: "创作者",
          creator_type_score: 80,
          reviewer_type_score: 20,
          source_fields: {
            country: "UK",
            language: "en",
            avg_views: 17515,
            avg_likes: 820,
            avg_comments: 30,
            engagement_rate: 0.046,
            source_type: "youtube_api",
            updated_at: "2026-07-23T12:00:00Z",
          },
        } as any}
      />,
    );

    expect(screen.getByTestId("candidate-identity-meta").textContent).toContain("youtube · UK · en · 创作者");
    const metrics = screen.getByTestId("candidate-observed-metrics");
    expect(metrics.textContent).toContain("粉丝 742K");
    expect(metrics.textContent).toContain("均播 18K");
    expect(metrics.textContent).toContain("均赞 820");
    expect(metrics.textContent).toContain("均评 30");
    expect(metrics.textContent).toContain("互动 4.6%");
    expect(screen.getByTestId("candidate-secondary-details").textContent).toContain("youtube_api");
    expect(screen.queryByText(/--|unknown|无数据|检索相关度待返回/)).toBeNull();
  });

  it("没有真实指标时整块隐藏，不把缺值写成 0 或推测曝光", () => {
    render(
      <RecallMiniItem
        index={6}
        item={{
          kol_pool_id: 12,
          bucket: "creator",
          handle: "sparse_creator",
          display_name: "Sparse Creator",
          platform: "unknown",
          followers: null,
          profile_type: "creator",
          type_label: "创作者",
          creator_type_score: 80,
          reviewer_type_score: 20,
          source_fields: { avg_views: 0, avg_comments: null, engagement_rate: 0 },
        } as any}
      />,
    );

    expect(screen.queryByTestId("candidate-observed-metrics")).toBeNull();
    expect(screen.getByTestId("candidate-identity-meta").textContent).toBe("创作者");
    expect(screen.queryByText(/0 粉|0 曝光|unknown|低合作|无数据/)).toBeNull();
  });

  it("优先使用 robust 检索分，并按真实方法标注本地词项相关度", () => {
    render(
      <RecallMiniItem
        index={3}
        item={{
          kol_pool_id: 9,
          bucket: "reviewer",
          handle: "local_ranked",
          display_name: "Local Ranked",
          platform: "youtube",
          vector_score: 0.99,
          robust_rank_score: 0.73,
          robust_rank_method: "provider_free_pool_text",
          profile_type: "reviewer",
          type_label: "测评号",
          creator_type_score: 10,
          reviewer_type_score: 90,
        }}
      />,
    );

    const signal = screen.getByTestId("candidate-rank-signal");
    expect(signal.textContent).toBe("本地词项相关度 0.73");
    expect(signal.getAttribute("title")).toContain("本地词项排序");
    expect(signal.getAttribute("title")).not.toContain("0.990");
    expect(screen.queryByText(/向量相似度/)).toBeNull();
    expect(screen.queryByText(/准确率/)).toBeNull();
  });

  it("兼容后端证据等级与覆盖字段，但不把置信值改写成准确率", () => {
    render(
      <RecallMiniItem
        index={4}
        item={{
          kol_pool_id: 10,
          bucket: "reviewer",
          handle: "quality_labeled",
          display_name: "Quality Labeled",
          platform: "youtube",
          retrieval_score: 0.62,
          retrieval_method: "hybrid_rrf_v1",
          profile_type: "reviewer",
          type_label: "测评号",
          creator_type_score: 10,
          reviewer_type_score: 90,
          evidence_confidence: 0.84,
          evidence_quality: { level: "high", coverage: 0.75 },
        }}
      />,
    );

    const grade = screen.getByTestId("candidate-evidence-grade");
    expect(grade.textContent).toBe("证据置信等级 · 较完整");
    expect(grade.getAttribute("title")).toContain("上游证据置信值 0.840");
    expect(grade.getAttribute("title")).toContain("证据覆盖 0.75");
    expect(screen.getByTestId("candidate-rank-signal").textContent).toBe("混合相关度 0.62");
    expect(screen.queryByText(/准确率/)).toBeNull();
  });

  it("30 人不足时展示硬筛选短缺、拒绝原因和不放宽约束", () => {
    render(
      <SearchFilterDiagnostics
        diagnostics={{
          requested_count: 30,
          strict_count: 18,
          backfill_count: 5,
          final_count: 23,
          shortfall: 7,
          result_contract_satisfied: false,
          hard_filter_rejected_count: 19,
          hard_filter_rejected_by: { languages: 8, followers_min: 11 },
          backfill_policy: "query_relevance_only_hard_filters_never_relaxed",
        }}
      />,
    );

    expect(screen.getByTestId("search-hard-filter-shortfall").textContent).toContain("硬筛选后仅有 23/30");
    expect(screen.getByTestId("search-hard-filter-shortfall").textContent).toContain("短缺 7");
    expect(screen.getByTestId("search-hard-filter-shortfall").textContent).toContain("显式硬筛选未放宽");
    expect(screen.getByText(/内容语言 8/)).toBeTruthy();
    expect(screen.getByText(/最低粉丝数 11/)).toBeTruthy();
    expect(screen.queryByText(/准确率/)).toBeNull();
  });

  it("只有已返回的计数才展示，且显式确认后才声称满足 30 人合同", () => {
    const { rerender } = render(<SearchFilterDiagnostics diagnostics={{ requested_count: 30 }} />);

    expect(screen.getByText("筛选后目标 30")).toBeTruthy();
    expect(screen.queryByText("最终 0")).toBeNull();
    expect(screen.queryByText(/已满足/)).toBeNull();

    rerender(
      <SearchFilterDiagnostics
        diagnostics={{
          requested_count: 30,
          strict_count: 30,
          backfill_count: 0,
          final_count: 30,
          shortfall: 0,
          result_contract_satisfied: true,
        }}
      />,
    );

    expect(screen.getByText("已满足筛选后 30 人结果合同")).toBeTruthy();
  });

  it("未评测、标注中和需重评状态不泄露历史准确率数字", () => {
    const { rerender } = render(
      <SearchEvaluationStatus
        evaluation={{ state: "not_evaluated", metrics: { precision_at_30: 0.91 } }}
      />,
    );
    expect(screen.getByText("搜索质量：未评测")).toBeTruthy();
    expect(screen.queryByTestId("search-evaluation-metrics")).toBeNull();
    expect(screen.queryByText(/91%/)).toBeNull();

    rerender(
      <SearchEvaluationStatus
        evaluation={{
          state: "labeling",
          target_count: 360,
          labeled_count: 84,
          dual_review_target: 180,
          dual_reviewed_count: 21,
          metrics: { precision_at_30: 0.91 },
        }}
      />,
    );
    expect(screen.getByText(/人工标注 84\/360/)).toBeTruthy();
    expect(screen.queryByText(/91%/)).toBeNull();

    rerender(
      <SearchEvaluationStatus
        evaluation={{ state: "stale", metrics: { precision_at_30: 0.91 } }}
      />,
    );
    expect(screen.getByText("搜索质量：需重评")).toBeTruthy();
    expect(screen.queryByText(/91%/)).toBeNull();
  });

  it("只有冻结且可分享的 Gold Set 才展示验证指标", () => {
    render(
      <SearchEvaluationStatus
        evaluation={{
          state: "shareable",
          gold_set_id: "Gold Set v2026.08",
          metrics: {
            precision_at_30: 0.8,
            hard_filter_violation_rate: 0,
            evidence_support_rate: 0.9,
            cohen_kappa: 0.76,
          },
        }}
      />,
    );

    expect(screen.getByText("搜索质量：可分享")).toBeTruthy();
    expect(screen.getByText(/Gold Set v2026.08/)).toBeTruthy();
    expect(screen.getByText("Precision@30 80%")).toBeTruthy();
    expect(screen.getByText("硬筛违规 0%")).toBeTruthy();
    expect(screen.getByText("证据支持 90%")).toBeTruthy();
    expect(screen.getByText("双审一致性 κ 0.76")).toBeTruthy();
  });
});
