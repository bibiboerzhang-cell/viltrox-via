import { describe, expect, it } from "vitest";

import { normalizeKolOption } from "./EditGroupModal";

describe("EditGroupModal KOL identity", () => {
  it("keeps opaque YouTube ids out of the group share selector", () => {
    const machineId = "UC0123456789abcdefghij";
    const option = normalizeKolOption({
      id: 7,
      handle: machineId,
      channel_id: machineId,
      platform: "youtube",
      profile_url: `https://www.youtube.com/channel/${machineId}`,
    });

    expect(option).toEqual({ id: "7", name: "Creator", sub: "youtube" });
    expect(JSON.stringify(option)).not.toContain(machineId);
  });
});
