# MaterialHack-TheSeven

This repository is being assembled into a Codex-operated agentic
protein-design system across several owned branches. The current integration
shape is:

- `memory/`: durable run, seed-selection, loop, rollback, and visualization
  records.
- `loop_runner/`: LangGraph optimization runner after `loop_0`.
- `app/`: Novacore composition package, harness, FastAPI workbench API, and
  React memory UI.
- `plugins/materialhack-novacore/`: repo-local Codex plugin skill that tells
  Codex how to operate the harness.
- `trs/`: Topological Reorganization Score implementation used by the screening
  adapter.
- `BOLTZ_MODELS.md`: model-usage guidance for Boltz-family generation and
  evaluation adapters.

Codex chat is the orchestrator and agent-of-record. The harness is the execution
and memory substrate: it enforces WF-style seed sourcing, durable `loop_0`
memory, Boltz evidence, TRS scoring, verifier evidence or pending status,
reflection completeness, and finalization order. In normal plugin operation the
harness must not privately choose post-seed mutations; Codex submits each loop
plan after reading memory.

Subagents may be used inside a loop as read-only advisors, such as mutation
planner, structure/metric critic, skeptic, or safety reviewer. They do not call
memory-changing endpoints directly. The main Codex agent synthesizes advisory
reports, chooses one bounded mutation, submits `/agent-loops`, requests
evaluations, writes reflection, and finalizes.

Run a preflight from the repository root:

```bash
./scripts/novacore preflight
```

Set up local Boltz CLI tooling in a Python 3.11 project venv:

```bash
PYTHON=/opt/homebrew/bin/python3.11 ./scripts/novacore setup-boltz --package boltz
```

Start the chat-operated workflow with the live memory workbench:

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

`--workbench --chat-agent` starts a fresh local Novacore API, starts the
memory-only React workbench on an available local port, opens the browser
directly to the new run's `/memory/:runId` route, creates `loop_0`, preserves
the requested post-seed loop budget in memory, and returns. Codex then operates
each requested loop through `/api/runs/:runId/agent-context`,
`/api/runs/:runId/agent-loops`, `/evaluations`, `/reflection`, and `/finalize`.
Use `--no-open-browser` only for headless runs.

The automatic loop runner still exists for regression and offline smoke tests.
Omit `--chat-agent` only when deliberately testing that deterministic internal
runner.

`--enable-external-tools` makes loop Boltz evaluation live. Novacore tries the
hosted Boltz API first when `BOLTZ_API_KEY` is configured and the `boltz-api`
SDK is installed, then stores returned job metadata, confidence metrics, and
downloaded structure/archive artifacts. If no key is configured, it falls back
to local `boltz predict`. Add `--no-boltz-api` only when local Boltz should be
used despite a configured key. Add `--boltz-use-msa-server` when Boltz should
use online MSA generation instead of single-sequence mode.

For manual UI debugging, run the memory workbench with two processes:

```bash
cd app
PYTHONPATH=../memory/src:../loop_runner/src:src \
python3 -m uvicorn materialhack_agent.workbench_api:app --port 8000
```

```bash
cd app/web
npm install
npm run dev
```

Open `http://localhost:5173`. The web UI is memory-only. Each loop page exposes
Boltz artifacts, TRS totals/components/weights, verifier output or pending
verifier status, reflection, lineage, and rollback markers.

If `BOLTZ_API_KEY` is not set, the workbench prompts for a Boltz API key. Keys
entered there are stored only in the local FastAPI process and are returned to
the browser as masked status values. A fresh workbench/API process uses that
key for Boltz API jobs before trying local Boltz.
