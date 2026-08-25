import React from "react";
import { createPortal } from "react-dom";
import { useModalFocusContract } from "../components/modals/modalFocus";
import { useT } from "../lib/i18n";

// Shared cockpit modal layer. It must stay outside react-grid-layout because transformed/overflow
// grid ancestors otherwise clip position:fixed descendants.
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
  const { t } = useT();
  const dialogRef = useModalFocusContract<HTMLDivElement>({ onClose });
  const titleId = React.useId();
  const subId = React.useId();

  const [on, setOn] = React.useState(false);
  React.useEffect(() => {
    const raf = requestAnimationFrame(() => setOn(true));
    return () => cancelAnimationFrame(raf);
  }, []);

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
            <div id={titleId} className="text-[17px] font-[680] tracking-[-0.02em] text-ink">
              {typeof title === "string" ? t(title) : title}
            </div>
            {sub ? (
              <div id={subId} className="mt-[3px] text-[12px] leading-5 text-muted">
                {typeof sub === "string" ? t(sub) : sub}
              </div>
            ) : null}
          </div>
          <button
            data-modal-initial-focus
            type="button"
            onClick={onClose}
            aria-label={t("关闭")}
            className="grid h-10 w-10 flex-none place-items-center rounded-xl border border-line text-[17px] text-ink-2 transition-colors hover:border-line-strong hover:bg-panel hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            ✕
          </button>
        </div>
        <div
          data-vkpi-modal-scroll="content"
          data-vkpi-density="readable-modal-body"
          className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-[22px] pb-10 pt-[18px] text-[12.5px] leading-[1.55]"
        >
          {children}
        </div>
      </div>
    </div>
  );
  return typeof document === "undefined" ? modal : createPortal(modal, document.body);
}
