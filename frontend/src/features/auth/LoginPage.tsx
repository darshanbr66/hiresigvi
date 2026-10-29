import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate, type Location } from "react-router-dom";
import { ApiError } from "../../lib/apiClient";
import { HoldBrandLoading } from "../../shared/components/BrandLoading";
import { Icon } from "../../shared/components/Icon";
import { Spinner } from "../../shared/components/Spinner";
import { Wordmark } from "../../shared/components/Wordmark";
import { ThemeToggle } from "../theme/ThemeToggle";
import { useAuth } from "./AuthContext";

/**
 * One login form for every staff principal (ORG_ADMIN, RECRUITER,
 * HIRING_MANAGER, INTERVIEWER, SUPER_ADMIN) — login is staff auth, not
 * "recruiter business data" (see backend/app/api/v1/recruiter/auth.py).
 * Where the caller lands afterward depends on which kind of account it is.
 */
export function LoginPage() {
  const { status, isSuperAdmin, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  if (status === "authenticated") {
    return (
      <>
        <HoldBrandLoading />
        <Navigate to={isSuperAdmin ? "/admin" : "/recruiter"} replace />
      </>
    );
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      const user = await login(email, password);
      const from = (location.state as { from?: Location } | null | undefined)?.from;
      const fallback = user.organization_id === null ? "/admin" : "/recruiter";
      navigate(from?.pathname ?? fallback, { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to reach the server.");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="signin">
      {/* On a cold start, a still-signed-in user is redirected as soon as
          the session restores; the startup screen covers the form until
          then (and through the redirect above) so it doesn't flash. The
          form itself renders as before. */}
      {status === "loading" && <HoldBrandLoading />}
      <div className="signin-canvas" aria-hidden="true" />
      <header className="signin-top">
        <Link to="/" className="signin-back">
          <Icon name="arrow-right" size={16} className="signin-back-icon" />
          Back to home
        </Link>
        <ThemeToggle />
      </header>

      <main className="signin-main">
        <div className="signin-card">
          <Link to="/" className="signin-brand" aria-label="HireZ home">
            <Wordmark size="md" />
          </Link>
          <h1>Sign in</h1>
          <p className="signin-lead">
            For recruiters, hiring managers, interviewers and organization admins.
          </p>

          <form onSubmit={handleSubmit} className="signin-form">
            <label className="field">
              <span>Email</span>
              <input
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={isSubmitting}
              />
            </label>

            <label className="field">
              <span>Password</span>
              <input
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={isSubmitting}
              />
            </label>

            {error && (
              <p className="alert alert-error" role="alert">
                {error}
              </p>
            )}

            <button
              type="submit"
              className="btn btn-primary signin-submit"
              disabled={isSubmitting}
              aria-busy={isSubmitting}
            >
              {isSubmitting ? <Spinner label="Signing in…" /> : "Sign in"}
            </button>
          </form>
        </div>
        <p className="signin-note">Staff accounts are created by your organization&apos;s admin.</p>
      </main>
    </div>
  );
}
