import { Suspense, useEffect, useLayoutEffect, useRef, useState, type SyntheticEvent } from "react";
import { createPortal } from "react-dom";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { ThemeToggle } from "../features/theme/ThemeToggle";
import { Avatar } from "../shared/components/Avatar";
import { Icon, type IconName } from "../shared/components/Icon";
import { PageTransition } from "../shared/components/PageTransition";
import { RouteFallback } from "../shared/components/RouteFallback";
import { Wordmark } from "../shared/components/Wordmark";
import { useMediaQuery } from "../shared/hooks/useMediaQuery";

const SIDEBAR_COLLAPSED_KEY = "ai-recruitment-sidebar-collapsed";

function readStoredCollapsed(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "true";
  } catch {
    return false;
  }
}

interface TooltipState {
  label: string;
  top: number;
  left: number;
  placement: "right" | "bottom";
}

export interface NavItem {
  to: string;
  label: string;
  icon: IconName;
  end?: boolean;
  /** An unread-style count shown as a small pill next to the label (e.g.
   * the Notifications nav item). Omitted or 0 shows nothing. */
  badge?: number;
}

export interface NavSection {
  label?: string;
  items: NavItem[];
}

/**
 * The application frame shared by the recruiter and platform-admin surfaces:
 * sidebar, contextual top bar, scrolling content region and (on phones) a
 * bottom tab bar. It knows nothing about roles or permissions — each layout
 * decides *which* navigation to pass in (the API remains the authority).
 *
 * Navigation feel: a single indicator slides between items instead of the
 * highlight snapping, the top bar names where you are, and on phones the four
 * most-used destinations sit under the thumb with everything else one tap
 * away in the drawer.
 */
export function AppShell({
  brandTitle,
  contextLabel,
  sections,
  upcoming = [],
  tabs = [],
  userName,
  userRole,
  onSignOut,
  variant = "recruiter",
  notificationBell,
}: {
  /** Product name — the top-bar title when no nav item matches the route.
   * The sidebar header shows the HireZ wordmark (its compact "Z" mark on the collapsed rail). */
  brandTitle: string;
  /** Small label above the page title in the top bar, e.g. "Recruiter Portal". */
  contextLabel: string;
  sections: NavSection[];
  upcoming?: string[];
  /** Up to four primary destinations for the phone tab bar. */
  tabs?: NavItem[];
  userName: string | undefined;
  userRole: string;
  onSignOut: () => void;
  variant?: "recruiter" | "admin";
  /** A bell icon in the top header, beside the theme toggle — the same
   * unread count/destination as the sidebar's Notifications item, just
   * reachable without opening the sidebar. Omitted entirely (e.g. the
   * admin surface, which has no notification center) when not passed. */
  notificationBell?: { unreadCount: number; to: string };
}) {
  const location = useLocation();
  // The drawer is open *for a path*: navigating elsewhere closes it without an effect.
  const [drawerPath, setDrawerPath] = useState<string | null>(null);
  const menuOpen = drawerPath === location.pathname;
  const setMenuOpen = (open: boolean) => setDrawerPath(open ? location.pathname : null);
  const isPhone = useMediaQuery("(max-width: 720px)");
  const navRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const [indicator, setIndicator] = useState<{ y: number; h: number } | null>(null);
  const [indicatorReady, setIndicatorReady] = useState(false);
  // Desktop-only preference, persisted across refresh/navigation; irrelevant
  // on phones, which already use the overlay drawer instead of a rail.
  const [collapsed, setCollapsed] = useState(readStoredCollapsed);
  const isCollapsed = collapsed && !isPhone;

  // A single floating tooltip, portalled to <body> and positioned with
  // `getBoundingClientRect` + `position: fixed`. NOT a CSS `::after` on the
  // trigger itself: that approach (still occupying its layout box even at
  // opacity 0) was what caused the collapsed sidebar's phantom horizontal
  // scrollbar — an invisible tooltip escaping the narrow rail still counted
  // toward `.sidebar`'s scrollable overflow. A portalled, viewport-fixed
  // element can never do that, however narrow its scrolling ancestor is.
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);

  function showTooltip(event: SyntheticEvent<HTMLElement>, label: string, placement: "right" | "bottom") {
    const rect = event.currentTarget.getBoundingClientRect();
    setTooltip(
      placement === "right"
        ? { label, placement, top: rect.top + rect.height / 2, left: rect.right + 10 }
        : { label, placement, top: rect.bottom + 8, left: rect.left + rect.width / 2 },
    );
  }
  function hideTooltip() {
    setTooltip(null);
  }

  // A stale tooltip must never outlive the thing it points at: navigating
  // can unmount the hovered link entirely. Adjusted directly during render
  // (React's documented pattern for resetting state when a prop-like value
  // changes) rather than in an effect, which would cost an extra commit.
  const [tooltipRoute, setTooltipRoute] = useState(location.pathname);
  if (location.pathname !== tooltipRoute) {
    setTooltipRoute(location.pathname);
    if (tooltip !== null) setTooltip(null);
  }

  function toggleCollapsed() {
    hideTooltip();
    setCollapsed((current) => {
      const next = !current;
      try {
        localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(next));
      } catch {
        // Private browsing / storage disabled — still applies for this load.
      }
      return next;
    });
  }

  const allItems = sections.flatMap((section) => section.items);
  const current = [...allItems]
    .sort((a, b) => b.to.length - a.to.length)
    .find((item) => (item.end ? location.pathname === item.to : location.pathname.startsWith(item.to)));

  // The browser tab names the page and the product ("Jobs · HireZ").
  const pageTitle = current?.label ?? brandTitle;
  useEffect(() => {
    document.title = pageTitle === "HireZ" ? "HireZ" : `${pageTitle} · HireZ`;
  }, [pageTitle]);

  // Move the sliding indicator to whichever link React Router marked active.
  // `isCollapsed` is a dependency because collapsing drops the section labels
  // out of the DOM and shortens every link: the links all shift up, so a
  // measurement taken in the expanded layout would leave the indicator
  // sitting below the icon it belongs to. Collapsing is local state here, so
  // nothing else in this list changes to trigger a re-measure.
  useLayoutEffect(() => {
    const active = navRef.current?.querySelector<HTMLElement>(".sidebar-link.active");
    setIndicator(active ? { y: active.offsetTop, h: active.offsetHeight } : null);
  }, [location.pathname, sections, isCollapsed]);

  // Enable the indicator's transition only after its first placement, so it
  // doesn't visibly fly in from the top on initial load.
  useEffect(() => {
    if (indicator && !indicatorReady) {
      const frame = requestAnimationFrame(() => setIndicatorReady(true));
      return () => cancelAnimationFrame(frame);
    }
  }, [indicator, indicatorReady]);

  // Drawer: close on Escape, and hand focus to its close button when it opens.
  useEffect(() => {
    if (!menuOpen) return;
    closeRef.current?.focus();
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setDrawerPath(null);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [menuOpen]);

  const drawerHidden = isPhone && !menuOpen;

  return (
    <div className={`app-shell app-shell-${variant}`}>
      <a href="#main-content" className="skip-link">
        Skip to main content
      </a>
      <button
        type="button"
        className={`sidebar-overlay${menuOpen ? " open" : ""}`}
        aria-hidden={!menuOpen}
        tabIndex={-1}
        onClick={() => setMenuOpen(false)}
      />
      <aside
        className={`sidebar${variant === "admin" ? " sidebar-admin" : ""}${menuOpen ? " open" : ""}${isCollapsed ? " sidebar-collapsed" : ""}`}
        aria-label="Primary"
        // A closed phone drawer is off-screen: keep it out of tab order and
        // the accessibility tree instead of leaving invisible focus stops.
        inert={drawerHidden}
      >
        {/* One compact row: [collapse/expand] [HireZ]. On the 72px rail the
            full wordmark doesn't fit, so the same row shows the toggle and
            the compact "Z" mark, centred together at the same row height.
            Keyed by form so the swap fades in (shell.css) rather than
            snapping. Phones never collapse: the drawer keeps the full
            wordmark and gets its close button instead of the toggle. */}
        <div className="sidebar-brand">
          {!isPhone && (
            <button
              type="button"
              className="sidebar-collapse-toggle"
              aria-label={isCollapsed ? "Expand sidebar" : "Collapse sidebar"}
              aria-pressed={isCollapsed}
              onClick={toggleCollapsed}
              onMouseEnter={(e) => showTooltip(e, isCollapsed ? "Expand sidebar" : "Collapse sidebar", "right")}
              onMouseLeave={hideTooltip}
              onFocus={(e) => showTooltip(e, isCollapsed ? "Expand sidebar" : "Collapse sidebar", "right")}
              onBlur={hideTooltip}
            >
              <Icon name={isCollapsed ? "sidebar-expand" : "sidebar-collapse"} size={18} />
            </button>
          )}
          <span className="brand-lockup">
            <Wordmark key={isCollapsed ? "mark" : "full"} variant={isCollapsed ? "mark" : "full"} />
          </span>
          <button
            ref={closeRef}
            type="button"
            className="sidebar-close"
            aria-label="Close menu"
            onClick={() => setMenuOpen(false)}
          >
            <Icon name="x" size={18} />
          </button>
        </div>

        <nav className="sidebar-nav" ref={navRef} onScroll={hideTooltip}>
          <span
            className={`nav-indicator${indicatorReady ? " ready" : ""}`}
            aria-hidden="true"
            style={
              indicator
                ? { transform: `translateY(${indicator.y}px)`, height: indicator.h, opacity: 1 }
                : { opacity: 0 }
            }
          />
          {sections.map((section, index) => (
            <div key={section.label ?? index} className="nav-group">
              {section.label && !isCollapsed && <div className="sidebar-section-label">{section.label}</div>}
              {section.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) => `sidebar-link${isActive ? " active" : ""}`}
                  onMouseEnter={(e) => isCollapsed && showTooltip(e, item.label, "right")}
                  onMouseLeave={hideTooltip}
                  onFocus={(e) => isCollapsed && showTooltip(e, item.label, "right")}
                  onBlur={hideTooltip}
                >
                  <Icon name={item.icon} size={18} />
                  <span>{item.label}</span>
                  {!!item.badge && <span className="sidebar-badge">{item.badge > 99 ? "99+" : item.badge}</span>}
                </NavLink>
              ))}
            </div>
          ))}

          {upcoming.length > 0 && !isCollapsed && (
            <div className="nav-group">
              <div className="sidebar-section-label">Coming soon</div>
              {upcoming.map((label) => (
                <span key={label} className="sidebar-link disabled" aria-disabled="true">
                  <Icon name="search" size={18} />
                  <span>{label}</span>
                </span>
              ))}
            </div>
          )}
        </nav>

        <div className="sidebar-footer">
          <div className="sidebar-user">
            <Avatar name={userName} />
            {!isCollapsed && (
              <span className="sidebar-user-text">
                <span className="user-name">{userName}</span>
                <span className="user-roles">{userRole}</span>
              </span>
            )}
          </div>
          <button
            type="button"
            className="btn btn-ghost btn-sm sidebar-signout"
            onClick={onSignOut}
            onMouseEnter={(e) => isCollapsed && showTooltip(e, "Sign out", "right")}
            onMouseLeave={hideTooltip}
            onFocus={(e) => isCollapsed && showTooltip(e, "Sign out", "right")}
            onBlur={hideTooltip}
          >
            {isCollapsed ? <Icon name="x" size={16} label="Sign out" /> : "Sign out"}
          </button>
        </div>
      </aside>

      <div className="app-main">
        <header className="topbar">
          <div className="topbar-left">
            <button
              type="button"
              className="mobile-menu-button"
              aria-label="Open menu"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen(true)}
            >
              <Icon name="menu" size={18} />
            </button>
            <div className="topbar-heading">
              <span className="topbar-context">{contextLabel}</span>
              <span className="topbar-title">{current?.label ?? brandTitle}</span>
            </div>
          </div>
          <div className="topbar-user">
            {notificationBell && (
              <NavLink
                to={notificationBell.to}
                className="topbar-icon-btn"
                aria-label={
                  notificationBell.unreadCount > 0
                    ? `Notifications, ${notificationBell.unreadCount} unread`
                    : "Notifications"
                }
                onMouseEnter={(e) => showTooltip(e, "Notifications", "bottom")}
                onMouseLeave={hideTooltip}
                onFocus={(e) => showTooltip(e, "Notifications", "bottom")}
                onBlur={hideTooltip}
              >
                <Icon name="inbox" size={19} />
                {notificationBell.unreadCount > 0 && (
                  <span className="topbar-icon-badge">
                    {notificationBell.unreadCount > 99 ? "99+" : notificationBell.unreadCount}
                  </span>
                )}
              </NavLink>
            )}
            <ThemeToggle />
            <div className="user-chip">
              <Avatar name={userName} />
              <div className="user-badge">
                <span className="user-name">{userName}</span>
                <span className="user-roles">{userRole}</span>
              </div>
            </div>
            <button type="button" className="btn btn-ghost topbar-signout" onClick={onSignOut}>
              Sign out
            </button>
          </div>
        </header>

        <main id="main-content" className="content" tabIndex={-1}>
          <PageTransition>
            <Suspense fallback={<RouteFallback />}>
              <Outlet />
            </Suspense>
          </PageTransition>
        </main>

        {tabs.length > 0 && (
          <nav className="tabbar" aria-label="Quick navigation">
            {tabs.map((tab) => (
              <NavLink
                key={tab.to}
                to={tab.to}
                end={tab.end}
                className={({ isActive }) => `tabbar-link${isActive ? " active" : ""}`}
              >
                <Icon name={tab.icon} size={20} />
                <span>{tab.label}</span>
              </NavLink>
            ))}
            <button type="button" className="tabbar-link" onClick={() => setMenuOpen(true)}>
              <Icon name="menu" size={20} />
              <span>More</span>
            </button>
          </nav>
        )}
      </div>

      {tooltip &&
        createPortal(
          <div
            className={`shell-tooltip shell-tooltip-${tooltip.placement}`}
            style={{ top: tooltip.top, left: tooltip.left }}
          >
            {tooltip.label}
          </div>,
          document.body,
        )}
    </div>
  );
}
