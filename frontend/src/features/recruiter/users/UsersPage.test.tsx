import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../../lib/apiClient";
import { ToastProvider } from "../../../shared/components/ToastContext";
import type { UserResponse } from "../../../types/auth";
import * as authApi from "../../auth/api";
import * as teamApi from "../team/api";
import { UsersPage } from "./UsersPage";

let mockUser: { id: string; roles: string[]; organization_name: string } = {
  id: "u-hr",
  roles: ["RECRUITER"],
  organization_name: "Acme Corp",
};
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ accessToken: "test-token", user: mockUser }),
}));

const NOW = new Date().toISOString();

function makeUser(id: string, full_name: string, role: string, is_active = true): UserResponse {
  return {
    id,
    organization_id: "org-1",
    email: `${id}@acme.dev`,
    full_name,
    is_active,
    created_at: NOW,
    roles: [role],
  };
}

const team: UserResponse[] = [
  makeUser("u-admin", "Ada Admin", "ORG_ADMIN"),
  makeUser("u-hr", "Harini HR", "RECRUITER"),
  makeUser("u-int", "Ivan Interviewer", "INTERVIEWER"),
];

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <UsersPage />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

async function openAccounts() {
  fireEvent.click(screen.getByRole("tab", { name: "Accounts" }));
  return within(await screen.findByRole("table"));
}

function rowFor(table: ReturnType<typeof within>, name: string) {
  return within(table.getByText(name).closest("tr") as HTMLElement);
}

describe("UsersPage (Team → Accounts)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockUser = { id: "u-hr", roles: ["RECRUITER"], organization_name: "Acme Corp" };
    vi.spyOn(teamApi, "listDepartments").mockResolvedValue([]);
    vi.spyOn(teamApi, "listEmployees").mockResolvedValue([]);
  });

  it("gives HR the account management UI", async () => {
    vi.spyOn(authApi, "listUsers").mockResolvedValue(team);
    renderPage();

    const table = await openAccounts();

    expect(screen.queryByText(/do not have permission/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "+ Add team member" })).toBeInTheDocument();
    expect(table.getByRole("columnheader", { name: "Actions" })).toBeInTheDocument();

    const interviewer = rowFor(table, "Ivan Interviewer");
    expect(interviewer.getByRole("button", { name: "Deactivate" })).toBeInTheDocument();
    const roleSelect = interviewer.getByRole("combobox", { name: "Role for Ivan Interviewer" });
    const options = within(roleSelect).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["RECRUITER", "HIRING_MANAGER", "INTERVIEWER"]);
  });

  it("does not offer HR any control over an Org Admin or over their own account", async () => {
    vi.spyOn(authApi, "listUsers").mockResolvedValue(team);
    renderPage();

    const table = await openAccounts();

    const admin = rowFor(table, "Ada Admin");
    expect(admin.queryByRole("combobox")).not.toBeInTheDocument();
    expect(admin.queryByRole("button")).not.toBeInTheDocument();
    expect(admin.getByText("ORG_ADMIN")).toBeInTheDocument();

    const self = rowFor(table, "Harini HR");
    expect(self.queryByRole("combobox")).not.toBeInTheDocument();
    expect(self.queryByRole("button")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "+ Add team member" }));
    const dialog = within(await screen.findByRole("dialog"));
    const createOptions = within(dialog.getByRole("combobox"))
      .getAllByRole("option")
      .map((o) => o.textContent);
    expect(createOptions).not.toContain("ORG_ADMIN");
  });

  it("lets HR deactivate a team member through the existing flow", async () => {
    vi.spyOn(authApi, "listUsers").mockResolvedValue(team);
    const update = vi
      .spyOn(authApi, "updateUser")
      .mockResolvedValue({ ...team[2], is_active: false });
    renderPage();

    const table = await openAccounts();
    fireEvent.click(rowFor(table, "Ivan Interviewer").getByRole("button", { name: "Deactivate" }));
    const dialog = within(await screen.findByRole("dialog"));
    fireEvent.click(dialog.getByRole("button", { name: "Deactivate" }));

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith(
        "u-int",
        { is_active: false, reason: undefined },
        "test-token",
      ),
    );
  });

  it("still gives an Org Admin control over other admins", async () => {
    mockUser = { id: "u-admin", roles: ["ORG_ADMIN"], organization_name: "Acme Corp" };
    vi.spyOn(authApi, "listUsers").mockResolvedValue([
      ...team,
      makeUser("u-admin2", "Otto Admin", "ORG_ADMIN"),
    ]);
    renderPage();

    const table = await openAccounts();

    const otherAdmin = rowFor(table, "Otto Admin");
    expect(otherAdmin.getByRole("button", { name: "Deactivate" })).toBeInTheDocument();
    const options = within(otherAdmin.getByRole("combobox"))
      .getAllByRole("option")
      .map((o) => o.textContent);
    expect(options).toContain("ORG_ADMIN");
  });

  it.each(["HIRING_MANAGER", "INTERVIEWER"])(
    "keeps %s restricted: the server's 403 shows the permission message and no controls",
    async (role) => {
      mockUser = { id: "u-x", roles: [role], organization_name: "Acme Corp" };
      vi.spyOn(authApi, "listUsers").mockRejectedValue(
        new ApiError("You do not have permission to perform this action.", 403, "forbidden"),
      );
      renderPage();

      fireEvent.click(screen.getByRole("tab", { name: "Accounts" }));

      expect(
        await screen.findByText("You do not have permission to view or manage team members."),
      ).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "+ Add team member" })).not.toBeInTheDocument();
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
    },
  );
});

describe("UsersPage: deleting a deactivated account", () => {
  const inactive = makeUser("u-old", "Olga Old", "RECRUITER", false);
  const roster = () => [...team, inactive];

  beforeEach(() => {
    vi.restoreAllMocks();
    mockUser = { id: "u-admin", roles: ["ORG_ADMIN"], organization_name: "Acme Corp" };
    vi.spyOn(teamApi, "listDepartments").mockResolvedValue([]);
    vi.spyOn(teamApi, "listEmployees").mockResolvedValue([]);
  });

  async function openDeleteDialog() {
    const table = await openAccounts();
    fireEvent.click(rowFor(table, "Olga Old").getByRole("button", { name: "Delete Olga Old" }));
    return { table, dialog: within(await screen.findByRole("dialog")) };
  }

  it("offers Delete only for inactive accounts, next to Reactivate", async () => {
    vi.spyOn(authApi, "listUsers").mockResolvedValue(roster());
    renderPage();
    const table = await openAccounts();

    const old = rowFor(table, "Olga Old");
    expect(old.getByRole("button", { name: "Reactivate" })).toBeInTheDocument();
    const remove = old.getByRole("button", { name: "Delete Olga Old" });
    expect(remove).toHaveClass("btn-danger"); // distinct from Reactivate
    expect(old.getByRole("button", { name: "Reactivate" })).not.toHaveClass("btn-danger");

    for (const name of ["Harini HR", "Ivan Interviewer"]) {
      const row = rowFor(table, name);
      expect(row.getByRole("button", { name: "Deactivate" })).toBeInTheDocument();
      expect(row.queryByRole("button", { name: /^Delete/ })).not.toBeInTheDocument();
    }
    // never on one's own row
    expect(rowFor(table, "Ada Admin").queryByRole("button", { name: /^Delete/ })).not.toBeInTheDocument();
  });

  it("is not offered to HR, even for inactive accounts", async () => {
    mockUser = { id: "u-hr", roles: ["RECRUITER"], organization_name: "Acme Corp" };
    vi.spyOn(authApi, "listUsers").mockResolvedValue(roster());
    renderPage();
    const table = await openAccounts();

    const old = rowFor(table, "Olga Old");
    expect(old.getByRole("button", { name: "Reactivate" })).toBeInTheDocument();
    expect(old.queryByRole("button", { name: /^Delete/ })).not.toBeInTheDocument();
  });

  it("asks for explicit confirmation, showing the name and email", async () => {
    vi.spyOn(authApi, "listUsers").mockResolvedValue(roster());
    const remove = vi.spyOn(authApi, "deleteUser").mockResolvedValue(undefined);
    renderPage();
    const { dialog } = await openDeleteDialog();

    const who = dialog.getByText("Team member:").closest("p") as HTMLElement;
    expect(who).toHaveTextContent("Team member: Olga Old");
    expect(who).toHaveTextContent("Email: u-old@acme.dev");
    expect(dialog.getByText(/permanently removes/)).toHaveTextContent(/cannot be undone/);

    const confirm = dialog.getByRole("button", { name: "Delete permanently" });
    expect(confirm).toBeDisabled();
    fireEvent.click(confirm);
    expect(remove).not.toHaveBeenCalled();
  });

  it("cancelling deletes nothing", async () => {
    vi.spyOn(authApi, "listUsers").mockResolvedValue(roster());
    const remove = vi.spyOn(authApi, "deleteUser").mockResolvedValue(undefined);
    renderPage();
    const { table, dialog } = await openDeleteDialog();

    fireEvent.click(dialog.getByRole("checkbox", { name: "I understand this cannot be undone" }));
    fireEvent.click(dialog.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(remove).not.toHaveBeenCalled();
    expect(table.getByText("Olga Old")).toBeInTheDocument();
  });

  it("deletes on confirmation and removes the row", async () => {
    const list = vi.spyOn(authApi, "listUsers").mockResolvedValue(roster());
    const remove = vi.spyOn(authApi, "deleteUser").mockResolvedValue(undefined);
    renderPage();
    const { table, dialog } = await openDeleteDialog();

    list.mockResolvedValue(team); // what the server holds afterwards
    fireEvent.click(dialog.getByRole("checkbox", { name: "I understand this cannot be undone" }));
    fireEvent.click(dialog.getByRole("button", { name: "Delete permanently" }));

    await waitFor(() => expect(remove).toHaveBeenCalledWith("u-old", "test-token"));
    await waitFor(() => expect(table.queryByText("Olga Old")).not.toBeInTheDocument());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(await screen.findByText("Olga Old's account was permanently deleted.")).toBeInTheDocument();
  });

  it("shows the server's refusal and keeps the account", async () => {
    vi.spyOn(authApi, "listUsers").mockResolvedValue(roster());
    vi.spyOn(authApi, "deleteUser").mockRejectedValue(
      new ApiError(
        "Olga Old can't be deleted because they created 2 jobs, which must keep their author.",
        409,
        "user_has_history",
      ),
    );
    renderPage();
    const { table, dialog } = await openDeleteDialog();

    fireEvent.click(dialog.getByRole("checkbox", { name: "I understand this cannot be undone" }));
    fireEvent.click(dialog.getByRole("button", { name: "Delete permanently" }));

    expect(await dialog.findByText(/created 2 jobs/)).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(table.getByText("Olga Old")).toBeInTheDocument();
  });

  it("shows a generic error when the server can't be reached", async () => {
    vi.spyOn(authApi, "listUsers").mockResolvedValue(roster());
    vi.spyOn(authApi, "deleteUser").mockRejectedValue(new TypeError("Failed to fetch"));
    renderPage();
    const { dialog } = await openDeleteDialog();

    fireEvent.click(dialog.getByRole("checkbox", { name: "I understand this cannot be undone" }));
    fireEvent.click(dialog.getByRole("button", { name: "Delete permanently" }));
    expect(await dialog.findByText("Unable to reach the server.")).toBeInTheDocument();
  });
});
