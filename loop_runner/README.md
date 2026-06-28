# MaterialHack Loop Runner

LangGraph orchestration for optimization loops after `loop_0` already exists in
the memory repository.

The runner owns loop execution, not seed selection. WF can create the selected
seed and memory can persist `loop_0`; this package then reads an existing
`run_id`, creates exactly one derived candidate per loop, runs Boltz, screening,
and verifier adapters, writes reflection, and finalizes the loop through the
memory contract.

## First Slice

- `ProteinDesignLoopRunner.run_until_stop(run_id, max_loops=None)`
- `ProteinDesignLoopRunner.run_for_loops(run_id, loop_count)`
- `ProteinDesignLoopRunner.continue_from_loop(run_id, loop_id, loop_count=None)`

The default adapters are deterministic stubs. They are intentionally typed and
memory-compatible so tests can exercise the full write order before real Boltz,
screening, and verifier implementations are ready.

## Memory Write Order

Each completed optimization loop is written in this order:

1. `append_loop(...)`
2. attach Boltz evaluation
3. attach screening evaluation
4. attach verifier evaluation
5. set loop reflection
6. `finalize_loop(...)`

If an adapter fails after the pending loop exists, the runner records a
`loop_runner` human input on that pending loop and stops without finalizing it.
