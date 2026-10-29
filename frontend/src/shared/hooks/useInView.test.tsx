import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useInView } from "./useInView";

function Probe() {
  const { ref, inView } = useInView<HTMLDivElement>();
  return (
    <div ref={ref} data-testid="probe">
      {inView ? "visible" : "hidden"}
    </div>
  );
}

type Callback = (entries: Partial<IntersectionObserverEntry>[]) => void;

function installObserver(onCreate?: (callback: Callback) => void) {
  const original = globalThis.IntersectionObserver;
  class FakeObserver {
    constructor(callback: Callback) {
      onCreate?.(callback);
    }
    observe() {}
    disconnect() {}
    unobserve() {}
    takeRecords() {
      return [];
    }
  }
  globalThis.IntersectionObserver = FakeObserver as unknown as typeof IntersectionObserver;
  return () => {
    globalThis.IntersectionObserver = original;
  };
}

describe("useInView — reveals never leave content invisible", () => {
  let restore: () => void = () => {};
  afterEach(() => restore());

  it("is visible without IntersectionObserver", () => {
    const original = globalThis.IntersectionObserver;
    // @ts-expect-error simulate a browser without the API
    delete globalThis.IntersectionObserver;
    restore = () => {
      globalThis.IntersectionObserver = original;
    };
    render(<Probe />);
    expect(screen.getByTestId("probe")).toHaveTextContent("visible");
  });

  it("fails open when creating the observer throws", () => {
    const original = globalThis.IntersectionObserver;
    globalThis.IntersectionObserver = vi.fn(() => {
      throw new Error("unsupported options");
    }) as unknown as typeof IntersectionObserver;
    restore = () => {
      globalThis.IntersectionObserver = original;
    };
    render(<Probe />);
    expect(screen.getByTestId("probe")).toHaveTextContent("visible");
  });

  it("reveals an element too tall to ever reach the threshold as soon as it intersects", () => {
    let fire: Callback = () => {};
    restore = installObserver((callback) => {
      fire = callback;
    });
    render(<Probe />);
    expect(screen.getByTestId("probe")).toHaveTextContent("hidden");
    act(() =>
      fire([
        {
          isIntersecting: true,
          intersectionRatio: 0.05,
          boundingClientRect: { height: 10_000 } as DOMRectReadOnly,
          rootBounds: { height: 800 } as DOMRectReadOnly,
        },
      ]),
    );
    expect(screen.getByTestId("probe")).toHaveTextContent("visible");
  });

  it("waits for the threshold on an ordinary element", () => {
    let fire: Callback = () => {};
    restore = installObserver((callback) => {
      fire = callback;
    });
    render(<Probe />);
    const entry = (ratio: number) => ({
      isIntersecting: true,
      intersectionRatio: ratio,
      boundingClientRect: { height: 300 } as DOMRectReadOnly,
      rootBounds: { height: 800 } as DOMRectReadOnly,
    });
    act(() => fire([entry(0.05)]));
    expect(screen.getByTestId("probe")).toHaveTextContent("hidden");
    act(() => fire([entry(0.2)]));
    expect(screen.getByTestId("probe")).toHaveTextContent("visible");
  });
});
