import { Link } from "react-router-dom";
import { Icon } from "../../shared/components/Icon";
import { HeroProductPreview } from "./HeroProductPreview";

/**
 * The home-page hero: one line on what HireZ is, a short description, the
 * two real ways in (staff sign in; candidates browsing open roles), and a
 * layered preview of the product beside the copy.
 */
export function HeroSection({ careersPath }: { careersPath: string }) {
  return (
    <section className="hero home-hero" aria-labelledby="hero-title">
      <div className="hero-inner home-hero-inner">
        <div className="hero-copy">
          <h1 id="hero-title" className="hero-title">
            Hiring, organized
            <span className="home-hero-accent"> end to end.</span>
          </h1>
          <p className="hero-sub">
            HireZ brings jobs, candidate screening, assessments, interviews and campus recruitment
            into one calm workspace for your hiring team.
          </p>
          <div className="hero-actions">
            <Link to="/recruiter/login" className="btn btn-primary btn-lg hero-cta">
              Staff sign in
              <Icon name="arrow-right" size={18} />
            </Link>
            <Link to={careersPath} className="btn btn-ghost btn-lg">
              View open roles
            </Link>
          </div>
        </div>
        <div className="home-hero-visual">
          <HeroProductPreview />
        </div>
      </div>
    </section>
  );
}
