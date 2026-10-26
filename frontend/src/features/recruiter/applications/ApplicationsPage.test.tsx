import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import { ApiError } from "../../../lib/apiClient";
import { ToastProvider } from "../../../shared/components/ToastContext";
import * as campusDrivesApi from "../campusDrives/api";
import * as candidatesApi from "../candidates/api";
import * as institutionsApi from "../institutions/api";
import * as jobsApi from "../jobs/api";
import * as api from "./api";
import { ApplicationsPage } from "./ApplicationsPage";
import { LocationProbe } from "./LocationProbe";
import {
  lastQuery,
  makeApplication,
  makeJob,
  pageOf,
  sentSearches,
  stubViewport,
} from "./testUtils";

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ accessToken: "test-token", user: { roles: ["ORG_ADMIN"] } }),
}));

function renderPage(url = "/recruiter/applications") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MemoryRouter initialEntries={[url]}>
          <Routes>
            <Route
              path="/recruiter/applications"
              element={
                <>
                  <ApplicationsPage />
                  <LocationProbe />
                </>
              }
            />
            <Route path="/recruiter/applications/:id" element={<p>Application detail page</p>} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

const location = () => screen.getByTestId("location").textContent ?? "";
const filtersButton = () => screen.getByRole("button", { name: /^Filters/ });

/** Serves `total` applications 25 at a time, honoring the offset the page asks for. */
function serveInPages(total: number) {
  return vi.spyOn(api, "listApplicationsPage").mockImplementation(async (query) => {
    const offset = Number(new URLSearchParams(query).get("offset") ?? 0);
    const count = Math.max(0, Math.min(25, total - offset));
    const items = Array.from({ length: count }, (_, i) => makeApplication(offset + i + 1));
    return pageOf(items, total);
  });
}

describe("ApplicationsPage — server-side search and filters", () => {
  let list: MockInstance<typeof api.listApplicationsPage>;

  beforeEach(() => {
    vi.restoreAllMocks();
    list = vi
      .spyOn(api, "listApplicationsPage")
      .mockResolvedValue(pageOf([makeApplication(1), makeApplication(2)]));
    vi.spyOn(jobsApi, "listJobs").mockResolvedValue([
      makeJob("job-1", "Data Analyst"),
      makeJob("job-2", "Backend Engineer"),
    ]);
    vi.spyOn(candidatesApi, "listCandidates").mockResolvedValue([]);
    vi.spyOn(campusDrivesApi, "listCampusDrives").mockResolvedValue([]);
    vi.spyOn(institutionsApi, "listInstitutions").mockResolvedValue([]);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // --- loading -----------------------------------------------------------------

  it("asks the server for the first page, newest first, and shows each row's contact details", async () => {
    renderPage();

    expect(await screen.findByText("Candidate 1")).toBeInTheDocument();
    const query = lastQuery(list);
    expect(query.get("limit")).toBe("25");
    expect(query.get("offset")).toBe("0");
    expect(query.get("sort_by")).toBe("applied_at");
    expect(query.get("sort_dir")).toBe("desc");
    expect(query.get("q")).toBeNull();
    // Email and phone are shown so a search hit on either is explained.
    expect(screen.getByText("candidate1@example.com · +91 90000 00001")).toBeInTheDocument();
    expect(screen.getByText("Showing 1–2 of 2 applications")).toBeInTheDocument();
  });

  it("never downloads everything to filter it in the browser", async () => {
    renderPage();
    await screen.findByText("Candidate 1");

    // Typing a term the loaded rows don't contain still just asks the server.
    fireEvent.change(screen.getByRole("searchbox", { name: "Search applications" }), {
      target: { value: "zzz" },
    });
    await waitFor(() => expect(lastQuery(list).get("q")).toBe("zzz"));
  });

  // --- search --------------------------------------------------------------------

  it("debounces the search: one request when typing pauses, none per keystroke", async () => {
    renderPage();
    await screen.findByText("Candidate 1");
    const box = screen.getByRole("searchbox", { name: "Search applications" });

    fireEvent.change(box, { target: { value: "j" } });
    // A real pause shorter than the debounce: still nothing sent for "j".
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(sentSearches(list)).toEqual([null]);
    fireEvent.change(box, { target: { value: "ja" } });
    fireEvent.change(box, { target: { value: "jane" } });

    expect(sentSearches(list)).toEqual([null]); // nothing sent yet
    await waitFor(() => expect(lastQuery(list).get("q")).toBe("jane"));
    expect(sentSearches(list)).toEqual([null, "jane"]); // "j" and "ja" were never sent
    expect(location()).toBe("/recruiter/applications?q=jane");
  });

  it("searches immediately when Enter is pressed", async () => {
    renderPage();
    await screen.findByText("Candidate 1");
    const box = screen.getByRole("searchbox", { name: "Search applications" });

    fireEvent.change(box, { target: { value: "9876543210" } });
    fireEvent.submit(box.closest("form") as HTMLFormElement);

    await waitFor(() => expect(lastQuery(list).get("q")).toBe("9876543210"));
  });

  it("restarts from page 1 when the search changes", async () => {
    serveInPages(60);
    renderPage("/recruiter/applications?page=2");
    await screen.findByText("Showing 26–50 of 60 applications");

    fireEvent.change(screen.getByRole("searchbox", { name: "Search applications" }), {
      target: { value: "asha" },
    });

    await waitFor(() => expect(lastQuery(list).get("q")).toBe("asha"));
    expect(lastQuery(list).get("offset")).toBe("0");
    expect(location()).not.toContain("page=");
  });

  // --- opening and applying filters -------------------------------------------------

  it("opens and closes the Filters panel", async () => {
    renderPage();
    await screen.findByText("Candidate 1");

    expect(filtersButton()).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByLabelText("Candidate type")).not.toBeInTheDocument();

    fireEvent.click(filtersButton());
    expect(filtersButton()).toHaveAttribute("aria-expanded", "true");
    for (const label of [
      "Job",
      "Status",
      "Candidate type",
      "Min experience (years)",
      "Max experience (years)",
      "Current title",
      "Current company",
      "Current location",
      "Preferred location",
      "Qualification",
      "Notice period",
      "Immediate joiner",
      "Applied from",
      "Applied to",
      "Source",
    ]) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }

    fireEvent.click(filtersButton());
    expect(screen.queryByLabelText("Candidate type")).not.toBeInTheDocument();
  });

  it("applies several filters together and sends them all to the server", async () => {
    renderPage();
    await screen.findByText("Candidate 1");
    fireEvent.click(filtersButton());

    // Job = Data Analyst AND Status = Shortlisted AND Experience >= 2 AND Applied 01-09..21-09 AND source
    fireEvent.change(screen.getByLabelText("Job"), { target: { value: "job-1" } });
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "SHORTLISTED" } });
    fireEvent.change(screen.getByLabelText("Min experience (years)"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("Applied from"), { target: { value: "2026-09-01" } });
    fireEvent.change(screen.getByLabelText("Applied to"), { target: { value: "2026-09-21" } });
    fireEvent.change(screen.getByLabelText("Source"), { target: { value: "REFERRAL" } });
    fireEvent.change(screen.getByLabelText("Current company"), { target: { value: "Acme" } });
    fireEvent.change(screen.getByLabelText("Candidate type"), { target: { value: "EXPERIENCED" } });
    fireEvent.change(screen.getByLabelText("Notice period"), { target: { value: "30" } });
    fireEvent.change(screen.getByLabelText("Immediate joiner"), { target: { value: "false" } });

    // Nothing is sent until Apply.
    const callsBefore = list.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Apply Filters" }));

    await waitFor(() => expect(list.mock.calls.length).toBeGreaterThan(callsBefore));
    const query = lastQuery(list);
    expect(Object.fromEntries(query)).toMatchObject({
      job_id: "job-1",
      status: "SHORTLISTED",
      min_experience: "2",
      applied_from: "2026-09-01",
      applied_to: "2026-09-21",
      source: "REFERRAL",
      current_company: "Acme",
      candidate_type: "EXPERIENCED",
      max_notice_period_days: "30",
      immediate_joiner: "false",
      offset: "0",
    });
    // The panel closes and the filters are in the URL.
    expect(screen.queryByLabelText("Candidate type")).not.toBeInTheDocument();
    expect(location()).toContain("status=SHORTLISTED");
    expect(location()).toContain("exp_min=2");
  });

  it("rejects an inverted experience or date range before sending anything", async () => {
    renderPage();
    await screen.findByText("Candidate 1");
    fireEvent.click(filtersButton());
    const callsBefore = list.mock.calls.length;

    fireEvent.change(screen.getByLabelText("Min experience (years)"), { target: { value: "8" } });
    fireEvent.change(screen.getByLabelText("Max experience (years)"), { target: { value: "3" } });

    expect(screen.getByRole("alert")).toHaveTextContent(/Minimum experience/);
    expect(screen.getByRole("button", { name: "Apply Filters" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Apply Filters" }));
    expect(list.mock.calls.length).toBe(callsBefore);

    fireEvent.change(screen.getByLabelText("Max experience (years)"), { target: { value: "10" } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Apply Filters" })).toBeEnabled();
  });

  // --- active filters ---------------------------------------------------------------

  it("shows what is filtering the list, with a count on the Filters button", async () => {
    renderPage(
      "/recruiter/applications?job=job-1&status=SHORTLISTED&exp_min=2&from=2026-09-01&to=2026-09-21&source=REFERRAL",
    );
    await screen.findByText("Candidate 1");

    const chips = screen.getByLabelText("Active filters");
    expect(within(chips).getByText("Job: Data Analyst")).toBeInTheDocument();
    expect(within(chips).getByText("Status: Shortlisted")).toBeInTheDocument();
    expect(within(chips).getByText("Experience: 2+ yrs")).toBeInTheDocument();
    expect(within(chips).getByText("From: 01-09-2026")).toBeInTheDocument();
    expect(within(chips).getByText("To: 21-09-2026")).toBeInTheDocument();
    expect(within(chips).getByText("Source: Referral")).toBeInTheDocument();
    expect(filtersButton()).toHaveTextContent("6");
  });

  it("removes a single filter from its chip and keeps the rest", async () => {
    renderPage("/recruiter/applications?status=SHORTLISTED&exp_min=2");
    await screen.findByText("Candidate 1");

    fireEvent.click(screen.getByRole("button", { name: "Remove filter Status: Shortlisted" }));

    await waitFor(() => expect(lastQuery(list).get("status")).toBeNull());
    expect(lastQuery(list).get("min_experience")).toBe("2");
    expect(screen.queryByText("Status: Shortlisted")).not.toBeInTheDocument();
    expect(location()).toBe("/recruiter/applications?exp_min=2");
  });

  // --- clearing ----------------------------------------------------------------------

  it("clears the search and every filter at once", async () => {
    renderPage("/recruiter/applications?q=jane&status=SHORTLISTED&exp_min=2&page=2");
    await screen.findByText("Candidate 1");
    expect(screen.getByRole("searchbox", { name: "Search applications" })).toHaveValue("jane");

    fireEvent.click(screen.getByRole("button", { name: "Clear all" }));

    await waitFor(() => expect(lastQuery(list).get("status")).toBeNull());
    const query = lastQuery(list);
    expect(query.get("q")).toBeNull();
    expect(query.get("min_experience")).toBeNull();
    expect(query.get("offset")).toBe("0");
    expect(screen.getByRole("searchbox", { name: "Search applications" })).toHaveValue("");
    expect(screen.queryByLabelText("Active filters")).not.toBeInTheDocument();
    expect(location()).toBe("/recruiter/applications");
  });

  it("clears from inside the panel with Clear All", async () => {
    renderPage("/recruiter/applications?status=SHORTLISTED");
    await screen.findByText("Candidate 1");
    fireEvent.click(filtersButton());

    fireEvent.click(screen.getByRole("button", { name: "Clear All" }));

    await waitFor(() => expect(lastQuery(list).get("status")).toBeNull());
    expect(screen.queryByLabelText("Status")).not.toBeInTheDocument(); // panel closed
  });

  // --- URL state ------------------------------------------------------------------------

  it("restores search, filters, sort and page from the URL", async () => {
    serveInPages(60);
    renderPage("/recruiter/applications?q=asha&status=INTERVIEW&exp_min=3&exp_max=9&sort=name_asc&page=2");

    await screen.findByText("Showing 26–50 of 60 applications");
    const query = lastQuery(list);
    expect(query.get("q")).toBe("asha");
    expect(query.get("status")).toBe("INTERVIEW");
    expect(query.get("min_experience")).toBe("3");
    expect(query.get("max_experience")).toBe("9");
    expect(query.get("sort_by")).toBe("candidate_name");
    expect(query.get("sort_dir")).toBe("asc");
    expect(query.get("offset")).toBe("25");
    expect(screen.getByRole("searchbox", { name: "Search applications" })).toHaveValue("asha");
    expect(screen.getByRole("combobox", { name: "Sort by" })).toHaveValue("name_asc");
  });

  it("ignores values in the URL that this page could never have produced", async () => {
    renderPage("/recruiter/applications?status=NOT_A_STATUS&exp_min=abc&from=yesterday&type=ROBOT&page=-3");
    await screen.findByText("Candidate 1");

    const query = lastQuery(list);
    expect(query.get("status")).toBeNull();
    expect(query.get("min_experience")).toBeNull();
    expect(query.get("applied_from")).toBeNull();
    expect(query.get("candidate_type")).toBeNull();
    expect(query.get("offset")).toBe("0");
  });

  it("sorts on the server and returns to page 1", async () => {
    serveInPages(60);
    renderPage("/recruiter/applications?page=2");
    await screen.findByText("Showing 26–50 of 60 applications");

    fireEvent.change(screen.getByRole("combobox", { name: "Sort by" }), { target: { value: "name_desc" } });

    await waitFor(() => expect(lastQuery(list).get("sort_by")).toBe("candidate_name"));
    expect(lastQuery(list).get("sort_dir")).toBe("desc");
    expect(lastQuery(list).get("offset")).toBe("0");
  });

  // --- pagination -----------------------------------------------------------------------

  it("pages through results while filters stay active", async () => {
    serveInPages(60);
    renderPage("/recruiter/applications?status=SHORTLISTED&exp_min=2");

    await screen.findByText("Showing 1–25 of 60 applications");
    expect(screen.getByText("Page 1 of 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Next page" }));

    await screen.findByText("Showing 26–50 of 60 applications");
    let query = lastQuery(list);
    expect(query.get("offset")).toBe("25");
    expect(query.get("status")).toBe("SHORTLISTED"); // the filters travel with the page
    expect(query.get("min_experience")).toBe("2");
    expect(location()).toBe("/recruiter/applications?status=SHORTLISTED&exp_min=2&page=2");
    // Row numbers continue across pages.
    expect(screen.getByText("Candidate 26")).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "26" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await screen.findByText("Showing 51–60 of 60 applications");
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Previous page" }));
    await screen.findByText("Showing 26–50 of 60 applications");
    query = lastQuery(list);
    expect(query.get("status")).toBe("SHORTLISTED");
  });

  it("goes back to page 1 when applying new filters from a later page", async () => {
    serveInPages(60);
    renderPage("/recruiter/applications?page=3");
    await screen.findByText("Showing 51–60 of 60 applications");

    fireEvent.click(filtersButton());
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "REJECTED" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply Filters" }));

    await waitFor(() => expect(lastQuery(list).get("status")).toBe("REJECTED"));
    expect(lastQuery(list).get("offset")).toBe("0");
  });

  it("moves to the last real page when the requested page no longer exists", async () => {
    vi.spyOn(api, "listApplicationsPage").mockImplementation(async (query) => {
      const offset = Number(new URLSearchParams(query).get("offset") ?? 0);
      return offset >= 30 ? pageOf([], 30) : pageOf([makeApplication(offset + 1)], 30);
    });
    renderPage("/recruiter/applications?page=9");

    await screen.findByText("Candidate 26");
    expect(location()).toBe("/recruiter/applications?page=2");
  });

  it("has no pager for a single page of results", async () => {
    renderPage();
    await screen.findByText("Candidate 1");
    expect(screen.queryByRole("button", { name: "Next page" })).not.toBeInTheDocument();
  });

  // --- empty and error states ----------------------------------------------------------------

  it("distinguishes 'no applications yet' from 'nothing matches'", async () => {
    vi.spyOn(api, "listApplicationsPage").mockResolvedValue(pageOf([], 0));
    const first = renderPage();
    expect(await screen.findByText("No applications yet")).toBeInTheDocument();
    // Nothing to search or filter yet.
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
    first.unmount();

    renderPage("/recruiter/applications?status=HIRED");
    expect(await screen.findByText("No applications match these filters")).toBeInTheDocument();
    // The way out is right there.
    expect(screen.getByRole("button", { name: "Clear all" })).toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "Search applications" })).toBeInTheDocument();
  });

  it("shows the server's message when the request fails, and keeps the controls", async () => {
    vi.spyOn(api, "listApplicationsPage").mockRejectedValue(
      new ApiError("Minimum experience cannot be greater than maximum experience.", 422, "validation_error"),
    );
    renderPage("/recruiter/applications?exp_min=9&exp_max=2");

    expect(await screen.findByText(/Minimum experience cannot be greater/)).toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "Search applications" })).toBeInTheDocument();
  });

  // --- responsive ---------------------------------------------------------------------------------

  it("on a phone, filters open in a dialog and apply from there", async () => {
    stubViewport(true);
    renderPage();
    await screen.findByText("Candidate 1");

    fireEvent.click(filtersButton());
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Filters" })).toBeInTheDocument();

    fireEvent.change(within(dialog).getByLabelText("Status"), { target: { value: "SHORTLISTED" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Apply Filters" }));

    await waitFor(() => expect(lastQuery(list).get("status")).toBe("SHORTLISTED"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText("Status: Shortlisted")).toBeInTheDocument();
  });

  it("on a phone, closing the dialog discards unapplied changes", async () => {
    stubViewport(true);
    renderPage();
    await screen.findByText("Candidate 1");
    fireEvent.click(filtersButton());
    const callsBefore = list.mock.calls.length;

    fireEvent.change(within(await screen.findByRole("dialog")).getByLabelText("Status"), {
      target: { value: "REJECTED" },
    });
    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(list.mock.calls.length).toBe(callsBefore);
  });

  it("on a desktop, filters open inline rather than in a dialog", async () => {
    stubViewport(false);
    renderPage();
    await screen.findByText("Candidate 1");

    fireEvent.click(filtersButton());

    expect(screen.getByRole("region", { name: "Filters" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  // --- existing behavior ------------------------------------------------------------------------------

  it("still opens an application when its row is clicked", async () => {
    renderPage();
    fireEvent.click(await screen.findByText("Candidate 1"));
    expect(await screen.findByText("Application detail page")).toBeInTheDocument();
  });

  it("still deletes through the reason dialog without opening the row", async () => {
    renderPage();
    await screen.findByText("Candidate 1");

    fireEvent.click(screen.getAllByRole("button", { name: "Delete" })[0]);

    expect(await screen.findByRole("heading", { name: "Delete application" })).toBeInTheDocument();
    expect(screen.queryByText("Application detail page")).not.toBeInTheDocument();
  });
});
