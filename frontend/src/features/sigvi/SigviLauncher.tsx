import { forwardRef } from "react";
import { SigviMascot } from "./SigviMascot";

/** The floating launcher: a quiet pill with Sigvi's head and a label (just
 * the head on phones). It stays still — it only needs to be findable. */
export const SigviLauncher = forwardRef<
  HTMLButtonElement,
  { open: boolean; onOpen: () => void }
>(function SigviLauncher({ open, onOpen }, ref) {
  return (
    <button
      ref={ref}
      type="button"
      className="sigvi-launcher"
      aria-label="Ask Sigvi, the AI assistant"
      aria-haspopup="dialog"
      aria-expanded={open}
      aria-controls="sigvi-panel"
      aria-hidden={open}
      inert={open}
      onClick={onOpen}
    >
      <span className="sigvi-launcher-icon">
        <SigviMascot size={26} variant="head" animated={false} />
      </span>
      <span className="sigvi-launcher-label">Ask Sigvi</span>
    </button>
  );
});
