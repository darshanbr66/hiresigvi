import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SigviLayer } from "./SigviLayer";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function renderAt(path: string) {
  // The same shape as app/routes.tsx: Zodi wraps the public pages only.
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<SigviLayer />}>
          <Route path="/" element={<p>home</p>} />
          <Route path="/org/:slug" element={<p>careers</p>} />
          <Route path="/org/:slug/jobs/:jobId" element={<p>job</p>} />
        </Route>
        <Route path="/assessment/:token" element={<p>assessment</p>} />
        <Route path="/campus-drive/:token" element={<p>campus drive</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

const launcher = () => screen.findByRole("button", { name: /ask zodi/i });

describe("SigviLayer", () => {
  it.each([
    ["/", "home"],
    ["/org/zodiac-patent", "careers"],
    ["/org/zodiac-patent/jobs/abc", "job"],
  ])("offers the assistant on %s without displacing the page", async (path, text) => {
    renderAt(path);

    expect(screen.getByText(text)).toBeInTheDocument();
    expect(await launcher()).toBeInTheDocument();
  });

  it.each([
    ["/assessment/tok", "assessment"],
    ["/campus-drive/tok", "campus drive"],
  ])("does not offer the assistant on %s", async (path, text) => {
    renderAt(path);

    expect(screen.getByText(text)).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 500)); // longer than the idle delay
    expect(screen.queryByRole("button", { name: /ask zodi/i })).not.toBeInTheDocument();
  });

  it("loads after first paint, not with the page", () => {
    renderAt("/");

    expect(screen.queryByRole("button", { name: /ask zodi/i })).not.toBeInTheDocument();
  });
});
