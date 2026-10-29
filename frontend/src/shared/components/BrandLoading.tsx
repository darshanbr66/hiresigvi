import { useLayoutEffect } from "react";
import { holdBrandLoading, markAppReady } from "../lib/brandLoading";

/**
 * Rendered by a screen that can't show the right content yet (a protected
 * route restoring the session, a pending redirect, a page whose code is
 * still loading): on a cold start the HireZ startup screen stays up instead
 * of revealing that interim state. Renders nothing itself; after startup it
 * has no visible effect.
 */
export function HoldBrandLoading() {
  useLayoutEffect(() => holdBrandLoading(), []);
  return null;
}

/**
 * Placed inside the router's Suspense boundary, after the routes. Its
 * layout effect runs only once the boundary has committed real content
 * (not the code-loading fallback) — the point at which the startup screen
 * may leave, unless something is holding it.
 */
export function AppReady() {
  useLayoutEffect(() => {
    markAppReady();
  }, []);
  return null;
}
