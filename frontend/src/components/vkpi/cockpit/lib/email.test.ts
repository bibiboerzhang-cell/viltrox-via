import { describe, expect, it } from "vitest";

import {
  genEmailBody,
  genEmailSubject,
} from "./email";
import {
  isOpaqueKolChannelId,
  kolHumanDisplayName,
  kolHumanIdentitySubtitle,
} from "./kolIdentity";

describe("KOL outreach identity", () => {
  const youtubeCreator = {
    display_name: "Future Shock Studios",
    handle: "UChYZp0fnZylVSXmAoocVS_w",
    channel_id: "UChYZp0fnZylVSXmAoocVS_w",
    platform: "youtube",
  };

  it("uses the creator display name instead of a YouTube channel id", () => {
    expect(genEmailSubject("Viltrox 镜头", youtubeCreator)).toBe(
      "Viltrox × Future Shock Studios · Viltrox 镜头 Collaboration",
    );
    expect(genEmailSubject("EPIC Cine", youtubeCreator)).toBe(
      "Viltrox Cine Series · Future Shock Studios Invitation",
    );
    expect(genEmailBody("Viltrox 镜头", youtubeCreator)).toContain("Hi Future,");
    expect(genEmailBody("Viltrox 镜头", youtubeCreator)).not.toContain(youtubeCreator.handle);
  });

  it("suppresses opaque channel ids from modal subtitles", () => {
    expect(isOpaqueKolChannelId(youtubeCreator.handle, youtubeCreator)).toBe(true);
    expect(kolHumanDisplayName(youtubeCreator)).toBe("Future Shock Studios");
    expect(kolHumanIdentitySubtitle(youtubeCreator)).toBe("Future Shock Studios");
  });

  it("keeps a useful public handle as secondary context", () => {
    const creator = { display_name: "Frank Trades", handle: "@frank" };
    expect(kolHumanIdentitySubtitle(creator)).toBe("Frank Trades · @frank");
  });

  it("never falls back to an opaque id when no display name exists", () => {
    expect(kolHumanDisplayName({ handle: "UC0123456789abcdefghij" })).toBe("Creator");
    expect(genEmailSubject("Viltrox 镜头", { handle: "UC0123456789abcdefghij" })).toContain("Viltrox × Creator");
  });

  it("uses the current sender identity and has a generic fallback", () => {
    expect(genEmailBody("Lens", youtubeCreator, { name: "Alice" })).toContain("I'm Alice from Viltrox");
    expect(genEmailBody("Lens", youtubeCreator, { name: "Alice" })).toContain("Best,\nAlice\nViltrox");
    expect(genEmailBody("Lens", youtubeCreator)).toContain("I'm with Viltrox Partnerships");
    expect(genEmailBody("Lens", youtubeCreator)).toContain("Best,\nViltrox Partnerships");
    expect(genEmailBody("Lens", youtubeCreator)).not.toContain("Jianbo");
  });
});
