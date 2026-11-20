import { Link } from "react-router-dom";
import { ThemeToggle } from "../theme/ThemeToggle";
import { ORGANIZATION_LOGO_SRC, ORGANIZATION_NAME } from "./homeContent";

/** The slim top bar shared by every candidate-facing page (careers list, role
 * detail, campus drive, program, assessment): the hiring organization's
 * identity, a "Careers" label, and the theme switch. Candidates are applying
 * to that organization, so it — not the ZPHIREX product brand — leads.
 *
 * A deep-navy bar in both themes (Zodiac Patent's official palette, see
 * styles/careers.css). For the organization this deployment serves, the
 * identity is its official logo, whose white wordmark needs the dark bar;
 * any other organization is shown by name. Deliberately quiet: candidates
 * are here to read and apply, not to navigate. */
export function PublicHeader({ title }: { title: string }) {
  const hasLogo = title === ORGANIZATION_NAME;
  return (
    <header className="public-nav">
      <Link to="/" className="public-brand">
        {hasLogo ? (
          <img src={ORGANIZATION_LOGO_SRC} alt={title} width={413} height={76} className="public-brand-logo" />
        ) : (
          <span className="topbar-title">{title}</span>
        )}
        <span className="public-brand-divider" aria-hidden="true" />
        <span className="public-brand-label">Careers</span>
      </Link>
      <ThemeToggle />
    </header>
  );
}
