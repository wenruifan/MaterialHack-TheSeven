from __future__ import annotations

import json
import subprocess
from pathlib import Path

import materialhack_agent.novacore as novacore
from materialhack_agent.novacore import NovacoreBoltzCliEvaluator, NovacoreToolConfig
from materialhack_memory import AgentLoopContext, ConditionSet, DesignObjective, LoopRecord, ProteinCandidate


def _context() -> AgentLoopContext:
    return AgentLoopContext(
        run_id="run_live",
        active_loop_id="loop_0",
        parent_loop_id=None,
        loop_index=0,
        sequence="ACDEFGHIKLMNPQRSTVWY",
        conditions=ConditionSet.common(binding_target="ZN2+", ph=5.0),
        objective=DesignObjective(description="design a zinc-binding protein"),
        latest_metrics={},
        lineage_loop_ids=("loop_0",),
        previous_change_sets=(),
        evaluations=(),
        reflection=novacore.LoopReflection(),
        human_inputs=(),
    )


def _loop() -> LoopRecord:
    return LoopRecord(
        run_id="run_live",
        loop_id="loop_1",
        index=1,
        parent_loop_id="loop_0",
        candidate=ProteinCandidate(sequence="ACDEFGHIKLMNPQRSTVWY", name="candidate"),
        conditions=ConditionSet.common(binding_target="ZN2+", ph=5.0),
    )


def test_live_boltz_cli_parses_output_artifacts(tmp_path, monkeypatch):
    fake_boltz = tmp_path / "bin" / "boltz"
    fake_boltz.parent.mkdir()
    fake_boltz.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_boltz.chmod(0o755)
    monkeypatch.setattr(novacore, "ROOT", tmp_path)

    def fake_run(command, **kwargs):
        out_dir = tmp_path / "artifacts" / "run_live" / "loop_1" / "boltz" / "output"
        prediction_dir = out_dir / "boltz_results_input" / "predictions" / "input"
        prediction_dir.mkdir(parents=True)
        (prediction_dir / "input_model_0.cif").write_text("data_input\n", encoding="utf-8")
        (prediction_dir / "confidence_input_model_0.json").write_text(
            json.dumps({
                "confidence_score": 0.81,
                "ptm": 0.71,
                "iptm": 0.62,
                "complex_plddt": 0.82,
            }),
            encoding="utf-8",
        )
        (prediction_dir / "plddt_input_model_0.npz").write_bytes(b"npz")
        assert str(fake_boltz) == command[0]
        assert "--accelerator" in command
        assert "cpu" in command
        assert "--model" in command
        assert "boltz2" in command
        assert kwargs["stdin"] is subprocess.DEVNULL
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(novacore.subprocess, "run", fake_run)
    evaluator = NovacoreBoltzCliEvaluator(
        NovacoreToolConfig(
            boltz_command=str(fake_boltz),
            enable_external_tools=True,
            boltz_accelerator="cpu",
            boltz_model="boltz2",
        )
    )

    result = evaluator.evaluate(_context(), _loop())

    metrics = result.metric_map()
    assert result.metadata["external_status"] == "succeeded"
    assert metrics["plddt"] == 0.82
    assert metrics["ptm"] == 0.71
    assert metrics["iptm"] == 0.62
    assert any(artifact.kind == "boltz_prediction" and artifact.sha256 for artifact in result.artifacts)
    assert any(artifact.kind == "boltz_confidence" and artifact.sha256 for artifact in result.artifacts)
    input_artifact = next(artifact for artifact in result.artifacts if artifact.kind == "boltz_input")
    assert "msa: empty" in Path(input_artifact.uri).read_text(encoding="utf-8")


def test_live_boltz_cli_missing_command_records_failure_without_metrics(tmp_path, monkeypatch):
    monkeypatch.setattr(novacore, "ROOT", tmp_path)
    evaluator = NovacoreBoltzCliEvaluator(
        NovacoreToolConfig(
            boltz_command="definitely-not-boltz",
            enable_external_tools=True,
        )
    )

    result = evaluator.evaluate(_context(), _loop())

    assert result.metrics == ()
    assert result.passed is None
    assert result.metadata["external_status"] == "boltz_cli_not_found"
    assert "not found" in str(result.metadata["error"])
    assert any(artifact.kind == "boltz_cli_result" for artifact in result.artifacts)
