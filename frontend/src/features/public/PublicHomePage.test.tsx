import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as careersApi from "../careers/api";
import { CareersPage } from "../careers/CareersPage";
import { ThemeProvider } from "../theme/ThemeContext";
import { careersPath } from "./careersOrg";
import { PublicHomePage } from "./PublicHomePage";

function renderHome() {
  return render(
    <ThemeProvider>
      <MemoryRouter>
        <PublicHomePage />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

describe("PublicHomePage", () => {
  it("is branded ZPHIREX with Zodiac Patent's official logo — never Sigvitas or HireZ", () => {
    const { container } = renderHome();

    expect(screen.getByRole("img", { name: "ZPHIREX" })).toBeInTheDocument();
    const logo = screen.getByRole("img", { name: "Zodiac Patent" });
    expect(logo).toHaveAttribute("src", "/brand/zodiac-patent-logo.png");
    expect(logo.closest("a")).toHaveAttribute("href", "https://zodiacpatent.com/");
    expect(container.textContent).not.toMatch(/sigvitas|hirez/i);
    for (const el of container.querySelectorAll("[aria-label], [alt]")) {
      expect(`${el.getAttribute("aria-label")} ${el.getAttribute("alt")}`).not.toMatch(/sigvitas|hirez/i);
    }
    expect(document.title).toBe("Careers at Zodiac Patent | ZPHIREX");
  });

  it("shows one heading and exactly four opportunity cards, in order", () => {
    renderHome();

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Explore Opportunities");
    expect(screen.getAllByRole("heading")).toHaveLength(1);
    const cards = within(screen.getByRole("list", { name: "Opportunity categories" })).getAllByRole("link");
    expect(cards.map((card) => card.querySelector(".zx-card-title")?.textContent)).toEqual([
      "Open Roles",
      "Internships",
      "Campus Hiring",
      "Training Programs",
    ]);
  });

  it("links each card to its exact careers destination", () => {
    renderHome();

    const hrefFor = (title: RegExp) => screen.getByRole("link", { name: title }).getAttribute("href");
    expect(careersPath).toBe("/org/zodiac-patent");
    expect(hrefFor(/^Open Roles/)).toBe("/org/zodiac-patent?view=all");
    expect(hrefFor(/^Internships/)).toBe("/org/zodiac-patent?view=jobs");
    expect(hrefFor(/^Campus Hiring/)).toBe("/org/zodiac-patent?view=jobs");
    expect(hrefFor(/^Training Programs/)).toBe("/org/zodiac-patent?view=jobs");
    const openRoles = screen.getByRole("link", { name: /^Open Roles/ });
    expect(openRoles).toHaveTextContent("Explore current job opportunities.");
    expect(openRoles).toHaveTextContent("View Open Roles");
    expect(screen.getByRole("link", { name: /^Training Programs/ })).toHaveTextContent("Explore Programs");
  });

  it("keeps the card decoration out of the accessibility tree", () => {
    const { container } = renderHome();

    const decos = container.querySelectorAll(".zx-card-deco");
    expect(decos).toHaveLength(4);
    for (const deco of decos) expect(deco).toHaveAttribute("aria-hidden", "true");
  });

  it("has no long-form sections, footer or marketing calls to action", () => {
    const { container } = renderHome();

    expect(container.querySelector("footer")).toBeNull();
    expect(container.querySelectorAll("section")).toHaveLength(0);
    expect(container.textContent).not.toMatch(/FAQ|Work Areas|Scholarship|Training Academy|About Zodiac/i);
  });

  it("keeps the staff sign-in as a quiet header link", () => {
    renderHome();

    const staff = screen.getByRole("link", { name: "Staff sign in" });
    expect(staff).toHaveAttribute("href", "/recruiter/login");
    expect(staff).not.toHaveClass("btn");
  });
});

describe("homepage card -> careers tab", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it.each([
    ["Open Roles", "All"],
    ["Internships", "Open Roles"],
    ["Campus Hiring", "Open Roles"],
    ["Training Programs", "Open Roles"],
  ])("%s opens the careers page on the %s tab", async (card, tab) => {
    vi.spyOn(careersApi, "listOpenJobs").mockResolvedValue([]);
    vi.spyOn(careersApi, "listPublicPrograms").mockResolvedValue([]);
    vi.spyOn(careersApi, "getPublicOrganization").mockResolvedValue({
      name: "Zodiac Patent",
      slug: "zodiac-patent",
      careers_contact_email: null,
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <ThemeProvider>
          <MemoryRouter initialEntries={["/"]}>
            <Routes>
              <Route path="/" element={<PublicHomePage />} />
              <Route path="/org/:slug" element={<CareersPage />} />
            </Routes>
          </MemoryRouter>
        </ThemeProvider>
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole("link", { name: new RegExp(`^${card}`) }));

    const tabs = await screen.findByRole("navigation", { name: "Opportunity type" });
    expect(within(tabs).getByRole("link", { name: tab })).toHaveAttribute("aria-current", "page");
    const other = tab === "All" ? "Open Roles" : "All";
    expect(within(tabs).getByRole("link", { name: other })).not.toHaveAttribute("aria-current");
  });
});
