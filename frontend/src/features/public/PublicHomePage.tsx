import { useEffect } from "react";
import { Link } from "react-router-dom";
import { Icon, type IconName } from "../../shared/components/Icon";
import { Reveal } from "../../shared/components/Reveal";
import { Wordmark } from "../../shared/components/Wordmark";
import { HeroSection } from "./HeroSection";
import { LandingNav } from "./LandingNav";

/** The organization this deployment serves; its careers page lists the open
 * roles candidates apply to (no candidate account is needed to apply). */
const CAREERS_ORG_SLUG = "sigvitas";

/**
 * The public HireZ product page. Deliberately short and outward-facing: what
 * HireZ does and how hiring flows through it, in plain language. Internal
 * operating rules (approvals, audit mechanics, tenancy, grading details)
 * live in the signed-in product and the docs, not here. Every statement
 * describes an implemented capability; there are no testimonials, logos,
 * statistics or integrations.
 */
const CAPABILITIES: { icon: IconName; title: string; text: string }[] = [
  {
    icon: "jobs",
    title: "Recruitment management",
    text: "Create jobs, publish them to your careers page and follow every application through one pipeline.",
  },
  {
    icon: "sparkles",
    title: "Candidate screening",
    text: "Review applications with AI-assisted screening that keeps your recruiters in charge of every decision.",
  },
  {
    icon: "assessments",
    title: "Assessments",
    text: "Build tests from a reusable question bank and review candidates' results in one place.",
  },
  {
    icon: "calendar",
    title: "Interviews",
    text: "Schedule interview rounds, assign interviewers and keep their feedback with the application.",
  },
  {
    icon: "campus",
    title: "Campus hiring",
    text: "Run hiring drives with colleges and universities, alongside your regular openings.",
  },
];

const STEPS: { title: string; text: string }[] = [
  { title: "Attract", text: "Publish roles; candidates apply online in a few minutes." },
  { title: "Evaluate", text: "Screen applications and invite candidates to assessments." },
  { title: "Interview", text: "Run structured interview rounds with your team." },
  { title: "Hire", text: "Make the decision and welcome your new colleague." },
];

export function PublicHomePage() {
  const careersPath = `/org/${CAREERS_ORG_SLUG}`;
  useEffect(() => {
    document.title = "HireZ — Recruitment workspace";
  }, []);

  return (
    <div className="landing home">
      <LandingNav careersPath={careersPath} />
      <main>
        <HeroSection careersPath={careersPath} />

        <section id="capabilities" className="home-section" aria-labelledby="capabilities-title">
          <Reveal className="home-intro home-intro-split">
            <div>
              <p className="home-kicker">Capabilities</p>
              <h2 id="capabilities-title" className="home-heading">
                Everything your hiring team needs, in one place
              </h2>
            </div>
            <p className="home-intro-lead">
              From the first application to a signed offer, your team works in one place — with the same
              people, stages and rules.
            </p>
          </Reveal>
          <ul className="home-capability-grid">
            {CAPABILITIES.map((item, index) => (
              <Reveal as="li" key={item.title} className="home-capability-card" delay={index * 70}>
                <span className="home-capability-icon" aria-hidden="true">
                  <Icon name={item.icon} size={20} />
                </span>
                <h3>{item.title}</h3>
                <p>{item.text}</p>
              </Reveal>
            ))}
          </ul>
        </section>

        <section id="how-it-works" className="home-section home-section-muted" aria-labelledby="how-title">
          <Reveal className="home-intro home-intro-center">
            <p className="home-kicker">How it works</p>
            <h2 id="how-title" className="home-heading">
              From application to offer
            </h2>
          </Reveal>
          <Reveal className="home-journey-wrap">
            <ol className="home-journey">
              {STEPS.map((step, index) => (
                <li key={step.title} className="home-journey-step" style={{ "--step": index } as React.CSSProperties}>
                  <span className="home-journey-index" aria-hidden="true">
                    {index + 1}
                  </span>
                  <h3>{step.title}</h3>
                  <p>{step.text}</p>
                </li>
              ))}
            </ol>
          </Reveal>
        </section>

        <section id="campus" className="home-section" aria-labelledby="campus-title">
          <div className="home-columns home-campus">
            <Reveal className="home-intro">
              <p className="home-kicker">Campus recruitment</p>
              <h2 id="campus-title" className="home-heading">
                Built for hiring from campus
              </h2>
            </Reveal>
            <Reveal className="home-campus-body" delay={60}>
              <p>
                Work with colleges and universities, run campus drives, and bring selected students into
                structured training programs designed to lead to full-time roles.
              </p>
              <ul className="home-checklist">
                <li>
                  <Icon name="institution" size={18} /> Partner institutions
                </li>
                <li>
                  <Icon name="campus" size={18} /> Campus drives
                </li>
                <li>
                  <Icon name="program" size={18} /> Training programs
                </li>
              </ul>
            </Reveal>
          </div>
        </section>

        <section className="home-closing" aria-labelledby="closing-title">
          <Reveal className="home-closing-inner">
            <h2 id="closing-title" className="home-heading">
              Hire with clarity.
            </h2>
            <p>Sign in to your HireZ workspace, or browse the roles that are open right now.</p>
            <div className="hero-actions">
              <Link to="/recruiter/login" className="btn btn-primary btn-lg">
                Staff sign in
                <Icon name="arrow-right" size={18} />
              </Link>
              <Link to={careersPath} className="btn btn-ghost btn-lg">
                View open roles
              </Link>
            </div>
          </Reveal>
        </section>
      </main>

      <footer className="landing-footer">
        <Wordmark size="sm" />
        <nav className="landing-footer-links" aria-label="Footer">
          <a href="#capabilities">Capabilities</a>
          <a href="#campus">Campus</a>
          <Link to={careersPath}>Open roles</Link>
          <Link to="/recruiter/login">Staff sign in</Link>
        </nav>
        <span className="muted">&copy; {new Date().getFullYear()} SIGVITAS</span>
      </footer>
    </div>
  );
}
