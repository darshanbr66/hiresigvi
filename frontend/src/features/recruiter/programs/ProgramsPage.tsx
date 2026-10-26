import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../../../lib/apiClient";
import { Alert } from "../../../shared/components/Alert";
import { EmptyState } from "../../../shared/components/EmptyState";
import { Modal } from "../../../shared/components/Modal";
import { SkeletonTable } from "../../../shared/components/Skeleton";
import { Spinner } from "../../../shared/components/Spinner";
import { useToast } from "../../../shared/components/ToastContext";
import { PROGRAM_STATUS_LABELS } from "../../../types/program";
import { useAuth } from "../../auth/AuthContext";
import { createProgram, listPrograms } from "./api";
import { PROGRAM_STATUS_BADGE } from "./presentation";

/** Recruitment programs, e.g. the MTech/PhD Scholarship & Paid Training
 * Program: a program starts as a draft, which may be incomplete while the
 * business finalizes its terms. */
export function ProgramsPage() {
  const { accessToken, user } = useAuth();
  const token = accessToken as string;
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const canManage = user?.roles.some((r) => ["ORG_ADMIN", "RECRUITER"].includes(r)) ?? false;
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);

  const listQuery = useQuery({
    queryKey: ["recruiter", "programs"],
    queryFn: () => listPrograms(token),
    enabled: accessToken !== null,
  });

  const createMutation = useMutation({
    mutationFn: () => createProgram({ title: title.trim(), description: description.trim() }, token),
    onSuccess: (program) => {
      showToast("Draft program created. Complete its configuration, then publish it.", "success");
      void queryClient.invalidateQueries({ queryKey: ["recruiter", "programs"] });
      navigate(`/recruiter/programs/${program.id}`);
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "Unable to reach the server."),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!title.trim() || !description.trim()) {
      setError("A title and description are required.");
      return;
    }
    createMutation.mutate();
  }

  const forbidden = listQuery.error instanceof ApiError && listQuery.error.status === 403;

  return (
    <div className="stack-lg">
      <div className="page-header">
        <div>
          <h1>Programs</h1>
          <p className="muted">
            Dedicated recruitment programs with their own eligibility, selection test, scholarship,
            paid training and employment pathway.
          </p>
        </div>
        {canManage && !forbidden && (
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => {
              setTitle("");
              setDescription("");
              setError(null);
              setCreating(true);
            }}
          >
            + New program
          </button>
        )}
      </div>

      {listQuery.isPending && <SkeletonTable columns={5} />}
      {forbidden && <Alert>You do not have permission to view programs.</Alert>}
      {listQuery.isError && !forbidden && <Alert>Could not load programs.</Alert>}
      {listQuery.isSuccess &&
        (listQuery.data.length === 0 ? (
          <EmptyState icon="program" title="No programs yet">
            Create a draft program — unfinalized fees and amounts can stay empty until approved.
          </EmptyState>
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Program</th>
                  <th>Status</th>
                  <th>Training</th>
                  <th>Applications close</th>
                  <th>Applicants</th>
                </tr>
              </thead>
              <tbody>
                {listQuery.data.map((program) => (
                  <tr
                    key={program.id}
                    className="clickable-row"
                    onClick={() => navigate(`/recruiter/programs/${program.id}`)}
                  >
                    <td>
                      <a
                        href={`/recruiter/programs/${program.id}`}
                        onClick={(e) => {
                          e.preventDefault();
                          navigate(`/recruiter/programs/${program.id}`);
                        }}
                      >
                        {program.title}
                      </a>
                    </td>
                    <td>
                      <span className={`badge ${PROGRAM_STATUS_BADGE[program.status]}`}>
                        {PROGRAM_STATUS_LABELS[program.status]}
                      </span>
                    </td>
                    <td>
                      {program.training_duration_options.length
                        ? program.training_duration_options.map((m) => `${m} months`).join(" / ")
                        : "Not set"}
                    </td>
                    <td>
                      {program.application_closes_at
                        ? new Date(program.application_closes_at).toLocaleDateString()
                        : "—"}
                    </td>
                    <td>{program.enrollment_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}

      {creating && (
        <Modal title="New program" onClose={() => !createMutation.isPending && setCreating(false)}>
          <form onSubmit={submit} className="stack-sm">
            <label className="field">
              <span>Title</span>
              <input
                required
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="e.g. MTech/PhD Scholarship & Paid Training Program"
                disabled={createMutation.isPending}
              />
            </label>
            <label className="field">
              <span>Description</span>
              <textarea
                required
                rows={4}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                disabled={createMutation.isPending}
              />
            </label>
            <p className="field-hint">
              The program is saved as a draft. Nothing is shown to candidates until you publish it.
            </p>
            {error && <Alert>{error}</Alert>}
            <div className="btn-group">
              <button type="submit" className="btn btn-primary" disabled={createMutation.isPending}>
                {createMutation.isPending ? <Spinner label="Creating…" /> : "Create draft"}
              </button>
              <button type="button" className="btn btn-ghost" onClick={() => setCreating(false)} disabled={createMutation.isPending}>
                Cancel
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
