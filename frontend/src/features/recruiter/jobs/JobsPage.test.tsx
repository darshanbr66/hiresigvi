import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../../shared/components/ToastContext";
import type { JobResponse } from "../../../types/recruitment";
import { JobsPage } from "./JobsPage";
import * as jobsApi from "./api";

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ accessToken: "test-token" }),
}));

const sampleJob: JobResponse = {
  id: "job-1",
  organization_id: "org-1",
  title: "Backend Engineer",
  department: null,
  location: null,
  employment_type: null,
  description: "Build things.",
  description_visible: true,
  status: "OPEN",
  openings_count: 1,
  created_by: "user-1",
  deleted_at: null,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <JobsPage />
        </ToastProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("JobsPage delete flow", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("shows a skeleton while loading, then the job row", async () => {
    vi.spyOn(jobsApi, "listJobs").mockResolvedValue([sampleJob]);

    const { container } = renderPage();
    expect(container.querySelector(".skeleton-table")).toBeInTheDocument();

    await screen.findByText("Backend Engineer");
    expect(container.querySelector(".skeleton-table")).not.toBeInTheDocument();
  });

  it("requires a reason, shows a loading state, and deletes the job on confirm", async () => {
    vi.spyOn(jobsApi, "listJobs").mockResolvedValue([sampleJob]);
    let resolveDelete: (value: JobResponse) => void;
    const deleteSpy = vi.spyOn(jobsApi, "deleteJob").mockReturnValue(
      new Promise((resolve) => {
        resolveDelete = resolve;
      }),
    );

    renderPage();
    await screen.findByText("Backend Engineer");

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete job" });

    // Programmatic submit bypasses the native `required` block, exercising
    // the app's own reason check — nothing is called for an empty reason.
    fireEvent.submit(dialog.querySelector("form")!);
    expect(deleteSpy).not.toHaveBeenCalled();
    await screen.findByText("A reason for deletion is required.");

    fireEvent.change(within(dialog).getByLabelText("Reason for deletion"), {
      target: { value: "Requisition cancelled" },
    });
    fireEvent.submit(dialog.querySelector("form")!);

    await waitFor(() =>
      expect(deleteSpy).toHaveBeenCalledWith("job-1", { reason: "Requisition cancelled" }, "test-token"),
    );
    // Duplicate-click prevention: the button is disabled while the request is in flight.
    expect(within(dialog).getByRole("button", { name: /deleting/i })).toBeDisabled();
    fireEvent.click(within(dialog).getByRole("button", { name: /deleting/i }));
    expect(deleteSpy).toHaveBeenCalledTimes(1);

    resolveDelete!({ ...sampleJob, deleted_at: new Date().toISOString() });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Delete job" })).not.toBeInTheDocument());
  });

  it("shows a server error inside the modal and keeps it open on failure", async () => {
    vi.spyOn(jobsApi, "listJobs").mockResolvedValue([sampleJob]);
    vi.spyOn(jobsApi, "deleteJob").mockRejectedValue(new Error("network down"));

    renderPage();
    await screen.findByText("Backend Engineer");

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog", { name: "Delete job" });
    fireEvent.change(within(dialog).getByLabelText("Reason for deletion"), {
      target: { value: "Requisition cancelled" },
    });
    fireEvent.submit(dialog.querySelector("form")!);

    await screen.findByText("Unable to reach the server.");
    expect(screen.getByRole("dialog", { name: "Delete job" })).toBeInTheDocument();
  });
});

describe("JobsPage employment types", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("creates an internship with only the details that apply to it", async () => {
    vi.spyOn(jobsApi, "listJobs").mockResolvedValue([]);
    const createSpy = vi
      .spyOn(jobsApi, "createJob")
      .mockResolvedValue({ ...sampleJob, employment_type: "INTERNSHIP" });

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Create your first job" }));
    const dialog = await screen.findByRole("dialog", { name: "Create a job" });

    // Defaults to full-time, which offers a joining date but no stipend status.
    const typeSelect = within(dialog).getByLabelText("Employment type");
    expect(typeSelect).toHaveValue("FULL_TIME");
    expect(within(dialog).getByLabelText("Expected joining date")).toBeInTheDocument();
    expect(within(dialog).queryByLabelText("Paid or unpaid")).not.toBeInTheDocument();

    fireEvent.change(typeSelect, { target: { value: "INTERNSHIP" } });
    expect(within(dialog).queryByLabelText("Expected joining date")).not.toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("Title"), { target: { value: "Data Intern" } });
    fireEvent.change(within(dialog).getByLabelText("Duration (months)"), { target: { value: "6" } });
    fireEvent.change(within(dialog).getByLabelText("Paid or unpaid"), { target: { value: "true" } });
    fireEvent.change(within(dialog).getByLabelText("Stipend from"), { target: { value: "15000" } });
    fireEvent.change(within(dialog).getByLabelText("Currency"), { target: { value: "inr" } });
    fireEvent.change(within(dialog).getByLabelText("Period"), { target: { value: "MONTHLY" } });
    fireEvent.change(dialog.querySelector("textarea[required]")!, { target: { value: "Learn things." } });
    fireEvent.submit(dialog.querySelector("form")!);

    await waitFor(() => expect(createSpy).toHaveBeenCalledTimes(1));
    const payload = createSpy.mock.calls[0][0];
    expect(payload).toMatchObject({
      title: "Data Intern",
      employment_type: "INTERNSHIP",
      duration_months: 6,
      is_paid: true,
      compensation_min: "15000",
      compensation_max: null,
      compensation_currency: "INR",
      compensation_period: "MONTHLY",
    });
    expect(payload).not.toHaveProperty("expected_joining_date");
    expect(payload).not.toHaveProperty("hours_per_week");
  });

  it("shows each job's type and filters by it", async () => {
    vi.spyOn(jobsApi, "listJobs").mockResolvedValue([
      { ...sampleJob, id: "job-1", title: "Backend Engineer", employment_type: "FULL_TIME" },
      { ...sampleJob, id: "job-2", title: "Weekend Support", employment_type: "PART_TIME" },
    ]);

    renderPage();
    await screen.findByText("Backend Engineer");
    expect(screen.getByRole("cell", { name: "Part-time" })).toBeInTheDocument();

    fireEvent.change(screen.getByRole("combobox", { name: "Employment type" }), {
      target: { value: "PART_TIME" },
    });
    expect(screen.queryByText("Backend Engineer")).not.toBeInTheDocument();
    expect(screen.getByText("Weekend Support")).toBeInTheDocument();
  });
});
