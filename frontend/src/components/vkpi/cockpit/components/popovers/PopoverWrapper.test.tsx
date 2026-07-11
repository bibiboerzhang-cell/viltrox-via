import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PopoverWrapper } from "./PopoverWrapper";

function Fixture({ onClose }: { onClose: () => void }) {
  const anchorRef = React.useRef<HTMLButtonElement | null>(null);
  return (
    <>
      <button ref={anchorRef} type="button">anchor</button>
      <button type="button">outside action</button>
      <PopoverWrapper anchorRef={anchorRef} onClose={onClose} width={320}>
        <div>popover content</div>
      </PopoverWrapper>
    </>
  );
}

describe("PopoverWrapper", () => {
  it("通过 body portal 固定在视口，不再复用 cockpit-shell", () => {
    const onClose = vi.fn();
    render(<Fixture onClose={onClose} />);

    const layer = document.body.querySelector(".vkpi-popover-layer") as HTMLElement;
    expect(layer).toBeInTheDocument();
    expect(layer).not.toHaveClass("cockpit-shell");
    expect(screen.getByText("popover content")).toBeInTheDocument();

    fireEvent.mouseDown(screen.getByRole("button", { name: "outside action" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Escape 关闭并把焦点还给锚点", () => {
    const onClose = vi.fn();
    render(<Fixture onClose={onClose} />);
    const anchor = screen.getByRole("button", { name: "anchor" });

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(anchor).toHaveFocus();
  });
});
