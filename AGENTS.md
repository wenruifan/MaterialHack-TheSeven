# Agent and Branch Coordination

This repository has multiple collaborators working on different parts of the
protein-design agent. Keep branch ownership explicit so work can be merged
without accidentally overwriting another collaborator's direction.

## Branch Ownership

| Branch | Owner | Scope | Status |
| --- | --- | --- | --- |
| `main` | Team | Stable shared base | Keep minimal until branches are reviewed and merged. |
| `memory` | M-priv (Michael) | Durable memory abstraction for protein-design runs, loop records, rollback, branching, future TuringDB integration, and durable seed-selection evidence. | Active. Memory implementation is merged to `origin/main`; branch remains the ownership line for follow-up memory changes. |
| `WF` | WenruiFan | LangGraph agentic protein-design pipeline, temporary pre-loop candidate flow, stubbed generation/screening/verifier nodes. | Owns temporary pre-loop seed sourcing, scoring, and ranking before handoff to memory. |
| `TRS` | Team | Topological Reorganization Score implementation for protein-metal binding structure scoring and explanation. | Remote branch. Candidate screening tool that should consume generated or retrieved structure artifacts. |
| `loop-runner` | M-priv (Michael) | Runner orchestration above memory after `loop_0`, including planner, generator, Boltz, screening, verifier, and reflection adapter boundaries. | Local branch/worktree scope observed; not present on `origin` yet. |
| `codex/define-boltz-model-usage` | M-priv (Michael) | Documents how the agentic system should use Boltz-family generation, structure, and evaluation models across WF, memory, TRS, and runner branches. | Active documentation branch. See `BOLTZ_MODELS.md`. |
| `codex/package-agent-app` | M-priv (Michael) | App composition layer that packages WF-style seed handoff, durable memory, loop runner, and future TRS/verifier adapter slots into a runnable agentic system. | Active integration branch. Additive only while owned branches remain separate. |
| `codex/agent-plugin-workflow` | M-priv (Michael) | Codex plugin and harness control plane for operating the WF-style framework from `main`, with memory workbench UI, TRS loop evidence, Boltz setup/preflight, and verifier adapter evidence. | Active integration branch. Additive; supersedes app-first operation with plugin/harness operation. |

Update this table when a collaborator takes ownership of a new branch or when a
branch changes scope.

## Current Memory Scope

The `memory` branch owns the durable memory contract:

- `loop_0` is the selected seed candidate for an optimization run.
- `loop_1...n` are one-candidate optimization checkpoints.
- Each loop stores sequence, artifacts, changes, rationale, evaluations,
  reflection, human input, parent lineage, and lifecycle status.
- Rollback changes the active loop head and preserves later loops as abandoned
  branches.
- TuringDB should be attached behind the repository interface after the memory
  abstraction is stable.

## Agreed Item

Issue #1 tracks pre-loop seed-selection memory:

https://github.com/wenruifan/MaterialHack-TheSeven/issues/1

M-priv (Michael) and WenruiFan agreed to split the responsibility across the
`WF` and `memory` branches.

Accepted boundary:

- `WF` branch can provide temporary working memory for sourcing, screening, and
  ranking candidate seeds.
- `memory` branch should persist the selected seed decision and enough evidence
  to restart from alternate seeds later.
- Optimization loops remain one candidate per loop after `loop_0` for now.

## Coordination Rules

- Do not change another branch's owned scope without discussing it first.
- Prefer additive integration points over rewrites while branches are separate.
- Keep issue links in commits or PR descriptions when work implements a tracked
  design decision.
- If a branch starts depending on another branch's API, document the expected
  interface in this file or the relevant README before wiring it in.

## Boltz Model Usage

The LLM-facing Boltz usage skill is documented in `BOLTZ_MODELS.md`. Use it when
an agent needs to decide how to call Boltz models, preserve Boltz artifacts, or
interpret Boltz outputs.
