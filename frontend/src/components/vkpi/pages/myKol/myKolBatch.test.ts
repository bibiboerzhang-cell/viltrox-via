import { describe, expect, it } from "vitest";

import { buildKolCsv } from "./myKolBatch";

describe("buildKolCsv spreadsheet safety", () => {
  it("neutralizes formula prefixes in externally sourced names and handles", () => {
    const csv = buildKolCsv([
      {
        name: "=HYPERLINK(\"https://invalid.example\",\"open\")",
        platform: "youtube",
        handle: "  @SUM(1+1)",
        followers: "12,345",
        fit: 81.2,
        email: "creator@example.com",
      },
    ]);

    expect(csv).toContain("\"'=HYPERLINK(\"\"https://invalid.example\"\",\"\"open\"\")\"");
    expect(csv).toContain("'  @SUM(1+1)");
    expect(csv).not.toContain("\n=HYPERLINK");
  });

  it("keeps ordinary text and numeric fields unchanged", () => {
    const csv = buildKolCsv([
      {
        name: "Alpha Camera",
        platform: "instagram",
        handle: "alpha_camera",
        followers: "1000",
        fit: 72,
        email: "alpha@example.com",
      },
    ]);

    expect(csv).toContain("Alpha Camera,instagram,alpha_camera,1000,72.0,a***@e***");
  });
});
