import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../features/auth/AuthContext";
import { HoldBrandLoading } from "../shared/components/BrandLoading";

/**
 * Client-side route guard for UX only — the backend re-enforces every
 * authorization decision independently (CLAUDE.md § 2: "Frontend
 * authorization != backend authorization").
 */
export function ProtectedRoute({
  children,
  requireSuperAdmin = false,
}: {
  children: ReactNode;
  requireSuperAdmin?: boolean;
}) {
  const { status, isSuperAdmin } = useAuth();
  const location = useLocation();

  if (status === "loading") {
    // On a cold start the HireZ startup screen covers this until the
    // session settles (or its time limit passes); the text below is what
    // remains if restoring takes longer than that.
    return (
      <div className="page-loading" role="status">
        <HoldBrandLoading />
        Loading session…
      </div>
    );
  }

  if (status === "unauthenticated") {
    return <Navigate to="/recruiter/login" state={{ from: location }} replace />;
  }

  if (requireSuperAdmin && !isSuperAdmin) {
    return <Navigate to="/recruiter" replace />;
  }

  if (!requireSuperAdmin && isSuperAdmin) {
    return <Navigate to="/admin" replace />;
  }

  return <>{children}</>;
}
