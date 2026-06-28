# Novacore Harness And Workbench

This folder is the implementation package behind the repo-local Novacore Codex
plugin. Codex should operate the workflow through `../scripts/novacore`; the web
app is the memory and evidence workbench.

- Novacore pre-loop seed sourcing from either de novo generation or local
  CCDC/CSD ligand models in `../ligands_10000.zip`, followed by scoring,
  ranking, and selection.
- `memory/` durable seed-selection and loop memory, including `loop_0`.
- `loop_runner/` post-`loop_0` evaluation orchestration with Novacore adapters
  for Boltz CLI preparation, real TRS scoring, and pending verifier MCP status.

## Do We Have Enough?

Yes, for a chat-operated working agent loop. The repository now has enough
structure for Codex chat to own orchestration, planning, and reflection while
the harness owns seed selection, evidence generation, memory writes, and
completeness checks:

```text
objective
  -> pre-loop seed candidates from de novo or local CCDC/CSD ligand models
  -> seed ranking and selection
  -> memory SeedCandidatePool + SeedSelectionDecision
  -> durable loop_0
  -> Codex-authored pending loop proposals
  -> Boltz artifacts + TRS totals/components/weights + pending verifier record
  -> Codex-authored reflection and next actions
  -> memory visualization snapshot in the Novacore UI
```

Subagents can participate as read-only advisors. The main Codex agent supplies
context, collects advisor reports, chooses the single loop mutation, and remains
the only caller for memory-changing endpoints.

The remaining production gaps are adapter implementations, not orchestration
shape:

- Structure-backed TRS atom-table adapter once Boltz artifacts provide the
  before/after structures needed for exact 3D scoring. The current adapter calls
  the real TRS graph API from sequence-derived contact graphs.
- Verifier MCP server/high-fidelity oracle. Until that exists, Novacore records
  a `verifier-mcp-pending` evaluation so the UI can show the missing step.
- Structure-backed Boltz/TRS coupling. With `--enable-external-tools`,
  Novacore now runs live `boltz predict` CLI workflows and stores returned
  artifacts. TRS still uses the current graph adapter until the atom-table
  structure adapter is wired.
- Durable TuringDB-backed repository configuration for deployed runs.

## Boltz Protocol

The Boltz protocol stays at the repository root in
[`BOLTZ_MODELS.md`](../BOLTZ_MODELS.md) because it is a cross-cutting contract
for WF seed selection, memory artifact storage, TRS screening inputs, and the
post-`loop_0` runner. App adapters that call Boltz or BoltzGen should follow
that file's artifact, failure, metrics, and mmCIF conventions instead of
inventing an app-local protocol.

## Harness

From the repository root:

```bash
./scripts/novacore preflight
```

```bash
PYTHON=/opt/homebrew/bin/python3.11 ./scripts/novacore setup-boltz --package boltz
```

```bash
./scripts/novacore run \
  "design a protein that binds Zn2+ at pH 5 and can polymerize" \
  --seed-source ccdc_csd \
  --seed-source de_novo \
  --metric-goal trs_total:gte:0.8:1.0 \
  --seed-count 5 \
  --loops 2 \
  --workbench \
  --chat-agent \
  --enable-external-tools \
  --boltz-accelerator cpu
```

The run command works offline. Boltz output remains dry-run evidence unless
`--enable-external-tools` is set and a usable Boltz command is installed.
When external tools are enabled, Novacore writes Boltz YAML inputs under
`artifacts/<run>/<loop>/boltz/`, runs `boltz predict`, captures process logs,
parses confidence metrics, and links mmCIF/confidence artifacts into loop
memory. Add `--boltz-use-msa-server` to call the online MSA server; otherwise
the harness uses `msa: empty` single-sequence mode.
With `--workbench --chat-agent`, the harness starts a fresh local Novacore API,
starts the memory-only React workbench on an available local port, opens the
browser to `/memory/:runId`, creates `loop_0`, and returns. Codex chat then
submits each post-seed loop through the agent-loop API.

The automatic internal loop runner is still available by omitting
`--chat-agent`, but that path is for regression and offline smoke testing, not
normal plugin operation.

## Chat Agent API

The workbench API exposes a chat-owned loop flow:

- `GET /api/runs/{run_id}/agent-context` returns the active memory head,
  objective, lineage, prior changes, evaluations, reflection, and human notes.
- `POST /api/runs/{run_id}/agent-loops` appends one Codex-authored pending loop
  with the full candidate sequence, `change_set`, planning note, and optional
  read-only `advisor_reports`.
- `POST /api/runs/{run_id}/agent-loops/{loop_id}/evaluations` runs Boltz, TRS,
  and verifier adapters for that pending loop only.
- `POST /api/runs/{run_id}/agent-loops/{loop_id}/reflection` stores the
  Codex-authored reflection, required next actions, and optional post-evaluation
  `advisor_reports`.
- `POST /api/runs/{run_id}/agent-loops/{loop_id}/finalize` validates memory
  completeness and makes the loop active.

## Memory Workbench

For manual UI debugging, start the API:

```bash
PYTHONPATH=../memory/src:../loop_runner/src:src \
python3 -m uvicorn materialhack_agent.workbench_api:app --port 8000
```

Start the web UI from `app/web`:

```bash
npm install
npm run dev
```

Open `http://localhost:5173`. The Vite dev server proxies `/api` to the
FastAPI process on port `8000`. The web UI is memory-only: it renders the
latest run memory when one exists, or a memory empty state while Codex has not
started a run.

## Package Layout

- `src/materialhack_agent/harness.py` exposes preflight, Boltz setup, and run commands.
- `src/materialhack_agent/seed_flow.py` owns the Novacore-to-memory pre-loop handoff.
- `src/materialhack_agent/novacore.py` owns legacy deterministic planning and loop adapters.
- `src/materialhack_agent/trs_adapter.py` adapts sequence/structure evidence into real TRS results.
- `src/materialhack_agent/ligand_catalog.py` resolves local CCDC/CSD `.mol2` models from `ligands_10000.zip`.
- `src/materialhack_agent/application.py` composes seed flow and loop runner.
- `src/materialhack_agent/workbench_api.py` exposes the FastAPI workbench and chat-agent APIs.
- `src/materialhack_agent/observable_memory.py` emits workbench events from memory writes.
- `src/materialhack_agent/cli.py` exposes a runnable command.
- `../plugins/materialhack-novacore/skills/novacore-agent/` contains the installable Codex plugin skill.
- `web/` contains the React workbench.
- `tests/` validates the end-to-end handoff and loop execution.
