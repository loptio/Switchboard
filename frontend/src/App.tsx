import { Navigate, Route, Routes } from "react-router-dom";

import { AuthProvider } from "./auth/AuthContext";
import { LoginPage } from "./auth/LoginPage";
import { RequireAuth } from "./auth/RequireAuth";
import { Layout } from "./components/Layout";
import { AgentBuilder } from "./agents/AgentBuilder";
import { AgentsPage } from "./agents/AgentsPage";
import { AssistantPage } from "./assistant/AssistantPage";
import { RunDetail } from "./runs/RunDetail";
import { RunsDashboard } from "./runs/RunsDashboard";
import { SchedulesPage } from "./schedules/SchedulesPage";
import { WorkflowBuilder } from "./workflows/WorkflowBuilder";
import { WorkflowsPage } from "./workflows/WorkflowsPage";

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireAuth />}>
          <Route element={<Layout />}>
            <Route path="/" element={<RunsDashboard />} />
            <Route path="/assistant" element={<AssistantPage />} />
            <Route path="/runs/:id" element={<RunDetail />} />
            <Route path="/schedules" element={<SchedulesPage />} />
            <Route path="/workflows" element={<WorkflowsPage />} />
            <Route path="/workflows/:defId/edit" element={<WorkflowBuilder />} />
            <Route path="/agents" element={<AgentsPage />} />
            <Route path="/agents/:agentId/edit" element={<AgentBuilder />} />
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  );
}
