import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  place?: "center" | "right";
  footer?: ReactNode;
  children?: ReactNode;
  closeOnOverlay?: boolean;
  width?: number | string;
}

/** 只吃 --ds-* token 的模态/右侧抽屉。Portal 到 body,ESC/遮罩可关。 */
export function Modal({
  open,
  onClose,
  title,
  place = "center",
  footer,
  children,
  closeOnOverlay = true,
  width,
}: ModalProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div
      className="ds-modal-scrim"
      data-place={place}
      onMouseDown={closeOnOverlay ? (e) => { if (e.target === e.currentTarget) onClose(); } : undefined}
    >
      <div className="ds-modal" style={width ? { width } : undefined} role="dialog" aria-modal="true">
        {(title || place === "center") && (
          <div className="ds-modal__head">
            <span className="ds-modal__title">{title}</span>
            <button type="button" className="ds-modal__close" onClick={onClose} aria-label="关闭">
              <X size={16} />
            </button>
          </div>
        )}
        <div className="ds-modal__body">{children}</div>
        {footer && <div className="ds-modal__foot">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}
