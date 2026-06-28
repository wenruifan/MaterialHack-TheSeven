import { CheckCircle2, CircleAlert, CircleDot, GitBranch, RotateCcw } from "lucide-react";
import type { WorkbenchEvent } from "../types";

interface EventFeedProps {
  events: WorkbenchEvent[];
  compact?: boolean;
}

export default function EventFeed({ events, compact = false }: EventFeedProps) {
  const visible = [...events].reverse().slice(0, compact ? 8 : 16);
  return (
    <section className="rounded-lg border border-zinc-800 bg-zinc-900/70">
      <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
        <h2 className="text-sm font-semibold text-zinc-100">Event stream</h2>
        <span className="text-xs text-zinc-500">{events.length} events</span>
      </div>
      <div className={compact ? "max-h-[340px] overflow-auto" : "max-h-[520px] overflow-auto"}>
        {visible.length === 0 ? (
          <div className="px-4 py-8 text-sm text-zinc-500">No events yet.</div>
        ) : (
          <ol className="divide-y divide-zinc-800">
            {visible.map((event) => (
              <li key={event.event_id} className="flex gap-3 px-4 py-3">
                <EventIcon eventType={event.event_type} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-3">
                    <p className="truncate text-sm font-medium text-zinc-100">{eventLabel(event)}</p>
                    <time className="shrink-0 text-[11px] text-zinc-500">{formatTime(event.created_at)}</time>
                  </div>
                  <p className="mt-1 truncate text-xs text-zinc-500">
                    {event.loop_id ? `loop ${event.loop_id}` : event.job_id ? `job ${event.job_id}` : "run event"}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  );
}

function EventIcon({ eventType }: { eventType: WorkbenchEvent["event_type"] }) {
  const className = "mt-0.5 h-4 w-4 shrink-0";
  if (eventType === "run_failed") return <CircleAlert className={`${className} text-rose-300`} />;
  if (eventType === "run_finished" || eventType === "loop_finalized") return <CheckCircle2 className={`${className} text-emerald-300`} />;
  if (eventType === "rollback_recorded" || eventType === "loop_rejected") return <RotateCcw className={`${className} text-amber-300`} />;
  if (eventType === "loop_appended") return <GitBranch className={`${className} text-sky-300`} />;
  return <CircleDot className={`${className} text-zinc-500`} />;
}

function eventLabel(event: WorkbenchEvent) {
  const labels: Record<string, string> = {
    run_started: "Run started",
    loop_appended: "Loop appended",
    evaluation_attached: `Evaluation attached: ${String(event.data.evaluator_name ?? event.data.evaluation_kind ?? "unknown")}`,
    reflection_written: "Reflection written",
    loop_finalized: "Loop finalized",
    loop_rejected: `Loop rejected by ${String(event.data.actor ?? "agent")}`,
    rollback_recorded: `Rollback by ${String(event.data.actor ?? "agent")}`,
    run_finished: "Run finished",
    run_failed: "Run failed",
    human_input_recorded: "Human input recorded"
  };
  return labels[event.event_type] ?? event.event_type;
}

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
