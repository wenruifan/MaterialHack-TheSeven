---
name: verify-metal-binder
description: Verify a designed metal-binding protein before committing it to wet-lab. Use after a generator (BoltzGen, RFdiffusionAA→LigandMPNN, Chai) proposes a candidate metal-binder structure (.pdb/.cif), when deciding which of several designs are worth synthesizing, or when scoring/ranking a batch as a reward signal. Calls the touchstone verifier — geometry + bond-valence + CSD + physics/MLIP + co-fold consensus → trust/weak/defer.
allowed-tools: mcp__touchstone__verify_metal_binder, Bash(touchstone:*), Read
---

# Verify a designed metal binder

touchstone is generator-agnostic: it judges whether a generator's **predicted** metal-coordination site is real enough to make, returning a trust/weak/defer consensus across independent methods. Use it to triage candidates to wet-lab and to score them.

## When to use
- A generator proposed a metal-binder → verify the predicted site **before** wet-lab.
- Choosing which of N candidates to synthesize → rank, take only the `trust` set.
- Scoring designs as an RLVR / best-of-N reward.

## How to call it
Single structure — the MCP tool:
> `verify_metal_binder(structure_path="design.pdb", metal="Ni2+", deep=False)`

Or the CLI:
```bash
touchstone verify design.pdb --metal Ni2+     # instant: geometry + bond-valence + CSD
touchstone rank designs/*.pdb --metal Ni2+    # batch, best-first by reward
```

## Reading the verdict
- **trust** — every verifier that ran agrees the site is on-manifold → clears the wet-lab bar.
- **weak** — judgeable but not confidently sound → iterate, don't synthesize yet.
- **defer** — off-manifold, or a verifier couldn't run → reject / needs a different check.

Consensus is defense-in-depth: a single `defer` collapses it. **Only `trust` is worth wet-lab.** When `weak`/`defer`, name which verifier flagged it (geometry σ, bond-valence Δ, co-fold disagreement…) and what to change.

The result also carries a **`stack`** list — every tier in cost order with its `status` (`ran` / `skipped` / `needs_input`), so the consensus is fully auditable. Each ran verdict includes a **`metrics`** dict with the raw numbers behind it (`strain_sigma`, `bvs`/`delta`, MLIP `drift_angstrom`/`retention`) — threshold on those directly rather than parsing the reason string.

## Depth
- Default (instant, runs anywhere): geometry z-score vs CSD/PDB prior + bond-valence + CSD reference.
- `deep=True` / `--deep`: adds MLIP (MACE) relaxation + MD — **needs a GPU**. Route deep runs to a host that has one (a remote HTTP touchstone MCP), not the local machine.

## Stress / operating conditions
`stress=True` (`--stress`) adds a **robustness map** — re-verifies the site under extreme
conditions: `neutral` (as-is), `leachate` (bonds stretched — hot/acidic/saline), `low_pH`
(labile donors protonated off). Use it when the binder must survive a real recovery
process, not just stand still: a site can be `trust` at rest but `defer` under leachate.
Report which conditions hold and which break.

## Honest scope
The trust threshold is grounded in CSD geometry + physics, **not yet calibrated to wet-lab outcomes** — read `trust` as "physically / precedent-plausible," not a calibrated binding probability. The thermostability (TemStaPro) and CSD/Mogul tiers need a GPU / CSD licence; without them those tiers report as `not_run` rather than guessing.
