import { Navigate, Outlet } from "react-router-dom";

import { Spinner } from "../components/Spinner";
import { useAuth } from "./AuthContext";

/** Route guard: wait for the bootstrap probe, then gate on auth status. */
export function RequireAuth() {
  const { status } = useAuth();
  if (status === "loading") {
    return (
      <div style={{ display: "grid", placeItems: "center", minHeight: "100vh" }}>
        <Spinner label="Loading…" />
      </div>
    );
  }
  if (status === "anon") {
    return <Navigate to="/login" replace />;
  }
  return <Outlet />;
}
