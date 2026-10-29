import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Wordmark } from "../../shared/components/Wordmark";
import { ThemeToggle } from "../theme/ThemeToggle";

/**
 * The home page's top bar: the HireZ wordmark (text only), a few section
 * links, open roles, the theme switch and staff sign-in. It gains a solid
 * surface and hairline once the page has scrolled.
 */
export function LandingNav({ careersPath }: { careersPath: string }) {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 24);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header className={`landing-nav${scrolled ? " is-scrolled" : ""}`}>
      <Link to="/" className="landing-brand" aria-label="HireZ home">
        <Wordmark size="md" />
      </Link>
      <nav className="landing-nav-links" aria-label="Sections">
        <a href="#capabilities">Capabilities</a>
        <a href="#how-it-works">How it works</a>
        <a href="#campus">Campus</a>
        <Link to={careersPath}>Open roles</Link>
      </nav>
      <div className="landing-nav-actions">
        <ThemeToggle />
        <Link to="/recruiter/login" className="btn btn-primary btn-sm">
          Staff sign in
        </Link>
      </div>
    </header>
  );
}
