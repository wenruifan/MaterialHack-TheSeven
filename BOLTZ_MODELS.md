# Boltz Model Usage Skill

This file defines how an agentic science system should use Boltz models and
Boltz-style outputs.

The agent should treat Boltz as an external scientific model system. The agent
may prepare inputs, submit jobs, monitor execution, parse outputs, compare
metrics, and decide the next experiment. The agent must not invent Boltz
outputs, rewrite model metrics, or mark a scientific step complete without
model artifacts or a recorded model failure.

## Interface Preference

Use the official Boltz API for agentic workflows when credentials and network
access are available.

Prefer interfaces in this order:

1. Python SDK for Python agents, backend services, notebooks, and workflow
   runners.
2. TypeScript SDK for web products, Node services, and TypeScript agents.
3. Boltz CLI for shell-based agents such as Codex-style environments.
4. Local open-source CLI execution only when the runtime has the required model
   files, dependencies, hardware, and disk budget.

When the agent is running inside a shell environment, it may use the CLI because
the CLI is inspectable, scriptable, and easy to log. When the agent is running
inside an application or long-lived service, prefer an SDK so job submission,
polling, retries, and artifact download are explicit in code.

Do not assume local Boltz execution is available. Before using a local CLI,
verify the executable, model assets, runtime dependencies, GPU or CPU
requirements, writable output directory, and expected runtime.

## Agent Responsibilities

The agent owns orchestration and interpretation, not the scientific model
output.

The agent may:

- translate a natural-language objective into a structured Boltz input,
- choose whether to use the API, SDK, or CLI based on the runtime,
- submit Boltz jobs,
- poll or wait for completion,
- download and catalog artifacts,
- parse structures, confidence metrics, affinity metrics, and error reports,
- compare outputs against the design objective,
- choose the next candidate, edit, or experiment,
- write a reflection grounded in the returned artifacts and metrics.

The agent must not:

- fabricate pLDDT, pTM, ipTM, PAE, affinity, or confidence values,
- treat an LLM estimate as a Boltz result,
- silently continue after a failed Boltz job,
- discard failed job logs that explain why a model call failed,
- replace a model output artifact with a manually edited structure,
- use stale artifacts from a previous candidate as if they belonged to the
  current candidate.

## Standard Job Flow

Use this flow for each Boltz-backed scientific step:

1. Build a structured input from the objective, sequence, chains, ligands,
   constraints, templates, and any experimental conditions.
2. Record the input payload or write it to an input file.
3. Submit the job through the preferred interface.
4. Record the job id, command, model name, model version when available, and
   timestamp.
5. Poll or wait until the job reaches a terminal state.
6. If the job succeeds, download or locate all result artifacts.
7. If the job fails, preserve the error payload, logs, and input that produced
   the failure.
8. Parse metrics and artifact paths into a machine-readable result.
9. Validate that the returned artifacts belong to the candidate being evaluated.
10. Use the result to decide whether to stop, screen, verify, or plan the next
    candidate.

Every successful result should have both human-readable and machine-readable
evidence. A sentence saying "Boltz looked good" is not enough.

## Canonical Artifacts

Use mmCIF as the canonical structure artifact when Boltz returns structures.

PDB exports may be useful for visualization or compatibility, but the agent
should prefer mmCIF for downstream analysis because it preserves richer
structure metadata and avoids fixed-width PDB limitations.

For each candidate, preserve:

- candidate id,
- input payload or input file path,
- sequence and chain definitions,
- ligand, cofactor, or complex definitions when present,
- output structure path,
- confidence and affinity metric files,
- model logs or job metadata,
- sampling seed or stochastic configuration when available.

For structure confidence, prefer values read from model outputs or structure
annotations. Do not recompute confidence with an LLM.

## Expected Result Shape

Represent a Boltz result as a structured record similar to:

```json
{
  "candidate_id": "cand_001",
  "interface": "api",
  "job_id": "job_123",
  "status": "succeeded",
  "model_name": "boltz",
  "model_version": "unknown",
  "input_uri": "runs/run_001/cand_001/input.yaml",
  "artifacts": [
    {
      "uri": "runs/run_001/cand_001/structure.cif",
      "kind": "structure",
      "format": "mmcif"
    },
    {
      "uri": "runs/run_001/cand_001/confidence.json",
      "kind": "confidence_metrics",
      "format": "json"
    }
  ],
  "metrics": {
    "plddt": 0.82,
    "ptm": 0.71,
    "iptm": 0.64,
    "pae_mean": 5.9
  },
  "metadata": {
    "sampling_seed": 1843221090
  }
}
```

Use `status: "failed"` with an error payload when the model call fails. Failed
model calls are still useful scientific and operational evidence.

## API Or SDK Usage

When using the Boltz API through an SDK:

- keep the input schema explicit,
- submit one job per candidate or per documented batch unit,
- poll with bounded retries and clear timeout handling,
- download artifacts into a durable run directory,
- store the job id and artifact URIs,
- distinguish model failure from transport failure,
- avoid retrying indefinitely if the input itself is invalid.

SDK usage is preferred when the agent is part of an application because it makes
job state and artifact handling explicit.

## CLI Usage

When using a CLI:

- check that the executable is available before planning the run,
- write the model input to a stable file path,
- run the command with a stable output directory,
- capture stdout, stderr, exit code, and runtime,
- parse files from the output directory instead of scraping terminal text,
- treat nonzero exit codes as failed model calls,
- preserve the exact command for reproducibility.

For local open-source Boltz prediction workflows, the agent should expect a
command shape like:

```bash
boltz predict input.yaml --out_dir runs/run_001/cand_001
```

The upstream Boltz prediction docs describe the current CLI as
`boltz predict <INPUT_PATH> [OPTIONS]`; `<INPUT_PATH>` can be a YAML or FASTA
file, with YAML preferred. Use a fresh Python 3.11/3.12 environment for local
Boltz because the package pulls scientific and ML wheels. In this repository,
the harness setup path is:

```bash
PYTHON=/opt/homebrew/bin/python3.11 ./scripts/novacore setup-boltz --package boltz
```

For local BoltzGen workflows, the agent should expect a command family like:

```bash
boltzgen configure ...
boltzgen run ...
boltzgen execute ...
```

The exact flags can change between releases. The agent should inspect installed
help output or pinned project documentation before executing a new command.

## Batching

Batch generation can be useful before selecting a candidate, but the agent must
keep batch and candidate semantics separate.

When a batch is used:

- record the batch input and batch job id,
- preserve per-candidate artifact paths and metrics,
- rank candidates with explicit criteria,
- record which candidate was selected and why,
- keep enough evidence to revisit non-selected candidates later.

Do not merge several competing candidates into one candidate record.

## Downstream Screening

Boltz outputs can feed downstream screening and verification tools. Those tools
should consume structure and metric artifacts, not an LLM summary of them.

Examples of downstream uses:

- structural confidence filtering,
- binding-site confidence checks,
- affinity or interaction ranking,
- topology or contact-map scoring,
- human visualization and review.

Screening and verification outputs should be stored as their own results while
linking back to the Boltz artifacts they consumed.

## Stop Conditions

Boltz metrics are evidence for a decision, not an automatic stop condition.

The agent should stop only when the configured objective is satisfied, the loop
budget is exhausted, a human stops the run, or a blocking failure requires human
input. A high confidence score can support stopping, but the agent should still
check the task's explicit goals.

## Reproducibility

Every Boltz-backed step should preserve:

- interface used: API, Python SDK, TypeScript SDK, or CLI,
- model name and version when available,
- input payload or file,
- command or SDK method name,
- job id when available,
- candidate id,
- output artifact URIs,
- metric values,
- sampling seed or stochastic settings,
- error logs for failed calls,
- timestamp and runtime.

This metadata lets later agents, scientists, and humans audit what happened and
rerun the same scientific step when the environment allows it.
