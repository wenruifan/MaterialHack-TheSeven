---
name: novacore-agent
description: Operate the MaterialHack Novacore protein-design workflow from Codex. Use when the user wants WF-style seed sourcing, durable loop memory, TRS screening, Boltz CLI setup/preflight, verifier output capture, rollback, branching, or the memory workbench UI.
---

# Novacore Agent

## Operating Contract

Codex chat is the Novacore orchestrator and agent-of-record. The repository
harness is the execution and memory substrate: it creates `loop_0`, runs
Boltz/TRS/verifier adapters, stores artifacts, validates loop completeness, and
serves the workbench. It must not privately choose optimization mutations for
agentic plugin runs.

In normal plugin operation, Codex must use chat-agent mode:

1. Start a seed-only workbench run. The harness sources/ranks seeds and writes
   the selected seed as `loop_0`.
2. For each requested post-`loop_0` loop, Codex reads the active agent context
   from memory.
3. Codex reasons in chat from the current sequence, objective, lineage,
   previous changes, Boltz metrics/artifacts, TRS result, verifier status,
   reflection, human notes, and optional read-only advisor reports.
4. Codex proposes exactly one bounded candidate change and writes the proposed
   candidate plus rationale into memory.
5. The harness evaluates that pending loop with Boltz, TRS, and verifier.
6. Codex reads the returned evidence, writes reflection and next actions, then
   finalizes or asks the user whether to rollback/branch when appropriate.
7. Codex must not propose another candidate while a chat-operated loop is
   pending, evaluating, awaiting reflection, or awaiting an accept/reject
   decision.

The automatic `run --workbench` loop runner still exists for regression and
offline smoke tests. Do not use it for agentic plugin runs unless the user
explicitly asks for the deterministic internal runner.

## Main-Agent And Advisor Boundary

Use subagents only as read-only advisors inside a loop. The main Codex chat
agent must remain the single writer and decision owner.

Good advisor roles:

- `mutation_planner`: propose candidate edits and tradeoffs.
- `structure_metric_critic`: interpret Boltz/TRS/verifier evidence.
- `skeptic`: identify failure modes, unsupported assumptions, and rollback
  triggers.
- `safety_reviewer`: flag unsafe or out-of-scope biological design directions.

Advisors may read the context supplied by the main agent and return structured
reports. They must not call `/agent-loops`, `/evaluations`, `/reflection`,
`/finalize`, or rollback endpoints directly. The main agent must synthesize
advisor reports, choose one bounded mutation, submit all memory-changing API
calls, and write the user-facing rationale.

Before starting a run, collect these inputs:

- Seed source: `ccdc_csd`, `de_novo`, or both.
- Objective and editable parameters: target, pH, required functions, sequence length, and seed count.
- Desired scores: at minimum `trs_total`; include Boltz confidence and verifier targets when relevant.
- Loop count. Codex should execute exactly this many chat-operated post-`loop_0`
  loops unless the user stops, changes the count, or requests rollback/branching.

Do not start a run until seed source, score targets, and loop count are explicit.

## Harness Commands

From the repository root:

```bash
./scripts/novacore preflight
```

Set up local Boltz CLI tooling in the project venv:

```bash
PYTHON=/opt/homebrew/bin/python3.11 ./scripts/novacore setup-boltz --package boltz
```

Set up hosted Boltz API tooling when cloud Boltz jobs are needed:

```bash
PYTHON=/opt/homebrew/bin/python3.11 ./scripts/novacore setup-boltz --package boltz-api
```

Start the live memory workbench in chat-agent mode:

```bash
./scripts/novacore run \
  "design a protein that binds Zn2+ at pH 5 and can polymerize" \
  --seed-source ccdc_csd \
  --seed-source de_novo \
  --metric-goal trs_total:gte:0.8:1.0 \
  --metric-goal plddt:gte:0.7:0.5 \
  --seed-count 5 \
  --loops 2 \
  --workbench \
  --chat-agent \
  --enable-external-tools \
  --boltz-accelerator cpu
```

This creates `loop_0`, preserves the requested loop budget in memory, opens the
workbench, and stops. Codex chat must operate every post-seed loop through the
local API printed by the harness.

Use `--json` when Codex needs machine-readable memory evidence.
Use `--enable-external-tools` for real Boltz evidence. In that mode, Novacore
writes a Boltz YAML input under `artifacts/<run>/<loop>/boltz/`, runs
`boltz predict`, captures stdout/stderr/exit code, parses returned confidence
JSON, and stores mmCIF/confidence/log artifact refs in loop memory. Add
`--boltz-use-msa-server` only when the user wants online MSA generation;
otherwise the CLI input uses `msa: empty` single-sequence mode.

## Chat-Operated Loop API

Use the `api` URL printed by the harness, for example `http://127.0.0.1:8005`.
For every loop:

1. Read context:

```bash
curl -fsS "$API/api/runs/$RUN_ID/agent-context"
```

2. Codex chooses one mutation and submits the pending loop:

```bash
curl -fsS -X POST "$API/api/runs/$RUN_ID/agent-loops" \
  -H 'Content-Type: application/json' \
  -d @plan.json
```

`plan.json` must include:

```json
{
  "author": "Codex",
  "parent_loop_id": "loop_...",
  "sequence": "FULLCANDIDATESEQUENCE",
  "candidate_name": "codex_loop_1",
  "planning_note": "Why Codex selected this one change after reading current evidence.",
  "advisor_reports": [
    {
      "advisor_name": "mutation-planner",
      "role": "mutation_planner",
      "summary": "Read-only advisory summary for the proposed edit.",
      "recommendations": ["E42H"],
      "concerns": ["Confirm Zn2+ geometry after Boltz/TRS evidence returns."],
      "confidence": 0.65
    },
    {
      "advisor_name": "skeptic",
      "role": "skeptic",
      "summary": "Read-only critique of the proposed edit.",
      "concerns": ["Do not infer loop success before evaluation."]
    }
  ],
  "change_set": {
    "summary": "Codex substitution E42H",
    "why": "Evidence-grounded rationale for this loop.",
    "author": "Codex",
    "changes": [
      {
        "operation": "substitute",
        "machine_diff": "E42H",
        "position": 42,
        "from_residue": "E",
        "to_residue": "H",
        "rationale": "Local rationale tied to target/objective/evidence.",
        "expected_effect": "Metric or structural effect Codex wants to test."
      }
    ]
  }
}
```

The workbench enforces one unresolved chat-operated pending loop per run. A new
candidate must normally use the active loop as its parent. Branching from a
non-active loop requires an explicit `branch_label` so the workbench can show
that the candidate continued from an earlier iteration rather than from the
current head.

3. Run harness evaluations for the pending loop and poll the returned job:

```bash
curl -fsS -X POST "$API/api/runs/$RUN_ID/agent-loops/$LOOP_ID/evaluations"
curl -fsS "$API/api/jobs/$JOB_ID"
```

4. Read memory/evidence again, then Codex writes reflection:

```bash
curl -fsS -X POST "$API/api/runs/$RUN_ID/agent-loops/$LOOP_ID/reflection" \
  -H 'Content-Type: application/json' \
  -d @reflection.json
```

`reflection.json` must include `next_actions`; otherwise finalization is
blocked by memory completeness checks. It may also include read-only
`advisor_reports` from post-evaluation critics. The main agent still owns the
reflection and finalization decision.

5. Finalize the loop only after Codex has reviewed the evaluations:

```bash
curl -fsS -X POST "$API/api/runs/$RUN_ID/agent-loops/$LOOP_ID/finalize"
```

If the candidate is evaluated and should not become active, reject it instead
of finalizing and rolling back through it:

```bash
curl -fsS -X POST "$API/api/runs/$RUN_ID/agent-loops/$LOOP_ID/reject" \
  -H 'Content-Type: application/json' \
  -d '{"actor":"Codex","reason":"Boltz confidence regressed versus the active parent."}'
```

Then read `/agent-context` again for the next loop. Do not submit another
candidate until the prior pending loop has been finalized, rejected, or
otherwise resolved.

The workbench exposes a per-user Boltz API key prompt when `BOLTZ_API_KEY` is
not configured. Keys entered there are stored only in the local FastAPI process
and returned to the browser only as masked status values. Do not ask users to
paste API keys into chat. The hosted Boltz evaluator still needs a dedicated
adapter before Novacore can submit cloud jobs through `boltz-api`.

## Workbench UI

When operating a run from Codex, prefer `--workbench --chat-agent`. The harness
starts a fresh local Novacore API, starts the memory-only web workbench on an
available local port, opens the browser directly to `/memory/:runId`, and
returns after `loop_0` is ready. Use `--no-open-browser` only for headless runs.

In Codex Desktop, when browser-control tools are available, also navigate the
in-app browser to the printed `memory_ui` URL as soon as the harness emits it,
so the user can watch loop memory update while the CLI keeps running.

For manual UI debugging, start the API:

```bash
PYTHONPATH=../memory/src:../loop_runner/src:src \
python3 -m uvicorn materialhack_agent.workbench_api:app --port 8000
```

Start the UI:

```bash
cd app/web
npm install
npm run dev
```

Open `http://localhost:5173`. The web UI is memory-only; it does not collect
run parameters or expose loop-control buttons. The memory page exposes:

- memory graph with active, available, pending, and abandoned loops,
- loop sequence and agent change plan,
- Boltz API key status and process-local key entry when `BOLTZ_API_KEY` is not
  set,
- Boltz artifacts and command/readiness metadata,
- TRS totals, raw totals, components, weights, and contact assumptions,
- verifier output or pending verifier status,
- reflection, human notes, rollback/reject markers, lineage, parent loop
  provenance, and branch labels when a candidate continues from an earlier
  loop iteration.

## Loop Procedure

For each requested loop, Codex chat must:

1. Read the active memory head and latest evaluations through `/agent-context`.
2. Optionally ask advisor subagents for read-only planning reports.
3. Synthesize advisor reports and plan exactly one bounded candidate change
   with an evidence-grounded rationale. Do not claim knowledge of future
   results.
4. Submit the full candidate sequence, `change_set`, and any advisory reports
   through `/agent-loops`.
5. Ask the harness to run Boltz/TRS/verifier through `/evaluations`.
6. Read the updated memory and compare the new evidence against the objective.
7. Optionally ask advisor subagents for read-only post-evaluation critique.
8. Write reflection with what worked, what failed, next actions, and any
   post-evaluation advisory reports.
9. Finalize the loop only after memory completeness checks pass, or reject the
   evaluated loop if it should remain evidence without becoming active.

Rollback must use the repository/API rollback path so later loops remain visible
as abandoned branches. New branch candidates from non-active loops must carry a
`branch_label`, and the workbench should make the parent loop and branch label
visible before any next-loop reasoning.
