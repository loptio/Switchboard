// Scaffold shell — replaced by the real auth-gated app (routes + AuthProvider)
// in the next commits. Kept minimal so the toolchain (Vite/TS/Vitest) is wired
// and verifiable on its own first.
export default function App() {
  return (
    <main style={{ padding: 24 }}>
      <h1>Agent Control Plane</h1>
      <p>Frontend scaffold is up. Wiring auth, runs, and schedules next.</p>
    </main>
  );
}
