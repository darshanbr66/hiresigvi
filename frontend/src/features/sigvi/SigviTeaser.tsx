import { useEffect, useState } from "react";
import { Icon } from "../../shared/components/Icon";
import { wasTeaserDismissed } from "./teaserStorage";

const SHOW_AFTER_MS = 5000;
const HIDE_AFTER_MS = 20000;

/**
 * A one-time nudge beside the launcher — "Hi, I'm Sigvi" — shown a few seconds
 * after the home page settles, at most once per browser session, and never
 * again once dismissed or once Sigvi has been opened (the widget owns that
 * decision and passes `dismissed`). It is a real button (opens the chat), not
 * a live region, so it never interrupts a screen reader.
 */
export function SigviTeaser({
  enabled,
  dismissed,
  onDismiss,
  onOpen,
}: {
  enabled: boolean;
  dismissed: boolean;
  onDismiss: () => void;
  onOpen: () => void;
}) {
  const [shown, setShown] = useState(false);

  useEffect(() => {
    if (!enabled || wasTeaserDismissed()) return;
    const show = window.setTimeout(() => setShown(true), SHOW_AFTER_MS);
    const hide = window.setTimeout(onDismiss, HIDE_AFTER_MS);
    return () => {
      window.clearTimeout(show);
      window.clearTimeout(hide);
    };
  }, [enabled, onDismiss]);

  if (!shown || dismissed) return null;

  return (
    <div className="sigvi-teaser">
      <button type="button" className="sigvi-teaser-main" onClick={onOpen}>
        <span className="sigvi-teaser-title">
          Hi, I'm Sigvi
        </span>
        <span className="sigvi-teaser-text">Ask me about open roles.</span>
      </button>
      <button type="button" className="sigvi-teaser-close" aria-label="Dismiss" onClick={onDismiss}>
        <Icon name="x" size={12} />
      </button>
    </div>
  );
}
