import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AccountUrlInlineOverview } from "./SmartKolInputPanel.UrlSummary.AccountOverview";

function renderOverview(item: Record<string, unknown>, videos: Array<Record<string, unknown>> = []) {
  render(
    <AccountUrlInlineOverview
      item={item}
      bundle={null}
      dossier={{ coverage: {}, videos }}
      recommendation={{}}
      loading={false}
      error=""
      freshness={{}}
    />,
  );
}

describe("AccountUrlInlineOverview observed metrics", () => {
  it("hides the empty metric grid and leaves one compact completion action", () => {
    renderOverview({ avg_views: null, avg_likes: null, avg_comments: null, engagement_rate: null });

    expect(screen.queryByTestId("account-observed-metrics")).toBeNull();
    expect(screen.getByTestId("account-metrics-completion-action")).toHaveTextContent("补全表现数据");
  });

  it("treats an explicit zero comment metric as observed data", () => {
    renderOverview({ avg_comments: 0 });

    const metrics = screen.getByTestId("account-observed-metrics");
    expect(within(metrics).getByText("账号均评论")).toBeTruthy();
    expect(within(metrics).getByText("0")).toBeTruthy();
    expect(screen.queryByTestId("account-metrics-completion-action")).toBeNull();
  });

  it("does not coerce a missing video comment count to a real zero", () => {
    renderOverview({}, [{ comment_count: null }]);

    expect(screen.queryByTestId("account-observed-metrics")).toBeNull();
    expect(screen.getByTestId("account-metrics-completion-action")).toBeTruthy();
  });

  it("keeps an explicit sampled zero comment count visible", () => {
    renderOverview({}, [{ comment_count: 0 }]);

    const metrics = screen.getByTestId("account-observed-metrics");
    expect(within(metrics).getByText("样本均评论")).toBeTruthy();
    expect(within(metrics).getByText("0")).toBeTruthy();
  });
});
