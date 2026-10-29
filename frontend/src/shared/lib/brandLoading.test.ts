import { readFileSync } from "node:fs";
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

describe("startup screen markup (index.html)", () => {
  // vitest runs from the frontend root
  const indexHtml = readFileSync("index.html", "utf8");
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("is a status overlay whose Z sits in its own layer beside the rotating ring, never inside it", () => {
    document.body.innerHTML = new DOMParser().parseFromString(indexHtml, "text/html").body.innerHTML;
    const splash = document.getElementById(BRAND_LOADING_ID);
    expect(splash).toHaveAttribute("role", "status");
    expect(splash).toHaveAttribute("aria-label", "Loading HireZ");

    const mark = splash?.querySelector(":scope > .brand-loading-mark");
    expect(mark).toHaveAttribute("aria-hidden", "true");
    const ring = mark?.querySelector(":scope > .brand-loading-ring");
    const z = mark?.querySelector(":scope > .brand-loading-z");
    expect(ring).toBeTruthy();
    expect(z).toHaveTextContent(/^Z$/);
    expect(ring?.contains(z ?? null)).toBe(false);
  });

  it("rotates only the ring, and stops it for reduced motion", () => {
    const loadingCss = readFileSync("src/styles/loading.css", "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    const animated = [...loadingCss.matchAll(/([^{}]+)\{[^}]*\banimation:\s*brand-loading-spin/g)].map((m) => m[1].trim());
    expect(animated).toEqual([".brand-loading-ring::before"]);
    expect(loadingCss).toMatch(
      /@media \(prefers-reduced-motion: reduce\)\s*\{\s*\.brand-loading-ring::before\s*\{\s*animation: none;/,
    );
  });
});
