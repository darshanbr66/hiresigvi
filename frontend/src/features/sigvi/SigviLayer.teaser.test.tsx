import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SigviLayer } from "./SigviLayer";

// The layer only decides *where* Zodi appears and what it is told; the widget
// itself is covered elsewhere, so stand in for it and read its props.
vi.mock("./SigviWidget", () => ({
  SigviWidget: (props: { teaser?: boolean; organizationSlug?: string }) => (
    <div data-testid="widget" data-teaser={String(props.teaser)} data-slug={props.organizationSlug ?? ""} />
  ),
}));

afterEach(cleanup);

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<SigviLayer />}>
          <Route path="/" element={<p>home</p>} />
          <Route path="/org/:slug" element={<p>careers</p>} />
          <Route path="/org/:slug/jobs/:jobId" element={<p>job</p>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("SigviLayer props", () => {
  it("offers the one-time teaser on the home page only", async () => {
    renderAt("/");

    expect(await screen.findByTestId("widget")).toHaveAttribute("data-teaser", "true");
  });

  it.each(["/org/zodiac-patent", "/org/zodiac-patent/jobs/abc"])("does not offer the teaser on %s", async (path) => {
    renderAt(path);

    const widget = await screen.findByTestId("widget");
    expect(widget).toHaveAttribute("data-teaser", "false");
    expect(widget).toHaveAttribute("data-slug", "zodiac-patent");
  });
});
