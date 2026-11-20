import "@testing-library/jest-dom/vitest";
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SigviMascot } from "./SigviMascot";

afterEach(cleanup);

const svgOf = (container: HTMLElement) => container.querySelector("svg") as SVGElement;

describe("SigviMascot", () => {
  it("is decorative unless given a name", () => {
    const { container, rerender } = render(<SigviMascot />);

    expect(svgOf(container)).toHaveAttribute("aria-hidden", "true");
    expect(svgOf(container)).not.toHaveAttribute("role");

    rerender(<SigviMascot label="Zodi, a friendly robot" />);
    expect(svgOf(container)).toHaveAttribute("role", "img");
    expect(svgOf(container)).toHaveAttribute("aria-label", "Zodi, a friendly robot");
    expect(svgOf(container)).not.toHaveAttribute("aria-hidden");
  });

  it("reports its state, variant and whether it animates", () => {
    const { container } = render(<SigviMascot state="thinking" variant="full" animated={false} />);

    expect(svgOf(container)).toHaveAttribute("data-state", "thinking");
    expect(svgOf(container)).toHaveAttribute("data-variant", "full");
    expect(svgOf(container)).toHaveAttribute("data-animated", "false");
  });

  it("defaults to an idle, animated head", () => {
    const { container } = render(<SigviMascot />);

    expect(svgOf(container)).toHaveAttribute("data-state", "idle");
    expect(svgOf(container)).toHaveAttribute("data-variant", "head");
    expect(svgOf(container)).toHaveAttribute("data-animated", "true");
  });

  it("scales with its size, and only the full variant has a body", () => {
    const head = svgOf(render(<SigviMascot size={100} variant="head" />).container);
    const full = svgOf(render(<SigviMascot size={100} variant="full" />).container);

    expect(head).toHaveAttribute("width", "100");
    expect(Number(full.getAttribute("height"))).toBeGreaterThan(Number(head.getAttribute("height")));
    expect(full.querySelector(".sv-chest")).not.toBeNull();
    expect(head.querySelector(".sv-chest")).toBeNull();
  });

  it("gives every instance its own gradient ids, so many mascots never clash", () => {
    const { container } = render(
      <>
        <SigviMascot />
        <SigviMascot />
      </>,
    );

    const ids = [...container.querySelectorAll("[id]")].map((el) => el.id);
    expect(ids.length).toBeGreaterThan(10);
    expect(new Set(ids).size).toBe(ids.length);
    // and every gradient reference points at an id that exists
    const refs = [...container.querySelectorAll("[fill]")]
      .map((el) => el.getAttribute("fill") as string)
      .filter((fill) => fill.startsWith("url(#"))
      .map((fill) => fill.slice(5, -1));
    expect(refs.length).toBeGreaterThan(0);
    refs.forEach((ref) => expect(ids).toContain(ref));
  });

  it("carries the parts the CSS animations act on", () => {
    const { container } = render(<SigviMascot />);

    for (const cls of [".sv-float", ".sv-aura", ".sv-eyes", ".sv-eye", ".sv-orbit", ".sv-burst", ".sv-tip-glow"]) {
      expect(container.querySelector(cls), cls).not.toBeNull();
    }
    expect(container.querySelectorAll(".sv-eye")).toHaveLength(2);
  });

  describe("idle life (blink and glance)", () => {
    beforeEach(() => {
      vi.useFakeTimers();
      vi.spyOn(Math, "random").mockReturnValue(0); // first beat at 1.8s, then every 3.5s
    });
    afterEach(() => {
      vi.useRealTimers();
      vi.restoreAllMocks();
      vi.unstubAllGlobals();
    });
    const advance = (ms: number) =>
      act(() => {
        vi.advanceTimersByTime(ms);
      });

    it("blinks briefly, then glances every other beat — and does nothing in between", () => {
      const { container } = render(<SigviMascot />);
      const svg = svgOf(container);

      advance(1700);
      expect(svg).not.toHaveAttribute("data-blink");
      advance(150); // 1.85s: first beat
      expect(svg).toHaveAttribute("data-blink", "true");
      expect(svg).not.toHaveAttribute("data-gaze");
      advance(130);
      expect(svg).not.toHaveAttribute("data-blink");

      advance(3600); // second beat: a glance too
      expect(svg).toHaveAttribute("data-gaze", "left");
    });

    it("stays still when it is not animated, is thinking, or motion is reduced", () => {
      const notAnimated = svgOf(render(<SigviMascot animated={false} />).container);
      const thinking = svgOf(render(<SigviMascot state="thinking" />).container);
      vi.stubGlobal("matchMedia", (query: string) => ({
        matches: query.includes("prefers-reduced-motion"),
        media: query,
        addEventListener: () => {},
        removeEventListener: () => {},
      }));
      const reduced = svgOf(render(<SigviMascot />).container);

      advance(20000);

      for (const svg of [notAnimated, thinking, reduced]) {
        expect(svg).not.toHaveAttribute("data-blink");
        expect(svg).not.toHaveAttribute("data-gaze");
      }
    });

    it("stops and clears itself when removed, and skips beats while the tab is hidden", () => {
      const { container, unmount } = render(<SigviMascot />);
      const svg = svgOf(container);
      Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
      advance(2000);
      expect(svg).not.toHaveAttribute("data-blink");

      Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
      advance(3350); // t=5.35s: the (skipped) 1.8s beat rescheduled for 5.3s, now visible
      expect(svg).toHaveAttribute("data-blink", "true");

      unmount();
      expect(vi.getTimerCount()).toBe(0);
    });
  });

  it("pulls in no images or filters (it stays cheap)", () => {
    const { container } = render(<SigviMascot variant="full" />);

    expect(container.querySelector("image, filter, foreignObject")).toBeNull();
  });
});
