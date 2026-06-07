import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import { AuthProvider } from "./AuthContext";
import { LoginPage } from "./LoginPage";

// Mock the endpoint layer so no real fetch happens; AuthProvider bootstraps via
// getMe (here: rejected -> starts logged out).
vi.mock("../api/endpoints", () => ({
  getMe: vi.fn(),
  login: vi.fn(),
  logout: vi.fn(),
}));
import { getMe, login } from "../api/endpoints";

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={["/login"]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<div>HOME DASHBOARD</div>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("LoginPage", () => {
  beforeEach(() => {
    vi.mocked(getMe).mockRejectedValue(new ApiError(401, "nope")); // not logged in
    vi.mocked(login).mockReset();
  });
  afterEach(() => vi.clearAllMocks());

  it("renders the login form", async () => {
    renderLogin();
    expect(await screen.findByLabelText(/username/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument();
  });

  it("shows an error on wrong credentials", async () => {
    vi.mocked(login).mockRejectedValue(new ApiError(401, "invalid"));
    renderLogin();

    await userEvent.type(await screen.findByLabelText(/username/i), "admin");
    await userEvent.type(screen.getByLabelText(/password/i), "wrong");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/invalid username or password/i);
  });

  it("logs in and navigates home on success", async () => {
    vi.mocked(login).mockResolvedValue({ username: "admin" });
    renderLogin();

    await userEvent.type(await screen.findByLabelText(/username/i), "admin");
    await userEvent.type(screen.getByLabelText(/password/i), "s3cret");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByText(/HOME DASHBOARD/i)).toBeInTheDocument();
    expect(login).toHaveBeenCalledWith("admin", "s3cret");
  });
});
