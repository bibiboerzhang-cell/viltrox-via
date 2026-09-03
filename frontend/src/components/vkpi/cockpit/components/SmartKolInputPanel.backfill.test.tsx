// 空墙不许沉默:精准命中不足(甚至为 0)时,界面必须说清「为什么这么少、补充的人从哪来、
// 谁被藏起来了」;补充人选的卡面角标必须说人话,并且从历史打开会话时也还在。
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RecallMiniItem } from "./SmartKolInputPanel.Sections";
import { SearchFilterDiagnostics } from "./SmartKolInputPanel.TextResult";

const EXPLANATION = {
  schema: "recall_result_explanation_v1",
  requested: 30,
  precise_count: 0,
  backfill_count: 20,
  returned_count: 20,
  headline: "精准命中 0 人，另补充 20 人（已标注补充原因）",
  backfill_reasons: [
    { code: "team_favorite", label: "已被同事关注", count: 12 },
    { code: "vertical_relaxed", label: "题材不完全匹配", count: 8 },
  ],
  gaps: [
    { code: "verticals", label: "题材不匹配", count: 30 },
    { code: "gear_content", label: "未见器材相关内容", count: 6 },
  ],
  favorited_by_team_hidden: 29,
  note: "补充的人选不是精准命中，已按原因标注，可按需忽略。",
};

const BANNED_WORDS = ["LLM", "lexicon", "rule_v0", "词表", "embedding", "Qdrant", "Apify", "backfill", "tier", "回填"];

describe("库内召回:空墙的解释与补充人选角标", () => {
  it("精准 0 也把原因逐条说清,而不是只留一句无结果", async () => {
    render(
      <SearchFilterDiagnostics
        diagnostics={{
          requested_count: 30,
          precise_count: 0,
          final_count: 20,
          shortfall: 30,
          result_contract_satisfied: false,
          result_explanation: EXPLANATION,
        }}
      />,
    );

    const headline = await screen.findByTestId("recall-explanation-headline");
    expect(headline.textContent).toContain("精准命中 0 人");
    expect((await screen.findByTestId("recall-explanation-gaps")).textContent).toContain("题材不匹配 30 人");
    expect((await screen.findByTestId("recall-explanation-gaps")).textContent).toContain("未见器材相关内容 6 人");
    const supplement = await screen.findByTestId("recall-explanation-supplement");
    expect(supplement.textContent).toContain("已被同事关注 12 人");
    expect(supplement.textContent).toContain("题材不完全匹配 8 人");
    expect((await screen.findByTestId("recall-explanation-favorited")).textContent).toContain("另有 29 人已被同事关注");
    expect((await screen.findByTestId("recall-result-explanation")).textContent).toContain("可按需忽略");
  });

  it("一个人都没有时也照样解释,不只丢一句空结果", async () => {
    render(
      <SearchFilterDiagnostics
        diagnostics={{
          result_explanation: {
            ...EXPLANATION,
            backfill_count: 0,
            returned_count: 0,
            backfill_reasons: [],
            headline: "本次没有找到符合全部条件的人选",
            note: "没有可补充的人选；可放宽平台/地区/粉丝量筛选再试。",
          },
        }}
      />,
    );

    expect((await screen.findByTestId("recall-explanation-headline")).textContent).toBe("本次没有找到符合全部条件的人选");
    expect((await screen.findByTestId("recall-explanation-gaps")).textContent).toContain("题材不匹配 30 人");
    expect((await screen.findByTestId("recall-result-explanation")).textContent).toContain("可放宽平台");
    expect(screen.queryByTestId("recall-explanation-supplement")).toBeNull();
  });

  it("解释区不写内部术语,也不把补充人选说成精准命中", async () => {
    render(<SearchFilterDiagnostics diagnostics={{ requested_count: 30, result_explanation: EXPLANATION }} />);

    const panel = await screen.findByTestId("recall-result-explanation");
    const text = panel.textContent || "";
    for (const word of BANNED_WORDS) {
      expect(text.toLowerCase()).not.toContain(word.toLowerCase());
    }
  });

  it("服务端口径对不上时整块不渲染,也不报错", async () => {
    render(
      <SearchFilterDiagnostics
        diagnostics={{ requested_count: 30, result_explanation: { ...EXPLANATION, schema: "something_else_v9" } }}
      />,
    );

    // 其余诊断照常显示,只有解释块因为口径不认而缺席。
    expect(await screen.findByText("筛选后目标 30")).toBeTruthy();
    await waitFor(() => expect(screen.queryByTestId("recall-result-explanation")).toBeNull());
    expect(screen.queryByTestId("recall-explanation-headline")).toBeNull();
  });

  it("完全没有解释字段时不凭空造一段话", async () => {
    render(<SearchFilterDiagnostics diagnostics={{ requested_count: 30 }} />);

    expect(await screen.findByText("筛选后目标 30")).toBeTruthy();
    await waitFor(() => expect(screen.queryByTestId("recall-result-explanation")).toBeNull());
  });

  it("补充人选的卡面角标说人话,并带稳定的分区钩子", async () => {
    render(
      <RecallMiniItem
        index={1}
        item={{
          kol_pool_id: 501,
          bucket: "creator",
          handle: "favorited_creator",
          display_name: "Favorited Creator",
          platform: "youtube",
          profile_type: "creator",
          type_label: "创作者",
          candidate_bucket: "expansion",
          match_tier: "strict",
          backfill_tier: "team_favorite",
          backfill_label: "已被同事关注",
          precision_match: false,
          counts_toward_target: false,
          selection_tier: "backfill_team_favorite",
          relaxed_filters: ["team_favorite"],
        } as never}
      />,
    );

    const badge = await screen.findByTestId("candidate-supplement-badge");
    expect(badge.textContent).toBe("已被同事关注");
    expect(badge.getAttribute("title")).toContain("不计入严格合格名单");
    const card = await screen.findByTestId("kol-recall-card");
    expect(card.getAttribute("data-backfill-tier")).toBe("team_favorite");
    for (const word of BANNED_WORDS) {
      expect((badge.textContent || "").toLowerCase()).not.toContain(word.toLowerCase());
    }
  });

  it("从历史打开会话时只剩 selection_tier,角标照样在", async () => {
    render(
      <RecallMiniItem
        index={2}
        item={{
          kol_pool_id: 502,
          bucket: "creator",
          handle: "replayed_creator",
          display_name: "Replayed Creator",
          platform: "instagram",
          profile_type: "creator",
          type_label: "创作者",
          selection_tier: "backfill_vertical_relaxed",
          backfill_label: "题材不完全匹配",
        } as never}
      />,
    );

    const card = await screen.findByTestId("kol-recall-card");
    expect(card.getAttribute("data-backfill-tier")).toBe("vertical_relaxed");
    expect((await screen.findByTestId("candidate-supplement-badge")).textContent).toBe("题材不完全匹配");
  });

  it("精准命中不挂补充角标", async () => {
    render(
      <RecallMiniItem
        index={3}
        item={{
          kol_pool_id: 503,
          bucket: "creator",
          handle: "strict_creator",
          display_name: "Strict Creator",
          platform: "youtube",
          profile_type: "creator",
          type_label: "创作者",
          candidate_bucket: "core_vertical",
          match_tier: "strict",
          precision_match: true,
        } as never}
      />,
    );

    const card = await screen.findByTestId("kol-recall-card");
    expect(card.getAttribute("data-backfill-tier")).toBe("none");
    expect(screen.queryByTestId("candidate-supplement-badge")).toBeNull();
  });
});
