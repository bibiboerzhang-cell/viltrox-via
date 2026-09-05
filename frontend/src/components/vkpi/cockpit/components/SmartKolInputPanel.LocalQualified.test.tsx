import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  LocalQualifiedList,
  OnlineContentEvidenceNotice,
  StrictQualifiedList,
} from "./SmartKolInputPanel.LocalQualifiedList";
import { localQualifiedSummary } from "./SmartKolInputPanel.LocalQualified";
import { readPersistedSearchDisplay, sanitizeSearchDisplayForCache } from "./SmartKolInputPanel.derivers";

function result(items: any[], diagnostics: Record<string, unknown> = {}): any {
  return {
    method: "vector_recall",
    query: {},
    ratio: { creator_quota: 30, reviewer_quota: 0, policy: "soft", mixed_policy: "dominant", dedupe: true },
    items,
    buckets: { creator: items, reviewer: [] },
    diagnostics,
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

describe("local qualified first-list contract", () => {
  it("counts only explicit server-qualified unique identities and preserves server rank", () => {
    const summary = localQualifiedSummary(result([
      {
        kol_pool_id: 2,
        handle: "second",
        platform: "youtube",
        followers: 9800,
        qualification_evidence: strictProof(),
        source_fields: { server_rank: 2, qualification_status: "qualified" },
      },
      {
        kol_pool_id: 1,
        handle: "first",
        platform: "youtube",
        followers: 12000,
        qualification_evidence: strictProof(),
        source_fields: { server_rank: 1, qualification: { status: "accepted" } },
      },
      {
        kol_pool_id: 99,
        handle: "FIRST",
        platform: "youtube",
        followers: 12000,
        qualification_evidence: strictProof(),
        source_fields: { server_rank: 4, qualification_status: "qualified" },
      },
      {
        kol_pool_id: 3,
        handle: "promising",
        platform: "instagram",
        followers: 300000,
        source_fields: {
          server_rank: 3,
          latest_video_published_at: "2026-08-10T00:00:00Z",
          market_evidence: { status: "pass", market: "US" },
        },
      },
    ]));

    expect(summary.rows.map((row) => row.name)).toEqual(["first", "second", "promising"]);
    expect(summary.qualified).toBe(2);
    expect(summary.uniqueQualified).toBe(2);
    expect(summary.pending).toBe(1);
    expect(summary.shortfall).toBe(28);
    expect(summary.shortfallReasons[0]).toContain("待服务端硬闸验收");
  });

  it("clamps a strict-v2 aggregate to visible qualified rows and keeps shortfall reasons", () => {
    const summary = localQualifiedSummary(result([
      { kol_pool_id: 1, handle: "pending", platform: "youtube", followers: 5000 },
    ], {
      local_lane: {
        schema: "smart_local_qualified_v2",
        target_count: 30,
        qualified_count: 18,
        returned_count: 18,
        unique_qualified_count: 18,
        shortfall_reasons: { freshness_unknown: 7, market_unverified: 5 },
      },
    }));

    expect(summary.qualified).toBe(0);
    expect(summary.serverReturned).toBe(1);
    expect(summary.uniqueQualified).toBe(0);
    expect(summary.pending).toBe(1);
    expect(summary.shortfallReasons).toEqual(["最近内容日期待核验 7", "市场证据待核验 5"]);
  });

  it("does not promote a legacy returned_count without the explicit Smart-local schema", () => {
    const summary = localQualifiedSummary(result([
      { kol_pool_id: 1, handle: "legacy-one", platform: "youtube", followers: 5000 },
      { kol_pool_id: 2, handle: "legacy-two", platform: "youtube", followers: 8000 },
    ], { returned_count: 10 }));

    expect(summary.serverReturned).toBe(10);
    expect(summary.qualified).toBe(0);
    expect(summary.serverQualified).toBe(0);
    expect(summary.pending).toBe(2);
    expect(summary.shortfall).toBe(30);
  });

  it("keeps a v1 proof in legacy pending state instead of treating it as strict v2", () => {
    const summary = localQualifiedSummary(result([{
      kol_pool_id: 7,
      handle: "legacy-v1",
      platform: "youtube",
      qualification_evidence: { schema: "smart_local_gate_evidence_v1", passed: true },
    }], {
      local_lane: { schema: "smart_local_qualified_v1", qualified_count: 30, returned_count: 30 },
    }));

    expect(summary.qualified).toBe(0);
    expect(summary.rows[0].qualification).toBe("pending");
    expect(summary.serverReturned).toBe(1);
  });

  it("selects only strict server-qualified recall rows and supports select all", () => {
    const onSelectionChange = vi.fn();
    render(<LocalQualifiedList
      result={result([
        { kol_pool_id: 1, handle: "qualified-one", platform: "youtube", qualification_evidence: strictProof() },
        { kol_pool_id: 2, handle: "qualified-two", platform: "instagram", qualification_evidence: strictProof() },
        { kol_pool_id: 3, handle: "legacy-pending", platform: "youtube", qualification_evidence: { schema: "smart_local_gate_evidence_v1", passed: true } },
        { kol_pool_id: 4, handle: "rejected", platform: "youtube", qualification_evidence: { schema: "smart_local_gate_evidence_v2", passed: false } },
      ])}
      selectedIds={new Set()}
      onSelectionChange={onSelectionChange}
    />);

    expect((screen.getByRole("checkbox", { name: "选择本地 KOL legacy-pending" }) as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole("checkbox", { name: "选择本地 KOL rejected" }) as HTMLInputElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: "全选本地合格 KOL" }));
    expect([...onSelectionChange.mock.calls[0][0]]).toEqual([1, 2]);
    fireEvent.click(screen.getByRole("checkbox", { name: "选择本地 KOL qualified-one" }));
    expect([...onSelectionChange.mock.calls[1][0]]).toEqual([1]);
  });

  it("keeps an accepted online row unselectable until its server snapshot is terminal", () => {
    const summary = localQualifiedSummary(result([
      { kol_pool_id: 31, handle: "online-one", platform: "youtube", qualification_evidence: strictProof() },
    ]));
    render(<StrictQualifiedList
      summary={summary}
      lane="online"
      selectionReady={false}
      selectedIds={new Set()}
      onSelectionChange={vi.fn()}
    />);

    expect(screen.getByText("联网净新增 1/30")).toBeTruthy();
    expect((screen.getByRole("checkbox", { name: "选择联网 KOL online-one" }) as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole("checkbox", { name: "全选联网净新增 KOL" }) as HTMLInputElement).disabled).toBe(true);
  });

  it("does not promise incoming accepted rows after an empty online lane is terminal", () => {
    render(<StrictQualifiedList summary={localQualifiedSummary(result([]))} lane="online" terminal />);

    expect(screen.getByText("本轮已结束，没有已通过联网严格验收的候选。")).toBeTruthy();
    expect(screen.queryByText(/首批通过验收后/)).toBeNull();
  });

  it("states that missing body or subtitles are unscheduled and excluded from the target", () => {
    render(<OnlineContentEvidenceNotice count={3} followupStatus="not_scheduled" target={30} />);

    expect(screen.getByTestId("online-content-evidence-pending")).toHaveTextContent(
      "缺正文/字幕 3 人 · 本轮未安排补抓 · 不计入联网严格 30 人目标",
    );
  });

  it("shows existing, in-progress, retry, and direct follow states on strict rows", () => {
    const onFavorite = vi.fn();
    const summary = localQualifiedSummary(result([
      { kol_pool_id: 31, handle: "already", platform: "youtube", qualification_evidence: strictProof() },
      { kol_pool_id: 32, handle: "busy", platform: "instagram", qualification_evidence: strictProof() },
      { kol_pool_id: 33, handle: "retry", platform: "tiktok", qualification_evidence: strictProof() },
    ]));
    render(<StrictQualifiedList
      summary={summary}
      lane="online"
      selectedIds={new Set()}
      onSelectionChange={vi.fn()}
      favoriteIds={new Set([31])}
      favoriteBusyIds={new Set([32])}
      favoriteErrors={new Map([[33, "关注失败，请重试"]])}
      onFavorite={onFavorite}
    />);

    expect(screen.getByText("已关注")).toBeTruthy();
    expect(screen.getByRole("button", { name: "关注联网 KOL busy" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "关注联网 KOL busy" })).toHaveTextContent("关注中");
    expect(screen.getByRole("button", { name: "关注联网 KOL retry" })).toHaveTextContent("重试");
    fireEvent.click(screen.getByRole("button", { name: "关注联网 KOL retry" }));
    expect(onFavorite).toHaveBeenCalledWith(33);
  });

  it("does not qualify an incomplete or internally failed v2 proof", () => {
    const summary = localQualifiedSummary(result([
      { kol_pool_id: 20, handle: "top-only", platform: "youtube", qualification_evidence: { schema: "smart_local_gate_evidence_v2", passed: true } },
      { kol_pool_id: 21, handle: "failed-market", platform: "youtube", qualification_evidence: strictProof({ market: { passed: false } }) },
    ]));
    expect(summary.rows.map((row) => row.qualification)).toEqual(["pending", "rejected"]);
    expect(summary.qualified).toBe(0);
  });

  it("prefers an authoritative polled contact preview over the static facet", () => {
    const summary = localQualifiedSummary(result([{
      kol_pool_id: 9,
      handle: "contact-updated",
      platform: "youtube",
      qualification_evidence: strictProof(),
      candidate_facets: { contact_available: "no" },
      source_fields: { contact_preview: { status: "ready" } },
    }, {
      kol_pool_id: 10,
      handle: "contact-empty",
      platform: "youtube",
      qualification_evidence: strictProof(),
      candidate_facets: { contact_available: "yes" },
      source_fields: { contactability: { status: "empty" } },
    }]));

    expect(summary.rows[0].contactStatus).toBe("可联系");
    expect(summary.rows[1].contactStatus).toBe("暂缺");
  });

  it("shows the operational columns but never renders a contact value", () => {
    render(<LocalQualifiedList result={result([{
      kol_pool_id: 8,
      handle: "creator-eight",
      display_name: "Creator Eight",
      platform: "youtube",
      followers: 18800,
      why_fit: "面向美国市场的摄影器材测评",
      qualification_evidence: strictProof({
        followers: { value: 18800, minimum: 3000, known: true, passed: true },
        activity: { posted_at: "2026-08-03T00:00:00Z", passed: true },
        market: { value: "US", passed: true, source: "channel_profile" },
        language: { values: ["en"], targets: ["en"], passed: true },
        profile_type: { values: ["reviewer"], targets: ["reviewer"], passed: true },
        account_quality: { verdict: "eligible_creator_account", passed: true },
      }),
      candidate_facets: { contact_available: "yes" },
      source_fields: {
        server_rank: 1,
        analysis_status: "processing",
      },
    }])} />);

    expect(screen.getByText("本地合格 1/30")).toBeTruthy();
    expect(screen.getByText("Creator Eight")).toBeTruthy();
    expect(screen.getByText(/1\.9万|18\.8K|1\.88万/)).toBeTruthy();
    expect(screen.getByText(/2026.*08.*03/)).toBeTruthy();
    // 语言格现在如实标注口径:这条是他自己填的,所以只出代码、不挂「推断」角标。
    expect(screen.getByText("EN")).toBeTruthy();
    expect(screen.queryByText("推断")).toBeNull();
    expect(screen.getByText("评测号")).toBeTruthy();
    expect(screen.getByText("可联系")).toBeTruthy();
    expect(screen.getByText("分析中")).toBeTruthy();
    expect(screen.queryByText("private@example.com")).toBeNull();
  });

  it("shows growth score, confidence, four dimensions and honest missing evidence in the strict list", () => {
    const onSelectionChange = vi.fn();
    const onFavorite = vi.fn();
    render(<LocalQualifiedList result={result([{
      kol_pool_id: 18,
      handle: "growth-eighteen",
      platform: "youtube",
      qualification_evidence: strictProof(),
      source_fields: {
        growth_candidate_score: 74.5,
        product_use_fit: 90,
        market_activation: 72,
        audience_fit: null,
        content_execution: 58,
        evidence_confidence: 61,
        claim_status: "descriptive_only",
        growth_qualification_pass: false,
        market_activation_pass: false,
        market_activation_status: "insufficient_sample",
        selection_rationale: {
          schema: "prospective_candidate_rationale_v1",
          claim_status: "descriptive_only",
          decision_readiness: "decision_support_ready",
          strict_gate_status: "blocked",
          why_find_this_creator: ["公开内容同时支持产品用途和使用场景。"],
          next_action: { code: "fetch_recent_3_5_video_metrics", label: "补齐近 45 天至少 3 条视频及观看、点赞、评论数据。" },
        },
        growth_candidate_scoring: { objective: "prospective_growth", claim_status: "descriptive_only" },
      },
    }])} onSelectionChange={onSelectionChange} onFavorite={onFavorite} />);

    const growth = screen.getByTestId("local-growth-18");
    expect(growth).toHaveTextContent("增长候选分 74.5");
    expect(growth).toHaveTextContent("证据置信度 61/100");
    expect(growth).toHaveTextContent("产品适配 90");
    expect(growth).toHaveTextContent("市场推进 72");
    expect(growth).toHaveTextContent("受众适配 待补证");
    expect(growth).toHaveTextContent("内容执行 58");
    expect(growth).toHaveTextContent("仅候选 · 待补证");
    expect(growth).toHaveTextContent("为什么找：公开内容同时支持产品用途和使用场景");
    expect(growth).toHaveTextContent("下一步：补齐近 45 天至少 3 条视频");
    expect(growth).toHaveTextContent("描述性决策支持，不代表转化");
    expect(growth).not.toHaveTextContent("值得人工复核");
    expect(growth).not.toHaveTextContent("检索相关度");
    expect(screen.getByText("本地合格 0/30")).toBeTruthy();
    expect((screen.getByRole("checkbox", { name: "选择本地 KOL growth-eighteen" }) as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText("过闸后可关注")).toBeTruthy();
  });

  // 门面上的说明只要与实际行为对不上,就是在替系统说一句没验证过的话。这两句过去
  // 都不对:一句说推断「只作参考」(其实它真的在参与语言筛选),一句把「未知」定义成
  // 「两样都没有」(其实还有别的来路)。锁住改后的文案。
  //
  // 历轮锁住的两件事:
  //  · 界面显示四档,说明就得讲满四档 —— 少讲的那一档会被顺手当成「他自己填的」;
  //  · 「两样都没有就是未知」这种排他式写法一个字都不许留 —— 照它读,「来源不明」
  //    的人本该显示成「未知」,与眼前看到的直接打架。
  //
  // 本轮:说明里只许出现界面上真会出现的状态。「手上两份记录说法不一致、判不出该信
  // 哪一份」那一档,随门面仲裁被删一起消失了 —— 归属现在只由服务端裁决说了算,门面
  // 不再自己判该信哪份,这个状态永远不会再出现,留着它就是在描述一个不存在的东西。
  // 顶上它位置的是「未知」真正的第二种来路:试着判断过、但把握不够,没当结论。
  it("keeps the language tooltips honest about filtering and about what 未知 covers", () => {
    render(<LocalQualifiedList result={result([{
      kol_pool_id: 11,
      handle: "inferred-eleven",
      platform: "youtube",
      followers: 21000,
      qualification_evidence: strictProof({
        language: { values: ["en"], targets: ["en"], origin: "inferred", passed: true },
      }),
      source_fields: { server_rank: 1 },
    }])} />);

    const statCopy = screen.getByTestId("local-language-origin-stat").getAttribute("title") || "";
    // 推断值确实在参与硬筛,不许再说它「只作参考,不改任何合格标准」。
    expect(statCopy).not.toContain("只作参考");
    expect(statCopy).toContain("参与语言筛选");
    // 但也不许反过来吓人:被影响的只有语言这一条,别的合格标准一格没动。
    expect(statCopy).toContain("语言之外的其他合格标准不受影响");
    const headerCopy = screen.getByText("语言").getAttribute("title") || "";

    // 四档一档不少 —— 界面会显示哪几档,说明就得讲哪几档。
    ["自报", "推断", "来源不明", "未知"].forEach((tier) => {
      expect(statCopy).toContain(tier);
      expect(headerCopy).toContain(tier);
    });

    // 「未知」的定义要说全:我们这里没有他的语言,**或者**试着判断过、但把握不够,
    // 没当结论 —— 后者才是现在真正的第二种来路。
    expect(statCopy).toContain("把握不够");
    expect(headerCopy).toContain("把握不够");
    // 门面仲裁已经删了,这一档永远不会出现在界面上,说明里一个字都不许留。
    expect(statCopy).not.toContain("说法不一致");
    expect(headerCopy).not.toContain("说法不一致");

    // 排他式写法必须绝迹:照「两样都没有就是未知」读,「来源不明」的人本该显示成
    // 「未知」,而他实际上带着值和「来源不明」角标显示 —— 说明与显示直接打架。
    ["两样都没有", "两者都没有", "都没有就是"].forEach((phrase) => {
      expect(statCopy).not.toContain(phrase);
      expect(headerCopy).not.toContain(phrase);
    });

    // 有推断值但印证不够的那一票:说明里必须有它的位置,并且写明它按「未知」算、
    // 不算进「推断」—— 否则操作员会以为我们连试都没试过,或者以为它被算成了推断。
    expect(statCopy).toContain("印证");
    expect(statCopy).toContain("不算进上面的「推断」");
    expect(headerCopy).toContain("印证不够");

    // 门面禁内部术语。
    ["置信度", "provenance", "origin", "projected", "哨兵"].forEach((term) => {
      expect(statCopy).not.toContain(term);
      expect(headerCopy).not.toContain(term);
    });
  });

  it("falls back to the server-owned follower proof when the session row has no root value", () => {
    const summary = localQualifiedSummary(result([{
      kol_pool_id: 9,
      handle: "proof-only",
      platform: "youtube",
      qualification_evidence: strictProof({
        followers: { value: 9600, minimum: 3000, known: true, passed: true },
      }),
    }]));

    expect(summary.rows[0].followers).toBe(9600);
  });
});

describe("search display cache privacy", () => {
  it("removes nested contact values but preserves status and counts", () => {
    const safe = sanitizeSearchDisplayForCache({
      input: "85mm portrait",
      activeSearchSession: {
        items: [{
          payload: {
            handle: "creator",
            email: "private@example.com",
            phone_number: "+1 555 0100",
            contact_channels: { whatsapp: "+1 555 0100" },
            contact_value: "private@example.com",
            other_contacts: [{ type: "telegram", value: "private-handle" }],
            contact_links_json: [{ type: "website", value: "https://private.example" }],
            contact_raw_json: { email: "private@example.com" },
            public_contact_value: "private@example.com",
            business_contact: "private@example.com",
            candidateContact: "+1 555 0100",
            external_contact: "private-handle",
            contactEmail: "private@example.com",
            contactUrl: "https://private.example",
            contactinfo: "private@example.com",
            lineId: "private_line_id",
            contact_preview: { status: "ready", channel_count: 2, email: "p***@example.com" },
            contact_enrichment: { status: "ready", count: 2, raw_value: "private@example.com" },
          },
        }],
      },
    });

    const payload = (safe as any).activeSearchSession.items[0].payload;
    expect(payload.handle).toBe("creator");
    expect(payload.email).toBeUndefined();
    expect(payload.phone_number).toBeUndefined();
    expect(payload.contact_channels).toBeUndefined();
    expect(payload.contact_value).toBeUndefined();
    expect(payload.other_contacts).toBeUndefined();
    expect(payload.contact_links_json).toBeUndefined();
    expect(payload.contact_raw_json).toBeUndefined();
    expect(payload.public_contact_value).toBeUndefined();
    expect(payload.business_contact).toBeUndefined();
    expect(payload.candidateContact).toBeUndefined();
    expect(payload.external_contact).toBeUndefined();
    expect(payload.contactEmail).toBeUndefined();
    expect(payload.contactUrl).toBeUndefined();
    expect(payload.contactinfo).toBeUndefined();
    expect(payload.lineId).toBeUndefined();
    expect(payload.contact_preview).toEqual({ status: "ready", channel_count: 2 });
    expect(payload.contact_enrichment).toEqual({ status: "ready", count: 2 });
  });

  it("cleans contact values left by an older cache version when it is read", () => {
    window.sessionStorage.setItem("vkpi:activeKolSearchDisplay", JSON.stringify({
      input: "portrait",
      mode: "text",
      recallResult: { items: [{ source_fields: { email: "old@example.com", contact_status: "ready" } }] },
      urlResult: null,
      activeSearchSession: null,
      activeSearchSessionId: null,
    }));

    const restored = readPersistedSearchDisplay() as any;
    expect(restored.recallResult.items[0].source_fields).toEqual({ contact_status: "ready" });
    expect(window.sessionStorage.getItem("vkpi:activeKolSearchDisplay")).not.toContain("old@example.com");
  });

  it("redacts contact values embedded in ordinary strings but preserves public copy and profile URLs", () => {
    const safe = sanitizeSearchDisplayForCache({
      bio: "Camera creator. Email private@example.com or call +1 (555) 010-2020 for work.",
      messenger: "WhatsApp: @private_handle / Telegram: @private_handle",
      mail_route: "mailto:private@example.com",
      tel_route: "tel:+15550102020",
      social_route_a: "https://wa.me/15550102020",
      social_route_b: "https://t.me/private_handle",
      candidate_link: "https://creator.example/contact",
      publicBio: "Camera reviews, portrait tutorials, and weekly field tests.",
      profile_url: "https://www.youtube.com/@public_creator",
      handle: "@public_creator",
    }) as any;

    expect(safe.bio).toContain("Camera creator.");
    expect(safe.bio).not.toContain("private@example.com");
    expect(safe.bio).not.toContain("555");
    expect(safe.messenger).toBeUndefined();
    expect(safe.mail_route).toBe("");
    expect(safe.tel_route).toBe("");
    expect(safe.social_route_a).toBe("");
    expect(safe.social_route_b).toBe("");
    expect(safe.candidate_link).toBe("");
    expect(safe.publicBio).toBe("Camera reviews, portrait tutorials, and weekly field tests.");
    expect(safe.profile_url).toBe("https://www.youtube.com/@public_creator");
    expect(safe.handle).toBe("@public_creator");
  });

  it("redacts nested social DM routes and contact URLs without erasing safe platform copy", () => {
    const safe = sanitizeSearchDisplayForCache({
      profile_flow: {
        profile_data: {
          bio: [
            "Camera creator.", "Messenger: private_handle", "DM me on Instagram @igprivate",
            "message me on TikTok @tikprivate", "Facebook DM @fbprivate",
            "Twitter @xprivate message me", "@reverseprivate on X DM me",
          ].join(" "),
          safe_copy: "Messenger app review. Follow @creator on Instagram for reviews.",
          instagram_profile: "https://www.instagram.com/public_creator/",
        },
      },
      routes: [
        "https://m.me/private", "https://line.me/R/ti/p/~private", "https://signal.me/#p/+15550100",
        "https://discord.gg/private", "https://discord.com/invite/private", "https://discord.com/users/123",
        "https://discord.com/channels/@me/123", "https://instagram.com/direct/t/123",
        "https://x.com/messages/compose?recipient_id=1", "https://twitter.com/messages/123",
        "https://facebook.com/messages/t/123", "sms:+15550100",
      ],
    }) as any;

    const profile = safe.profile_flow.profile_data;
    expect(profile.bio).toBe("Camera creator.");
    expect(profile.safe_copy).toBe("Messenger app review. Follow @creator on Instagram for reviews.");
    expect(profile.instagram_profile).toBe("https://www.instagram.com/public_creator/");
    expect(safe.routes).toEqual(Array(12).fill(""));
  });

  it("keeps only bounded enum, boolean, and numeric values in contact status containers", () => {
    const safe = sanitizeSearchDisplayForCache({
      contact_preview: {
        status: "ready",
        state: "mailto:private@example.com",
        score: "private@example.com",
        channel_count: 2,
        count: 1_000_001,
        available: true,
        note: "private@example.com",
      },
    }) as any;

    expect(safe.contact_preview).toEqual({ status: "ready", channel_count: 2, available: true });
  });
});

// 「从没抓到过视频」桶:不计入 30 人目标数,但既然返回给了操作员,就必须
// 一眼看得出与真·活跃的人不同,而且点得动。返回了却点不动是最坏的一种。
function deferredProof(): Record<string, unknown> {
  return strictProof({
    passed: false,
    deferred: true,
    deferred_reason: "latest_video_unknown",
    rejection_reasons: [],
    activity: {
      passed: false,
      known: false,
      deferred: true,
      age_days: null,
      posted_at: null,
      status: "activity_unknown_pending_fetch",
      deferred_reason: "latest_video_unknown",
    },
  });
}

function contractResult(items: any[], contract: Record<string, unknown>): any {
  return {
    ...result(items),
    local_qualification: {
      schema: "smart_local_qualified_v2",
      policy: { target_count: 30 },
      ...contract,
    },
  };
}

describe("activity-unknown bucket stays honest and stays clickable", () => {
  it("labels it as never-crawled instead of rejected, and keeps it out of the 30", () => {
    const summary = localQualifiedSummary(contractResult(
      [
        { kol_pool_id: 1, handle: "fresh", platform: "youtube", qualification_evidence: strictProof() },
        { kol_pool_id: 2, handle: "never-crawled", platform: "youtube", qualification_evidence: deferredProof() },
      ],
      { qualified_count: 1, returned_count: 2, qualified_returned_count: 1, shortfall: 29 },
    ));

    expect(summary.rows.map((row) => row.qualification)).toEqual(["qualified", "pending"]);
    expect(summary.rows[1].qualificationLabel).toBe("活跃度未知 · 从没抓到过视频");
    expect(summary.rows[1].activityUnknown).toBe(true);
    expect(summary.rows[1].strictQualified).toBe(false);
    expect(summary.activityUnknown).toBe(1);
    expect(summary.pending).toBe(0);
    expect(summary.rejected).toBe(0);
    expect(summary.qualified).toBe(1);
    expect(summary.shortfall).toBe(29);
  });

  it("reads the marker off a replayed session item that carries no proof block", () => {
    const summary = localQualifiedSummary(result([
      {
        kol_pool_id: 5,
        handle: "replayed",
        platform: "youtube",
        selection_tier: "deferred_activity_unknown",
        activity_status: "activity_unknown_pending_fetch",
      },
    ]));

    expect(summary.rows[0].activityUnknown).toBe(true);
    expect(summary.rows[0].qualificationLabel).toBe("活跃度未知 · 从没抓到过视频");
  });

  it("lets the operator tick it one by one while keeping it out of 全选", () => {
    const onSelectionChange = vi.fn();
    const summary = localQualifiedSummary(contractResult(
      [
        { kol_pool_id: 1, handle: "fresh", platform: "youtube", qualification_evidence: strictProof() },
        { kol_pool_id: 2, handle: "never-crawled", platform: "youtube", qualification_evidence: deferredProof() },
      ],
      { qualified_count: 1, returned_count: 2, qualified_returned_count: 1, shortfall: 29 },
    ));
    render(<StrictQualifiedList summary={summary} selectedIds={new Set()} onSelectionChange={onSelectionChange} />);

    const unknownBox = screen.getByRole("checkbox", { name: "选择本地 KOL never-crawled" }) as HTMLInputElement;
    expect(unknownBox.disabled).toBe(false);
    fireEvent.click(unknownBox);
    expect([...onSelectionChange.mock.calls[0][0]]).toEqual([2]);

    fireEvent.click(screen.getByRole("checkbox", { name: "全选本地合格 KOL" }));
    expect([...onSelectionChange.mock.calls[1][0]]).toEqual([1]);

    expect(screen.getByTestId("local-activity-unknown-count").textContent)
      .toContain("从没抓到过视频 1（不计入 30 人；增长候选先补证）");
    expect(screen.getByText("从没抓到过")).toBeTruthy();
    expect(screen.getByText("还缺 29 人", { exact: false })).toBeTruthy();
    expect(screen.queryByText("未通过", { exact: false })).toBeNull();
  });
});
