import { Link } from "react-router-dom";
import { ThemeToggle } from "../theme/ThemeToggle";

/** The slim top bar shared by every candidate-facing page (careers list, role
 * detail, campus drive, program, assessment): the hiring organization's name
 * and the theme switch. Candidates are applying to that organization, so its
 * name — not the HireZ product brand, and no logo mark — leads. Deliberately
 * quiet: candidates are here to read and apply, not to navigate. */
export function PublicHeader({ title }: { title: string }) {
  return (
    <header className="public-nav">
      <Link to="/" className="public-brand">
        <span className="topbar-title">{title}</span>
      </Link>
      <ThemeToggle />
    </header>
  );
}
