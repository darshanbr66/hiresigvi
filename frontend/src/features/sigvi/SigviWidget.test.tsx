import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import { ApiError } from "../../lib/apiClient";
import type { SigviChatResponse } from "../../types/sigvi";
import * as sigviApi from "./api";
import { SigviWidget } from "./SigviWidget";

function reply(message: string, extra: Partial<SigviChatResponse> = {}): SigviChatResponse {
  return { conversation_id: "11111111-1111-4111-8111-111111111111", message, sources: [], jobs: [], ...extra };
}

function renderWidget(props: { organizationSlug?: string } = {}) {
  return render(
    <MemoryRouter>
      <SigviWidget {...props} />
    </MemoryRouter>,
  );
}

const launcher = () => screen.getByRole("button", { name: /ask sigvi/i });
const input = () => screen.getByRole("textbox", { name: /message sigvi/i });
const openPanel = () => fireEvent.click(launcher());
const type = (text: string) => fireEvent.change(input(), { target: { value: text } });
const pressEnter = (init: KeyboardEventInit = {}) =>
  fireEvent.keyDown(input(), { key: "Enter", ...init });

/** A promise the test resolves/rejects by hand, to observe the in-flight state. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

let send: MockInstance<typeof sigviApi.sendChatMessage>;

beforeEach(() => {
  send = vi.spyOn(sigviApi, "sendChatMessage").mockResolvedValue(reply("Hello from Sigvi."));
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  document.body.style.overflow = "";
});

describe("opening and closing", () => {
  it("starts closed: a launcher and no visible dialog", () => {
    renderWidget();

    expect(launcher()).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens with a welcome message, suggestions and focus in the message box", async () => {
    renderWidget();
    openPanel();

    const dialog = screen.getByRole("dialog", { name: /sigvi/i });
    expect(within(dialog).getByText("Hi! How can I help?")).toBeInTheDocument();
    expect(within(dialog).getByText("Ask me about careers at SIGVITAS.")).toBeInTheDocument();
    for (const suggestion of [
      "View open positions",
      "How do I apply?",
      "Campus opportunities",
      "Hiring process",
    ]) {
      expect(within(dialog).getByRole("button", { name: suggestion })).toBeInTheDocument();
    }
    await waitFor(() => expect(input()).toHaveFocus());
    // The launcher steps aside while the panel is open.
    expect(screen.queryByRole("button", { name: /ask sigvi/i })).not.toBeInTheDocument();
  });

  it("closes with the Close button and returns focus to the launcher", async () => {
    renderWidget();
    openPanel();

    fireEvent.click(screen.getByRole("button", { name: /close sigvi/i }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(launcher()).toHaveFocus());
    expect(launcher()).toHaveAttribute("aria-expanded", "false");
  });

  it("closes on Escape and returns focus to the launcher", async () => {
    renderWidget();
    openPanel();
    await waitFor(() => expect(input()).toHaveFocus());

    fireEvent.keyDown(input(), { key: "Escape" });

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(launcher()).toHaveFocus());
  });

  it("keeps the conversation when closed and reopened", async () => {
    renderWidget();
    openPanel();
    type("Hi Sigvi");
    pressEnter();
    await screen.findByText("Hello from Sigvi.");

    fireEvent.click(screen.getByRole("button", { name: /close sigvi/i }));
    openPanel();

    expect(screen.getByText("Hi Sigvi")).toBeInTheDocument();
    expect(screen.getByText("Hello from Sigvi.")).toBeInTheDocument();
  });
});

describe("sending messages", () => {
  it("sends on Enter, shows both sides, and clears the box", async () => {
    renderWidget({ organizationSlug: "sigvitas" });
    openPanel();

    type("What jobs are available?");
    pressEnter();

    expect(await screen.findByText("Hello from Sigvi.")).toBeInTheDocument();
    expect(screen.getByText("What jobs are available?")).toBeInTheDocument();
    expect(input()).toHaveValue("");
    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledWith(
      expect.objectContaining({
        message: "What jobs are available?",
        history: [],
        organization_slug: "sigvitas",
      }),
    );
  });

  it("sends with the Send button too", async () => {
    renderWidget();
    openPanel();

    type("hello");
    fireEvent.click(screen.getByRole("button", { name: /send message/i }));

    await screen.findByText("Hello from Sigvi.");
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("Shift+Enter does not send (it is a newline)", () => {
    renderWidget();
    openPanel();

    type("line one");
    pressEnter({ shiftKey: true });

    expect(send).not.toHaveBeenCalled();
    expect(input()).toHaveValue("line one");
  });

  it("does not send while an IME composition is in progress", () => {
    renderWidget();
    openPanel();

    type("こんにちは");
    fireEvent.compositionStart(input());
    pressEnter();
    expect(send).not.toHaveBeenCalled();

    fireEvent.compositionEnd(input());
    pressEnter();
    expect(send).toHaveBeenCalledTimes(1);
  });

  it("ignores empty and whitespace-only messages", () => {
    renderWidget();
    openPanel();

    expect(screen.getByRole("button", { name: /send message/i })).toBeDisabled();
    type("    ");
    expect(screen.getByRole("button", { name: /send message/i })).toBeDisabled();
    pressEnter();

    expect(send).not.toHaveBeenCalled();
  });

  it("limits the message length and shows a counter near the limit", () => {
    renderWidget();
    openPanel();

    expect(input()).toHaveAttribute("maxlength", "1000");
    expect(screen.queryByText(/\/1000/)).not.toBeInTheDocument();
    type("a".repeat(850));
    expect(screen.getByText("850/1000")).toBeInTheDocument();
  });

  it("carries the recent turns as history so follow-ups make sense", async () => {
    send.mockResolvedValueOnce(reply("We have two roles."));
    renderWidget();
    openPanel();

    type("What jobs are available?");
    pressEnter();
    await screen.findByText("We have two roles.");
    type("Which one requires React?");
    pressEnter();
    await screen.findAllByText("Hello from Sigvi.");

    expect(send).toHaveBeenLastCalledWith(
      expect.objectContaining({
        message: "Which one requires React?",
        history: [
          { role: "user", content: "What jobs are available?" },
          { role: "assistant", content: "We have two roles." },
        ],
      }),
    );
  });

  it("bounds the history it sends", async () => {
    renderWidget();
    openPanel();

    for (let i = 1; i <= 7; i++) {
      type(`question ${i}`);
      pressEnter();
      await waitFor(() => expect(screen.getAllByText("Hello from Sigvi.")).toHaveLength(i));
    }

    const { history } = send.mock.calls[6][0];
    expect(history.length).toBeLessThanOrEqual(8);
    expect(history.at(-1)).toEqual({ role: "assistant", content: "Hello from Sigvi." });
  });

  it("keeps the same conversation id across requests", async () => {
    renderWidget();
    openPanel();

    type("one");
    pressEnter();
    await screen.findByText("Hello from Sigvi.");
    type("two");
    pressEnter();
    await waitFor(() => expect(send).toHaveBeenCalledTimes(2));

    expect(send.mock.calls[1][0].conversation_id).toBe("11111111-1111-4111-8111-111111111111");
  });
});

describe("suggested questions", () => {
  it("clicking a suggestion sends it, and the suggestions then go away", async () => {
    renderWidget();
    openPanel();

    fireEvent.click(screen.getByRole("button", { name: "How do I apply?" }));

    await screen.findByText("Hello from Sigvi.");
    expect(send).toHaveBeenCalledWith(expect.objectContaining({ message: "How do I apply?" }));
    expect(screen.queryByRole("group", { name: /suggested questions/i })).not.toBeInTheDocument();
  });
});

describe("loading state", () => {
  it("shows that Sigvi is thinking, blocks a second send, then shows the reply", async () => {
    const pending = deferred<SigviChatResponse>();
    send.mockReturnValueOnce(pending.promise);
    renderWidget();
    openPanel();

    type("Tell me about Sigvitas");
    pressEnter();

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/sigvi is thinking/i);
    type("another question");
    expect(screen.getByRole("button", { name: /send message/i })).toBeDisabled();
    pressEnter();
    expect(send).toHaveBeenCalledTimes(1);

    await act(async () => pending.resolve(reply("Sigvitas is a recruitment platform.")));

    expect(await screen.findByText("Sigvitas is a recruitment platform.")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});

describe("rendering replies", () => {
  it("renders bold text, bullet lists and paragraphs as real elements", async () => {
    send.mockResolvedValueOnce(
      reply("You will need:\n\n- a **resume**\n- your email\n\nThen submit.\n\n1. First\n2. Second"),
    );
    renderWidget();
    openPanel();
    type("what do I need?");
    pressEnter();

    const strong = await screen.findByText("resume");
    expect(strong.tagName).toBe("STRONG");
    expect(screen.getAllByRole("list")).toHaveLength(2);
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
    expect(screen.getByText("Then submit.")).toBeInTheDocument();
  });

  it("never turns model output into markup", async () => {
    send.mockResolvedValueOnce(
      reply('<img src=x onerror="alert(1)"> <script>alert(2)</script> [link](javascript:alert(3))'),
    );
    renderWidget();
    openPanel();
    type("hi");
    pressEnter();

    await screen.findByText(/<script>alert\(2\)<\/script>/);
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("script")).toBeNull();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("shows which knowledge a reply was based on", async () => {
    send.mockResolvedValueOnce(
      reply("Apply from a role page.", {
        sources: [
          { id: "how-to-apply", title: "How to apply for a job", type: "knowledge", url: "/org/sigvitas" },
        ],
      }),
    );
    renderWidget();
    openPanel();
    type("how do I apply");
    pressEnter();

    const chip = await screen.findByText(/based on sigvitas careers/i);
    expect(chip).toBeInTheDocument();
    // the specific topic is available on hover, but the chip names nothing internal
    expect(chip).toHaveAttribute("title", "How to apply for a job");
  });

  it("shows job cards that link to the real role page and apply form", async () => {
    send.mockResolvedValueOnce(
      reply("This one fits.", {
        jobs: [
          {
            id: "job-1",
            title: "React Frontend Developer",
            department: "Engineering",
            location: "Bengaluru",
            employment_type: "Full-time",
            summary: "Build interfaces in React.",
            organization_slug: "sigvitas",
            view_path: "/org/sigvitas/jobs/job-1",
            apply_path: "/org/sigvitas/jobs/job-1#apply",
          },
        ],
      }),
    );
    renderWidget();
    openPanel();
    type("react jobs?");
    pressEnter();

    const card = await screen.findByRole("article", { name: /react frontend developer/i });
    expect(within(card).getByText("Open role")).toBeInTheDocument();
    expect(within(card).getByText("Engineering")).toBeInTheDocument();
    expect(within(card).getByText("Bengaluru · Full-time")).toBeInTheDocument();
    expect(within(card).getByText("Build interfaces in React.")).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: /view role/i })).toHaveAttribute(
      "href",
      "/org/sigvitas/jobs/job-1",
    );
    expect(within(card).getByRole("link", { name: /apply/i })).toHaveAttribute(
      "href",
      "/org/sigvitas/jobs/job-1#apply",
    );
  });

  it("closes the panel when a job card is followed", async () => {
    send.mockResolvedValueOnce(
      reply("Here.", {
        jobs: [
          {
            id: "job-1",
            title: "Backend Engineer",
            department: null,
            location: null,
            employment_type: null,
            summary: null,
            organization_slug: "sigvitas",
            view_path: "/org/sigvitas/jobs/job-1",
            apply_path: "/org/sigvitas/jobs/job-1#apply",
          },
        ],
      }),
    );
    renderWidget();
    openPanel();
    type("jobs");
    pressEnter();

    fireEvent.click(await screen.findByRole("link", { name: /view role/i }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("error state and retry", () => {
  it("shows the server's friendly message with a Retry that resends the same message once", async () => {
    send.mockRejectedValueOnce(
      new ApiError("Sigvi is temporarily unavailable. Please try again in a little while.", 503, "ai_unavailable"),
    );
    renderWidget();
    openPanel();
    type("What is Sigvitas?");
    pressEnter();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/temporarily unavailable/i);
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /retry/i }));

    expect(await screen.findByText("Hello from Sigvi.")).toBeInTheDocument();
    expect(send).toHaveBeenCalledTimes(2);
    expect(send.mock.calls[1][0].message).toBe("What is Sigvitas?");
    expect(send.mock.calls[1][0].history).toEqual([]);
    // The user's message is shown once — a retry does not duplicate it.
    expect(screen.getAllByText("What is Sigvitas?")).toHaveLength(1);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("explains a network failure without exposing technical detail", async () => {
    send.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    renderWidget();
    openPanel();
    type("hello");
    pressEnter();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/couldn't be reached/i);
    expect(alert).not.toHaveTextContent(/failed to fetch/i);
  });

  it("can fail again and still be retried", async () => {
    send
      .mockRejectedValueOnce(new ApiError("busy", 429, "ai_busy"))
      .mockRejectedValueOnce(new ApiError("still busy", 429, "ai_busy"));
    renderWidget();
    openPanel();
    type("hello");
    pressEnter();
    await screen.findByText("busy");

    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    await screen.findByText("still busy");
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));

    expect(await screen.findByText("Hello from Sigvi.")).toBeInTheDocument();
    expect(send).toHaveBeenCalledTimes(3);
  });
});

describe("clearing the conversation", () => {
  it("resets to the welcome state and forgets earlier turns", async () => {
    renderWidget();
    openPanel();
    type("first question");
    pressEnter();
    await screen.findByText("Hello from Sigvi.");

    fireEvent.click(screen.getByRole("button", { name: /clear conversation/i }));

    expect(screen.queryByText("first question")).not.toBeInTheDocument();
    expect(screen.queryByText("Hello from Sigvi.")).not.toBeInTheDocument();
    expect(screen.getByRole("group", { name: /suggested questions/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /clear conversation/i })).toBeDisabled();

    type("a fresh start");
    pressEnter();
    await waitFor(() => expect(send).toHaveBeenCalledTimes(2));
    expect(send.mock.calls[1][0].history).toEqual([]);
  });

  it("drops a reply that arrives after the conversation was cleared", async () => {
    const pending = deferred<SigviChatResponse>();
    send.mockReturnValueOnce(pending.promise);
    renderWidget();
    openPanel();
    type("slow question");
    pressEnter();
    await screen.findByRole("status");

    fireEvent.click(screen.getByRole("button", { name: /clear conversation/i }));
    await act(async () => pending.resolve(reply("A stale answer.")));

    expect(screen.queryByText("A stale answer.")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("clears an error too", async () => {
    send.mockRejectedValueOnce(new ApiError("nope", 503, "ai_unavailable"));
    renderWidget();
    openPanel();
    type("hi");
    pressEnter();
    await screen.findByRole("alert");

    fireEvent.click(screen.getByRole("button", { name: /clear conversation/i }));

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("layout", () => {
  function stubMatchMedia(matching: (query: string) => boolean) {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: matching(query),
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }));
  }

  it("is a floating panel on desktop", () => {
    stubMatchMedia(() => false);
    renderWidget();
    openPanel();

    expect(screen.getByRole("dialog")).toHaveAttribute("data-layout", "floating");
    expect(document.body.style.overflow).toBe("");
  });

  it("is a full-screen sheet on phones, and stops the page scrolling beneath it", () => {
    stubMatchMedia((query) => query.includes("max-width: 640px"));
    renderWidget();
    openPanel();

    expect(screen.getByRole("dialog")).toHaveAttribute("data-layout", "sheet");
    expect(document.body.style.overflow).toBe("hidden");

    fireEvent.click(screen.getByRole("button", { name: /close sigvi/i }));
    expect(document.body.style.overflow).toBe("");
  });

  it("scrolls without animation for people who asked for reduced motion", async () => {
    const scrollTo = vi.fn();
    Element.prototype.scrollTo = scrollTo;
    stubMatchMedia((query) => query.includes("prefers-reduced-motion"));
    try {
      renderWidget();
      openPanel();
      type("hi");
      pressEnter();
      await screen.findByText("Hello from Sigvi.");

      expect(scrollTo).not.toHaveBeenCalled();
    } finally {
      // Restore the jsdom default (elements have no scrollTo).
      delete (Element.prototype as { scrollTo?: unknown }).scrollTo;
    }
  });

  it("scrolls smoothly to the newest message otherwise", async () => {
    const scrollTo = vi.fn();
    Element.prototype.scrollTo = scrollTo;
    stubMatchMedia(() => false);
    try {
      renderWidget();
      openPanel();
      type("hi");
      pressEnter();
      await screen.findByText("Hello from Sigvi.");

      expect(scrollTo).toHaveBeenCalledWith(expect.objectContaining({ behavior: "smooth" }));
    } finally {
      delete (Element.prototype as { scrollTo?: unknown }).scrollTo;
    }
  });
});

describe("accessibility", () => {
  it("names the launcher, dialog, conversation log and controls", () => {
    renderWidget();

    expect(launcher()).toHaveAccessibleName(/ask sigvi/i);
    expect(launcher()).toHaveAttribute("aria-haspopup", "dialog");
    expect(launcher()).toHaveAttribute("aria-controls", "sigvi-panel");

    openPanel();

    expect(screen.getByRole("dialog")).toHaveAccessibleName(/sigvi/i);
    const log = screen.getByRole("log", { name: /conversation/i });
    expect(log).toHaveAttribute("aria-live", "polite");
    expect(input()).toHaveAccessibleName(/message sigvi/i);
    for (const name of [/send message/i, /clear conversation/i, /close sigvi/i]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
  });

  it("is fully operable from the keyboard: type, Enter, Escape", async () => {
    renderWidget();
    launcher().focus();
    fireEvent.click(launcher()); // Enter/Space on a button dispatch click

    await waitFor(() => expect(input()).toHaveFocus());
    type("hi");
    pressEnter();
    await screen.findByText("Hello from Sigvi.");
    fireEvent.keyDown(input(), { key: "Escape" });

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("announces failures and progress through live regions", async () => {
    send.mockRejectedValueOnce(new ApiError("Try later", 503, "ai_unavailable"));
    renderWidget();
    openPanel();
    type("hi");
    pressEnter();

    expect(await screen.findByRole("alert")).toHaveTextContent("Try later");
  });

  it("marks decorative avatars as hidden from assistive tech", () => {
    renderWidget();
    openPanel();

    // Every mascot is either decorative (hidden) or a named image.
    const mascots = document.querySelectorAll("svg.sigvi-mascot");
    expect(mascots.length).toBeGreaterThan(1);
    mascots.forEach((svg) => {
      const decorative = svg.getAttribute("aria-hidden") === "true";
      const named = svg.getAttribute("role") === "img" && !!svg.getAttribute("aria-label");
      expect(decorative || named).toBe(true);
    });
  });

  it("tells people it is an AI that can be wrong", () => {
    renderWidget();
    openPanel();

    expect(screen.getByText(/ai can make mistakes/i)).toBeInTheDocument();
    expect(screen.getByText("AI assistant for SIGVITAS")).toBeInTheDocument();
  });
});
