import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";

/** Generic dialog surface — dimmed backdrop, background not interactive
 * while open, Escape-to-close, click-outside-to-close. Reuses the same
 * `.dialog-overlay`/`.dialog-card` styling as ConfirmDialog so every modal
 * in the app looks and behaves the same way.
 *
 * Rendered into `document.body` through a portal, never inline. Inline, the
 * `position: fixed` overlay resolves against the nearest ancestor that has a
 * transform (the page-transition wrapper keeps one after its entrance), so
 * the dialog would be positioned — and scroll — with the page's content
 * instead of the viewport. */
export function Modal({
  title,
  onClose,
  children,
  wide = false,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** true = 640px; "extra" = 960px, for dialogs that hold a review table. */
  wide?: boolean | "extra";
}) {
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  return createPortal(
    <div className="dialog-overlay" role="presentation" onClick={onClose}>
      <div
        className={
          wide === "extra" ? "dialog-card dialog-card-xwide" : wide ? "dialog-card dialog-card-wide" : "dialog-card"
        }
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="page-header" style={{ marginBottom: "1rem" }}>
          <h2 id="modal-title" style={{ margin: 0 }}>
            {title}
          </h2>
          <button type="button" className="btn btn-ghost btn-sm" aria-label="Close" onClick={onClose}>
            &times;
          </button>
        </div>
        {children}
      </div>
    </div>,
    document.body,
  );
}
