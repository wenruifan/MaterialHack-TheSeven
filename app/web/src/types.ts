export type SeedSource = "ccdc_csd" | "de_novo";
export type RunMode = "seed_and_loop" | "seed_only";
export type GoalComparator = "gte" | "lte" | "eq";

export interface OptimizationTarget {
  name: string;
  target: number;
  comparator: GoalComparator;
  weight: number;
  unit?: string | null;
  description?: string | null;
}

export interface ObjectiveParameters {
  target: string;
  ph: number;
  functions: string[];
  length: number;
  seed_count: number;
  seed_sources: SeedSource[];
  target_score: number;
  loop_count: number;
  optimization_targets: OptimizationTarget[];
}

export interface CreateRunPayload extends ObjectiveParameters {
  objective: string;
  run_mode: RunMode;
  rng_seed?: number;
}

export interface RunResponse {
  run_id: string;
  job_id: string;
  status: string;
}

export interface LatestRunResponse {
  run_id?: string | null;
}

export interface BoltzApiAuthStatus {
  configured: boolean;
  source?: "environment" | "runtime" | null;
  masked_key?: string | null;
}

export interface MetricValue {
  name: string;
  value: number;
  unit?: string | null;
  higher_is_better?: boolean | null;
  uncertainty?: number | null;
  metadata: Record<string, unknown>;
}

export interface ArtifactRef {
  uri: string;
  kind: string;
  format?: string | null;
  sha256?: string | null;
  metadata: Record<string, unknown>;
}

export interface EvaluationResult {
  kind: "boltz" | "screening" | "verifier" | "human_review" | "other";
  evaluator_name: string;
  evaluator_version?: string | null;
  metrics: MetricValue[];
  passed?: boolean | null;
  summary?: string | null;
  artifacts: ArtifactRef[];
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface HumanInput {
  author: string;
  note: string;
  requested_changes: string[];
  created_at: string;
  metadata: Record<string, unknown>;
}

export interface LoopReflection {
  went_well: string[];
  went_wrong: string[];
  next_actions: string[];
  notes?: string | null;
}

export interface LoopNodeSummary {
  loop_id: string;
  parent_loop_id?: string | null;
  index: number;
  status: "pending" | "active" | "available" | "abandoned" | "rejected" | "terminal";
  is_active: boolean;
  can_branch_from: boolean;
  branch_label?: string | null;
  sequence_length: number;
  sequence_preview: string;
  change_summary?: string | null;
  change_rationale?: string | null;
  change_diffs: string[];
  latest_metrics: Record<string, number>;
  evaluation_kinds: EvaluationResult["kind"][];
  human_input_count: number;
  human_inputs: HumanInput[];
  reflection: LoopReflection;
  completeness?: unknown;
  created_at: string;
}

export interface LoopGraphEdge {
  parent_loop_id: string;
  child_loop_id: string;
}

export interface MemorySnapshot {
  run_id: string;
  root_loop_id: string;
  active_loop_id: string;
  objective: {
    description: string;
    goals: Array<Record<string, unknown>>;
    max_loops?: number | null;
    custom: Record<string, unknown>;
  };
  conditions: {
    parameters: Record<string, unknown>;
    schema_version: string;
  };
  nodes: LoopNodeSummary[];
  edges: LoopGraphEdge[];
  seed_selection_decision?: Record<string, unknown> | null;
  created_at: string;
}

export interface LoopDetail {
  loop: {
    run_id: string;
    loop_id: string;
    index: number;
    parent_loop_id?: string | null;
    candidate: {
      sequence: string;
      origin: string;
      name?: string | null;
      structure_artifacts: ArtifactRef[];
      boltz_artifacts: ArtifactRef[];
      metadata: Record<string, unknown>;
    };
    conditions: Record<string, unknown>;
    change_set?: {
      summary: string;
      why: string;
      changes: Array<Record<string, unknown>>;
      author: string;
      created_at: string;
      metadata: Record<string, unknown>;
    } | null;
    evaluations: EvaluationResult[];
    reflection: LoopReflection;
    human_inputs: HumanInput[];
    status: LoopNodeSummary["status"];
    branch_label?: string | null;
    created_at: string;
    metadata: Record<string, unknown>;
  };
  lineage_loop_ids: string[];
}

export interface WorkbenchEvent {
  event_id: string;
  event_type:
    | "run_started"
    | "loop_appended"
    | "evaluation_attached"
    | "reflection_written"
    | "loop_finalized"
    | "loop_rejected"
    | "rollback_recorded"
    | "run_finished"
    | "run_failed"
    | "human_input_recorded";
  created_at: string;
  run_id?: string | null;
  job_id?: string | null;
  loop_id?: string | null;
  data: Record<string, unknown>;
}

export interface JobPayload {
  job_id: string;
  run_id: string;
  action: string;
  status: "queued" | "running" | "succeeded" | "failed";
  started_at?: string | null;
  completed_at?: string | null;
  error?: string | null;
  result?: Record<string, unknown> | null;
}
