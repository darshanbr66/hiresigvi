/** Roles holding `candidate.reapply.grant` on the backend (see the reapply
 * migration seeding it). UX only — the API re-checks the permission. */
const REAPPLY_GRANT_ROLES = ["ORG_ADMIN", "RECRUITER"];

export function canGrantReapply(roles: readonly string[] | undefined): boolean {
  return roles?.some((role) => REAPPLY_GRANT_ROLES.includes(role)) ?? false;
}
