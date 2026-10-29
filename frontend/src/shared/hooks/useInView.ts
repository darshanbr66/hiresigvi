import { useEffect, useRef, useState } from "react";

interface InViewOptions {
  /** Fraction of the element that must be visible. */
  threshold?: number;
  /** Grow/shrink the viewport used for the test, e.g. "0px 0px -10% 0px". */
  rootMargin?: string;
  /** Latch to `true` the first time it enters view (default), or track live. */
  once?: boolean;
}

/**
 * Whether an element is in the viewport. Fail-open by design, so a reveal
 * can never leave content invisible:
 *
 * - starts `true` when IntersectionObserver isn't available;
 * - becomes `true` if creating the observer throws;
 * - an element too tall to ever reach `threshold` of itself on screen (a
 *   long list on a short phone) counts as in view as soon as any of it
 *   intersects.
 */
export function useInView<T extends Element>({
  threshold = 0.15,
  rootMargin = "0px",
  once = true,
}: InViewOptions = {}) {
  const ref = useRef<T | null>(null);
  const [inView, setInView] = useState(typeof IntersectionObserver === "undefined");

  useEffect(() => {
    const node = ref.current;
    if (!node || typeof IntersectionObserver === "undefined") return;

    let observer: IntersectionObserver;
    try {
      observer = new IntersectionObserver(
        ([entry]) => {
          // geometry may be missing on partial entries (polyfills, test
          // doubles): treat an intersecting entry without it as in view
          const viewport = entry.rootBounds?.height ?? window.innerHeight;
          const height = entry.boundingClientRect?.height;
          const tooTallForThreshold = height === undefined || height * threshold > viewport;
          const ratio = entry.intersectionRatio ?? 1;
          const visible = entry.isIntersecting && (ratio >= threshold || tooTallForThreshold);
          if (visible) {
            setInView(true);
            if (once) observer.disconnect();
          } else if (!once && !entry.isIntersecting) {
            setInView(false);
          }
        },
        { threshold: [0, threshold], rootMargin },
      );
      observer.observe(node);
    } catch {
      setInView(true);
      return;
    }
    return () => observer.disconnect();
  }, [threshold, rootMargin, once]);

  return { ref, inView };
}
