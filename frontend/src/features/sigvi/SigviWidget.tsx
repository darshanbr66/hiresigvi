import { useCallback, useEffect, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { Icon } from "../../shared/components/Icon";
import { useMediaQuery } from "../../shared/hooks/useMediaQuery";
import { useReducedMotion } from "../../shared/hooks/useReducedMotion";
import { SIGVI_MAX_MESSAGE_CHARS } from "../../types/sigvi";
// Sigvi's styles ship with Sigvi's (lazy) chunk, not the main stylesheet.
import "../../styles/sigvi.css";
import { FormattedMessage } from "./formatMessage";
import { SigviAvatar } from "./SigviAvatar";
import { SigviJobCard } from "./SigviJobCard";
import { SigviLauncher } from "./SigviLauncher";
import { SigviMascot } from "./SigviMascot";
import { SigviTeaser } from "./SigviTeaser";
import { SigviWelcome } from "./SigviWelcome";
import { rememberTeaserDismissed } from "./teaserStorage";
import { useSigviChat, type ChatTurn } from "./useSigviChat";

const MOBILE_QUERY = "(max-width: 640px)";
const COUNTER_FROM = Math.floor(SIGVI_MAX_MESSAGE_CHARS * 0.8);

function AssistantTurn({ turn, onNavigate }: { turn: ChatTurn; onNavigate: () => void }) {
  const knowledge = (turn.sources ?? []).filter((source) => source.type === "knowledge");
  return (
    <div className="sigvi-row sigvi-row-assistant">
      <SigviAvatar size={24} />
      <div className="sigvi-stack">
        <div className="sigvi-bubble sigvi-bubble-assistant">
          <FormattedMessage text={turn.content} />
        </div>
        {turn.jobs && turn.jobs.length > 0 && (
          <div className="sigvi-jobs">
            {turn.jobs.map((job) => (
              <SigviJobCard key={job.id} job={job} onNavigate={onNavigate} />
            ))}
          </div>
        )}
        {knowledge.length > 0 && (
          // The specific topics are in the tooltip; the chip itself stays
          // simple and never names anything internal.
          <p className="sigvi-sources" title={knowledge.map((source) => source.title).join(" · ")}>
            Based on SIGVITAS Careers
          </p>
        )}
      </div>
    </div>
  );
}

/**
 * The floating Sigvi assistant. Rendered through a portal into <body> for the
 * same reason `Modal` is: a `position: fixed` element inside a transformed
 * ancestor is positioned against that ancestor, not the viewport.
 *
 * Non-modal on desktop (the page stays usable beside it), a full-screen sheet
 * on phones. Escape closes it and returns focus to the launcher.
 */
export function SigviWidget({
  organizationSlug,
  teaser = false,
}: {
  organizationSlug?: string;
  /** Offer the one-time "Hi, I'm Sigvi" nudge (the home page only). */
  teaser?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [teaserDismissed, setTeaserDismissed] = useState(false);
  const { turns, status, error, send, retry, clear } = useSigviChat({ organizationSlug });
  const isMobile = useMediaQuery(MOBILE_QUERY);
  const reducedMotion = useReducedMotion();

  const launcherRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const restoreFocus = useRef(false);
  const composing = useRef(false);

  const pending = status === "pending";
  // The header avatar's only state change: it looks busy while a reply is
  // on its way (the "Sigvi is thinking…" row says the same in words).
  const mascotState = pending ? "thinking" : "idle";

  // Opening moves focus to the message box; closing (button / Escape) returns
  // it to the launcher. Following a link out of the panel does neither.
  useEffect(() => {
    if (open) {
      inputRef.current?.focus({ preventScroll: true });
    } else if (restoreFocus.current) {
      restoreFocus.current = false;
      launcherRef.current?.focus({ preventScroll: true });
    }
  }, [open]);

  // Keep the newest message in view.
  useLayoutEffect(() => {
    const log = logRef.current;
    if (!log || !open) return;
    // The greeting reads from the top; only a conversation follows the end.
    if (turns.length === 0) {
      log.scrollTop = 0;
      return;
    }
    if (!reducedMotion && typeof log.scrollTo === "function") {
      log.scrollTo({ top: log.scrollHeight, behavior: "smooth" });
    } else {
      log.scrollTop = log.scrollHeight;
    }
  }, [turns.length, status, open, reducedMotion]);

  // A phone sheet covers the page, so the page must not scroll beneath it.
  useEffect(() => {
    if (!open || !isMobile) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [open, isMobile]);

  // On a phone the on-screen keyboard shrinks the *visual* viewport but not
  // the layout viewport, which would leave the message box underneath it. Size
  // the sheet to what is actually visible so the input stays in reach.
  useEffect(() => {
    const viewport = window.visualViewport;
    const panel = panelRef.current;
    if (!open || !isMobile || !viewport || !panel) return;
    const update = () => {
      panel.style.setProperty("--sigvi-vh", `${viewport.height}px`);
      panel.style.setProperty("--sigvi-top", `${viewport.offsetTop}px`);
    };
    update();
    viewport.addEventListener("resize", update);
    viewport.addEventListener("scroll", update);
    return () => {
      viewport.removeEventListener("resize", update);
      viewport.removeEventListener("scroll", update);
      panel.style.removeProperty("--sigvi-vh");
      panel.style.removeProperty("--sigvi-top");
    };
  }, [open, isMobile]);

  // Grow the box with its content (up to a cap), shrink back after sending.
  useLayoutEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
  }, [draft]);

  const dismissTeaser = useCallback(() => {
    setTeaserDismissed(true);
    rememberTeaserDismissed();
  }, []);

  // Opening Sigvi — from the launcher or the teaser — retires the teaser for good.
  const openPanel = () => {
    dismissTeaser();
    setOpen(true);
  };

  const close = () => {
    restoreFocus.current = true;
    setOpen(false);
  };

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    if (pending || draft.trim() === "") return;
    send(draft);
    setDraft("");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !composing.current) {
      event.preventDefault();
      submit();
    }
  };

  const hasConversation = turns.length > 0 || error !== null;

  return createPortal(
    <div className="sigvi" data-open={open} data-state={mascotState}>
      <SigviTeaser
        enabled={teaser}
        dismissed={teaserDismissed || open}
        onDismiss={dismissTeaser}
        onOpen={openPanel}
      />
      <SigviLauncher ref={launcherRef} open={open} onOpen={openPanel} />

      <section
        ref={panelRef}
        id="sigvi-panel"
        className="sigvi-panel"
        role="dialog"
        aria-label="Sigvi, AI assistant"
        aria-modal="false"
        aria-hidden={!open}
        inert={!open}
        data-layout={isMobile ? "sheet" : "floating"}
        onKeyDown={(event) => {
          if (event.key === "Escape") close();
        }}
      >
        <header className="sigvi-header">
          <span className="sigvi-head-avatar">
            <SigviMascot size={28} variant="head" state={mascotState} />
          </span>
          <div className="sigvi-header-text">
            <h2 className="sigvi-title">Sigvi</h2>
            <p className="sigvi-subtitle">AI assistant for SIGVITAS</p>
          </div>
          <button
            type="button"
            className="sigvi-icon-btn"
            aria-label="Clear conversation"
            title="Clear conversation"
            disabled={!hasConversation}
            onClick={() => {
              clear();
              inputRef.current?.focus({ preventScroll: true });
            }}
          >
            <Icon name="trash" size={18} />
          </button>
          {/* Closing keeps the conversation, so this doubles as "minimize". */}
          <button
            type="button"
            className="sigvi-icon-btn"
            aria-label="Close Sigvi"
            title="Close — your conversation is kept"
            onClick={close}
          >
            <Icon name="x" size={18} />
          </button>
        </header>

        <div
          ref={logRef}
          className="sigvi-log"
          role="log"
          aria-label="Conversation with Sigvi"
          aria-live="polite"
          aria-relevant="additions"
          tabIndex={0}
        >
          <SigviWelcome onPick={send} disabled={pending} showSuggestions={turns.length === 0} />

          {turns.map((turn) =>
            turn.role === "user" ? (
              <div key={turn.id} className="sigvi-row sigvi-row-user">
                <div className="sigvi-bubble sigvi-bubble-user">{turn.content}</div>
              </div>
            ) : (
              <AssistantTurn key={turn.id} turn={turn} onNavigate={() => setOpen(false)} />
            ),
          )}

          {pending && (
            <div className="sigvi-row sigvi-row-assistant" role="status">
              <SigviAvatar size={24} state="thinking" />
              <div className="sigvi-bubble sigvi-bubble-assistant sigvi-typing">
                <span className="sigvi-dots" aria-hidden="true">
                  <span />
                  <span />
                  <span />
                </span>
                <span>Sigvi is thinking…</span>
              </div>
            </div>
          )}

          {status === "error" && error && (
            <div className="sigvi-error" role="alert">
              <p>{error}</p>
              <button type="button" className="sigvi-retry" onClick={retry}>
                Retry
              </button>
            </div>
          )}
        </div>

        <form className="sigvi-composer" onSubmit={submit}>
          <div className="sigvi-field">
            <textarea
              ref={inputRef}
              className="sigvi-input"
              aria-label="Message Sigvi"
              placeholder="Ask about roles, applying, or careers…"
              rows={1}
              maxLength={SIGVI_MAX_MESSAGE_CHARS}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={onKeyDown}
              onCompositionStart={() => {
                composing.current = true;
              }}
              onCompositionEnd={() => {
                composing.current = false;
              }}
            />
            <button
              type="submit"
              className="sigvi-send"
              aria-label="Send message"
              disabled={pending || draft.trim() === ""}
            >
              <Icon name="send" size={18} />
            </button>
          </div>
          <p className="sigvi-footnote">
            <span>AI can make mistakes. Don't share sensitive details.</span>
            {draft.length >= COUNTER_FROM && (
              <span className="sigvi-counter" aria-live="polite">
                {draft.length}/{SIGVI_MAX_MESSAGE_CHARS}
              </span>
            )}
          </p>
        </form>
      </section>
    </div>,
    document.body,
  );
}
