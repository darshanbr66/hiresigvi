import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import { ApiError } from "../../lib/apiClient";
import type { SigviChatResponse } from "../../types/sigvi";
import * as sigviApi from "./api";
import { SigviWidget } from "./SigviWidget";

/** The experience around the conversation: welcome screen, launcher, header
 * avatar, the one-time teaser and the phone keyboard. (Conversation mechanics are in
 * SigviWidget.test.tsx.) */

function reply(message: string): SigviChatResponse {
  return { conversation_id: "11111111-1111-4111-8111-111111111111", message, sources: [], jobs: [] };
}

function renderWidget(props: { organizationSlug?: string; teaser?: boolean } = {}) {
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
const pressEnter = () => fireEvent.keyDown(input(), { key: "Enter" });

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

let send: MockInstance<typeof sigviApi.sendChatMessage>;

beforeEach(() => {
  send = vi.spyOn(sigviApi, "sendChatMessage").mockResolvedValue(reply("Hello from Sigvi."));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  document.body.style.overflow = "";
});

describe("welcome screen", () => {
  it("greets the visitor with a short question and one line of context", () => {
    renderWidget();
    openPanel();

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Hi! How can I help?" })).toBeInTheDocument();
    expect(within(dialog).getByText("Ask me about careers at SIGVITAS.")).toBeInTheDocument();
    // The long introduction is gone.
    expect(within(dialog).queryByText(/your ai assistant for sigvitas/i)).not.toBeInTheDocument();
    // No oversized artwork: every mascot in the panel is a small decorative icon.
    expect(within(dialog).queryByRole("img")).not.toBeInTheDocument();
  });

  it.each([
    ["View open positions", "What positions are open right now?"],
    ["How do I apply?", "How do I apply?"],
    ["Campus opportunities", "What campus opportunities do you offer?"],
    ["Hiring process", "How does the hiring process work?"],
  ])("the “%s” suggestion sends its question through the chat", async (label, question) => {
    renderWidget();
    openPanel();

    fireEvent.click(screen.getByRole("button", { name: label }));

    await screen.findByText("Hello from Sigvi.");
    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledWith(expect.objectContaining({ message: question, history: [] }));
    // The visitor's bubble shows what was asked.
    expect(screen.getByText(question, { selector: ".sigvi-bubble-user" })).toBeInTheDocument();
  });

  it("offers a small set of suggestions, each named by its question alone", () => {
    renderWidget();
    openPanel();

    const suggestions = within(screen.getByRole("group", { name: /suggested questions/i })).getAllByRole("button");
    expect(suggestions).toHaveLength(4);
    suggestions.forEach((button) => expect(button.querySelector("svg")).toBeNull());
  });

  it("keeps the greeting at the top of the conversation once it starts", async () => {
    renderWidget();
    openPanel();
    type("hello");
    pressEnter();
    await screen.findByText("Hello from Sigvi.");

    expect(screen.getByRole("heading", { name: "Hi! How can I help?" })).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: /suggested questions/i })).not.toBeInTheDocument();
    expect(screen.getAllByRole("heading", { name: "Hi! How can I help?" })).toHaveLength(1);
  });
});

describe("the launcher", () => {
  it("shows Sigvi's head beside an 'Ask Sigvi' label, with a single accessible name", () => {
    renderWidget();

    const button = launcher();
    expect(within(button).getByText("Ask Sigvi")).toBeInTheDocument();
    expect(button.querySelector("svg.sigvi-mascot")).toHaveAttribute("aria-hidden", "true");
    expect(button).toHaveAccessibleName("Ask Sigvi, the AI assistant");
  });

  it("stays still: no animated mascot and nothing that keeps waking it", () => {
    vi.useFakeTimers();
    renderWidget();
    const mascot = launcher().querySelector("svg.sigvi-mascot");
    expect(mascot).toHaveAttribute("data-animated", "false");

    act(() => {
      vi.advanceTimersByTime(130_000);
    });

    // Same element: the launcher is never remounted to replay an animation.
    expect(launcher().querySelector("svg.sigvi-mascot")).toBe(mascot);
  });
});

describe("the header avatar", () => {
  const headerMascot = () => document.querySelector(".sigvi-header svg.sigvi-mascot");

  it("looks busy only while a reply is on its way", async () => {
    const pending = deferred<SigviChatResponse>();
    send.mockReturnValueOnce(pending.promise);
    renderWidget();
    openPanel();
    expect(headerMascot()).toHaveAttribute("data-state", "idle");

    type("hello");
    pressEnter();
    const status = await screen.findByRole("status");

    expect(headerMascot()).toHaveAttribute("data-state", "thinking");
    expect(within(status).getByText(/sigvi is thinking/i)).toBeInTheDocument();

    await act(async () => pending.resolve(reply("All done.")));
    await screen.findByText("All done.");
    // No celebration: straight back to idle.
    expect(headerMascot()).toHaveAttribute("data-state", "idle");
  });

  it("returns to idle after an error", async () => {
    send.mockRejectedValueOnce(new ApiError("nope", 503, "ai_unavailable"));
    renderWidget();
    openPanel();
    type("hi");
    pressEnter();
    await screen.findByRole("alert");

    expect(headerMascot()).toHaveAttribute("data-state", "idle");
  });

  it("keeps the small avatars beside messages still", async () => {
    renderWidget();
    openPanel();
    type("hi");
    pressEnter();
    await screen.findByText("Hello from Sigvi.");

    document.querySelectorAll(".sigvi-row-assistant svg.sigvi-mascot").forEach((svg) => {
      expect(svg).toHaveAttribute("data-animated", "false");
    });
  });
});

describe("the one-time teaser", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    window.sessionStorage.clear();
  });

  const teaserText = () => screen.queryByText("Ask me about open roles.");
  const advance = (ms: number) =>
    act(() => {
      vi.advanceTimersByTime(ms);
    });

  it("appears a few seconds after load on the home page, never sooner", () => {
    renderWidget({ teaser: true });

    advance(4000);
    expect(teaserText()).not.toBeInTheDocument();
    advance(1500);
    expect(teaserText()).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /i'm sigvi/i })).toBeInTheDocument();
    // it never competes for the launcher's accessible name
    expect(screen.getAllByRole("button", { name: /ask sigvi/i })).toHaveLength(1);
  });

  it("does not appear on pages that didn't ask for it", () => {
    renderWidget({ teaser: false });

    advance(30000);
    expect(teaserText()).not.toBeInTheDocument();
  });

  it("opens the chat when clicked, and is gone for the rest of the session", () => {
    const { unmount } = renderWidget({ teaser: true });
    advance(5500);

    fireEvent.click(screen.getByRole("button", { name: /i'm sigvi/i }));

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(teaserText()).not.toBeInTheDocument();
    unmount();

    renderWidget({ teaser: true });
    advance(30000);
    expect(teaserText()).not.toBeInTheDocument();
  });

  it("can be dismissed, and stays dismissed", () => {
    const { unmount } = renderWidget({ teaser: true });
    advance(5500);

    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));

    expect(teaserText()).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    unmount();

    renderWidget({ teaser: true });
    advance(30000);
    expect(teaserText()).not.toBeInTheDocument();
  });

  it("goes away by itself if ignored", () => {
    renderWidget({ teaser: true });
    advance(5500);
    expect(teaserText()).toBeInTheDocument();

    advance(16000);

    expect(teaserText()).not.toBeInTheDocument();
  });

  it("opening from the launcher instead also retires it", () => {
    renderWidget({ teaser: true });
    advance(5500);

    fireEvent.click(launcher());

    expect(teaserText()).not.toBeInTheDocument();
  });

  it("still works when session storage is unavailable", () => {
    const original = Object.getOwnPropertyDescriptor(window, "sessionStorage");
    Object.defineProperty(window, "sessionStorage", {
      configurable: true,
      get() {
        throw new Error("blocked");
      },
    });
    try {
      renderWidget({ teaser: true });
      advance(5500);
      expect(teaserText()).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));
      expect(teaserText()).not.toBeInTheDocument();
    } finally {
      if (original) Object.defineProperty(window, "sessionStorage", original);
    }
  });
});

describe("phone keyboard", () => {
  function stubViewport(height: number, mobile: boolean) {
    const listeners = new Map<string, () => void>();
    const viewport = {
      height,
      offsetTop: 0,
      addEventListener: (name: string, fn: () => void) => listeners.set(name, fn),
      removeEventListener: (name: string) => listeners.delete(name),
    };
    vi.stubGlobal("visualViewport", viewport);
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: mobile && query.includes("max-width: 640px"),
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }));
    return { viewport, listeners };
  }

  it("sizes the sheet to the visible viewport, so the keyboard cannot cover the input", () => {
    const { viewport, listeners } = stubViewport(780, true);
    renderWidget();
    openPanel();
    const panel = screen.getByRole("dialog");
    expect(panel.style.getPropertyValue("--sigvi-vh")).toBe("780px");

    // the on-screen keyboard opens
    viewport.height = 430;
    act(() => listeners.get("resize")?.());
    expect(panel.style.getPropertyValue("--sigvi-vh")).toBe("430px");

    fireEvent.click(screen.getByRole("button", { name: /close sigvi/i }));
    expect(panel.style.getPropertyValue("--sigvi-vh")).toBe("");
  });

  it("leaves the desktop layout alone", () => {
    stubViewport(500, false);
    renderWidget();
    openPanel();

    expect(screen.getByRole("dialog").style.getPropertyValue("--sigvi-vh")).toBe("");
  });
});
