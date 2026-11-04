import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { ApiError } from "../../../lib/apiClient";
import { Alert } from "../../../shared/components/Alert";
import { BackLink } from "../../../shared/components/BackLink";
import { SkeletonLines } from "../../../shared/components/Skeleton";
import { Spinner } from "../../../shared/components/Spinner";
import { useToast } from "../../../shared/components/ToastContext";
import {
  EDUCATION_LEVEL_LABELS,
  FUNDING_TYPE_LABELS,
  type EducationLevel,
  type FundingType,
  type ProgramSettingsResponse,
  type ProgramSettingsValues,
} from "../../../types/program";
import { useAuth } from "../../auth/AuthContext";
import { getProgramSettings, updateProgramSettings } from "./api";
import { formatMoney, splitFee } from "./finance";
import { StudyYearRulesInput } from "./policyInputs";
import {
  durationProblem,
  parseNumberList,
  rulesToText,
  textToRules,
  type StudyYearRuleText,
} from "./policyRules";

interface SettingsForm {
  durations: string;
  fee: string;
  currency: string;
  fundingType: FundingType | "";
  fundingValue: string;
  independentApproval: boolean;
  internshipMonths: string;
  bondRequired: boolean;
  bondMonths: string;
  levels: EducationLevel[];
  yearRules: StudyYearRuleText;
}

function toForm(s: ProgramSettingsResponse): SettingsForm {
  return {
    durations: s.training_duration_options.join(", "),
    fee: s.training_fee_amount ?? "",
    currency: s.currency ?? "",
    fundingType: s.company_funding_type ?? "",
    fundingValue: s.company_funding_value ?? "",
    independentApproval: s.funding_requires_independent_approval,
    internshipMonths: s.internship_duration_months ? String(s.internship_duration_months) : "",
    bondRequired: s.bond_required,
    bondMonths: s.bond_duration_months ? String(s.bond_duration_months) : "",
    levels: s.eligible_education_levels,
    yearRules: rulesToText(s.study_year_rules),
  };
}

function toPayload(f: SettingsForm): ProgramSettingsValues {
  return {
    training_duration_options: parseNumberList(f.durations),
    training_fee_amount: f.fee.trim() || null,
    currency: f.currency.trim().toUpperCase() || null,
    company_funding_type: f.fundingType || null,
    company_funding_value: f.fundingType ? f.fundingValue.trim() || null : null,
    funding_requires_independent_approval: f.independentApproval,
    internship_duration_months: f.internshipMonths.trim() ? Number(f.internshipMonths) : null,
    bond_required: f.bondRequired,
    bond_duration_months: f.bondRequired && f.bondMonths.trim() ? Number(f.bondMonths) : null,
    eligible_education_levels: f.levels,
    study_year_rules: textToRules(f.yearRules, f.levels),
  };
}

/**
 * Organization-wide starting values for new recruitment programs
 * (docs/programs.md § 1a). Every program created afterwards copies them as
 * its draft configuration; changing them never alters an existing program,
 * a candidate's accepted terms, a funding decision or a payment record.
 * Anyone with program access can read them; only `program.settings.manage`
 * (ORG_ADMIN by default) can save — the server enforces both.
 */
export function ProgramSettingsPage() {
  const { accessToken } = useAuth();
  const token = accessToken as string;
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [form, setForm] = useState<SettingsForm | null>(null);
  const [error, setError] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["recruiter", "program-settings"],
    queryFn: () => getProgramSettings(token),
    enabled: accessToken !== null,
  });
  const save = useMutation({
    mutationFn: (payload: ProgramSettingsValues) => updateProgramSettings(payload, token),
    onSuccess: (saved) => {
      queryClient.setQueryData(["recruiter", "program-settings"], saved);
      setForm(toForm(saved));
      setError(null);
      showToast("Default program settings saved. They apply to programs created from now on.", "success");
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "Unable to reach the server."),
  });

  if (query.isPending) return <SkeletonLines count={8} />;
  if (query.isError) {
    const forbidden = query.error instanceof ApiError && query.error.status === 403;
    return <Alert>{forbidden ? "You do not have permission to view program settings." : "Could not load program settings."}</Alert>;
  }
  const settings = query.data;
  const f = form ?? toForm(settings);
  const disabled = !settings.can_edit || save.isPending;
  const patch = (fields: Partial<SettingsForm>) => setForm({ ...f, ...fields });

  const durationError = durationProblem(parseNumberList(f.durations));
  const split =
    f.fee && f.fundingType && f.fundingValue ? splitFee(f.fee, f.fundingType, f.fundingValue) : null;
  const clientProblem =
    durationError ??
    (typeof split === "string" ? split : null) ??
    (f.fee && !f.currency.trim() ? "Enter the currency of the default fee." : null) ??
    (f.bondRequired && !f.bondMonths.trim() ? "Enter the default bond duration, or turn the bond off." : null);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (clientProblem) {
      setError(clientProblem);
      return;
    }
    save.mutate(toPayload(f));
  }

  function toggleLevel(level: EducationLevel) {
    patch({ levels: f.levels.includes(level) ? f.levels.filter((l) => l !== level) : [...f.levels, level] });
  }

  const check = (key: "independentApproval" | "bondRequired", label: string) => (
    <label style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
      <input
        type="checkbox"
        checked={f[key]}
        onChange={(e) => patch({ [key]: e.target.checked } as Partial<SettingsForm>)}
        disabled={disabled}
        style={{ width: "auto" }}
      />
      {label}
    </label>
  );

  return (
    <div className="stack-lg">
      <p>
        <BackLink to="/recruiter/programs">Back to programs</BackLink>
      </p>
      <div className="page-header">
        <div>
          <h1>Program settings</h1>
          <p className="muted">
            Starting values for new programs. Each new program copies them into its draft, where HR can still
            change anything. Editing these never changes existing programs, accepted candidate terms, funding
            decisions or payments.
          </p>
          <p className="field-hint">
            Last changed {new Date(settings.updated_at).toLocaleString()}
            {settings.updated_by_name ? ` by ${settings.updated_by_name}` : ""}.
          </p>
        </div>
      </div>
      {!settings.can_edit && (
        <Alert>Only organization administrators can change these settings. You can view them.</Alert>
      )}

      <form onSubmit={submit} className="stack-lg">
        <fieldset className="card stack-sm">
          <legend style={{ fontWeight: 600 }}>Training</legend>
          <label className="field">
            <span>Training duration options (months)</span>
            <input value={f.durations} onChange={(e) => patch({ durations: e.target.value })} disabled={disabled} placeholder="e.g. 6, 12" />
            <span className="field-hint">Comma-separated, 1–60 months each.</span>
          </label>
          <div className="field-row">
            <label className="field">
              <span>Default training fee</span>
              <input type="number" min={0} value={f.fee} onChange={(e) => patch({ fee: e.target.value })} disabled={disabled} />
            </label>
            <label className="field">
              <span>Currency</span>
              <input value={f.currency} onChange={(e) => patch({ currency: e.target.value })} disabled={disabled} placeholder="INR" />
            </label>
          </div>
        </fieldset>

        <fieldset className="card stack-sm">
          <legend style={{ fontWeight: 600 }}>Scholarship / company funding</legend>
          <div className="field-row">
            <label className="field">
              <span>Standard funding basis</span>
              <select value={f.fundingType} onChange={(e) => patch({ fundingType: e.target.value as FundingType | "" })} disabled={disabled}>
                <option value="">No default</option>
                {(Object.keys(FUNDING_TYPE_LABELS) as FundingType[]).map((t) => (
                  <option key={t} value={t}>{FUNDING_TYPE_LABELS[t]}</option>
                ))}
              </select>
            </label>
            {f.fundingType && (
              <label className="field">
                <span>{f.fundingType === "PERCENTAGE" ? "Percentage of the fee (0–100)" : "Amount"}</span>
                <input type="number" min={0} value={f.fundingValue} onChange={(e) => patch({ fundingValue: e.target.value })} disabled={disabled} />
              </label>
            )}
          </div>
          {split && typeof split !== "string" && (
            <p className="field-hint" data-testid="settings-funding-preview">
              With these defaults: company {formatMoney(split.companyContribution, f.currency || null)}, candidate pays{" "}
              {formatMoney(split.candidatePayable, f.currency || null)} of {formatMoney(split.fee, f.currency || null)}. 0
              means no company funding.
            </p>
          )}
          {check("independentApproval", "Funding must be approved by someone other than the person who proposed it")}
        </fieldset>

        <fieldset className="card stack-sm">
          <legend style={{ fontWeight: 600 }}>Internship and commitment</legend>
          <label className="field">
            <span>Default internship duration (months) <span className="optional-mark">(Optional)</span></span>
            <input type="number" min={1} max={60} value={f.internshipMonths} onChange={(e) => patch({ internshipMonths: e.target.value })} disabled={disabled} />
          </label>
          {check("bondRequired", "Funded participants agree to a commitment (bond)")}
          {f.bondRequired && (
            <label className="field">
              <span>Default bond duration (months)</span>
              <input type="number" min={1} max={120} value={f.bondMonths} onChange={(e) => patch({ bondMonths: e.target.value })} disabled={disabled} />
            </label>
          )}
          <p className="field-hint">
            A bond applies only to a participant whose company funding is approved, under the company's approved
            agreement. The platform never writes agreement wording.
          </p>
        </fieldset>

        <fieldset className="card stack-sm">
          <legend style={{ fontWeight: 600 }}>Eligibility</legend>
          <div className="btn-group">
            {(Object.keys(EDUCATION_LEVEL_LABELS) as EducationLevel[]).map((level) => (
              <label key={level} style={{ display: "flex", alignItems: "center", gap: "0.35rem" }}>
                <input type="checkbox" checked={f.levels.includes(level)} onChange={() => toggleLevel(level)} disabled={disabled} style={{ width: "auto" }} />
                {EDUCATION_LEVEL_LABELS[level]}
              </label>
            ))}
          </div>
          <StudyYearRulesInput
            levels={f.levels}
            value={f.yearRules}
            onChange={(yearRules) => patch({ yearRules })}
            disabled={disabled}
            fallbackHint="A level left empty accepts any year."
          />
        </fieldset>

        {error && <Alert>{error}</Alert>}
        {settings.can_edit && (
          <div className="btn-group">
            <button type="submit" className="btn btn-primary" disabled={save.isPending}>
              {save.isPending ? <Spinner label="Saving…" /> : "Save settings"}
            </button>
            {form && (
              <button type="button" className="btn btn-ghost" onClick={() => { setForm(null); setError(null); }} disabled={save.isPending}>
                Discard changes
              </button>
            )}
          </div>
        )}
      </form>
    </div>
  );
}
