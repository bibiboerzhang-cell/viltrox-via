import { describe, expect, it } from "vitest";

import { candidateVerticalTags } from "./SmartKolInputPanel.CandidateEvidence";
import type { VkpiKolRecallItem } from "../../../../domains/kol";

const base = { kol_pool_id: 1, handle: "a", platform: "youtube" } as unknown as VkpiKolRecallItem;

describe("候选卡垂类标签", () => {
  it("一个人可以带多个垂类,每个都带得住「为什么算他是这一类」", () => {
    const tags = candidateVerticalTags({
      ...base,
      vertical_tags: ["lifestyle", "camera_system"],
      vertical_evidence: [
        { vertical: "lifestyle", label: "生活方式", reasons: ["频道关键词命中「travel」:travel photography"] },
        { vertical: "camera_system", label: "相机系统", reasons: ["作品里标记过镜头品牌 @viltrox.official"] },
      ],
    } as VkpiKolRecallItem);
    expect(tags.map((tag) => tag.label)).toEqual(["生活方式", "相机系统"]);
    expect(tags[0].reasons[0]).toContain("travel");
    expect(tags[1].reasons[0]).toContain("viltrox.official");
  });

  it("判不出垂类就返回空数组,由卡面显示未知,绝不默认归进某一类", () => {
    expect(candidateVerticalTags({ ...base } as VkpiKolRecallItem)).toEqual([]);
    expect(
      candidateVerticalTags({ ...base, vertical_tags: [], vertical_evidence: [] } as VkpiKolRecallItem),
    ).toEqual([]);
  });

  it("旧会话回放只给 id 时翻成中文;认不出的 id 一律不摆到卡面上", () => {
    const tags = candidateVerticalTags({
      ...base,
      vertical_tags: ["vlog", "some_internal_id"],
    } as VkpiKolRecallItem);
    expect(tags).toEqual([{ label: "Vlog", reasons: [] }]);
  });

  it("同一个垂类不会因为 tags 与 evidence 同时出现而重复", () => {
    const tags = candidateVerticalTags({
      ...base,
      vertical_tags: ["lifestyle"],
      vertical_evidence: [{ vertical: "lifestyle", label: "生活方式", reasons: ["主页简介命中「travel」"] }],
    } as VkpiKolRecallItem);
    expect(tags).toHaveLength(1);
    expect(tags[0].reasons).toHaveLength(1);
  });
});
