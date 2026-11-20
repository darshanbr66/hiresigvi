import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../../lib/apiClient";
import { SIGVI_HISTORY_TURNS, type SigviJobCard, type SigviSource } from "../../types/sigvi";
import { sendChatMessage } from "./api";

export interface ChatTurn {
  id: string;
  role: "user" | "assistant";
  content: string;
  jobs?: SigviJobCard[];
  sources?: SigviSource[];
}

export type ChatStatus = "idle" | "pending" | "error";

const NETWORK_ERROR = "Zodi couldn't be reached. Check your connection and try again.";

let turnCounter = 0;
const nextId = () => `turn-${++turnCounter}`;

function newConversationId() {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : undefined;
}

/**
 * Conversation state for the Sigvi widget. Deliberately in-memory only: the
 * backend is stateless and a conversation is never written to storage, so
 * closing the tab (or "Clear") ends it. Each request carries the most recent
 * turns — bounded — so a follow-up like "which one requires React?" resolves.
 */
export function useSigviChat({ organizationSlug }: { organizationSlug?: string }) {
  const [turns, setTurnsState] = useState<ChatTurn[]>([]);
  const [status, setStatus] = useState<ChatStatus>("idle");
  const [error, setError] = useState<string | null>(null);

  // The turns as of *now*, kept in step with state so `send`/`retry` read the
  // latest without depending on a render (and without side effects inside a
  // state updater, which StrictMode runs twice).
  const turnsRef = useRef<ChatTurn[]>([]);
  const conversationId = useRef<string | undefined>(newConversationId());
  // Bumped by "clear" and on unmount so a reply that lands afterwards is dropped.
  const generation = useRef(0);
  const inFlight = useRef(false);
  // The user turn whose request failed — what Retry resends.
  const failedTurn = useRef<ChatTurn | null>(null);

  const setTurns = useCallback((next: ChatTurn[]) => {
    turnsRef.current = next;
    setTurnsState(next);
  }, []);

  useEffect(() => {
    const counter = generation;
    return () => {
      counter.current += 1;
    };
  }, []);

  const request = useCallback(
    async (message: string, history: ChatTurn[]) => {
      const mine = generation.current;
      inFlight.current = true;
      setStatus("pending");
      setError(null);
      try {
        const response = await sendChatMessage({
          message,
          conversation_id: conversationId.current,
          history: history
            .slice(-SIGVI_HISTORY_TURNS)
            .map(({ role, content }) => ({ role, content })),
          organization_slug: organizationSlug,
        });
        if (mine !== generation.current) return;
        conversationId.current = response.conversation_id;
        failedTurn.current = null;
        setTurns([
          ...turnsRef.current,
          {
            id: nextId(),
            role: "assistant",
            content: response.message,
            jobs: response.jobs,
            sources: response.sources,
          },
        ]);
        setStatus("idle");
      } catch (caught) {
        if (mine !== generation.current) return;
        setError(caught instanceof ApiError ? caught.message : NETWORK_ERROR);
        setStatus("error");
      } finally {
        if (mine === generation.current) inFlight.current = false;
      }
    },
    [organizationSlug, setTurns],
  );

  const send = useCallback(
    (text: string) => {
      const message = text.trim();
      if (!message || inFlight.current) return;
      const history = turnsRef.current;
      const userTurn: ChatTurn = { id: nextId(), role: "user", content: message };
      failedTurn.current = userTurn;
      setTurns([...history, userTurn]);
      void request(message, history);
    },
    [request, setTurns],
  );

  const retry = useCallback(() => {
    const failed = failedTurn.current;
    if (!failed || inFlight.current) return;
    // The failed message stays on screen; history is what came before it.
    const history = turnsRef.current.filter((turn) => turn.id !== failed.id);
    void request(failed.content, history);
  }, [request]);

  const clear = useCallback(() => {
    generation.current += 1;
    inFlight.current = false;
    failedTurn.current = null;
    conversationId.current = newConversationId();
    setTurns([]);
    setStatus("idle");
    setError(null);
  }, [setTurns]);

  return { turns, status, error, send, retry, clear };
}
