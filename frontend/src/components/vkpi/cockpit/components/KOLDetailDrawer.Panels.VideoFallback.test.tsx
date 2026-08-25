import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("framer-motion", () => {
  const cache: Record<string, unknown> = {};
  const proxy = new Proxy({}, {
    get: (_target, key: string) => {
      if (!cache[key]) {
        cache[key] = React.forwardRef((props: Record<string, unknown>, ref: React.ForwardedRef<HTMLElement>) =>
          React.createElement(key, { ...props, ref }, props.children as React.ReactNode));
      }
      return cache[key];
    },
  });
  return { m: proxy, motion: proxy };
});

import { RepresentativeVideoPlayerModal } from "./KOLDetailDrawer.Panels";
import { CenterModal } from "./modals/CenterModal";

describe("RepresentativeVideoPlayerModal cached-video fallback", () => {
  it("keeps Escape and Tab on the topmost player, then restores focus to the lower dialog", async () => {
    const closeLower = vi.fn();
    const closePlayer = vi.fn();
    const externalOpener = document.createElement("button");
    externalOpener.textContent = "打开底层";
    document.body.appendChild(externalOpener);
    externalOpener.focus();

    function NestedLayers() {
      const [playerOpen, setPlayerOpen] = React.useState(false);
      return (
        <CenterModal ariaLabel="KOL 详情底层" onClose={closeLower}>
          <button type="button" data-modal-initial-focus onClick={() => setPlayerOpen(true)}>打开播放器</button>
          {playerOpen ? (
            <RepresentativeVideoPlayerModal
              video={{
                platform: "instagram",
                title: "嵌套代表作",
                content_url: "https://www.instagram.com/reel/nested/",
              }}
              onClose={() => {
                closePlayer();
                setPlayerOpen(false);
              }}
            />
          ) : null}
        </CenterModal>
      );
    }

    const view = render(<NestedLayers />);
    const openPlayer = screen.getByRole("button", { name: "打开播放器" });
    await waitFor(() => expect(openPlayer).toHaveFocus());
    fireEvent.click(openPlayer);

    expect(screen.getByRole("dialog", { name: "KOL 详情底层" })).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "代表作视频播放器" })).toBeInTheDocument();
    const closeButton = screen.getByRole("button", { name: "关闭播放器" });
    await waitFor(() => expect(closeButton).toHaveFocus());

    fireEvent.keyDown(window, { key: "Tab", shiftKey: true });
    const originalLinks = screen.getAllByRole("link", { name: "打开原帖" });
    expect(originalLinks[originalLinks.length - 1]).toHaveFocus();
    fireEvent.keyDown(window, { key: "Tab" });
    expect(closeButton).toHaveFocus();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(closePlayer).toHaveBeenCalledTimes(1);
    expect(closeLower).not.toHaveBeenCalled();
    await waitFor(() => expect(openPlayer).toHaveFocus());
    expect(document.body.style.overflow).toBe("hidden");

    fireEvent.keyDown(window, { key: "Escape" });
    expect(closeLower).toHaveBeenCalledTimes(1);
    view.unmount();
    expect(externalOpener).toHaveFocus();
    expect(document.body.style.overflow).toBe("");
    externalOpener.remove();
  });

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
