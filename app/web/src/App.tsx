import { Navigate, Route, Routes } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Activity, Loader2 } from "lucide-react";
import { getLatestRun } from "./api";
import MemoryPage from "./components/MemoryPage";

export default function App() {
  return (
    <div className="min-h-[100dvh] bg-zinc-950 text-zinc-100">
      <AppHeader />
      <main className="mx-auto grid w-full max-w-[1500px] gap-4 px-4 py-4">
        <Routes>
          <Route path="/" element={<LatestMemoryRoute />} />
          <Route path="/memory/:runId" element={<MemoryPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

function AppHeader() {
  return (
    <header className="border-b border-zinc-800 bg-zinc-950/95">
      <div className="mx-auto flex h-16 max-w-[1500px] items-center justify-between px-4">
        <div className="flex items-center gap-3">
          <div className="grid h-9 w-9 place-items-center rounded-lg border border-emerald-400/40 bg-emerald-400/10">
            <Activity className="h-4 w-4 text-emerald-300" />
          </div>
          <div>
            <div className="text-sm font-semibold tracking-wide text-zinc-50">Novacore</div>
            <div className="text-xs text-zinc-500">Codex-run protein design</div>
          </div>
        </div>
        <div className="font-mono text-xs text-zinc-500">memory workbench</div>
      </div>
    </header>
  );
}

function LatestMemoryRoute() {
  const latest = useQuery({
    queryKey: ["latest-run"],
    queryFn: getLatestRun,
    refetchInterval: 2500
  });

  if (latest.isLoading) {
    return (
      <div className="grid min-h-[calc(100dvh-96px)] place-items-center rounded-lg border border-zinc-800 bg-zinc-900/70 text-sm text-zinc-500">
        <div className="inline-flex items-center gap-2">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading memory.
        </div>
      </div>
    );
  }

  if (latest.data?.run_id) {
    return <Navigate to={`/memory/${latest.data.run_id}`} replace />;
  }

  return <MemoryPage />;
}
