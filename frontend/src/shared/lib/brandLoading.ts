/**
 * The startup screen: the large animated "Z" in index.html (#brand-loading),
 * painted before any JavaScript runs so a cold load never shows a blank
 * page. This module decides when it leaves.
 *
 * - The first time real route content commits, `markAppReady()` is called
 *   (`AppReady`, inside the router's Suspense boundary).
 * - Anything that can't show the right screen yet holds it with
 *   `holdBrandLoading()`: a protected route restoring the session, the
 *   sign-in page while the session is checked or a redirect is pending, and
 *   the Suspense fallback while a page's code is still arriving.
 * - It leaves once the app is ready and nothing holds it, checked on the
 *   next frame so a redirect that immediately suspends (and holds again)
 *   doesn't let it go early. It fades out once and is removed; later route
 *   changes never bring it back. No artificial delay.
 * - Fail-safe: whatever happens (slow or failing session restore, a render
 *   error), it leaves after `MAX_VISIBLE_MS`, revealing the app's own
 *   loading/error state underneath.
 */

export const BRAND_LOADING_ID = "brand-loading";
/** Upper bound on how long the startup screen may cover the app. */
export const MAX_VISIBLE_MS = 6_000;
/** Matches the fade in styles/loading.css (plus a little slack). */
const FADE_MS = 320;

let holds = 0;
let ready = false;
let released = false;
let checkScheduled = false;
let safetyTimer: ReturnType<typeof setTimeout> | null = null;

function splash(): HTMLElement | null {
  return typeof document === "undefined" ? null : document.getElementById(BRAND_LOADING_ID);
}

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

function release() {
  if (released) return;
  released = true;
  if (safetyTimer !== null) {
    clearTimeout(safetyTimer);
    safetyTimer = null;
  }
  const root = document.getElementById("root");
  if (root) root.inert = false;

  const element = splash();
  if (!element) return;
  // Out of the interaction and accessibility flow at once; the fade is
  // purely visual.
  element.setAttribute("aria-hidden", "true");
  element.removeAttribute("role");
  if (prefersReducedMotion()) {
    element.remove();
    return;
  }
  element.classList.add("is-leaving");
  setTimeout(() => element.remove(), FADE_MS);
}

function scheduleCheck() {
  if (released || checkScheduled) return;
  checkScheduled = true;
  const run = () => {
    checkScheduled = false;
    if (!released && ready && holds === 0) release();
  };
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(run);
  else setTimeout(run, 16);
}

/** Called once from main.tsx before React renders. Keeps the page beneath
 * the startup screen out of the tab order while it is shown, and starts
 * the fail-safe timer. A no-op when there is no startup screen (tests). */
export function initBrandLoading() {
  if (released || safetyTimer !== null || !splash()) return;
  const root = document.getElementById("root");
  if (root) root.inert = true;
  safetyTimer = setTimeout(release, MAX_VISIBLE_MS);
}

/** Keep the startup screen up; returns the matching release. */
export function holdBrandLoading(): () => void {
  holds += 1;
  let active = true;
  return () => {
    if (!active) return;
    active = false;
    holds -= 1;
    if (holds === 0) scheduleCheck();
  };
}

/** Real route content has committed; leave once nothing holds. */
export function markAppReady() {
  ready = true;
  scheduleCheck();
}

/** Test-only: restore the initial state. */
export function resetBrandLoadingForTests() {
  holds = 0;
  ready = false;
  released = false;
  checkScheduled = false;
  if (safetyTimer !== null) clearTimeout(safetyTimer);
  safetyTimer = null;
}
