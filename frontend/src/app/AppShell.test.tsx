import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "../features/theme/ThemeContext";
import { AppShell, type NavItem, type NavSection } from "./AppShell";

const ITEMS: NavItem[] = [
  { to: "/recruiter", label: "Overview", icon: "overview", end: true },
  { to: "/recruiter/jobs", label: "Jobs", icon: "jobs" },
  { to: "/recruiter/candidates", label: "Candidates", icon: "candidates" },
];
const SECTIONS: NavSection[] = [{ label: "Workspace", items: ITEMS }];

function renderShell({
  path = "/recruiter/jobs",
  tabs = ITEMS,
  onSignOut = () => {},
  notificationBell,
}: {
  path?: string;
  tabs?: NavItem[];
  onSignOut?: () => void;
  notificationBell?: { unreadCount: number; to: string };
} = {}) {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/recruiter"
            element={
              <AppShell
                brandTitle="HireZ"
                contextLabel="Recruiter Portal"
                sections={SECTIONS}
                upcoming={["Sourcing"]}
                tabs={tabs}
                userName="Riya Recruiter"
                userRole="RECRUITER"
                onSignOut={onSignOut}
                notificationBell={notificationBell}
              />
            }
          >
            <Route index element={<p>Overview page</p>} />
            <Route path="jobs" element={<p>Jobs page</p>} />
            <Route path="candidates" element={<p>Candidates page</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  );
}

describe("AppShell", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("renders the page in the content region, with a skip link straight to it", () => {
    renderShell();

    expect(screen.getByText("Jobs page")).toBeInTheDocument();
    const skip = screen.getByRole("link", { name: "Skip to main content" });
    expect(skip).toHaveAttribute("href", "#main-content");
    expect(document.getElementById("main-content")).toContainElement(screen.getByText("Jobs page"));
  });

  it("marks the current page in the navigation and names it in the top bar", () => {
    const { container } = renderShell({ path: "/recruiter/candidates" });
    const sidebar = container.querySelector(".sidebar") as HTMLElement;

    // exactly one item is current, and it is the page being viewed
    const current = within(sidebar).getAllByRole("link").filter((link) => link.getAttribute("aria-current") === "page");
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveTextContent("Candidates");
    expect(current[0]).toHaveClass("active");

    expect(container.querySelector(".topbar-title")).toHaveTextContent("Candidates");
    expect(container.querySelector(".topbar-context")).toHaveTextContent("Recruiter Portal");
  });

  it("names the section from the longest matching route, not just the first prefix", () => {
    const { container } = renderShell({ path: "/recruiter/jobs" });

    // "/recruiter" (Overview, end-only) must not claim "/recruiter/jobs"
    expect(container.querySelector(".topbar-title")).toHaveTextContent("Jobs");
  });

  it("groups navigation, keeps disabled 'coming soon' items non-navigable, and shows who is signed in", () => {
    const { container } = renderShell();

    const sidebar = container.querySelector(".sidebar") as HTMLElement;
    expect(within(sidebar).getByText("Workspace")).toBeInTheDocument();
    // Text-only wordmark: the product name, no image logo.
    expect(within(sidebar).getByRole("img", { name: "HireZ" })).toHaveTextContent("HireZ");
    expect(sidebar.querySelector(".logo-mark")).toBeNull();
    for (const item of ITEMS) {
      expect(within(sidebar).getByRole("link", { name: item.label })).toHaveAttribute("href", item.to);
    }
    const upcoming = within(sidebar).getByText("Sourcing").closest(".sidebar-link");
    expect(upcoming).toHaveAttribute("aria-disabled", "true");
    expect(upcoming?.tagName).not.toBe("A");

    expect(container.querySelector(".topbar")).toHaveTextContent("Riya Recruiter");
    expect(container.querySelector(".topbar")).toHaveTextContent("RECRUITER");
  });

  it("signs out from the top bar", () => {
    const onSignOut = vi.fn();
    const { container } = renderShell({ onSignOut });

    fireEvent.click(within(container.querySelector(".topbar") as HTMLElement).getByRole("button", { name: "Sign out" }));

    expect(onSignOut).toHaveBeenCalledTimes(1);
  });

  it("opens the phone drawer from the menu button and closes it with Escape or the close button", () => {
    const { container } = renderShell();
    const sidebar = container.querySelector(".sidebar") as HTMLElement;
    expect(sidebar).not.toHaveClass("open");

    fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
    expect(sidebar).toHaveClass("open");
    expect(screen.getByRole("button", { name: "Open menu" })).toHaveAttribute("aria-expanded", "true");

    fireEvent.keyDown(document, { key: "Escape" });
    expect(sidebar).not.toHaveClass("open");

    fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
    fireEvent.click(screen.getByRole("button", { name: "Close menu" }));
    expect(sidebar).not.toHaveClass("open");
  });

  it("closes the drawer when a navigation link is followed", () => {
    const { container } = renderShell({ path: "/recruiter" });
    const sidebar = container.querySelector(".sidebar") as HTMLElement;

    fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
    expect(sidebar).toHaveClass("open");
    fireEvent.click(within(sidebar).getByRole("link", { name: "Candidates" }));

    expect(screen.getByText("Candidates page")).toBeInTheDocument();
    expect(sidebar).not.toHaveClass("open");
  });

  it("gives phones a bottom tab bar (with More) when tabs are supplied, and none otherwise", () => {
    const { container, unmount } = renderShell();
    const tabbar = container.querySelector(".tabbar") as HTMLElement;
    expect(within(tabbar).getAllByRole("link")).toHaveLength(ITEMS.length);
    expect(within(tabbar).getByRole("button", { name: "More" })).toBeInTheDocument();
    unmount();

    const admin = renderShell({ tabs: [] });
    expect(admin.container.querySelector(".tabbar")).toBeNull();
  });

  it("shows a notification bell with an unread badge that links to notifications", () => {
    const { container } = renderShell({ notificationBell: { unreadCount: 3, to: "/recruiter/notifications" } });

    const bell = screen.getByRole("link", { name: /notifications, 3 unread/i });
    expect(bell).toHaveAttribute("href", "/recruiter/notifications");
    expect(container.querySelector(".topbar-icon-badge")).toHaveTextContent("3");
  });

  it("hides the unread badge when there is nothing unread", () => {
    const { container } = renderShell({ notificationBell: { unreadCount: 0, to: "/recruiter/notifications" } });

    expect(screen.getByRole("link", { name: "Notifications" })).toBeInTheDocument();
    expect(container.querySelector(".topbar-icon-badge")).toBeNull();
  });

  it("omits the bell entirely when no notificationBell prop is passed", () => {
    renderShell();
    expect(screen.queryByRole("link", { name: /notifications/i })).not.toBeInTheDocument();
  });

  it("collapses and expands the sidebar, hiding labels while keeping icons", () => {
    const { container } = renderShell();
    const sidebar = container.querySelector(".sidebar") as HTMLElement;
    expect(sidebar).not.toHaveClass("sidebar-collapsed");

    // Expanded: the toggle offers to collapse, and shows the collapse glyph.
    const expandedToggle = screen.getByRole("button", { name: "Collapse sidebar" });
    expect(expandedToggle.querySelector("[data-icon='sidebar-collapse']")).toBeInTheDocument();

    fireEvent.click(expandedToggle);
    expect(sidebar).toHaveClass("sidebar-collapsed");
    // The icon is still present and the link still navigable; only the
    // visible label text is hidden by CSS, not removed from the DOM's
    // accessible name (the link's name).
    expect(within(sidebar).getByRole("link", { name: "Jobs" })).toBeInTheDocument();

    const collapsedToggle = screen.getByRole("button", { name: "Expand sidebar" });
    expect(collapsedToggle.querySelector("[data-icon='sidebar-expand']")).toBeInTheDocument();

    fireEvent.click(collapsedToggle);
    expect(sidebar).not.toHaveClass("sidebar-collapsed");
    expect(
      screen.getByRole("button", { name: "Collapse sidebar" }).querySelector("[data-icon='sidebar-collapse']"),
    ).toBeInTheDocument();
  });

  it("shows the full wordmark expanded and the compact Z mark beside the toggle when collapsed", () => {
    const { container } = renderShell();
    const brand = container.querySelector(".sidebar-brand") as HTMLElement;

    const full = within(brand).getByRole("img", { name: "HireZ" });
    expect(full).toHaveTextContent("HireZ");
    expect(full).not.toHaveClass("wordmark-mark");

    fireEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    // Same header row: the toggle first, then the compact mark — still
    // named "HireZ", showing only the indigo Z.
    const mark = within(brand).getByRole("img", { name: "HireZ" });
    expect(mark).toHaveClass("wordmark-mark");
    expect(mark).toHaveTextContent(/^Z$/);
    expect(mark.querySelector(".wordmark-z")).toBeInTheDocument();
    const toggle = within(brand).getByRole("button", { name: "Expand sidebar" });
    expect(toggle.compareDocumentPosition(mark) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    fireEvent.click(toggle);
    expect(within(brand).getByRole("img", { name: "HireZ" })).toHaveTextContent("HireZ");
  });

  it("keeps the full wordmark and the close button in the phone drawer, even with a stored collapse", () => {
    localStorage.setItem("ai-recruitment-sidebar-collapsed", "true");
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query.includes("max-width: 720px"),
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }));
    try {
      const { container } = renderShell();
      const sidebar = container.querySelector(".sidebar") as HTMLElement;
      expect(sidebar).not.toHaveClass("sidebar-collapsed");
      const brand = container.querySelector(".sidebar-brand") as HTMLElement;
      const wordmark = within(brand).getByRole("img", { name: "HireZ", hidden: true });
      expect(wordmark).toHaveTextContent("HireZ");
      expect(wordmark).not.toHaveClass("wordmark-mark");
      expect(brand.querySelector(".sidebar-collapse-toggle")).toBeNull();
      expect(brand.querySelector(".sidebar-close")).toBeInTheDocument();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("settles the collapse toggle on the first render after collapsing, with no navigation", () => {
    const { container } = renderShell();
    const sidebar = container.querySelector(".sidebar") as HTMLElement;
    const toggle = screen.getByRole("button", { name: "Collapse sidebar" });

    fireEvent.click(toggle);

    // Everything the toggle's collapsed appearance depends on has to be true
    // on this render — the route never changed, so nothing else will come
    // along to correct it.
    expect(sidebar).toHaveClass("sidebar-collapsed");
    // Same element, re-styled rather than remounted: a remount here would
    // drop keyboard focus off the control the user just activated.
    expect(screen.getByRole("button", { name: "Expand sidebar" })).toBe(toggle);
    expect(toggle.querySelector("[data-icon='sidebar-expand']")).toBeInTheDocument();
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    // No tooltip left pointing at where the toggle used to be.
    expect(document.querySelector(".shell-tooltip")).not.toBeInTheDocument();

    // And the same on the way back out.
    fireEvent.click(toggle);
    expect(sidebar).not.toHaveClass("sidebar-collapsed");
    expect(screen.getByRole("button", { name: "Collapse sidebar" })).toBe(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(document.querySelector(".shell-tooltip")).not.toBeInTheDocument();
  });

  it("shows a floating tooltip on hover only once collapsed, and hides it on mouse leave", () => {
    const { container } = renderShell();
    const sidebar = container.querySelector(".sidebar") as HTMLElement;
    const jobsLink = within(sidebar).getByRole("link", { name: "Jobs" });

    fireEvent.mouseEnter(jobsLink);
    expect(document.querySelector(".shell-tooltip")).not.toBeInTheDocument();
    fireEvent.mouseLeave(jobsLink);

    fireEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    fireEvent.mouseEnter(jobsLink);
    expect(document.querySelector(".shell-tooltip")).toHaveTextContent("Jobs");

    fireEvent.mouseLeave(jobsLink);
    expect(document.querySelector(".shell-tooltip")).not.toBeInTheDocument();
  });

  it("hides any open tooltip immediately when the sidebar is collapsed or expanded", () => {
    const { container } = renderShell();
    const sidebar = container.querySelector(".sidebar") as HTMLElement;
    const jobsLink = within(sidebar).getByRole("link", { name: "Jobs" });

    fireEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    fireEvent.mouseEnter(jobsLink);
    expect(document.querySelector(".shell-tooltip")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Expand sidebar" }));
    expect(document.querySelector(".shell-tooltip")).not.toBeInTheDocument();
  });

  it("shows the top header notification bell's tooltip on hover", () => {
    renderShell({ notificationBell: { unreadCount: 1, to: "/recruiter/notifications" } });

    fireEvent.mouseEnter(screen.getByRole("link", { name: /notifications, 1 unread/i }));
    const tooltip = document.querySelector(".shell-tooltip");
    expect(tooltip).toHaveTextContent("Notifications");
    expect(tooltip).toHaveClass("shell-tooltip-bottom");
  });

  it("persists the collapsed preference across remounts", () => {
    const first = renderShell();
    fireEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    first.unmount();

    const second = renderShell();
    expect(second.container.querySelector(".sidebar")).toHaveClass("sidebar-collapsed");
  });
});
