import { describe, expect, it } from "vitest";

import type { VkpiKolSearchHistoryItem } from "../../../../domains/kol";

import {
  onlineQualifiedSummaryFromSession,
  strictOnlineDiscoveryPlatforms,
} from "./SmartKolInputPanel.OnlineQualified";

function proof(overrides: Record<string, unknown> = {}) {
  return {
    schema: "smart_local_gate_evidence_v2",
    passed: true,
    account_quality: { passed: true, verdict: "creator" },
    followers: { passed: true, value: 12_000 },
    activity: { passed: true, posted_at: "2026-08-10T00:00:00Z" },
    market: { passed: true, value: "US", source: "profile_declared_country" },
    language: { passed: true, values: ["en"] },
    profile_type: { passed: true, values: ["creator"] },
    platform: { passed: true, values: ["youtube"] },
    relevance: { passed: true, evidence: [{ field: "bio", term: "camera", source: "server_profile_evidence" }] },
    ...overrides,
  };
}

function onlineItem(id: number, serverRank: number, proofValue = proof()) {
  const canonicalFingerprint = String(id).padStart(64, "0");
  const globalUniqueRank = 30 + serverRank;
  return {
    id: 100 + id,
    item_type: "online_qualified_candidate",
    status: "ready",
    rank: serverRank,
    kol_pool_id: id,
    source_url: `https://youtube.com/@creator${id}`,
    payload: {
      schema: "smart_online_net_new_qualified_v1",
      origin_lane: "online",
      source: "platform_discovery_strict",
      qualification_status: "accepted",
      canonical_fingerprint: canonicalFingerprint,
      server_rank: serverRank,
      global_unique_rank: globalUniqueRank,
      snapshot_revision: 3,
      snapshot_id: "snapshot-three",
      handle: `creator${id}`,
      display_name: `Creator ${id}`,
      platform: "youtube",
      followers: 12_000,
      profile_type: "creator",
      qualification_evidence: {
        kol_pool_id: id,
        canonical_fingerprint: canonicalFingerprint,
        snapshot_id: "snapshot-three",
        snapshot_revision: 3,
        server_rank: serverRank,
        global_unique_rank: globalUniqueRank,
        ...proofValue,
      },
      contact_preview: { status: "not_enriched", channel_count: 0 },
    },
  };
}

function session(items: unknown[]): VkpiKolSearchHistoryItem {
  return {
    id: 7,
    status: "partial",
    items: items as VkpiKolSearchHistoryItem["items"],
    result_summary: {
      online_qualification: {
        schema: "smart_online_net_new_qualified_v1",
        policy_version: 1,
        server_owned: true,
        origin_lane: "online",
        source: "platform_discovery_strict",
        status: "shortfall",
        terminal: true,
        snapshot_complete: true,
        snapshot_revision: 3,
        snapshot_id: "snapshot-three",
        target_count: 30,
        evaluated_count: 44,
        strict_qualified_count: 4,
        net_new_accepted_count: 2,
        returned_count: 2,
        pending_count: 5,
        rejected_count: 12,
        duplicate_local_count: 2,
        duplicate_online_count: 3,
        duplicate_local_inventory_count: 1,
        provider_rounds: 3,
        provider_calls: 3,
        candidate_budget: 150,
        candidate_budget_used: 44,
        shortfall: 28,
        shortfall_reasons: { market_mismatch: 8, duplicate_local: 2 },
      },
    },
  };
}

describe("SmartKolInputPanel online strict lane", () => {
  it("never sends unsupported Facebook into the strict-online provider lane", () => {
    expect(strictOnlineDiscoveryPlatforms(["facebook", "youtube", "youtube"])).toEqual(["youtube"]);
    expect(strictOnlineDiscoveryPlatforms(["facebook"])).toEqual(["youtube", "instagram", "tiktok"]);
  });

  it("counts only exact accepted v1 rows with complete strict-v2 proof and orders by server rank", () => {
    const incomplete = proof({ language: { passed: false, values: ["ja"] } });
    const value = session([
      onlineItem(12, 2, incomplete),
      { item_type: "new_creator", kol_pool_id: 99, payload: { handle: "legacy-discovery" } },
      onlineItem(11, 1),
    ]);

    const summary = onlineQualifiedSummaryFromSession(value);

    expect(summary.contractValid).toBe(true);
    expect(summary.rows.map((row) => row.item.kol_pool_id)).toEqual([11, 12]);
    expect(summary.qualified).toBe(1);
    expect(summary.pending).toBe(5);
    expect(summary.rejected).toBe(13);
    expect(summary.selectionReady).toBe(true);
    expect(summary.duplicateLocal).toBe(2);
    expect(summary.duplicateOnline).toBe(3);
    expect(summary.duplicateLocalInventory).toBe(1);
    expect(summary.shortfallReasons).toEqual(["不符合目标市场 8", "与本地名单重复 2"]);
  });

  it("fails closed when the aggregate contract is absent or a row revision does not match", () => {
    const noContract = onlineQualifiedSummaryFromSession({ items: [onlineItem(11, 1)] } as VkpiKolSearchHistoryItem);
    expect(noContract.qualified).toBe(0);
    expect(noContract.rows[0]).toMatchObject({ qualification: "pending", strictQualified: false });
    expect(noContract.selectionReady).toBe(false);

    const mismatch = session([{
      ...onlineItem(11, 1),
      payload: { ...onlineItem(11, 1).payload, snapshot_revision: 2 },
    }]);
    const mismatchSummary = onlineQualifiedSummaryFromSession(mismatch);
    expect(mismatchSummary.qualified).toBe(0);
    expect(mismatchSummary.rows[0]).toMatchObject({ qualification: "pending", strictQualified: false });
  });

  it("rejects copied proof bindings and payloads from another snapshot", () => {
    const wrongPool = onlineItem(11, 1, proof({ kol_pool_id: 99 }));
    const copiedProof = onlineItem(12, 2, proof({ canonical_fingerprint: String(11).padStart(64, "0") }));
    const wrongSnapshot = onlineItem(13, 3);
    wrongSnapshot.payload.snapshot_id = "older-snapshot";

    const summary = onlineQualifiedSummaryFromSession(session([wrongPool, copiedProof, wrongSnapshot]));
    expect(summary.qualified).toBe(0);
    expect(summary.rows.every((row) => row.qualification === "pending" && !row.strictQualified)).toBe(true);
  });

  it("rejects proof ranks or revisions copied from another accepted row", () => {
    const copiedRanks = onlineItem(12, 2, proof({ server_rank: 1, global_unique_rank: 31 }));
    const staleProof = onlineItem(13, 3, proof({ snapshot_revision: 2 }));
    const summary = onlineQualifiedSummaryFromSession(session([copiedRanks, staleProof]));
    expect(summary.qualified).toBe(0);
    expect(summary.rows.every((row) => row.qualification === "pending")).toBe(true);
  });

  it("requires relevance evidence even when every proof bit says passed", () => {
    const missingEvidence = proof({ relevance: { passed: true, evidence: [] } });
    const summary = onlineQualifiedSummaryFromSession(session([onlineItem(11, 1, missingEvidence)]));
    expect(summary.qualified).toBe(0);
    expect(summary.rows[0]).toMatchObject({ qualification: "pending", strictQualified: false });
  });

  it("does not invent a creator type or language when unfiltered strict gates pass unknown values", () => {
    const unknownFacets = proof({
      language: { passed: true, values: [], targets: [] },
      profile_type: { passed: true, values: [], targets: [] },
    });
    const item = onlineItem(11, 1, unknownFacets);
    item.payload.profile_type = "";
    const summary = onlineQualifiedSummaryFromSession(session([item]));

    expect(summary.qualified).toBe(1);
    expect(summary.rows[0].profileType).toBe("");
    expect(summary.rows[0].languageEvidence).toBe("");
  });
});
