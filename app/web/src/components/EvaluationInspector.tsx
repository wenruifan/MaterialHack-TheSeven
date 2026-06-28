import * as Tabs from "@radix-ui/react-tabs";
import { useState, type ReactNode } from "react";
import { CheckCircle2, CircleAlert } from "lucide-react";
import type { EvaluationResult, LoopDetail } from "../types";

export default function EvaluationInspector({ detail }: { detail: LoopDetail }) {
  const boltz = detail.loop.evaluations.filter((evaluation) => evaluation.kind === "boltz");
  const screening = detail.loop.evaluations.filter((evaluation) => evaluation.kind === "screening");
  const verifier = detail.loop.evaluations.filter((evaluation) => evaluation.kind === "verifier");
  return (
    <Tabs.Root defaultValue="verifier" className="rounded-lg border border-zinc-800 bg-zinc-950">
      <Tabs.List className="grid grid-cols-4 border-b border-zinc-800 p-1">
        <Tab value="boltz">Boltz</Tab>
        <Tab value="screening">TRS</Tab>
        <Tab value="verifier">Verifier</Tab>
        <Tab value="reflection">Reflection</Tab>
      </Tabs.List>
      <Tabs.Content value="boltz" className="p-3">
        <EvaluationList evaluations={boltz} empty="No Boltz output." />
      </Tabs.Content>
      <Tabs.Content value="screening" className="p-3">
        <TrsPanel evaluations={screening} />
      </Tabs.Content>
      <Tabs.Content value="verifier" className="p-3">
        <EvaluationList evaluations={verifier} empty="No verifier output." />
      </Tabs.Content>
      <Tabs.Content value="reflection" className="grid gap-3 p-3 text-sm text-zinc-300">
        <ReflectionBlock title="Went well" items={detail.loop.reflection.went_well} />
        <ReflectionBlock title="Went wrong" items={detail.loop.reflection.went_wrong} />
        <ReflectionBlock title="Next actions" items={detail.loop.reflection.next_actions} />
        {detail.loop.reflection.notes ? <p className="rounded-lg border border-zinc-800 bg-zinc-900 p-3">{detail.loop.reflection.notes}</p> : null}
      </Tabs.Content>
    </Tabs.Root>
  );
}

function Tab({ value, children }: { value: string; children: ReactNode }) {
  return (
    <Tabs.Trigger value={value} className="rounded-md px-2 py-2 text-xs font-medium text-zinc-500 transition data-[state=active]:bg-zinc-800 data-[state=active]:text-zinc-100">
      {children}
    </Tabs.Trigger>
  );
}

function EvaluationList({ evaluations, empty }: { evaluations: EvaluationResult[]; empty: string }) {
  if (!evaluations.length) return <div className="py-6 text-sm text-zinc-500">{empty}</div>;
  return (
    <div className="grid gap-3">
      {evaluations.map((evaluation) => (
        <EvaluationCard key={`${evaluation.kind}-${evaluation.evaluator_name}-${evaluation.created_at}`} evaluation={evaluation} />
      ))}
    </div>
  );
}

function EvaluationCard({ evaluation }: { evaluation: EvaluationResult }) {
  const [metadataOpen, setMetadataOpen] = useState(evaluation.kind === "verifier" && evaluation.passed == null);
  const hasMetadata = Object.keys(evaluation.metadata).length > 0;
  return (
    <article className="rounded-lg border border-zinc-800 bg-zinc-900 p-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-zinc-100">{evaluation.evaluator_name}</h3>
          <p className="mt-1 text-xs text-zinc-500">{evaluation.evaluator_version ?? "unversioned"} · {formatDate(evaluation.created_at)}</p>
        </div>
        <PassIcon passed={evaluation.passed} />
      </div>
      {evaluation.summary ? <p className="mt-3 text-sm leading-5 text-zinc-300">{evaluation.summary}</p> : null}
      <MetricGrid metrics={evaluation.metrics} />
      {evaluation.artifacts.length ? (
        <div className="mt-3 grid gap-1 text-xs text-zinc-500">
          {evaluation.artifacts.map((artifact) => (
            <div key={artifact.uri} className="truncate font-mono">{artifact.kind}: {artifact.uri}</div>
          ))}
        </div>
      ) : null}
      {hasMetadata ? (
        <div className="mt-3">
          <button
            type="button"
            onClick={() => setMetadataOpen((open) => !open)}
            className="rounded-md border border-zinc-700 px-2 py-1 text-xs text-zinc-300 hover:bg-zinc-800"
          >
            {metadataOpen ? "Hide output" : "Show output"}
          </button>
          {metadataOpen ? (
            <pre className="mt-2 max-h-64 overflow-auto rounded-md border border-zinc-800 bg-zinc-950 p-2 text-[11px] leading-4 text-zinc-400">
              {JSON.stringify(evaluation.metadata, null, 2)}
            </pre>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

function TrsPanel({ evaluations }: { evaluations: EvaluationResult[] }) {
  if (!evaluations.length) return <div className="py-6 text-sm text-zinc-500">No TRS screening output.</div>;
  const trs = evaluations.find((evaluation) => evaluation.evaluator_name.toLowerCase().includes("trs")) ?? evaluations.at(-1)!;
  const components = asNumberRecord(trs.metadata.trs_components);
  const weights = asNumberRecord(trs.metadata.trs_weights ?? trs.metadata.weights);
  return (
    <div className="grid gap-3">
      <EvaluationCard evaluation={trs} />
      {Object.keys(components).length ? (
        <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-3">
          <div className="text-xs font-medium text-zinc-500">TRS components</div>
          <div className="mt-3 grid gap-2">
            {Object.entries(components).map(([name, value]) => (
              <div key={name} className="grid gap-1">
                <div className="flex justify-between gap-3 text-xs">
                  <span className="text-zinc-300">{name}</span>
                  <span className="font-mono text-zinc-500">{value.toFixed(3)}</span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-zinc-800">
                  <div className="h-full rounded-full bg-emerald-300" style={{ width: `${componentWidth(value)}%` }} />
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
      {Object.keys(weights).length ? (
        <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-3">
          <div className="text-xs font-medium text-zinc-500">TRS weights</div>
          <div className="mt-2 grid grid-cols-2 gap-2">
            {Object.entries(weights).map(([name, value]) => (
              <div key={name} className="rounded-md border border-zinc-800 bg-zinc-950 p-2">
                <div className="truncate text-[11px] text-zinc-500">{name}</div>
                <div className="mt-1 font-mono text-xs text-zinc-200">{value.toFixed(2)}</div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function MetricGrid({ metrics }: { metrics: EvaluationResult["metrics"] }) {
  if (!metrics.length) return null;
  return (
    <div className="mt-3 grid grid-cols-2 gap-2">
      {metrics.map((metric) => (
        <div key={metric.name} className="rounded-md border border-zinc-800 bg-zinc-950 p-2">
          <div className="truncate text-[11px] text-zinc-500">{metric.name}</div>
          <div className="mt-1 font-mono text-sm text-zinc-100">{metric.value.toFixed(3)}{metric.unit ? ` ${metric.unit}` : ""}</div>
        </div>
      ))}
    </div>
  );
}

function ReflectionBlock({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <div className="text-xs font-medium text-zinc-500">{title}</div>
      {items.length ? (
        <ul className="mt-2 grid gap-1">
          {items.map((item) => (
            <li key={item} className="rounded-md border border-zinc-800 bg-zinc-900 p-2 text-sm text-zinc-300">{item}</li>
          ))}
        </ul>
      ) : (
        <div className="mt-2 text-sm text-zinc-600">None recorded.</div>
      )}
    </div>
  );
}

function PassIcon({ passed }: { passed?: boolean | null }) {
  if (passed === true) return <CheckCircle2 className="h-4 w-4 text-emerald-300" />;
  if (passed === false) return <CircleAlert className="h-4 w-4 text-amber-300" />;
  return <span className="h-4 w-4 rounded-full border border-zinc-700" />;
}

function asNumberRecord(value: unknown): Record<string, number> {
  if (!value || typeof value !== "object") return {};
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .filter((entry): entry is [string, number] => typeof entry[1] === "number")
  );
}

function componentWidth(value: number) {
  const normalized = value <= 1 ? value * 100 : value * 20;
  return Math.max(4, Math.min(100, normalized));
}

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}
