import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../../../lib/apiClient";
import { Alert } from "../../../shared/components/Alert";
import { Modal } from "../../../shared/components/Modal";
import { Spinner } from "../../../shared/components/Spinner";
import { useAuth } from "../../auth/AuthContext";
import { grantReapplyPermission } from "./api";

/**
 * HR "Grant Reapply Permission" confirmation. Granting permanently clears
 * the candidate's previous careers-site/campus-link applications (and what
 * is attached to them) so the same email and mobile number can apply again
 * from scratch — hence an explicit confirm step. The candidate profile,
 * HR-added applications and the Activities trail are kept (backend:
 * reapply_service.grant_reapply_permission).
 */
export function GrantReapplyModal({
  candidate,
  onClose,
}: {
  candidate: { id: string; full_name: string; email: string; phone: string | null };
  onClose: () => void;
}) {
  const { accessToken } = useAuth();
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: () => grantReapplyPermission(candidate.id, accessToken as string),
    onSuccess: () => {
      // Applications were removed: anything listing or counting them.
      void queryClient.invalidateQueries({ queryKey: ["recruiter", "candidates"] });
      void queryClient.invalidateQueries({ queryKey: ["recruiter", "applications"] });
    },
  });

  const close = mutation.isPending ? () => {} : onClose;

  if (mutation.isSuccess) {
    return (
      <Modal title="Reapply permission granted" onClose={onClose}>
        <div role="status">
          <p>
            Previous application data for <strong>{candidate.full_name}</strong> has been cleared.
            The candidate can now apply again using the same email and mobile number.
          </p>
        </div>
        <div className="btn-group" style={{ marginTop: "1rem" }}>
          <button type="button" className="btn btn-primary" onClick={onClose}>
            Done
          </button>
        </div>
      </Modal>
    );
  }

  return (
    <Modal title="Grant reapply permission?" onClose={close}>
      <p>
        <strong>{candidate.full_name}</strong>
        <br />
        <span className="muted">
          {candidate.email}
          {candidate.phone ? ` · ${candidate.phone}` : ""}
        </span>
      </p>
      <p className="muted">
        Granting reapply permission will remove the candidate's previous application data so they
        can apply again. Applications your team added for them, their profile and the Activities
        history are kept.
      </p>

      {mutation.isError && (
        <Alert>
          {mutation.error instanceof ApiError
            ? mutation.error.message
            : "Unable to reach the server."}
        </Alert>
      )}

      <div className="btn-group" style={{ marginTop: "1rem" }}>
        <button
          type="button"
          className="btn btn-ghost"
          onClick={onClose}
          disabled={mutation.isPending}
        >
          Cancel
        </button>
        <button
          type="button"
          className="btn btn-danger"
          onClick={() => mutation.mutate()}
          disabled={mutation.isPending}
        >
          {mutation.isPending ? <Spinner label="Granting…" /> : "Grant Reapply Permission"}
        </button>
      </div>
    </Modal>
  );
}
