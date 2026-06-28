from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import materialhack_agent.novacore as novacore
from materialhack_agent.novacore import NovacoreBoltzCliEvaluator, NovacoreToolConfig, TouchstoneVerifierAdapter
from materialhack_memory import (
    AgentLoopContext,
    ArtifactRef,
    ConditionSet,
    DesignObjective,
    EvaluationKind,
    EvaluationResult,
    LoopRecord,
    MetricValue,
    ProteinCandidate,
)


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
    monkeypatch.delenv("BOLTZ_API_KEY", raising=False)
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
    monkeypatch.delenv("BOLTZ_API_KEY", raising=False)
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


def test_boltz_api_key_prefers_hosted_api_and_downloads_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("BOLTZ_API_KEY", "bapi_secret_value")
    monkeypatch.setattr(novacore, "ROOT", tmp_path)
    submitted: dict[str, object] = {}

    class FakePrediction:
        id = "pred_123"
        status = "succeeded"

        def model_dump(self, **kwargs):
            return {
                "id": self.id,
                "status": self.status,
                "model": "boltz-2.1",
                "output": {
                    "best_sample": {
                        "metrics": {
                            "structure_confidence": 0.83,
                            "complex_plddt": 0.84,
                            "ptm": 0.72,
                            "iptm": 0.61,
                        },
                        "structure": {"url": "https://example.test/structure.cif"},
                    },
                    "archive": {"url": "https://example.test/archive.zip"},
                },
            }

    class FakeStructureAndBinding:
        def start(self, **kwargs):
            submitted.update(kwargs)
            return FakePrediction()

        def retrieve(self, prediction_id):
            raise AssertionError(f"retrieve should not be needed for terminal start response: {prediction_id}")

    class FakeBoltz:
        def __init__(self):
            pass

        predictions = SimpleNamespace(structure_and_binding=FakeStructureAndBinding())

    class FakeUrlResponse:
        def __init__(self, body: bytes):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return self.body

    def fake_urlopen(url, timeout):
        if url.endswith("structure.cif"):
            return FakeUrlResponse(b"data_prediction\n")
        if url.endswith("archive.zip"):
            return FakeUrlResponse(b"zip-bytes")
        raise AssertionError(url)

    monkeypatch.setitem(sys.modules, "boltz_api", SimpleNamespace(Boltz=FakeBoltz))
    monkeypatch.setattr(novacore.urllib.request, "urlopen", fake_urlopen)
    evaluator = NovacoreBoltzCliEvaluator(
        NovacoreToolConfig(
            boltz_command="definitely-not-boltz",
            enable_external_tools=True,
            boltz_api_poll_interval_seconds=0.01,
        )
    )

    result = evaluator.evaluate(_context(), _loop())

    metrics = result.metric_map()
    assert result.evaluator_name == "novacore-boltz-api"
    assert result.metadata["interface"] == "api"
    assert result.metadata["job_id"] == "pred_123"
    assert result.passed is True
    assert metrics["plddt"] == 0.84
    assert metrics["boltz_confidence"] == 0.83
    assert submitted["model"] == "boltz-2.1"
    assert submitted["idempotency_key"] == "novacore:run_live:loop_1"
    assert submitted["input"]["entities"][0]["msa"] == {"type": "empty"}
    assert any(artifact.kind == "boltz_prediction" and artifact.sha256 for artifact in result.artifacts)
    assert any(artifact.kind == "boltz_archive" and artifact.sha256 for artifact in result.artifacts)
    serialized = json.dumps(novacore._read_json(tmp_path / "artifacts" / "run_live" / "loop_1" / "boltz" / "boltz_api_result.json"))
    assert "bapi_secret_value" not in serialized
    assert "bapi_secret_value" not in json.dumps(result.metadata)


def test_touchstone_verifier_parses_structured_cli_result(tmp_path, monkeypatch):
    fake_touchstone = tmp_path / "bin" / "touchstone"
    fake_touchstone.parent.mkdir()
    fake_touchstone.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_touchstone.chmod(0o755)
    structure_path = tmp_path / "prediction.cif"
    structure_path.write_text("data_prediction\n", encoding="utf-8")
    monkeypatch.setattr(novacore, "ROOT", tmp_path)

    def fake_run(command, **kwargs):
        assert command[:3] == [str(fake_touchstone), "verify", str(structure_path)]
        assert "--metal" in command
        assert "Zn2+" in command
        assert kwargs["stdin"] is subprocess.DEVNULL
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({
                "consensus": "trust",
                "stack": [
                    {
                        "label": "geometry",
                        "status": "ran",
                        "score": 0.93,
                        "metrics": {"strain_sigma": 0.3, "cn": 4},
                    }
                ],
            }),
            stderr="",
        )

    monkeypatch.setattr(novacore.subprocess, "run", fake_run)
    boltz_evaluation = EvaluationResult(
        kind=EvaluationKind.BOLTZ,
        evaluator_name="novacore-boltz-cli",
        artifacts=(ArtifactRef(uri=str(structure_path), kind="boltz_prediction", format="mmcif"),),
    )
    screening_evaluation = EvaluationResult(
        kind=EvaluationKind.SCREENING,
        evaluator_name="trs",
        metrics=(MetricValue(name="trs_total", value=0.82, higher_is_better=True),),
    )
    verifier = TouchstoneVerifierAdapter(
        NovacoreToolConfig(
            enable_external_tools=True,
            touchstone_command=str(fake_touchstone),
            touchstone_use_uvx=False,
        )
    )

    result = verifier.evaluate(_context(), _loop(), boltz_evaluation, screening_evaluation)

    metrics = result.metric_map()
    assert result.evaluator_name == "touchstone"
    assert result.passed is True
    assert metrics["touchstone_consensus_score"] == 1.0
    assert metrics["touchstone_geometry_score"] == 0.93
    assert metrics["touchstone_geometry_strain_sigma"] == 0.3
    assert result.metadata["consensus"] == "trust"
    assert any(artifact.kind == "touchstone_result" and artifact.sha256 for artifact in result.artifacts)


def test_touchstone_verifier_records_needs_structure_without_boltz_prediction():
    boltz_evaluation = EvaluationResult(
        kind=EvaluationKind.BOLTZ,
        evaluator_name="novacore-boltz-cli",
    )
    screening_evaluation = EvaluationResult(kind=EvaluationKind.SCREENING, evaluator_name="trs")
    verifier = TouchstoneVerifierAdapter(NovacoreToolConfig(enable_external_tools=True))

    result = verifier.evaluate(_context(), _loop(), boltz_evaluation, screening_evaluation)

    assert result.evaluator_name == "touchstone-pending"
    assert result.passed is None
    assert result.metadata["adapter_status"] == "needs_structure"


def test_touchstone_parser_extracts_consensus_from_rendered_output():
    payload = novacore._parse_touchstone_stdout("verifier table\nconsensus: TRUST  (CN 6)\n")

    assert payload == {"consensus": "trust", "source": "rendered_stdout"}
    assert [metric.value for metric in novacore._metrics_from_touchstone(payload)] == [1.0]
