import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import "../../../styles/ai.css";
import { ApiError } from "../../../lib/apiClient";
import { Alert } from "../../../shared/components/Alert";
import { EmptyState } from "../../../shared/components/EmptyState";
import { Icon, type IconName } from "../../../shared/components/Icon";
import { SkeletonCard } from "../../../shared/components/Skeleton";
import { Spinner } from "../../../shared/components/Spinner";
import { useToast } from "../../../shared/components/ToastContext";
import type { InternalAIQueryResponse } from "../../../types/internalAi";
import { useAuth } from "../../auth/AuthContext";
import { listApplicationsForCandidate, listApplicationsForJob } from "../applications/api";
import { getCandidate, listCandidates } from "../candidates/api";
import { listJobs } from "../jobs/api";
import { computeJobMatches, computeMatch, queryInternalAi } from "./api";
import { AiDisclaimer } from "./AiDisclaimer";
import { MatchResultCard } from "./MatchResultCard";
import { PickerModal } from "./PickerModal";
import { PipelineStatsPanel } from "./PipelineStatsPanel";
import { normalizeMatchCard, normalizeMatchResponse } from "./presentation";

type ConversationTurn =
  | { role: "user"; content: string }
  | { role: "assistant"; content: string; response?: InternalAIQueryResponse };

const AI_ROLES = ["ORG_ADMIN", "RECRUITER", "HIRING_MANAGER"];

const SUGGESTED_PROMPTS = [
  "Give me a summary of the current recruitment pipeline.",
  "Which candidates are currently shortlisted?",
  "How many candidates are currently in screening?",
];

const QUICK_ACTIONS: { key: "find" | "analyze" | "compare" | "ask"; icon: IconName; title: string; description: string }[] = [
  { key: "find", icon: "jobs", title: "Find candidates for a job", description: "Rank every applicant to an open role by match score." },
  { key: "analyze", icon: "candidates", title: "Analyze a candidate", description: "See how one candidate stacks up against a role." },
  { key: "compare", icon: "graph", title: "Compare candidates", description: "Line several candidates up side by side for one job." },
  { key: "ask", icon: "search", title: "Ask AI about recruitment data", description: "Ask a natural-language question about your pipeline." },
];

function friendlyAiError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return "You do not have permission to use the internal AI.";
    if (error.status === 404) return "That candidate or job could not be found.";
    if (error.status === 429) return "The AI assistant is getting a lot of requests. Please try again shortly.";
    if (error.status >= 500) return "The AI assistant is temporarily unavailable. Please try again in a moment.";
    return error.message;
  }
  return "Could not reach the server.";
}

/**
 * "AI Intelligence" — the internal recruitment-intelligence workspace
 * (candidate matching, screening and recruitment intelligence for
 * ORG_ADMIN/RECRUITER/HIRING_MANAGER — backend permission
 * `internal_ai.use`). Reads `jobId` / `candidateId` from the URL so
 * Jobs/Candidates pages can deep-link straight into a job-wide ranking or a
 * candidate analysis; with neither present it shows the workspace home
 * (quick actions + natural-language query).
 */
export function AiWorkspacePage() {
  const { accessToken, user } = useAuth();
  const token = accessToken as string;
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const jobId = searchParams.get("jobId");
  const candidateId = searchParams.get("candidateId");

  const [conversation, setConversation] = useState<ConversationTurn[]>([]);
  const [input, setInput] = useState("");
  const [conversationId, setConversationId] = useState<string | undefined>();
  const [selectedCompareIds, setSelectedCompareIds] = useState<Set<string>>(new Set());
  const [picker, setPicker] = useState<"job" | "job-compare" | "candidate" | null>(null);
  const [analysisJobId, setAnalysisJobId] = useState<string | null>(null);

  const canUseAi = user?.roles.some((role) => AI_ROLES.includes(role)) ?? true;

  const jobsQuery = useQuery({
    queryKey: ["recruiter", "jobs"],
    queryFn: () => listJobs(token),
    enabled: accessToken !== null,
  });
  const candidatesPickerQuery = useQuery({
    queryKey: ["recruiter", "candidates"],
    queryFn: () => listCandidates(token),
    enabled: accessToken !== null && picker === "candidate",
  });

  // --- Job-wide ranking ("Find candidates for a job" / "Compare candidates") ---
  const jobMatchQuery = useQuery({
    queryKey: ["recruiter", "ai", "match-job", jobId],
    queryFn: () => computeJobMatches({ job_id: jobId as string }, token),
    enabled: accessToken !== null && Boolean(jobId),
  });
  // MatchResponse carries ids only — names come from that job's applications.
  const jobApplicationsQuery = useQuery({
    queryKey: ["recruiter", "applications", "job", jobId],
    queryFn: () => listApplicationsForJob(jobId as string, token),
    enabled: accessToken !== null && Boolean(jobId),
  });
  const activeJob = jobsQuery.data?.find((job) => job.id === jobId);
  const candidateNameById = new Map(
    jobApplicationsQuery.data?.map((application) => [application.candidate_id, application.candidate_full_name]) ?? [],
  );

  // Adjusting state during render (React's documented pattern for "reset
  // some state when a prop changes") rather than a useEffect — the reset is
  // a direct consequence of `jobId`/`candidateId` changing, not a
  // synchronization with an external system.
  const [lastJobId, setLastJobId] = useState(jobId);
  if (jobId !== lastJobId) {
    setLastJobId(jobId);
    setSelectedCompareIds(new Set());
  }
  const [lastCandidateId, setLastCandidateId] = useState(candidateId);
  if (candidateId !== lastCandidateId) {
    setLastCandidateId(candidateId);
    setAnalysisJobId(null);
  }

  // --- Candidate analysis ("Analyze a candidate") ---
  const candidateQuery = useQuery({
    queryKey: ["recruiter", "candidates", candidateId],
    queryFn: () => getCandidate(candidateId as string, token),
    enabled: accessToken !== null && Boolean(candidateId),
  });
  const candidateApplicationsQuery = useQuery({
    queryKey: ["recruiter", "applications", "candidate", candidateId],
    queryFn: () => listApplicationsForCandidate(candidateId as string, token),
    enabled: accessToken !== null && Boolean(candidateId),
  });
  // Defaults to the candidate's most recent application's job once loaded,
  // without needing an effect: derived directly from query data, and an
  // explicit selection (setAnalysisJobId from the <select> below) always wins.
  const effectiveAnalysisJobId = analysisJobId ?? candidateApplicationsQuery.data?.[0]?.job_id ?? null;

  const candidateMatchQuery = useQuery({
    queryKey: ["recruiter", "ai", "match", candidateId, effectiveAnalysisJobId],
    queryFn: () =>
      computeMatch({ candidate_id: candidateId as string, job_id: effectiveAnalysisJobId as string }, token),
    enabled: accessToken !== null && Boolean(candidateId) && Boolean(effectiveAnalysisJobId),
  });

  // --- Natural-language query ---
  const chatMutation = useMutation({
    mutationFn: (message: string) =>
      queryInternalAi(
        {
          message,
          conversation_id: conversationId,
          history: conversation.slice(-8).map((turn) => ({ role: turn.role, content: turn.content })),
          context:
            jobId || candidateId
              ? { job_id: jobId ?? undefined, candidate_id: candidateId ?? undefined }
              : undefined,
        },
        token,
      ),
    onSuccess: (response) => {
      setConversationId(response.conversation_id);
      setConversation((current) => [...current, { role: "assistant", content: response.message, response }]);
    },
    onError: (error) => {
      const message = friendlyAiError(error);
      setConversation((current) => [...current, { role: "assistant", content: message }]);
      showToast(message, "error");
    },
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const message = input.trim();
    if (!message || chatMutation.isPending) return;
    setConversation((current) => [...current, { role: "user", content: message }]);
    setInput("");
    chatMutation.mutate(message);
  }

  function toggleCompare(candidateIdToToggle: string) {
    setSelectedCompareIds((current) => {
      const next = new Set(current);
      if (next.has(candidateIdToToggle)) next.delete(candidateIdToToggle);
      else next.add(candidateIdToToggle);
      return next;
    });
  }

  function goHome() {
    setSearchParams({});
  }

  function refreshJobMatches() {
    void queryClient.invalidateQueries({ queryKey: ["recruiter", "ai", "match-job", jobId] });
  }

  function handleQuickAction(key: "find" | "analyze" | "compare" | "ask") {
    if (key === "find") setPicker("job");
    else if (key === "compare") setPicker("job-compare");
    else if (key === "analyze") setPicker("candidate");
    else document.getElementById("ai-chat-input")?.focus();
  }

  const showHome = !jobId && !candidateId;
  const comparisonRows = jobMatchQuery.data?.filter((match) => selectedCompareIds.has(match.candidate_id)) ?? [];

  return (
    <div className="stack-lg">
      <div className="page-header">
        <div style={{ display: "flex", gap: "1rem", alignItems: "center" }}>
          <span className="ai-hero-icon" aria-hidden="true">
            <Icon name="sparkles" size={26} />
          </span>
          <div>
            <h1>AI Intelligence</h1>
            <p className="muted">Candidate matching, screening and recruitment intelligence.</p>
          </div>
        </div>
        {!showHome && (
          <button type="button" className="btn btn-ghost" onClick={goHome}>
            ← Back to workspace
          </button>
        )}
      </div>

      <AiDisclaimer />

      {!canUseAi && (
        <Alert>You do not have permission to use the internal AI. Ask an organization admin for access.</Alert>
      )}

      {canUseAi && showHome && (
        <>
          <section className="card-grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))" }}>
            {QUICK_ACTIONS.map((action) => (
              <button
                key={action.key}
                type="button"
                className="module-card"
                style={{ textAlign: "left", cursor: "pointer", width: "100%" }}
                onClick={() => handleQuickAction(action.key)}
              >
                <span className="module-icon ai-quick-action-icon">
                  <Icon name={action.icon} size={18} />
                </span>
                <span className="module-title">{action.title}</span>
                <span className="module-phase">{action.description}</span>
              </button>
            ))}
          </section>

          <section className="card ai-chat-panel">
            <h2>Ask AI about recruitment data</h2>
            {conversation.length === 0 && (
              <EmptyState compact icon="sparkles" title="No AI analysis yet">
                Ask a question below, or use a quick action above to find, analyze or compare candidates.
              </EmptyState>
            )}

            {conversation.length > 0 && (
              <div className="ai-conversation">
                {conversation.map((turn, index) => (
                  <div key={index} className={`ai-turn ai-turn-${turn.role}`}>
                    <p style={{ margin: 0 }}>{turn.content}</p>
                    {turn.role === "assistant" && turn.response && (
                      <div className="ai-turn-results">
                        {turn.response.pipeline_stats != null && (
                          <PipelineStatsPanel stats={turn.response.pipeline_stats} />
                        )}
                        {turn.response.matches.map((match) => (
                          <MatchResultCard
                            key={`${match.candidate_id}-${match.job_id}`}
                            match={normalizeMatchCard(match)}
                            showJobTitle
                          />
                        ))}
                      </div>
                    )}
                  </div>
                ))}
                {chatMutation.isPending && (
                  <div className="ai-turn ai-turn-assistant">
                    <Spinner label="Thinking…" />
                  </div>
                )}
              </div>
            )}

            <div className="ai-suggested-prompts">
              {SUGGESTED_PROMPTS.map((prompt) => (
                <button key={prompt} type="button" className="ai-suggested-prompt" onClick={() => setInput(prompt)}>
                  {prompt}
                </button>
              ))}
            </div>

            <form className="ai-chat-form" onSubmit={handleSubmit}>
              <textarea
                id="ai-chat-input"
                placeholder="Ask about a candidate, a job, or your recruitment pipeline…"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleSubmit(e);
                  }
                }}
                disabled={chatMutation.isPending}
              />
              <button type="submit" className="btn btn-primary" disabled={chatMutation.isPending || !input.trim()}>
                {chatMutation.isPending ? <Spinner label="Sending…" /> : "Ask"}
              </button>
            </form>
          </section>
        </>
      )}

      {canUseAi && jobId && (
        <section className="stack-lg" style={{ gap: "1rem" }}>
          <div className="page-header">
            <div>
              <h2 style={{ margin: 0 }}>{activeJob ? `Candidates for “${activeJob.title}”` : "Job-wide match"}</h2>
              <p className="muted">Ranked by deterministic match score against this role's requirements.</p>
            </div>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={refreshJobMatches}
              disabled={jobMatchQuery.isFetching}
            >
              {jobMatchQuery.isFetching ? <Spinner label="Refreshing…" /> : "Refresh matches"}
            </button>
          </div>

          {jobMatchQuery.isPending && (
            <div className="stack-lg" style={{ gap: "1rem" }}>
              <SkeletonCard lines={3} />
              <SkeletonCard lines={3} />
            </div>
          )}

          {jobMatchQuery.isError && <Alert>{friendlyAiError(jobMatchQuery.error)}</Alert>}

          {jobMatchQuery.isSuccess && jobMatchQuery.data.length === 0 && (
            <EmptyState icon="candidates" title="No applicants yet">
              This job has no applications to match against yet.
            </EmptyState>
          )}

          {jobMatchQuery.isSuccess && jobMatchQuery.data.length > 0 && (
            <>
              {comparisonRows.length >= 2 && (
                <section className="card">
                  <h2>Comparison</h2>
                  <div className="ai-comparison-scroll">
                    <table className="ai-comparison-table">
                      <thead>
                        <tr>
                          <th>Candidate</th>
                          <th>Match</th>
                          <th>Alignment</th>
                          <th>Matching skills</th>
                          <th>Missing skills</th>
                        </tr>
                      </thead>
                      <tbody>
                        {comparisonRows.map((match) => (
                          <tr key={match.candidate_id}>
                            <td>{candidateNameById.get(match.candidate_id) ?? "Candidate"}</td>
                            <td>{match.overall_match_score ?? "—"}%</td>
                            <td>{match.role_alignment ?? "—"}</td>
                            <td>{match.matching_skills.join(", ") || "—"}</td>
                            <td>{match.missing_skills.join(", ") || "None"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              )}

              <div className="stack-lg" style={{ gap: "1rem" }}>
                {jobMatchQuery.data.map((match) => (
                  <MatchResultCard
                    key={match.candidate_id}
                    match={normalizeMatchResponse(
                      candidateNameById.get(match.candidate_id) ?? "Candidate",
                      activeJob?.title ?? "",
                      match,
                    )}
                    compareSelected={selectedCompareIds.has(match.candidate_id)}
                    onToggleCompare={() => toggleCompare(match.candidate_id)}
                  />
                ))}
              </div>
            </>
          )}
        </section>
      )}

      {canUseAi && candidateId && (
        <section className="stack-lg" style={{ gap: "1rem" }}>
          {candidateQuery.isPending && <SkeletonCard lines={3} />}
          {candidateQuery.isError && <Alert>{friendlyAiError(candidateQuery.error)}</Alert>}

          {candidateQuery.isSuccess && (
            <>
              <div className="page-header">
                <div>
                  <h2 style={{ margin: 0 }}>Analysis for {candidateQuery.data.full_name}</h2>
                  <p className="muted">Choose a role to see how this candidate matches it.</p>
                </div>
                <label className="field" style={{ margin: 0, minWidth: "14rem" }}>
                  <span>Match against role</span>
                  <select
                    value={effectiveAnalysisJobId ?? ""}
                    onChange={(e) => setAnalysisJobId(e.target.value || null)}
                    disabled={jobsQuery.isPending}
                  >
                    <option value="" disabled>
                      Select a job…
                    </option>
                    {jobsQuery.data?.map((job) => (
                      <option key={job.id} value={job.id}>
                        {job.title}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              {!effectiveAnalysisJobId && (
                <EmptyState icon="jobs" title="Pick a role to analyze">
                  Choose an open job above to see this candidate's match score, evidence and AI explanation.
                </EmptyState>
              )}

              {effectiveAnalysisJobId && candidateMatchQuery.isPending && <SkeletonCard lines={4} />}
              {effectiveAnalysisJobId && candidateMatchQuery.isError && (
                <Alert>{friendlyAiError(candidateMatchQuery.error)}</Alert>
              )}
              {effectiveAnalysisJobId && candidateMatchQuery.isSuccess && (
                <MatchResultCard
                  match={normalizeMatchResponse(
                    candidateQuery.data.full_name,
                    jobsQuery.data?.find((job) => job.id === effectiveAnalysisJobId)?.title ?? "",
                    candidateMatchQuery.data,
                  )}
                  showJobTitle
                />
              )}
            </>
          )}
        </section>
      )}

      {picker === "job" && (
        <PickerModal
          title="Find candidates for a job"
          items={jobsQuery.data}
          isLoading={jobsQuery.isPending}
          isError={jobsQuery.isError}
          getKey={(job) => job.id}
          renderLabel={(job) => (
            <span>
              {job.title}
              <span className="muted" style={{ display: "block", fontSize: "0.78rem" }}>
                {job.department ?? "—"} · {job.location ?? "—"}
              </span>
            </span>
          )}
          matches={(job, term) => job.title.toLowerCase().includes(term)}
          searchPlaceholder="Search jobs…"
          emptyLabel="No jobs found"
          onClose={() => setPicker(null)}
          onSelect={(job) => {
            setPicker(null);
            setSearchParams({ jobId: job.id });
          }}
        />
      )}

      {picker === "job-compare" && (
        <PickerModal
          title="Compare candidates for a job"
          items={jobsQuery.data}
          isLoading={jobsQuery.isPending}
          isError={jobsQuery.isError}
          getKey={(job) => job.id}
          renderLabel={(job) => job.title}
          matches={(job, term) => job.title.toLowerCase().includes(term)}
          searchPlaceholder="Search jobs…"
          emptyLabel="No jobs found"
          onClose={() => setPicker(null)}
          onSelect={(job) => {
            setPicker(null);
            setSearchParams({ jobId: job.id });
          }}
        />
      )}

      {picker === "candidate" && (
        <PickerModal
          title="Analyze a candidate"
          items={candidatesPickerQuery.data}
          isLoading={candidatesPickerQuery.isPending}
          isError={candidatesPickerQuery.isError}
          getKey={(candidate) => candidate.id}
          renderLabel={(candidate) => (
            <span>
              {candidate.full_name}
              <span className="muted" style={{ display: "block", fontSize: "0.78rem" }}>
                {candidate.email}
              </span>
            </span>
          )}
          matches={(candidate, term) =>
            candidate.full_name.toLowerCase().includes(term) || candidate.email.toLowerCase().includes(term)
          }
          searchPlaceholder="Search candidates…"
          emptyLabel="No candidates found"
          onClose={() => setPicker(null)}
          onSelect={(candidate) => {
            setPicker(null);
            setSearchParams({ candidateId: candidate.id });
          }}
        />
      )}
    </div>
  );
}
