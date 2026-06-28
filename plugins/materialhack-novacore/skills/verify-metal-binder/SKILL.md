---
name: verify-metal-binder
description: Verify a designed metal-binding protein with Touchstone before wet-lab commitment. Use after Boltz, RFdiffusionAA/LigandMPNN, Chai, or another generator proposes a candidate structure, when ranking designs, or when using verifier score as a loop reward.
---

# Verify a Designed Metal Binder

Touchstone is generator-agnostic. It judges whether a generator's predicted
metal-coordination site is plausible enough to make, returning a
trust/weak/defer consensus across independent methods.

## When To Use

- A generator proposed a metal-binder structure and the design needs verifier
  evidence before wet-lab.
- Several candidates need ranking; keep only the `trust` set as synthesis
  candidates.
- A design loop needs a reward signal beyond Boltz confidence and TRS topology.

## How Novacore Calls It

Novacore runs Touchstone from the verifier stage after Boltz produces a real
`.cif`, `.mmcif`, or `.pdb` structure artifact:

```bash
touchstone verify design.cif --metal Zn2+
```

If the `touchstone` CLI is not installed and `uvx` is available, Novacore can
fall back to the verifier PR package:

```bash
uvx --from 'touchstone[mcp] @ git+https://github.com/charleneleong-ai/ai4science.git#subdirectory=touchstone' \
  touchstone verify design.cif --metal Zn2+
```

Use `--touchstone-deep` for MLIP relaxation/MD checks on GPU-capable hosts.
Use `--touchstone-stress` for neutral/leachate/low-pH robustness maps.

## Reading The Verdict

- `trust`: every verifier that ran agrees the site is on-manifold.
- `weak`: judgeable but not confidently sound; iterate before synthesis.
- `defer`: off-manifold or missing required evidence; reject or rerun with a
  different/deeper check.

Consensus is defense-in-depth: a single `defer` collapses it. Only `trust`
should be treated as wet-lab ready.

The result can include a `stack` list. Each tier reports `status`
(`ran`, `skipped`, or `needs_input`) and raw metrics such as `strain_sigma`,
bond-valence `delta`, `nvecsum`, `angle_rmsd_deg`, MLIP `drift_angstrom`, and
MD `retention`. Prefer these structured metrics over parsing reason strings.

## Scope

The trust threshold is grounded in CSD geometry and physics, not calibrated
wet-lab binding probability. Tiers needing GPU, CSD/Mogul licensing, apo
structures, or scorer services must report `needs_input` rather than guessing.
