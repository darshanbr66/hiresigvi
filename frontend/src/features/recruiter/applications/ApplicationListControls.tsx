import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Modal } from "../../../shared/components/Modal";
import { useDebouncedValue } from "../../../shared/hooks/useDebouncedValue";
import { useMediaQuery } from "../../../shared/hooks/useMediaQuery";
import { humanizeStatus } from "../../../shared/lib/statusTone";
import { APPLICATION_STATUSES } from "../../../types/recruitment";
import {
  APPLICATION_SOURCES,
  CANDIDATE_TYPES,
  EMPTY_FILTERS,
  NOTICE_PERIODS,
  SORT_OPTIONS,
  activeFilterChips,
  hasActiveCriteria,
  validateFilters,
  type ApplicationFilters,
  type ApplicationListState,
  type SortKey,
} from "./applicationFilters";

/** How long typing must pause before the search is sent to the server. */
export const SEARCH_DEBOUNCE_MS = 300;

/** The filter fields a list can offer. A list shows only those that make sense
 * for it (a Campus Drive has one job, so no job filter there). */
export type FilterField =
  | "job"
  | "status"
  | "candidateType"
  | "experience"
  | "currentTitle"
  | "currentCompany"
  | "location"
  | "preferredLocation"
  | "qualification"
  | "notice"
  | "immediateJoiner"
  | "applied"
  | "source"
  | "campusDrive"
  | "institution";

interface JobOption {
  id: string;
  title: string;
}

interface NamedOption {
  id: string;
  name: string;
}

/**
 * Search box + expandable Filters + active-filter chips (+ optional sort) for an
 * application list. It owns no data: every change is handed to `onChange` as a
 * new list state (page reset to 1), and the caller asks the server.
 *
 * - Search is debounced, so the server sees one request when typing pauses
 *   (Enter sends it immediately).
 * - Filter fields are edited in a draft and applied together with "Apply
 *   Filters", so a text field doesn't fire a request per keystroke.
 * - On a phone the filters open in a modal instead of inline.
 */
export function ApplicationListControls({
  state,
  onChange,
  fields,
  jobs = [],
  campusDrives = [],
  institutions = [],
  showSort = false,
  searchLabel,
  searchPlaceholder,
}: {
  state: ApplicationListState;
  onChange: (next: ApplicationListState) => void;
  fields: FilterField[];
  jobs?: JobOption[];
  campusDrives?: NamedOption[];
  institutions?: NamedOption[];
  showSort?: boolean;
  searchLabel: string;
  searchPlaceholder: string;
}) {
  const panelId = useId();
  const isPhone = useMediaQuery("(max-width: 720px)");
  const [panelOpen, setPanelOpen] = useState(false);
  const [draft, setDraft] = useState<ApplicationFilters>(state.filters);
  const [searchText, setSearchText] = useState(state.filters.q);
  const debouncedSearch = useDebouncedValue(searchText, SEARCH_DEBOUNCE_MS);

  // The handlers below must see the latest state without re-running on every
  // render; this effect is declared first so it runs first.
  const latest = useRef({ state, onChange });
  useEffect(() => {
    latest.current = { state, onChange };
  });
  // The last search text this component pushed up (or received), used to tell
  // "the user typed" apart from "the URL changed under us".
  const sentSearch = useRef(state.filters.q);

  function pushSearch(text: string) {
    if (text === sentSearch.current) return;
    sentSearch.current = text;
    const { state: current, onChange: emit } = latest.current;
    emit({ ...current, filters: { ...current.filters, q: text }, page: 1 });
  }

  // Typing settled -> tell the parent.
  useEffect(() => {
    pushSearch(debouncedSearch.trim());
  }, [debouncedSearch]);

  // Search text changed elsewhere (Clear all, browser back) -> show it in the box.
  useEffect(() => {
    if (state.filters.q !== sentSearch.current) {
      sentSearch.current = state.filters.q;
      setSearchText(state.filters.q);
    }
  }, [state.filters.q]);

  // Keep the draft in step when the applied filters change (a chip removed, back button).
  const appliedKey = JSON.stringify(state.filters);
  useEffect(() => {
    setDraft(state.filters);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on the serialized value
  }, [appliedKey]);

  const chips = activeFilterChips(state.filters, jobs, { campusDrives, institutions });
  const draftError = validateFilters(draft);

  function togglePanel() {
    if (!panelOpen) setDraft(state.filters);
    setPanelOpen(!panelOpen);
  }

  function applyFilters(event: FormEvent) {
    event.preventDefault();
    if (draftError) return;
    // The search box is applied separately; carry its current text along.
    onChange({ ...state, filters: { ...draft, q: state.filters.q }, page: 1 });
    setPanelOpen(false);
  }

  function clearAll() {
    sentSearch.current = "";
    setSearchText("");
    setDraft(EMPTY_FILTERS);
    onChange({ ...state, filters: EMPTY_FILTERS, page: 1 });
    setPanelOpen(false);
  }

  function removeChip(keys: (keyof ApplicationFilters)[]) {
    const next = { ...state.filters };
    for (const key of keys) next[key] = "" as never;
    onChange({ ...state, filters: next, page: 1 });
  }

  const filterForm = (
    <form onSubmit={applyFilters} noValidate>
      <div className="filter-grid">
        {fields.includes("job") && (
          <label className="field">
            <span>Job</span>
            <select value={draft.job} onChange={(e) => setDraft({ ...draft, job: e.target.value })}>
              <option value="">All jobs</option>
              {jobs.map((job) => (
                <option key={job.id} value={job.id}>
                  {job.title}
                </option>
              ))}
            </select>
          </label>
        )}
        {fields.includes("status") && (
          <label className="field">
            <span>Status</span>
            <select
              value={draft.status}
              onChange={(e) => setDraft({ ...draft, status: e.target.value as ApplicationFilters["status"] })}
            >
              <option value="">All statuses</option>
              {APPLICATION_STATUSES.map((status) => (
                <option key={status} value={status}>
                  {humanizeStatus(status)}
                </option>
              ))}
            </select>
          </label>
        )}
        {fields.includes("candidateType") && (
          <label className="field">
            <span>Candidate type</span>
            <select
              value={draft.candidateType}
              onChange={(e) =>
                setDraft({ ...draft, candidateType: e.target.value as ApplicationFilters["candidateType"] })
              }
            >
              <option value="">All</option>
              {CANDIDATE_TYPES.map((type) => (
                <option key={type.value} value={type.value}>
                  {type.label}
                </option>
              ))}
            </select>
          </label>
        )}
        {fields.includes("experience") && (
          <>
            <label className="field">
              <span>Min experience (years)</span>
              <input
                type="number"
                inputMode="numeric"
                min={0}
                max={80}
                placeholder="Min"
                value={draft.minExperience}
                onChange={(e) => setDraft({ ...draft, minExperience: e.target.value })}
              />
            </label>
            <label className="field">
              <span>Max experience (years)</span>
              <input
                type="number"
                inputMode="numeric"
                min={0}
                max={80}
                placeholder="Max"
                value={draft.maxExperience}
                onChange={(e) => setDraft({ ...draft, maxExperience: e.target.value })}
              />
            </label>
          </>
        )}
        {fields.includes("currentTitle") && (
          <TextFilter label="Current title" value={draft.currentTitle} onChange={(v) => setDraft({ ...draft, currentTitle: v })} />
        )}
        {fields.includes("currentCompany") && (
          <TextFilter label="Current company" value={draft.currentCompany} onChange={(v) => setDraft({ ...draft, currentCompany: v })} />
        )}
        {fields.includes("location") && (
          <TextFilter label="Current location" value={draft.location} onChange={(v) => setDraft({ ...draft, location: v })} />
        )}
        {fields.includes("preferredLocation") && (
          <TextFilter label="Preferred location" value={draft.preferredLocation} onChange={(v) => setDraft({ ...draft, preferredLocation: v })} />
        )}
        {fields.includes("qualification") && (
          <TextFilter label="Qualification" value={draft.qualification} onChange={(v) => setDraft({ ...draft, qualification: v })} />
        )}
        {fields.includes("notice") && (
          <label className="field">
            <span>Notice period</span>
            <select value={draft.maxNotice} onChange={(e) => setDraft({ ...draft, maxNotice: e.target.value })}>
              <option value="">Any</option>
              {NOTICE_PERIODS.map((period) => (
                <option key={period.value} value={period.value}>
                  {period.label}
                </option>
              ))}
            </select>
          </label>
        )}
        {fields.includes("immediateJoiner") && (
          <label className="field">
            <span>Immediate joiner</span>
            <select
              value={draft.immediateJoiner}
              onChange={(e) =>
                setDraft({ ...draft, immediateJoiner: e.target.value as ApplicationFilters["immediateJoiner"] })
              }
            >
              <option value="">Any</option>
              <option value="true">Yes</option>
              <option value="false">No</option>
            </select>
          </label>
        )}
        {fields.includes("applied") && (
          <>
            <label className="field">
              <span>Applied from</span>
              <input
                type="date"
                value={draft.appliedFrom}
                max={draft.appliedTo || undefined}
                onChange={(e) => setDraft({ ...draft, appliedFrom: e.target.value })}
              />
            </label>
            <label className="field">
              <span>Applied to</span>
              <input
                type="date"
                value={draft.appliedTo}
                min={draft.appliedFrom || undefined}
                onChange={(e) => setDraft({ ...draft, appliedTo: e.target.value })}
              />
            </label>
          </>
        )}
        {fields.includes("source") && (
          <label className="field">
            <span>Source</span>
            <select
              value={draft.source}
              onChange={(e) => setDraft({ ...draft, source: e.target.value as ApplicationFilters["source"] })}
            >
              <option value="">All sources</option>
              {APPLICATION_SOURCES.map((source) => (
                <option key={source.value} value={source.value}>
                  {source.label}
                </option>
              ))}
            </select>
          </label>
        )}
        {fields.includes("campusDrive") && (
          <label className="field">
            <span>Campus drive</span>
            <select value={draft.campusDrive} onChange={(e) => setDraft({ ...draft, campusDrive: e.target.value })}>
              <option value="">All drives</option>
              {campusDrives.map((drive) => (
                <option key={drive.id} value={drive.id}>
                  {drive.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {fields.includes("institution") && (
          <label className="field">
            <span>Institution</span>
            <select value={draft.institution} onChange={(e) => setDraft({ ...draft, institution: e.target.value })}>
              <option value="">All institutions</option>
              {institutions.map((institution) => (
                <option key={institution.id} value={institution.id}>
                  {institution.name}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      {draftError && (
        <p className="field-error" role="alert">
          {draftError}
        </p>
      )}

      <div className="btn-group filter-actions">
        <button type="submit" className="btn btn-primary btn-sm" disabled={draftError !== null}>
          Apply Filters
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={clearAll}>
          Clear All
        </button>
      </div>
    </form>
  );

  return (
    <div className="list-controls">
      <div className="toolbar">
        <form
          role="search"
          className="list-search"
          onSubmit={(event) => {
            event.preventDefault();
            pushSearch(searchText.trim());
          }}
        >
          <input
            type="search"
            className="search-input"
            aria-label={searchLabel}
            placeholder={searchPlaceholder}
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
          />
        </form>
        <button
          type="button"
          className={panelOpen ? "btn btn-ghost filters-toggle is-open" : "btn btn-ghost filters-toggle"}
          aria-expanded={panelOpen}
          aria-controls={panelId}
          onClick={togglePanel}
        >
          Filters
          {chips.length > 0 && <span className="filter-count">{chips.length}</span>}
          <span aria-hidden="true" className="filters-caret">
            ▾
          </span>
        </button>
        {showSort && (
          <select
            className="filter-select"
            aria-label="Sort by"
            value={state.sort}
            onChange={(e) => onChange({ ...state, sort: e.target.value as SortKey, page: 1 })}
          >
            {SORT_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        )}
      </div>

      {panelOpen && !isPhone && (
        <section id={panelId} className="filter-panel" aria-label="Filters">
          {filterForm}
        </section>
      )}
      {panelOpen && isPhone && (
        <Modal title="Filters" onClose={() => setPanelOpen(false)}>
          <div id={panelId}>{filterForm}</div>
        </Modal>
      )}

      {(chips.length > 0 || hasActiveCriteria(state.filters)) && (
        <div className="filter-chips" aria-label="Active filters">
          {chips.map((chip) => (
            <span key={chip.label} className="filter-chip">
              {chip.label}
              <button
                type="button"
                aria-label={`Remove filter ${chip.label}`}
                onClick={() => removeChip(chip.keys)}
              >
                ×
              </button>
            </span>
          ))}
          <button type="button" className="btn btn-ghost btn-sm" onClick={clearAll}>
            Clear all
          </button>
        </div>
      )}
    </div>
  );
}

function TextFilter({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input type="text" maxLength={255} value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}
