import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  BRAND_LOADING_ID,
  MAX_VISIBLE_MS,
  holdBrandLoading,
  initBrandLoading,
  markAppReady,
  resetBrandLoadingForTests,
} from "./brandLoading";

function mountStartupScreen() {
  document.body.innerHTML = `<div id="${BRAND_LOADING_ID}" role="status"><span>Z</span></div><div id="root"></div>`;
  return {
    splash: () => document.getElementById(BRAND_LOADING_ID),
    root: document.getElementById("root") as HTMLElement,
  };
}

/** the readiness check runs on the next frame */
const nextFrame = () => vi.advanceTimersByTime(20);

describe("startup screen lifecycle", () => {
  beforeEach(() => {
    resetBrandLoadingForTests();
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "requestAnimationFrame", "cancelAnimationFrame"] });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
  });

  it("covers the app until the first screen is ready, then fades out and is removed", () => {
    const { splash, root } = mountStartupScreen();
    initBrandLoading();
    expect(root.inert).toBe(true);

    markAppReady();
    expect(splash()).not.toHaveClass("is-leaving"); // not before the next frame
    nextFrame();
    expect(splash()).toHaveClass("is-leaving");
    expect(splash()).toHaveAttribute("aria-hidden", "true");
    expect(splash()).not.toHaveAttribute("role");
    expect(root.inert).toBe(false);

    vi.advanceTimersByTime(400);
    expect(splash()).toBeNull();
  });

  it("stays while a screen holds it, and leaves when the last hold ends", () => {
    const { splash } = mountStartupScreen();
    initBrandLoading();
    const release = holdBrandLoading();

    markAppReady();
    nextFrame();
    expect(splash()).not.toHaveClass("is-leaving");

    release();
    release(); // idempotent
    nextFrame();
    expect(splash()).toHaveClass("is-leaving");
  });

  it("is not let go when one hold hands over to another in the same frame (redirect into a loading page)", () => {
    const { splash } = mountStartupScreen();
    initBrandLoading();
    markAppReady();
    const sessionHold = holdBrandLoading();

    sessionHold();
    const codeLoadingHold = holdBrandLoading();
    nextFrame();
    expect(splash()).not.toHaveClass("is-leaving");

    codeLoadingHold();
    nextFrame();
    expect(splash()).toHaveClass("is-leaving");
  });

  it("does not leave before real content is ready, even with no holds", () => {
    const { splash } = mountStartupScreen();
    initBrandLoading();
    holdBrandLoading()();
    nextFrame();
    expect(splash()).not.toHaveClass("is-leaving");
  });

  it("never hides the app indefinitely: leaves after the time limit even while held", () => {
    const { splash, root } = mountStartupScreen();
    initBrandLoading();
    holdBrandLoading();

    vi.advanceTimersByTime(MAX_VISIBLE_MS - 1);
    expect(splash()).not.toHaveClass("is-leaving");
    vi.advanceTimersByTime(1);
    expect(splash()).toHaveClass("is-leaving");
    expect(root.inert).toBe(false);
  });

  it("is removed at once, without a fade, for reduced motion", () => {
    const { splash } = mountStartupScreen();
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: query.includes("reduce") }));
    initBrandLoading();
    markAppReady();
    nextFrame();
    expect(splash()).toBeNull();
  });

  it("does nothing when there is no startup screen", () => {
    document.body.innerHTML = `<div id="root"></div>`;
    initBrandLoading();
    expect(document.getElementById("root")?.inert).toBeFalsy();
    expect(() => {
      markAppReady();
      nextFrame();
    }).not.toThrow();
  });
});
