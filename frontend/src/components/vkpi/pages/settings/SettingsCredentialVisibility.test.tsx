import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SettingsApiKeyPoolPanel } from "./SettingsPage.Sections";
import { SettingsProviderGrid } from "./SettingsPage.fragments";

const leakedPrefix = "sensitive-prefix...";

describe("settings credential visibility", () => {
  it("never renders provider key_mask values returned by an older backend", () => {
    render(
      <SettingsProviderGrid
        providers={[
          {
            provider: "youtube",
            label: "YouTube Data API",
            configured: true,
            key_mask: leakedPrefix,
            latest_status: "healthy",
          },
        ]}
      />,
    );

    expect(screen.getByText("凭据已配置（仅服务端保存，不回显）")).toBeTruthy();
    expect(screen.queryByText(leakedPrefix)).toBeNull();
    expect(document.body.textContent).not.toContain("sensitive-prefix");
  });

  it("uses an API-key-pool configuration status instead of its stored prefix", () => {
    render(
      <SettingsApiKeyPoolPanel
        apiKeyPool={[
          {
            id: 7,
            account_name: "youtube-primary",
            provider: "youtube",
            credential_status: "configured",
            key_prefix: leakedPrefix,
            daily_quota: 100,
            enabled: true,
          },
        ]}
        keyDraft={{ account_name: "", provider: "youtube", key: "", daily_quota: "", enabled: true }}
        busy={false}
        onKeyDraftChange={vi.fn()}
        onToggleApiKey={vi.fn()}
        onRemoveApiKey={vi.fn()}
        onSaveApiKey={vi.fn()}
      />,
    );

    expect(screen.getByText("(已配置,留空不改)")).toBeTruthy();
    expect(screen.queryByText(leakedPrefix)).toBeNull();
    expect(document.body.textContent).not.toContain("sensitive-prefix");
  });
});
