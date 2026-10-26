import { SigviAvatar } from "./SigviAvatar";
import { SIGVI_SUGGESTIONS } from "./suggestions";

/** Sigvi's opening message. It stays at the top of the conversation; the
 * suggested questions beneath it go away once the visitor asks something. */
export function SigviWelcome({
  onPick,
  disabled,
  showSuggestions,
}: {
  onPick: (question: string) => void;
  disabled: boolean;
  showSuggestions: boolean;
}) {
  return (
    <>
      <div className="sigvi-row sigvi-row-assistant">
        <SigviAvatar size={24} />
        <div className="sigvi-bubble sigvi-bubble-assistant sigvi-greeting">
          <h3 className="sigvi-greeting-title">Hi! How can I help?</h3>
          <p className="sigvi-greeting-text">Ask me about careers at SIGVITAS.</p>
        </div>
      </div>

      {showSuggestions && (
        <div className="sigvi-suggestions" role="group" aria-label="Suggested questions">
          {SIGVI_SUGGESTIONS.map(({ label, question }) => (
            <button
              key={label}
              type="button"
              className="sigvi-suggest"
              disabled={disabled}
              onClick={() => onPick(question)}
            >
              {label}
            </button>
          ))}
        </div>
      )}
    </>
  );
}
