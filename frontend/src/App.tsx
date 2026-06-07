import { Navigate, Route, Routes } from "react-router-dom";

import { AuthProvider } from "./auth/AuthContext";
import { LoginPage } from "./auth/LoginPage";
import { RequireAuth } from "./auth/RequireAuth";
import { Layout } from "./components/Layout";
import { RunDetail } from "./runs/RunDetail";
import { RunsDashboard } from "./runs/RunsDashboard";

// Placeholder until the real page lands in a later commit.
function Placeholder({ title }: { title: string }) {
  return <p>{title} — coming next.</p>;
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireAuth />}>
          <Route element={<Layout />}>
            <Route path="/" element={<RunsDashboard />} />
            <Route path="/runs/:id" element={<RunDetail />} />
            <Route path="/schedules" element={<Placeholder title="Schedules" />} />
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  );
}
