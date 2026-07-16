import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("framer-motion", () => {
  const cache: Record<string, unknown> = {};
  const proxy = new Proxy({}, {
    get: (_target, key: string) => {
      if (!cache[key]) {
        cache[key] = (props: Record<string, unknown>) =>
          React.createElement(key, props, props.children as React.ReactNode);
      }
      return cache[key];
    },
  });
  return { m: proxy, motion: proxy };
});

import { RepresentativeVideoPlayerModal } from "./KOLDetailDrawer.Panels";

describe("RepresentativeVideoPlayerModal cached-video fallback", () => {
  it("stops a broken cache player and keeps every original-post link usable", () => {
    const digest = "a".repeat(64);
    const cachedRoute = `/api/vkpi-media/video-cache/${digest}`;
    const originalPost = "https://www.instagram.com/reel/RealPostA/";
    const { container } = render(
      <RepresentativeVideoPlayerModal
        video={{
          id: 581,
          platform: "instagram",
          title: "Stale cached video",
          cached_video_url: cachedRoute,
          watch_url: cachedRoute,
          content_url: originalPost,
        }}
        onClose={vi.fn()}
      />,
    );

    const player = container.querySelector("video");
    expect(player).toHaveAttribute("src", cachedRoute);
    expect(screen.getByRole("link", { name: "打开原帖" })).toHaveAttribute("href", originalPost);

    fireEvent.error(player as HTMLVideoElement);

    expect(container.querySelector("video")).toBeNull();
    expect(screen.getByText("缓存视频加载失败")).toBeInTheDocument();
    expect(screen.getByText("已停止使用失效缓存；可以打开原帖查看")).toBeInTheDocument();
    const originalLinks = screen.getAllByRole("link", { name: "打开原帖" });
    expect(originalLinks.length).toBeGreaterThanOrEqual(1);
    originalLinks.forEach((link) => expect(link).toHaveAttribute("href", originalPost));
  });
});
