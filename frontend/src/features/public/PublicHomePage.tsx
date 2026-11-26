import { useEffect } from "react";
import { Link } from "react-router-dom";
import { Icon } from "../../shared/components/Icon";
import { ThemeToggle } from "../theme/ThemeToggle";
import { careersViewPath } from "./careersOrg";
import { OpportunityGlyph, ZphirexWordmark } from "./HomeGraphics";
import { OFFICIAL_SITE_URL, OPPORTUNITIES, ORGANIZATION_LOGO_SRC, ORGANIZATION_NAME } from "./homeContent";

/**
 * The public ZPHIREX homepage — a single-screen opportunity picker for
 * Zodiac Patent candidates. A compact brand header (ZPHIREX with Zodiac
 * Patent's official logo), one heading, and four cards — Open Jobs,
 * Internships, Campus Hiring, Training Programs — each opening the matching
 * careers-page view (careers/careersView.ts). No marketing sections and no
 * footer: it is sized to fit the viewport without scrolling. The staff sign-in
 * is a quiet header link, not a call to action.
 */
export function PublicHomePage() {
  useEffect(() => {
    document.title = "Careers at Zodiac Patent | ZPHIREX";
  }, []);

  return (
    <div className="zx-home">
      <header className="zx-header">
        <div className="zx-header-brand">
          <Link to="/" className="zx-header-zphirex" aria-label="ZPHIREX home">
            <ZphirexWordmark />
          </Link>
          <span className="zx-header-divider" aria-hidden="true" />
          <a
            href={OFFICIAL_SITE_URL}
            className="zx-header-org"
            target="_blank"
            rel="noopener noreferrer"
            aria-label={`${ORGANIZATION_NAME} — official website (opens in a new tab)`}
          >
            <img src={ORGANIZATION_LOGO_SRC} alt={ORGANIZATION_NAME} width={413} height={76} className="zx-org-logo" />
          </a>
        </div>
        <div className="zx-header-actions">
          <Link to="/recruiter/login" className="zx-header-staff">
            Staff sign in
          </Link>
          <ThemeToggle />
        </div>
      </header>

      <main className="zx-main">
        <div className="zx-intro">
          <h1 className="zx-title">Explore Opportunities</h1>
          <p className="zx-subtitle">Find the opportunity that's right for you.</p>
        </div>

        {/* the light surface below the brand-blue rule; the cards lift over its top edge */}
        <div className="zx-stage">
          <ul className="zx-cards" aria-label="Opportunity categories">
            {OPPORTUNITIES.map((card) => (
              <li key={card.id}>
                {card.comingSoon ? (
                  // Not open yet: the same card and styles (hover, focus and
                  // all), but no destination — no href and no handlers, so
                  // clicks, Enter/Space and taps do nothing. Still reachable
                  // by keyboard, announced as an unavailable link.
                  <div className="zx-card" role="link" aria-disabled="true" tabIndex={0}>
                    <span className="zx-card-deco" aria-hidden="true" />
                    <span className="zx-card-icon">
                      <OpportunityGlyph name={card.glyph} size={22} />
                    </span>
                    <span className="zx-card-body">
                      <span className="zx-card-title">{card.title}</span>
                      <span className="zx-card-text">{card.text}</span>
                      <span className="zx-card-cta">{card.cta}</span>
                    </span>
                  </div>
                ) : (
                  <Link to={careersViewPath(card.destination)} className="zx-card">
                    {/* decoration only: corner rings and dot pattern (styles/home.css) */}
                    <span className="zx-card-deco" aria-hidden="true" />
                    <span className="zx-card-icon">
                      <OpportunityGlyph name={card.glyph} size={22} />
                    </span>
                    <span className="zx-card-body">
                      <span className="zx-card-title">{card.title}</span>
                      <span className="zx-card-text">{card.text}</span>
                      <span className="zx-card-cta">
                        {card.cta} <Icon name="arrow-right" size={16} />
                      </span>
                    </span>
                  </Link>
                )}
              </li>
            ))}
          </ul>
        </div>
      </main>
    </div>
  );
}
