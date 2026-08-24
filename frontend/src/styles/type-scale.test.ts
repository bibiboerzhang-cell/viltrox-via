import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_SRC = path.resolve(HERE, "..");
const TYPE_SCALE_PATH = path.join(HERE, "type-scale.css");

const COCKPIT_TYPE_SOURCES = [
  "components/vkpi/cockpit/components/ai-evidence-cards.css",
  "components/vkpi/cockpit/components/provenance/provenance.css",
  "components/vkpi/cockpit/styles/cockpit-reference.ask-details.css",
  "components/vkpi/cockpit/styles/cockpit-reference.chrome.css",
  "components/vkpi/cockpit/styles/cockpit-reference.modules.css",
  "components/vkpi/cockpit/styles/cockpit-reference.routes.css",
  "components/vkpi/cockpit/styles/cockpit-reference.shell.css",
  "components/vkpi/cockpit/styles/dashboard-mockup.css",
  "components/vkpi/cockpit/styles/mockup.css",
  "components/vkpi/pages/myKol/myKolContentLayer.css",
  "components/vkpi/pages/myKol/myKolEmployeeLayer.css",
  "components/vkpi/pages/myKol/myKolPage.css",
  "components/vkpi/pages/myKol/myKolTeamMatrix.css",
] as const;

const RAW_SMALL_FONT_SIZE = /font-size:\s*(?:7(?:\.5)?|8(?:\.5)?|9(?:\.5)?|10(?:\.5)?|11(?:\.5)?|12(?:\.5)?|13(?:\.5)?)px\b/;
const RAW_SMALL_FONT_SHORTHAND = /\bfont:\s*[^;{}]*(?:7(?:\.5)?|8(?:\.5)?|9(?:\.5)?|10(?:\.5)?|11(?:\.5)?|12(?:\.5)?|13(?:\.5)?)px\b/;

describe("cockpit ultrawide type scale", () => {
  it("keeps the legacy raw-CSS bridge pixel-exact below 1920px", () => {
    const source = fs.readFileSync(TYPE_SCALE_PATH, "utf8");

    for (const [token, value] of [
      ["7", "7px"], ["7-5", "7.5px"], ["8", "8px"], ["8-5", "8.5px"],
      ["9", "9px"], ["9-5", "9.5px"], ["10", "10px"], ["10-5", "10.5px"],
      ["11", "11px"], ["11-5", "11.5px"], ["12", "12px"], ["12-5", "12.5px"],
      ["13", "13px"], ["13-5", "13.5px"],
    ]) {
      expect(source).toContain(`--ds-cockpit-fs-${token}: ${value};`);
    }
  });

  it("enables the comfort scale only at the 1920px ultrawide boundary", () => {
    const source = fs.readFileSync(TYPE_SCALE_PATH, "utf8");
    const mediaStart = source.indexOf("@media (min-width: 1920px)");
    const mediaSource = mediaStart >= 0 ? source.slice(mediaStart) : "";

    expect(mediaStart).toBeGreaterThan(0);
    expect(source).not.toContain("@media (min-width: 1919px)");
    expect(mediaSource).toContain(".cockpit-shell");
    expect(mediaSource).toContain(".vkpi-page-stage--overlay");
    expect(mediaSource).not.toMatch(/\n\s*:root\s*{/);
    expect(mediaSource).toContain("--ds-fs-9: 11px;");
    expect(mediaSource).toContain("--ds-fs-11: 13px;");
    expect(mediaSource).toContain("--ds-fs-13: 15px;");
    expect(mediaSource).toContain("--ds-cockpit-fs-11: var(--ds-fs-11);");
    expect(source).not.toMatch(/(?:^|[;{])\s*(?:zoom|transform)\s*:/m);
  });

  it.each(COCKPIT_TYPE_SOURCES)("routes small text in %s through the shared bridge", (relativePath) => {
    const source = fs.readFileSync(path.join(FRONTEND_SRC, relativePath), "utf8");

    expect(source).not.toMatch(RAW_SMALL_FONT_SIZE);
    expect(source).not.toMatch(RAW_SMALL_FONT_SHORTHAND);
  });

  it("widens only the dashboard canvas at the same ultrawide boundary", () => {
    const source = fs.readFileSync(
      path.join(FRONTEND_SRC, "components/vkpi/cockpit/styles/cockpit-reference.routes.css"),
      "utf8",
    );

    expect(source).toMatch(/\.vkpi-dashboard-canvas\s*{[^}]*max-width:\s*1560px;/s);
    expect(source).toMatch(/@media \(min-width: 1920px\)\s*{\s*\.vkpi-dashboard-canvas\s*{\s*max-width:\s*1920px;/s);
  });

  it("sizes one-line My KOL text slots from their computed line height", () => {
    const source = fs.readFileSync(
      path.join(FRONTEND_SRC, "components/vkpi/pages/myKol/myKolEmployeeLayer.css"),
      "utf8",
    );
    const cardText = source.slice(source.indexOf(".mykol-employee-content .vkpi-my-kol-content-card__body h3"));

    expect(cardText).toContain("min-height: 1lh;");
    expect(cardText).toContain("max-height: 1lh;");
    expect(cardText).not.toMatch(/(?:min|max)-height:\s*(?:15|17)px/);
  });
});
