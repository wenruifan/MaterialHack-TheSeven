import { memo, useMemo } from "react";
import type { ReactElement } from "react";
import { Background, Controls, Handle, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import { BadgeCheck, Beaker, GitBranch, GitCommit, MessageSquare, RotateCcw, ShieldCheck } from "lucide-react";
import type { LoopNodeSummary, MemorySnapshot } from "../types";

interface LoopGraphProps {
  snapshot: MemorySnapshot;
  selectedLoopId: string | null;
  onSelectLoop: (loopId: string) => void;
}

export default function LoopGraph({ snapshot, selectedLoopId, onSelectLoop }: LoopGraphProps) {
  const nodes = useMemo<Node<LoopNodeData>[]>(
    () =>
      snapshot.nodes.map((loop, index) => {
        const parent = loop.parent_loop_id ? snapshot.nodes.find((candidate) => candidate.loop_id === loop.parent_loop_id) : null;
        const siblings = loop.parent_loop_id ? snapshot.nodes.filter((candidate) => candidate.parent_loop_id === loop.parent_loop_id) : [];
        return {
          id: loop.loop_id,
          type: "loopNode",
          position: { x: 80 + loop.index * 260, y: 120 + branchOffset(snapshot, loop, index) },
          data: {
            ...loop,
            parent_display: parent ? `loop_${parent.index}` : loop.parent_loop_id,
            is_branch_child: Boolean(loop.branch_label) || siblings.length > 1
          },
          selected: loop.loop_id === selectedLoopId
        };
      }),
    [selectedLoopId, snapshot]
  );
  const edges = useMemo<Edge[]>(
    () =>
      snapshot.edges.map((edge) => ({
        id: `${edge.parent_loop_id}-${edge.child_loop_id}`,
        source: edge.parent_loop_id,
        target: edge.child_loop_id,
        animated: snapshot.active_loop_id === edge.child_loop_id,
        className: "memory-edge"
      })),
    [snapshot]
  );
  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      fitView
      minZoom={0.35}
      maxZoom={1.4}
      onNodeClick={(_, node: Node<LoopNodeData>) => onSelectLoop(node.id)}
      className="memory-flow"
    >
      <Background color="#3f3f46" gap={24} />
      <Controls className="flow-controls" />
    </ReactFlow>
  );
}

type LoopNodeData = LoopNodeSummary & Record<string, unknown> & {
  parent_display?: string | null;
  is_branch_child?: boolean;
};

const LoopNode = memo(function LoopNode({ data, selected }: NodeProps<Node<LoopNodeData>>) {
  const rollbackInput = [...data.human_inputs].reverse().find((input) => input.metadata?.action === "rollback");
  const rollbackActor = typeof rollbackInput?.metadata.actor === "string" ? rollbackInput.metadata.actor : rollbackInput?.author;
  return (
    <div className={nodeClass(data, Boolean(selected))}>
      <Handle type="target" position={Position.Left} className="!h-2 !w-2 !border-zinc-500 !bg-zinc-900" />
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="font-mono text-xs text-zinc-500">loop_{data.index}</div>
          <div className="mt-1 max-w-48 truncate text-sm font-semibold text-zinc-100">{data.loop_id}</div>
        </div>
        <span className="rounded-full border border-zinc-700 px-2 py-1 text-[11px] text-zinc-300">{loopStatusLabel(data)}</span>
      </div>
      <div className="mt-3 text-xs text-zinc-500">
        {data.change_summary ?? "Seed candidate"} · {data.sequence_length} aa
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {data.evaluation_kinds.includes("boltz") ? <MiniBadge label="Boltz" icon={<Beaker />} /> : null}
        {data.evaluation_kinds.includes("screening") ? <MiniBadge label="TRS" icon={<GitCommit />} /> : null}
        {data.evaluation_kinds.includes("verifier") ? <MiniBadge label="Verifier" icon={<ShieldCheck />} /> : null}
        {data.human_input_count ? <MiniBadge label={String(data.human_input_count)} icon={<MessageSquare />} /> : null}
        {data.parent_loop_id ? <MiniBadge label={`From ${data.parent_display ?? data.parent_loop_id}`} icon={<GitCommit />} /> : null}
        {data.is_branch_child ? <MiniBadge label={data.branch_label ? `Branch: ${data.branch_label}` : "Branch"} icon={<GitBranch />} tone="amber" /> : null}
        {rollbackInput ? <MiniBadge label={`${formatActor(rollbackActor)} rollback`} icon={<RotateCcw />} tone="amber" /> : null}
        {data.is_active ? <MiniBadge label="Active" icon={<BadgeCheck />} tone="emerald" /> : null}
      </div>
      <Handle type="source" position={Position.Right} className="!h-2 !w-2 !border-zinc-500 !bg-zinc-900" />
    </div>
  );
});

const nodeTypes = { loopNode: LoopNode };

function MiniBadge({ label, icon, tone = "zinc" }: { label: string; icon: ReactElement; tone?: "zinc" | "emerald" | "amber" }) {
  const color = tone === "emerald" ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-200" : tone === "amber" ? "border-amber-400/40 bg-amber-400/10 text-amber-200" : "border-zinc-700 bg-zinc-950 text-zinc-300";
  return (
    <span title={label} className={`inline-flex max-w-full items-center gap-1 rounded-md border px-1.5 py-1 text-[11px] ${color}`}>
      {memoIcon(icon)}
      <span className="truncate">{label}</span>
    </span>
  );
}

function memoIcon(icon: ReactElement) {
  return <span className="[&>svg]:h-3 [&>svg]:w-3">{icon}</span>;
}

export function loopStatusLabel(loop: Pick<LoopNodeSummary, "status" | "evaluation_kinds" | "reflection">) {
  if (loop.status !== "pending") return loop.status;
  if (!loop.evaluation_kinds.length) return "Evaluating candidate";
  if (!loop.reflection.next_actions.length) return "Awaiting reflection";
  return "Awaiting decision";
}

function nodeClass(data: LoopNodeSummary, selected: boolean) {
  const statusClass: Record<LoopNodeSummary["status"], string> = {
    active: "border-emerald-400/70 bg-emerald-400/10",
    available: "border-zinc-700 bg-zinc-900",
    abandoned: "border-zinc-800 bg-zinc-950 opacity-70",
    pending: "border-dashed border-sky-400/60 bg-sky-400/10",
    rejected: "border-rose-400/50 bg-rose-400/10",
    terminal: "border-zinc-600 bg-zinc-900"
  };
  return [
    "w-64 rounded-lg border p-3 shadow-2xl shadow-black/20 transition",
    statusClass[data.status],
    selected ? "ring-2 ring-emerald-300/70" : ""
  ].join(" ");
}

function formatActor(actor?: string) {
  if (!actor) return "Agent";
  return actor.charAt(0).toUpperCase() + actor.slice(1);
}

function branchOffset(snapshot: MemorySnapshot, loop: LoopNodeSummary, index: number) {
  if (!loop.parent_loop_id) return 0;
  const siblings = snapshot.nodes.filter((candidate) => candidate.parent_loop_id === loop.parent_loop_id);
  if (siblings.length <= 1) return 0;
  const siblingIndex = siblings.findIndex((candidate) => candidate.loop_id === loop.loop_id);
  return (siblingIndex - (siblings.length - 1) / 2) * 150 + (index % 2) * 12;
}
