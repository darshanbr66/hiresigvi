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
    const list = screen.getByRole("list", { name: "Opportunity categories" });
    const cards = [...list.querySelectorAll(".zx-card")];
    expect(cards.map((card) => card.querySelector(".zx-card-title")?.textContent)).toEqual([
      "Open Roles",
      "Internships",
      "Campus Hiring",
      "Training Programs",
    ]);
  });

  it("keeps Open Roles as the one link, to the careers page's All view", () => {
    renderHome();

    const list = screen.getByRole("list", { name: "Opportunity categories" });
    // the only real link (with a destination) among the cards
    expect(list.querySelectorAll("a[href]")).toHaveLength(1);
    expect(careersPath).toBe("/org/zodiac-patent");
    const openRoles = screen.getByRole("link", { name: /^Open Roles/ });
    expect(openRoles).toHaveAttribute("href", "/org/zodiac-patent?view=all");
    expect(openRoles.tagName).toBe("A");
    expect(openRoles).toHaveAttribute("class", "zx-card");
    expect(openRoles).not.toHaveAttribute("aria-disabled");
    expect(openRoles).toHaveTextContent("Explore current job opportunities.");
    expect(openRoles).toHaveTextContent("View Open Roles");
    expect(openRoles).not.toHaveTextContent("Coming Soon");
  });

  it.each([
    ["Internships", "Explore internship opportunities.", "Explore Internships"],
    ["Campus Hiring", "Explore opportunities for students and campus candidates.", "Explore Campus Opportunities"],
    ["Training Programs", "Discover training and trainee opportunities.", "Explore Programs"],
  ])("shows %s as an inactive Coming Soon card", (title, text, oldCta) => {
    renderHome();

    const card = screen.getByRole("link", { name: new RegExp(`^${title}`) });
    // exactly the original card styling (same class, nothing added), icon
    // and description; "Coming Soon" where the link text was
    expect(card.tagName).toBe("DIV");
    expect(card).toHaveAttribute("class", "zx-card");
    expect(card.querySelector(".zx-card-deco")).toHaveAttribute("aria-hidden", "true");
    expect(card.querySelector(".zx-card-icon svg")).not.toBeNull();
    expect(card).toHaveTextContent(text);
    expect(card.querySelector(".zx-card-cta")).toHaveTextContent(/^Coming Soon$/);
    expect(card).not.toHaveTextContent(oldCta);
    // keyboard-reachable, announced as unavailable, with nowhere to go
    expect(card).toHaveAttribute("tabindex", "0");
    expect(card).toHaveAttribute("aria-disabled", "true");
    expect(card).not.toHaveAttribute("href");
    expect(card.querySelector("a, button, [href]")).toBeNull();
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

  function renderHomeWithCareers() {
    vi.spyOn(careersApi, "listOpenJobs").mockResolvedValue([]);
    vi.spyOn(careersApi, "listPublicPrograms").mockResolvedValue([]);
    vi.spyOn(careersApi, "getPublicOrganization").mockResolvedValue({
      name: "Zodiac Patent",
      slug: "zodiac-patent",
      careers_contact_email: null,
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
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
  }

  const renderedCards = (container: HTMLElement) => expect(container.querySelectorAll(".zx-card")).toHaveLength(4);

  it.each(["Internships", "Campus Hiring", "Training Programs"])(
    "%s goes nowhere on click, keyboard or touch",
    (title) => {
      const { container } = renderHomeWithCareers();
      renderedCards(container);
      const card = screen.getByRole("link", { name: new RegExp(`^${title}`) });
      card.focus();
      expect(card).toHaveFocus();
      for (const target of [card, card.querySelector(".zx-card-cta") as HTMLElement]) {
        fireEvent.click(target);
        fireEvent.keyDown(target, { key: "Enter" });
        fireEvent.keyDown(target, { key: " " });
        fireEvent.touchStart(target);
        fireEvent.touchEnd(target);
      }
      // still on the homepage: the careers page never opened
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Explore Opportunities");
      expect(screen.queryByRole("navigation", { name: "Opportunity type" })).not.toBeInTheDocument();
    },
  );

  it.each([["Open Roles", "All"]])("%s opens the careers page on the %s tab", async (card, tab) => {
    renderHomeWithCareers();

    fireEvent.click(screen.getByRole("link", { name: new RegExp(`^${card}`) }));

    const tabs = await screen.findByRole("navigation", { name: "Opportunity type" });
    expect(within(tabs).getByRole("link", { name: tab })).toHaveAttribute("aria-current", "page");
    const other = tab === "All" ? "Open Roles" : "All";
    expect(within(tabs).getByRole("link", { name: other })).not.toHaveAttribute("aria-current");
  });
});
