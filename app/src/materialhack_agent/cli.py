from __future__ import annotations

import argparse
import json

from materialhack_memory import to_jsonable

from materialhack_agent.application import MaterialHackAgentApp


DEFAULT_OBJECTIVE = "design a protein that binds Zn2+ at pH 5 and can polymerize"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Novacore protein-design agent app.")
    parser.add_argument(
        "objective",
        nargs="?",
        default=DEFAULT_OBJECTIVE,
        help="natural-language protein-design objective",
    )
    parser.add_argument(
        "--seed-count",
        type=int,
        default=5,
        help="number of pre-loop seed candidates to generate and rank",
    )
    parser.add_argument(
        "--loops",
        type=int,
        default=2,
        help="number of post-loop_0 optimization loops to run",
    )
    parser.add_argument(
        "--target-score",
        type=float,
        default=0.8,
        help="target TRS score stored on the design objective",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=7,
        help="deterministic seed for offline stub candidate generation",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the memory visualization snapshot as JSON",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = MaterialHackAgentApp()
    result = app.run(
        args.objective,
        seed_count=args.seed_count,
        loop_count=args.loops,
        target_score=args.target_score,
        rng_seed=args.seed,
    )

    if args.json:
        print(json.dumps(to_jsonable(result.snapshot), indent=2, sort_keys=True))
        return

    seed = result.seed_flow.selected_seed
    decision = result.seed_flow.decision
    active_node = next(node for node in result.snapshot.nodes if node.is_active)

    print("Novacore Agent App")
    print(f"  run_id          : {result.run_id}")
    print(f"  seed_pool_id    : {result.seed_flow.pool.pool_id}")
    print(f"  selected_seed   : {seed.seed_candidate_id} ({seed.origin.value})")
    print(f"  seed_rank       : {', '.join(decision.ranked_seed_candidate_ids)}")
    print(f"  active_loop     : {active_node.loop_id} (index={active_node.index})")
    print(f"  sequence_length : {active_node.sequence_length}")
    print(f"  latest_metrics  : {dict(sorted(active_node.latest_metrics.items()))}")

    if result.runner_result is not None:
        print(f"  loops_completed : {result.runner_result.loops_completed}")
        print(f"  stop_reason     : {result.runner_result.stop_reason.value}")
