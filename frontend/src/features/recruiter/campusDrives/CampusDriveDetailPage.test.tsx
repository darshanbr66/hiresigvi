import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import { ToastProvider } from "../../../shared/components/ToastContext";
import type {
  CampusDriveFunnelCounts,
  CampusDriveResponse,
  CampusDriveStatus,
} from "../../../types/campusDrive";
import * as applicationsApi from "../applications/api";
import { LocationProbe } from "../applications/LocationProbe";
import {
  lastQuery,
  makeApplication,
  pageOf,
  sentSearches,
  stubViewport,
} from "../applications/testUtils";
import * as api from "./api";
import { CampusDriveDetailPage } from "./CampusDriveDetailPage";

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ accessToken: "test-token", user: { roles: ["ORG_ADMIN"] } }),
}));

const drive = (status: CampusDriveStatus = "ACTIVE"): CampusDriveResponse => ({
  id: "drive-1",
  name: "Fall 2026 Drive",
  job_id: "job-1",
  job_title: "Graduate Engineer",
  college_name: "MIT",
  institution_id: "inst-1",
  institution_name: "MIT",
  affiliation_verification_method: "NONE",
  description: null,
  batch_year: 2026,
  start_date: null,
  end_date: null,
  registration_deadline: null,
  default_assessment_id: null,
  default_assessment_title: null,
  status,
  application_count: 60,
  deleted_at: null,
  created_at: new Date().toISOString(),
  application_link: null,
});

const funnel: CampusDriveFunnelCounts = {
  registered: 60,
  screening: 0,
  assessment_invited: 0,
  assessment_completed: 0,
  assessment_passed: 0,
  assessment_failed: 0,
  shortlisted: 0,
  interview: 0,
  selected: 0,
  rejected: 0,
  hired: 0,
  verification_pending: 0,
  verified: 0,
  verification_rejected: 0,
};

function renderPage(url = "/recruiter/campus-drives/drive-1") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MemoryRouter initialEntries={[url]}>
          <Routes>
            <Route
              path="/recruiter/campus-drives/:driveId"
              element={
                <>
                  <CampusDriveDetailPage />
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
const searchBox = () => screen.getByRole("searchbox", { name: "Search candidates in this drive" });

function serveInPages(total: number) {
  return vi.spyOn(applicationsApi, "listApplicationsPage").mockImplementation(async (query) => {
    const offset = Number(new URLSearchParams(query).get("offset") ?? 0);
    const count = Math.max(0, Math.min(25, total - offset));
    const items = Array.from({ length: count }, (_, i) =>
      makeApplication(offset + i + 1, { campus_drive_id: "drive-1" }),
    );
    return pageOf(items, total);
  });
}

describe("CampusDriveDetailPage — Candidates in this drive", () => {
  let list: MockInstance<typeof applicationsApi.listApplicationsPage>;

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "getCampusDrive").mockResolvedValue(drive());
    vi.spyOn(api, "getCampusDriveFunnel").mockResolvedValue(funnel);
    list = vi
      .spyOn(applicationsApi, "listApplicationsPage")
      .mockResolvedValue(pageOf([makeApplication(1), makeApplication(2)]));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("asks the server for this drive's candidates, one page at a time", async () => {
    renderPage();

    expect(await screen.findByText("Candidate 1")).toBeInTheDocument();
    const query = lastQuery(list);
    expect(query.get("campus_drive_id")).toBe("drive-1");
    expect(query.get("limit")).toBe("25");
    expect(query.get("offset")).toBe("0");
    expect(screen.getByText("candidate1@example.com · +91 90000 00001")).toBeInTheDocument();
    expect(screen.getByText("Showing 1–2 of 2 candidates")).toBeInTheDocument();
  });

  it("searches by name, email or phone on the server, debounced", async () => {
    renderPage();
    await screen.findByText("Candidate 1");

    fireEvent.change(searchBox(), { target: { value: "9" } });
    // A real pause shorter than the debounce: still nothing sent for "9".
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(sentSearches(list)).toEqual([null]);
    fireEvent.change(searchBox(), { target: { value: "98" } });
    fireEvent.change(searchBox(), { target: { value: "98765" } });

    expect(sentSearches(list)).toEqual([null]);
    await waitFor(() => expect(lastQuery(list).get("q")).toBe("98765"));
    expect(sentSearches(list)).toEqual([null, "98765"]);
    // Still scoped to the drive.
    expect(lastQuery(list).get("campus_drive_id")).toBe("drive-1");
    expect(location()).toBe("/recruiter/campus-drives/drive-1?q=98765");
  });

  it("offers only the filters that make sense for one drive", async () => {
    renderPage();
    await screen.findByText("Candidate 1");

    fireEvent.click(filtersButton());

    for (const label of ["Status", "Qualification", "Applied from", "Applied to"]) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
    // A drive has one job and its own source — no job/source/experience noise.
    for (const label of ["Job", "Source", "Candidate type", "Min experience (years)", "Notice period"]) {
      expect(screen.queryByLabelText(label)).not.toBeInTheDocument();
    }
    // And no sort menu on this table.
    expect(screen.queryByRole("combobox", { name: "Sort by" })).not.toBeInTheDocument();
  });

  it("applies status, qualification and date filters together", async () => {
    renderPage();
    await screen.findByText("Candidate 1");
    fireEvent.click(filtersButton());

    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "SHORTLISTED" } });
    fireEvent.change(screen.getByLabelText("Qualification"), { target: { value: "B.Tech" } });
    fireEvent.change(screen.getByLabelText("Applied from"), { target: { value: "2026-09-03" } });
    fireEvent.change(screen.getByLabelText("Applied to"), { target: { value: "2026-09-08" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply Filters" }));

    await waitFor(() => expect(lastQuery(list).get("status")).toBe("SHORTLISTED"));
    expect(Object.fromEntries(lastQuery(list))).toMatchObject({
      campus_drive_id: "drive-1",
      status: "SHORTLISTED",
      qualification: "B.Tech",
      applied_from: "2026-09-03",
      applied_to: "2026-09-08",
      offset: "0",
    });
    const chips = screen.getByLabelText("Active filters");
    expect(within(chips).getByText("Status: Shortlisted")).toBeInTheDocument();
    expect(within(chips).getByText("Qualification: B.Tech")).toBeInTheDocument();
    expect(within(chips).getByText("From: 03-09-2026")).toBeInTheDocument();
    expect(within(chips).getByText("To: 08-09-2026")).toBeInTheDocument();
  });

  it("clears the search and filters but stays on this drive", async () => {
    renderPage("/recruiter/campus-drives/drive-1?q=asha&status=REJECTED&page=2");
    await screen.findByText("Candidate 1");

    fireEvent.click(screen.getByRole("button", { name: "Clear all" }));

    await waitFor(() => expect(lastQuery(list).get("status")).toBeNull());
    expect(lastQuery(list).get("q")).toBeNull();
    expect(lastQuery(list).get("offset")).toBe("0");
    expect(lastQuery(list).get("campus_drive_id")).toBe("drive-1");
    expect(searchBox()).toHaveValue("");
    expect(location()).toBe("/recruiter/campus-drives/drive-1");
  });

  it("pages through the drive's candidates with filters kept", async () => {
    serveInPages(60);
    renderPage("/recruiter/campus-drives/drive-1?status=APPLIED");

    await screen.findByText("Showing 1–25 of 60 candidates");
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));

    await screen.findByText("Showing 26–50 of 60 candidates");
    expect(lastQuery(list).get("offset")).toBe("25");
    expect(lastQuery(list).get("status")).toBe("APPLIED");
    expect(lastQuery(list).get("campus_drive_id")).toBe("drive-1");
    expect(location()).toBe("/recruiter/campus-drives/drive-1?status=APPLIED&page=2");
    expect(screen.getByText("Candidate 26")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Previous page" }));
    await screen.findByText("Showing 1–25 of 60 candidates");
  });

  it("returns to page 1 when the search changes", async () => {
    serveInPages(60);
    renderPage("/recruiter/campus-drives/drive-1?page=2");
    await screen.findByText("Showing 26–50 of 60 candidates");

    fireEvent.change(searchBox(), { target: { value: "meera" } });

    await waitFor(() => expect(lastQuery(list).get("q")).toBe("meera"));
    expect(lastQuery(list).get("offset")).toBe("0");
  });

  it("restores its state from the URL", async () => {
    renderPage("/recruiter/campus-drives/drive-1?q=rao&status=SELECTED&from=2026-09-01&qualification=BCA");
    await screen.findByText("Candidate 1");

    const query = lastQuery(list);
    expect(query.get("q")).toBe("rao");
    expect(query.get("status")).toBe("SELECTED");
    expect(query.get("applied_from")).toBe("2026-09-01");
    expect(query.get("qualification")).toBe("BCA");
    expect(searchBox()).toHaveValue("rao");
  });

  it("explains an empty drive and an empty search differently", async () => {
    list.mockResolvedValue(pageOf([], 0));
    const first = renderPage();
    expect(await screen.findByText(/No applications yet/)).toBeInTheDocument();
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument(); // nothing to search yet
    first.unmount();

    renderPage("/recruiter/campus-drives/drive-1?status=HIRED");
    expect(await screen.findByText("No candidates in this drive match these filters.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear all" })).toBeInTheDocument();
  });

  it("on a phone, filters open in a dialog", async () => {
    stubViewport(true);
    renderPage();
    await screen.findByText("Candidate 1");

    fireEvent.click(filtersButton());
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Status"), { target: { value: "REJECTED" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Apply Filters" }));

    await waitFor(() => expect(lastQuery(list).get("status")).toBe("REJECTED"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("still opens a candidate's application when the row is clicked", async () => {
    renderPage();
    fireEvent.click(await screen.findByText("Candidate 2"));
    expect(await screen.findByText("Application detail page")).toBeInTheDocument();
  });

  it.each<CampusDriveStatus>(["DRAFT", "ACTIVE", "PAUSED", "CLOSED"])(
    "searches and filters the candidates of a %s drive the same way",
    async (status) => {
      vi.spyOn(api, "getCampusDrive").mockResolvedValue(drive(status));
      renderPage("/recruiter/campus-drives/drive-1?status=SHORTLISTED");

      expect(await screen.findByText("Candidate 1")).toBeInTheDocument();
      expect(screen.getByText(status, { selector: ".badge" })).toBeInTheDocument();
      expect(lastQuery(list).get("status")).toBe("SHORTLISTED");
      fireEvent.change(searchBox(), { target: { value: "x" } });
      await waitFor(() => expect(lastQuery(list).get("q")).toBe("x"));
    },
  );

  it("keeps the drive's own actions", async () => {
    renderPage();
    await screen.findByText("Candidate 1");

    expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
  });
});
