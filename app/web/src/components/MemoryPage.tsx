import * as Dialog from "@radix-ui/react-dialog";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { CircleAlert, KeyRound, Loader2, X } from "lucide-react";
import {
  clearBoltzApiKey,
  createRunEventSource,
  decodeEvent,
  getBoltzApiAuthStatus,
  getLoopDetail,
  getMemory,
  setBoltzApiKey
} from "../api";
import type { BoltzApiAuthStatus, LoopDetail, MemorySnapshot, WorkbenchEvent } from "../types";
import EventFeed from "./EventFeed";
import EvaluationInspector from "./EvaluationInspector";
import LoopGraph from "./LoopGraph";
import { loopStatusLabel } from "./LoopGraph";

export default function MemoryPage() {
  const { runId } = useParams<{ runId: string }>();
  const queryClient = useQueryClient();
  const [selectedLoopId, setSelectedLoopId] = useState<string | null>(null);
  const [events, setEvents] = useState<WorkbenchEvent[]>([]);
  const [boltzPromptOpen, setBoltzPromptOpen] = useState(false);
  const [boltzPromptDismissed, setBoltzPromptDismissed] = useState(false);

  const memoryQuery = useQuery({
    queryKey: ["memory", runId],
    queryFn: () => getMemory(runId!),
    enabled: Boolean(runId),
    refetchInterval: 2500
  });

  const boltzAuthQuery = useQuery({
    queryKey: ["boltz-api-auth"],
    queryFn: getBoltzApiAuthStatus,
    refetchInterval: 10000
  });

  useEffect(() => {
    if (!runId) return;
    const eventSource = createRunEventSource(runId);
    const append = (message: MessageEvent<string>) => {
      const event = decodeEvent(message);
      setEvents((current) => [...current, event]);
      void queryClient.invalidateQueries({ queryKey: ["memory", runId] });
      if (event.loop_id) {
        void queryClient.invalidateQueries({ queryKey: ["loop", runId, event.loop_id] });
      }
    };
    for (const eventName of [
      "run_started",
      "loop_appended",
      "evaluation_attached",
      "reflection_written",
      "loop_finalized",
      "rollback_recorded",
      "run_finished",
      "run_failed",
      "human_input_recorded"
    ]) {
      eventSource.addEventListener(eventName, append as EventListener);
    }
    return () => eventSource.close();
  }, [queryClient, runId]);

  useEffect(() => {
    if (!selectedLoopId && memoryQuery.data?.active_loop_id) {
      setSelectedLoopId(memoryQuery.data.active_loop_id);
    }
  }, [memoryQuery.data, selectedLoopId]);

  useEffect(() => {
    if (boltzAuthQuery.data && !boltzAuthQuery.data.configured && !boltzPromptDismissed) {
      setBoltzPromptOpen(true);
    }
  }, [boltzAuthQuery.data, boltzPromptDismissed]);

  if (!runId) {
    return (
      <>
        <div className="grid min-h-[calc(100dvh-96px)] rounded-lg border border-zinc-800 bg-zinc-900/70">
          <div className="grid place-items-center p-8 text-center">
            <div className="max-w-md">
              <h1 className="text-sm font-semibold text-zinc-100">Memory graph</h1>
              <p className="mt-2 text-sm leading-6 text-zinc-500">
                No Novacore run memory is loaded yet. Start a run from Codex and this workbench will show the active loop memory.
              </p>
            </div>
          </div>
        </div>
        <BoltzApiKeyDialog
          open={boltzPromptOpen}
          onOpenChange={(open) => {
            setBoltzPromptOpen(open);
            if (!open) setBoltzPromptDismissed(true);
          }}
        />
      </>
    );
  }

  return (
    <>
      <div className="grid min-h-[calc(100dvh-96px)] gap-4 xl:grid-cols-[minmax(0,1fr)_460px]">
        <section className="grid min-h-[680px] overflow-hidden rounded-lg border border-zinc-800 bg-zinc-900/70">
          <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
            <div>
              <h1 className="text-sm font-semibold text-zinc-100">Memory graph</h1>
              <div className="mt-1 break-all font-mono text-xs text-zinc-500">{runId}</div>
            </div>
            <div className="flex items-center gap-2">
              <BoltzApiStatusButton
                status={boltzAuthQuery.data}
                isLoading={boltzAuthQuery.isLoading}
                onClick={() => setBoltzPromptOpen(true)}
              />
              {memoryQuery.isFetching ? <Loader2 className="h-4 w-4 animate-spin text-zinc-500" /> : null}
            </div>
          </div>
          {memoryQuery.isLoading ? (
            <div className="grid place-items-center text-sm text-zinc-500">Loading memory.</div>
          ) : memoryQuery.error ? (
            <div className="grid place-items-center p-8 text-sm text-rose-200">{memoryQuery.error.message}</div>
          ) : memoryQuery.data ? (
            <LoopGraph snapshot={memoryQuery.data} selectedLoopId={selectedLoopId} onSelectLoop={setSelectedLoopId} />
          ) : null}
        </section>

        <aside className="grid content-start gap-4">
          <LoopInspector runId={runId} snapshot={memoryQuery.data} selectedLoopId={selectedLoopId} onSelectLoop={setSelectedLoopId} />
          <EventFeed events={events} compact />
        </aside>
      </div>
      <BoltzApiKeyDialog
        open={boltzPromptOpen}
        onOpenChange={(open) => {
          setBoltzPromptOpen(open);
          if (!open) setBoltzPromptDismissed(true);
        }}
      />
    </>
  );
}

function BoltzApiStatusButton({
  status,
  isLoading,
  onClick
}: {
  status?: BoltzApiAuthStatus;
  isLoading: boolean;
  onClick: () => void;
}) {
  if (isLoading) {
    return (
      <button type="button" onClick={onClick} className="inline-flex h-8 items-center gap-2 rounded-lg border border-zinc-800 px-2 text-xs text-zinc-500 hover:bg-zinc-800">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        Boltz API
      </button>
    );
  }
  if (status?.configured) {
    return (
      <button type="button" onClick={onClick} className="inline-flex h-8 items-center gap-2 rounded-lg border border-emerald-400/30 bg-emerald-400/10 px-2 text-xs text-emerald-200 hover:bg-emerald-400/15">
        <KeyRound className="h-3.5 w-3.5" />
        {status.masked_key ?? "Boltz API"}
      </button>
    );
  }
  return (
    <button type="button" onClick={onClick} className="inline-flex h-8 items-center gap-2 rounded-lg border border-amber-400/30 bg-amber-400/10 px-2 text-xs text-amber-200 hover:bg-amber-400/15">
      <CircleAlert className="h-3.5 w-3.5" />
      Boltz API key
    </button>
  );
}

function BoltzApiKeyDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const queryClient = useQueryClient();
  const [apiKey, setApiKey] = useState("");
  const authQuery = useQuery({
    queryKey: ["boltz-api-auth"],
    queryFn: getBoltzApiAuthStatus,
    enabled: open
  });
  const setKeyMutation = useMutation({
    mutationFn: setBoltzApiKey,
    onSuccess: () => {
      setApiKey("");
      void queryClient.invalidateQueries({ queryKey: ["boltz-api-auth"] });
      onOpenChange(false);
    }
  });
  const clearKeyMutation = useMutation({
    mutationFn: clearBoltzApiKey,
    onSuccess: () => {
      setApiKey("");
      void queryClient.invalidateQueries({ queryKey: ["boltz-api-auth"] });
    }
  });
  const configured = authQuery.data?.configured;
  const busy = setKeyMutation.isPending || clearKeyMutation.isPending;
  const error = setKeyMutation.error ?? clearKeyMutation.error;

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/70" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 grid w-[min(calc(100vw-32px),440px)] -translate-x-1/2 -translate-y-1/2 gap-4 rounded-lg border border-zinc-800 bg-zinc-950 p-5 shadow-2xl shadow-black/40">
          <div className="flex items-start justify-between gap-3">
            <div className="grid gap-1">
              <Dialog.Title className="text-sm font-semibold text-zinc-100">Boltz API key</Dialog.Title>
              <Dialog.Description className="text-sm leading-5 text-zinc-500">
                Stored only in this local workbench process.
              </Dialog.Description>
            </div>
            <Dialog.Close className="grid h-8 w-8 place-items-center rounded-lg border border-zinc-800 text-zinc-400 hover:bg-zinc-900">
              <X className="h-4 w-4" />
            </Dialog.Close>
          </div>

          {configured ? (
            <div className="rounded-lg border border-emerald-400/30 bg-emerald-400/10 p-3 text-sm text-emerald-100">
              Active key: <span className="font-mono">{authQuery.data?.masked_key}</span>
              {authQuery.data?.source ? <span className="text-emerald-200/70"> ({authQuery.data.source})</span> : null}
            </div>
          ) : (
            <div className="rounded-lg border border-amber-400/30 bg-amber-400/10 p-3 text-sm text-amber-100">
              No key configured.
            </div>
          )}

          <form
            className="grid gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              setKeyMutation.mutate(apiKey);
            }}
          >
            <label className="grid gap-2">
              <span className="text-xs font-medium text-zinc-500">API key</span>
              <input
                className="field-input"
                type="password"
                autoComplete="off"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder="bapi_..."
              />
            </label>

            {error ? <div className="rounded-lg border border-rose-400/30 bg-rose-400/10 p-3 text-sm text-rose-100">{error.message}</div> : null}

            <div className="flex flex-wrap justify-end gap-2">
              {configured ? (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => clearKeyMutation.mutate()}
                  className="h-9 rounded-lg border border-zinc-700 px-3 text-sm text-zinc-300 hover:bg-zinc-900 disabled:opacity-50"
                >
                  Clear
                </button>
              ) : null}
              <button
                type="button"
                onClick={() => onOpenChange(false)}
                className="h-9 rounded-lg border border-zinc-700 px-3 text-sm text-zinc-300 hover:bg-zinc-900"
              >
                Not now
              </button>
              <button
                type="submit"
                disabled={busy || !apiKey.trim()}
                className="inline-flex h-9 items-center gap-2 rounded-lg border border-emerald-400/30 bg-emerald-400/15 px-3 text-sm text-emerald-100 hover:bg-emerald-400/20 disabled:opacity-50"
              >
                {setKeyMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <KeyRound className="h-4 w-4" />}
                Save key
              </button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function LoopInspector({
  runId,
  snapshot,
  selectedLoopId,
  onSelectLoop
}: {
  runId: string;
  snapshot?: MemorySnapshot;
  selectedLoopId: string | null;
  onSelectLoop: (loopId: string) => void;
}) {
  const detailQuery = useQuery({
    queryKey: ["loop", runId, selectedLoopId],
    queryFn: () => getLoopDetail(runId, selectedLoopId!),
    enabled: Boolean(selectedLoopId)
  });

  const selectedSummary = useMemo(
    () => snapshot?.nodes.find((node) => node.loop_id === selectedLoopId),
    [selectedLoopId, snapshot]
  );

  if (!selectedLoopId) {
    return <section className="rounded-lg border border-zinc-800 bg-zinc-900/70 p-6 text-sm text-zinc-500">Select a loop.</section>;
  }

  if (detailQuery.isLoading) {
    return <section className="rounded-lg border border-zinc-800 bg-zinc-900/70 p-6 text-sm text-zinc-500">Loading loop.</section>;
  }

  if (detailQuery.error) {
    return <section className="rounded-lg border border-zinc-800 bg-zinc-900/70 p-6 text-sm text-rose-200">{detailQuery.error.message}</section>;
  }

  const detail = detailQuery.data;
  if (!detail) return null;

  return (
    <section className="rounded-lg border border-zinc-800 bg-zinc-900/70">
      <div className="border-b border-zinc-800 px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-zinc-100">Loop inspector</h2>
            <div className="mt-1 break-all font-mono text-xs text-zinc-500">{selectedLoopId}</div>
          </div>
          <span className="rounded-full border border-zinc-700 px-2 py-1 text-xs text-zinc-300">
            {selectedSummary ? loopStatusLabel(selectedSummary) : detail.loop.status}
          </span>
        </div>
      </div>
      <div className="grid gap-4 p-4">
        <MetricStrip detail={detail} />
        <div className="grid gap-2">
          <div className="text-xs font-medium text-zinc-500">Sequence</div>
          <div className="break-all rounded-lg border border-zinc-800 bg-zinc-950 p-3 font-mono text-xs leading-5 text-zinc-300">
            {detail.loop.candidate.sequence}
          </div>
        </div>
        <BranchContextPanel detail={detail} selectedSummary={selectedSummary} onSelectLoop={onSelectLoop} />
        <AgentPlanPanel detail={detail} />
        <CandidateArtifactsPanel detail={detail} />
        <EvaluationInspector detail={detail} />
        <HumanInputPanel inputs={detail.loop.human_inputs} />
        <div className="grid gap-2">
          <div className="text-xs font-medium text-zinc-500">Lineage</div>
          <div className="flex flex-wrap gap-2">
            {detail.lineage_loop_ids.map((loopId) => (
              <button key={loopId} type="button" onClick={() => onSelectLoop(loopId)} className="rounded-lg border border-zinc-800 px-2 py-1 font-mono text-xs text-zinc-300 hover:bg-zinc-800">
                {loopId}
              </button>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

function BranchContextPanel({
  detail,
  selectedSummary,
  onSelectLoop
}: {
  detail: LoopDetail;
  selectedSummary?: MemorySnapshot["nodes"][number];
  onSelectLoop: (loopId: string) => void;
}) {
  const parentLoopId = detail.loop.parent_loop_id;
  if (!parentLoopId) return null;

  const branchLabel = selectedSummary?.branch_label ?? detail.loop.branch_label;
  return (
    <div className="grid gap-2 rounded-lg border border-zinc-800 bg-zinc-950 p-3">
      <div className="flex items-center justify-between gap-3">
        <div className="text-xs font-medium text-zinc-500">Loop origin</div>
        {branchLabel ? (
          <span className="rounded-md border border-amber-400/30 bg-amber-400/10 px-2 py-1 text-xs text-amber-200">
            Branch
          </span>
        ) : null}
      </div>
      <div className="grid gap-2 text-sm">
        <div className="flex items-center justify-between gap-3">
          <span className="text-zinc-500">Parent</span>
          <button type="button" onClick={() => onSelectLoop(parentLoopId)} className="break-all rounded-md border border-zinc-700 px-2 py-1 font-mono text-xs text-zinc-300 hover:bg-zinc-800">
            {parentLoopId}
          </button>
        </div>
        {branchLabel ? (
          <div className="flex items-center justify-between gap-3">
            <span className="text-zinc-500">Branch label</span>
            <span className="break-all rounded-md border border-zinc-700 px-2 py-1 font-mono text-xs text-zinc-300">
              {branchLabel}
            </span>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function AgentPlanPanel({ detail }: { detail: LoopDetail }) {
  const changeSet = detail.loop.change_set;
  if (!changeSet) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-950 p-3 text-sm text-zinc-500">
        Loop 0 seed selected by Novacore pre-loop ranking.
      </div>
    );
  }

  return (
    <div className="grid gap-2 rounded-lg border border-zinc-800 bg-zinc-950 p-3">
      <div className="flex items-center justify-between gap-3">
        <div className="text-xs font-medium text-zinc-500">Agent plan</div>
        <span className="rounded-md border border-emerald-400/30 bg-emerald-400/10 px-2 py-1 text-xs text-emerald-200">
          {changeSet.author}
        </span>
      </div>
      <div className="text-sm font-medium text-zinc-100">{changeSet.summary}</div>
      <p className="text-sm leading-5 text-zinc-400">{changeSet.why}</p>
      <div className="flex flex-wrap gap-2">
        {changeSet.changes.map((change) => (
          <span key={String(change.machine_diff)} className="rounded-md border border-zinc-700 px-2 py-1 font-mono text-xs text-zinc-300">
            {String(change.machine_diff)}
          </span>
        ))}
      </div>
    </div>
  );
}

function CandidateArtifactsPanel({ detail }: { detail: LoopDetail }) {
  const artifacts = [...detail.loop.candidate.structure_artifacts, ...detail.loop.candidate.boltz_artifacts];
  if (!artifacts.length) return null;

  return (
    <div className="grid gap-2">
      <div className="text-xs font-medium text-zinc-500">Candidate artifacts</div>
      <div className="grid gap-1 rounded-lg border border-zinc-800 bg-zinc-950 p-3">
        {artifacts.map((artifact) => (
          <div key={`${artifact.kind}-${artifact.uri}`} className="grid gap-1 text-xs">
            <div className="text-zinc-300">{artifact.kind}</div>
            <div className="break-all font-mono text-zinc-500">{artifact.uri}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function HumanInputPanel({ inputs }: { inputs: LoopDetail["loop"]["human_inputs"] }) {
  return (
    <div className="grid gap-2">
      <div className="text-xs font-medium text-zinc-500">Human and agent notes</div>
      {inputs.length ? (
        <div className="grid gap-2">
          {inputs.map((input) => {
            const action = typeof input.metadata.action === "string" ? input.metadata.action : "note";
            const actor = typeof input.metadata.actor === "string" ? input.metadata.actor : input.author;
            const previousActive = typeof input.metadata.previous_active_loop_id === "string" ? input.metadata.previous_active_loop_id : null;
            return (
              <article key={`${input.created_at}-${input.note}`} className="rounded-lg border border-zinc-800 bg-zinc-950 p-3">
                <div className="flex items-center justify-between gap-3">
                  <span className="rounded-md border border-zinc-700 px-2 py-1 text-xs text-zinc-300">
                    {action === "rollback" ? `Rollback: ${actor}` : actor}
                  </span>
                  <time className="shrink-0 text-[11px] text-zinc-500">{formatDate(input.created_at)}</time>
                </div>
                <p className="mt-2 text-sm leading-5 text-zinc-300">{input.note}</p>
                {previousActive ? (
                  <div className="mt-2 break-all font-mono text-[11px] text-zinc-500">
                    Previous active: {previousActive}
                  </div>
                ) : null}
              </article>
            );
          })}
        </div>
      ) : (
        <div className="rounded-lg border border-zinc-800 bg-zinc-950 p-3 text-sm text-zinc-500">No notes recorded.</div>
      )}
    </div>
  );
}

function MetricStrip({ detail }: { detail: LoopDetail }) {
  const latestMetrics = detail.loop.evaluations.reduce<Record<string, number>>((acc, evaluation) => {
    for (const metric of evaluation.metrics) {
      acc[metric.name] = metric.value;
    }
    return acc;
  }, {});
  const preferred = ["trs_total", "plddt", "ptm", "trs_raw_total", "screen_score", "binding_score"];
  const metrics = Object.entries(latestMetrics)
    .sort(([left], [right]) => {
      const leftIndex = preferred.indexOf(left);
      const rightIndex = preferred.indexOf(right);
      return (leftIndex === -1 ? preferred.length : leftIndex) - (rightIndex === -1 ? preferred.length : rightIndex);
    })
    .slice(0, 4);
  return (
    <div className="grid grid-cols-2 gap-2">
      {metrics.length === 0 ? (
        <div className="col-span-2 rounded-lg border border-zinc-800 bg-zinc-950 p-3 text-sm text-zinc-500">No metrics recorded.</div>
      ) : (
        metrics.map(([name, value]) => (
          <div key={name} className="rounded-lg border border-zinc-800 bg-zinc-950 p-3">
            <div className="text-[11px] uppercase tracking-[0.12em] text-zinc-500">{name}</div>
            <div className="mt-1 font-mono text-lg text-zinc-100">{Number(value).toFixed(3)}</div>
          </div>
        ))
      )}
    </div>
  );
}

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}
