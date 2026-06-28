from __future__ import annotations

from argparse import Namespace
import subprocess

from materialhack_agent import harness
from materialhack_agent.harness import _workbench_run_payload
from materialhack_memory import MetricGoal


def test_workbench_run_payload_uses_parsed_objective_and_loop_contract():
    args = Namespace(
        objective="design a 72 residue protein that binds Zn2+ at pH 5 and can polymerize",
        seed_count=4,
        seed_source=["ccdc_csd", "de_novo"],
        target_score=0.85,
        loops=3,
        seed=11,
        chat_agent=False,
        run_mode="seed_and_loop",
    )
    goals = (
        MetricGoal(name="trs_total", comparator="gte", target=0.8, weight=1.0),
        MetricGoal(name="plddt", comparator="gte", target=0.7, weight=0.5),
    )

    payload = _workbench_run_payload(args, goals)

    assert payload["objective"] == args.objective
    assert payload["target"] == "ZN2+"
    assert payload["ph"] == 5.0
    assert payload["functions"] == ["bind", "polymerize"]
    assert payload["length"] == 72
    assert payload["seed_count"] == 4
    assert payload["seed_sources"] == ["ccdc_csd", "de_novo"]
    assert payload["target_score"] == 0.85
    assert payload["loop_count"] == 3
    assert payload["run_mode"] == "seed_and_loop"
    assert payload["rng_seed"] == 11
    assert [target["name"] for target in payload["optimization_targets"]] == ["trs_total", "plddt"]


def test_workbench_run_payload_chat_agent_uses_seed_only_without_losing_loop_budget():
    args = Namespace(
        objective="design a protein that binds Zn2+ at pH 5",
        seed_count=4,
        seed_source=["ccdc_csd"],
        target_score=0.85,
        loops=3,
        seed=11,
        chat_agent=True,
        run_mode="seed_and_loop",
    )

    payload = _workbench_run_payload(args, ())

    assert payload["loop_count"] == 3
    assert payload["run_mode"] == "seed_only"


def test_start_background_process_detaches_stdin(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(harness, "ROOT", tmp_path)

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        kwargs["stdout"].close()

    monkeypatch.setattr(harness.subprocess, "Popen", fake_popen)

    harness._start_background_process(
        ["python", "-m", "uvicorn"],
        name="api-9000",
        cwd=tmp_path,
        env={},
    )

    assert calls
    assert calls[0][1]["stdin"] is subprocess.DEVNULL
