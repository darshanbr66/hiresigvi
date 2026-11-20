import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { careersPath, LEGACY_ORG_SLUGS } from "../features/public/careersOrg";
import { LegacyOrgRedirect } from "./routes";

function Where() {
  const { pathname, search, hash } = useLocation();
  return <p data-testid="where">{`${pathname}${search}${hash}`}</p>;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/org/sigvitas" element={<LegacyOrgRedirect to="zodiac-patent" />} />
        <Route path="/org/sigvitas/jobs/:jobId" element={<LegacyOrgRedirect to="zodiac-patent" />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("legacy careers slug", () => {
  it("is mapped to the current Zodiac Patent slug, which the homepage links to", () => {
    expect(LEGACY_ORG_SLUGS).toEqual({ sigvitas: "zodiac-patent" });
    expect(careersPath).toBe("/org/zodiac-patent");
  });

  it("redirects the old careers page", () => {
    renderAt("/org/sigvitas");
    expect(screen.getByTestId("where")).toHaveTextContent("/org/zodiac-patent");
  });

  it("redirects an old role link, keeping the query string and hash", () => {
    renderAt("/org/sigvitas/jobs/job-123?ref=email#apply");
    expect(screen.getByTestId("where")).toHaveTextContent("/org/zodiac-patent/jobs/job-123?ref=email#apply");
  });
});
