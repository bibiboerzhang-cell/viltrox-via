import React from "react";
import { createPortal } from "react-dom";

// Shared cockpit modal layer. It must stay outside react-grid-layout because transformed/overflow
// grid ancestors otherwise clip position:fixed descendants.
const MODAL_STACK: symbol[] = [];
const MODAL_FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");
let modalBodyLockCount = 0;
let modalBodyPreviousOverflow = "";

export function ModalShell({
  title,
  sub,
  onClose,
  children,
  maxWidth = "max-w-[700px]",
}: {
  title: React.ReactNode;
  sub?: React.ReactNode;
  onClose: () => void;
  children: React.ReactNode;
  maxWidth?: string;
}) {
  const idRef = React.useRef<symbol | null>(null);
  const dialogRef = React.useRef<HTMLDivElement | null>(null);
  const restoreFocusRef = React.useRef<HTMLElement | null>(null);
  const titleId = React.useId();
  const subId = React.useId();
  if (!idRef.current) idRef.current = Symbol("vkpi-modal");

  const [on, setOn] = React.useState(false);
  React.useEffect(() => {
    const raf = requestAnimationFrame(() => setOn(true));
    return () => cancelAnimationFrame(raf);
  }, []);

  React.useEffect(() => {
    const id = idRef.current as symbol;
    MODAL_STACK.push(id);
    restoreFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (modalBodyLockCount === 0) {
      modalBodyPreviousOverflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";
    }
    modalBodyLockCount += 1;
    const focusRaf = requestAnimationFrame(() => {
      if (MODAL_STACK[MODAL_STACK.length - 1] !== id) return;
      const target = dialogRef.current?.querySelector<HTMLElement>("[data-modal-initial-focus]") || dialogRef.current;
      target?.focus();
    });
    return () => {
      cancelAnimationFrame(focusRaf);
      const at = MODAL_STACK.indexOf(id);
      if (at >= 0) MODAL_STACK.splice(at, 1);
      modalBodyLockCount = Math.max(0, modalBodyLockCount - 1);
      if (modalBodyLockCount === 0) {
        document.body.style.overflow = modalBodyPreviousOverflow;
        modalBodyPreviousOverflow = "";
      }
      const restoreTarget = restoreFocusRef.current;
      if (restoreTarget?.isConnected) restoreTarget.focus();
    };
  }, []);

  React.useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (MODAL_STACK[MODAL_STACK.length - 1] !== idRef.current) return;
      if (ev.key === "Escape") {
        ev.preventDefault();
        onClose();
        return;
      }
      if (ev.key !== "Tab") return;
      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(MODAL_FOCUSABLE)).filter(
        (element) => element.getAttribute("aria-hidden") !== "true" && !element.hidden,
      );
      if (focusable.length === 0) {
        ev.preventDefault();
        dialog.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (ev.shiftKey && (active === first || !dialog.contains(active))) {
        ev.preventDefault();
        last.focus();
      } else if (!ev.shiftKey && (active === last || !dialog.contains(active))) {
        ev.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const modal = (
    <div
      data-vkpi-modal-layer="body-portal"
      className={`cockpit-modal cockpit-modal--themed ds-modal-fade ${on ? "is-on" : ""} fixed inset-0 flex items-center justify-center overflow-hidden bg-[var(--ds-scrim)] p-3 backdrop-blur-[3px] sm:p-4`}
      style={{ zIndex: 9999 }}
      role="presentation"
      onMouseDown={(ev) => {
        if (ev.target === ev.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={sub ? subId : undefined}
        tabIndex={-1}
        className={`ds-dialog-pop ${on ? "is-on" : ""} relative flex max-h-[calc(100dvh-1.5rem)] w-full ${maxWidth} flex-col overflow-hidden rounded-[18px] border border-line bg-[var(--ds-overlay-surface)] shadow-ds sm:max-h-[86dvh]`}
      >
        <div className="flex flex-none items-start justify-between gap-3 border-b border-line px-[22px] pb-3.5 pt-[18px]">
          <div className="min-w-0">
            <div id={titleId} className="text-[17px] font-[680] tracking-[-0.02em] text-ink">{title}</div>
            {sub ? <div id={subId} className="mt-[3px] text-[11px] text-muted">{sub}</div> : null}
          </div>
          <button
            data-modal-initial-focus
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="grid h-[30px] w-[30px] flex-none place-items-center rounded-[9px] border border-line text-[15px] text-ink-2 transition-colors hover:border-line-strong hover:text-ink"
          >
            ✕
          </button>
        </div>
        <div data-vkpi-modal-scroll="content" className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-[22px] pb-10 pt-[18px]">{children}</div>
      </div>
    </div>
  );
  return typeof document === "undefined" ? modal : createPortal(modal, document.body);
}
