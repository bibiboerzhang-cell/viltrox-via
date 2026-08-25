import React from "react";

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

type ModalFocusOptions = {
  active?: boolean;
  lockBody?: boolean;
  onClose?: () => void;
};

/**
 * Shared accessibility contract for cockpit dialogs and drawers.
 *
 * Every active layer joins one global stack so only the topmost layer handles
 * Escape/Tab. Focus returns to the element that opened that layer, including
 * when a dialog is nested inside a drawer.
 */
export function useModalFocusContract<T extends HTMLElement>({
  active = true,
  lockBody = true,
  onClose,
}: ModalFocusOptions): React.RefObject<T> {
  const layerIdRef = React.useRef<symbol | null>(null);
  const layerRef = React.useRef<T>(null);
  const restoreFocusRef = React.useRef<HTMLElement | null>(null);
  const onCloseRef = React.useRef(onClose);
  onCloseRef.current = onClose;
  if (!layerIdRef.current) layerIdRef.current = Symbol("vkpi-modal");

  React.useEffect(() => {
    if (!active) return;
    const layerId = layerIdRef.current as symbol;
    MODAL_STACK.push(layerId);
    restoreFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;

    if (lockBody) {
      if (modalBodyLockCount === 0) {
        modalBodyPreviousOverflow = document.body.style.overflow;
        document.body.style.overflow = "hidden";
      }
      modalBodyLockCount += 1;
    }

    const focusRaf = requestAnimationFrame(() => {
      if (MODAL_STACK[MODAL_STACK.length - 1] !== layerId) return;
      const target = layerRef.current?.querySelector<HTMLElement>("[data-modal-initial-focus]") || layerRef.current;
      target?.focus();
    });

    return () => {
      cancelAnimationFrame(focusRaf);
      const index = MODAL_STACK.indexOf(layerId);
      if (index >= 0) MODAL_STACK.splice(index, 1);

      if (lockBody) {
        modalBodyLockCount = Math.max(0, modalBodyLockCount - 1);
        if (modalBodyLockCount === 0) {
          document.body.style.overflow = modalBodyPreviousOverflow;
          modalBodyPreviousOverflow = "";
        }
      }

      const restoreTarget = restoreFocusRef.current;
      if (restoreTarget?.isConnected) restoreTarget.focus();
    };
  }, [active, lockBody]);

  React.useEffect(() => {
    if (!active) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (MODAL_STACK[MODAL_STACK.length - 1] !== layerIdRef.current) return;
      if (event.key === "Escape") {
        if (!onCloseRef.current) return;
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;

      const layer = layerRef.current;
      if (!layer) return;
      const focusable = Array.from(layer.querySelectorAll<HTMLElement>(MODAL_FOCUSABLE)).filter(
        (element) => element.getAttribute("aria-hidden") !== "true" && !element.hidden,
      );
      if (focusable.length === 0) {
        event.preventDefault();
        layer.focus();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const focused = document.activeElement;
      if (event.shiftKey && (focused === first || !layer.contains(focused))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (focused === last || !layer.contains(focused))) {
        event.preventDefault();
        first.focus();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [active]);

  return layerRef;
}
