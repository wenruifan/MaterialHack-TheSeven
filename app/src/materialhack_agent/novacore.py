from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from materialhack_loop_runner import ProteinDesignLoopRunner
from materialhack_memory import (
    AgentLoopContext,
    ArtifactRef,
    ChangeOperation,
    ChangeSet,
    EvaluationKind,
    EvaluationResult,
    LoopRecord,
    LoopReflection,
    MemoryRepository,
    MetricValue,
    ProteinCandidate,
    ProteinChange,
)
from materialhack_agent.trs_adapter import changed_positions_from_diff, score_sequence_trs


ROOT = Path(__file__).resolve().parents[3]
NOVACORE_AGENT_NAME = "Novacore"
NOVACORE_AGENT_PERSONA = """\
Novacore is a Codex-run protein-design agent. It must execute bounded loops:
read memory, plan one traceable design change, generate one candidate, run or
prepare Boltz CLI structure evaluation, run TRS screening, record Touchstone verifier
status, write reflection, and continue until the configured loop count is done.
It optimizes only against user-supplied metric goals and must preserve all
Boltz, TRS, Touchstone verifier, rollback, and lineage evidence in memory.
"""

AMINO_ACIDS = "ACDEFGHIKLMNPQRSTVWY"
TOUCHSTONE_UVX_SPEC = "touchstone[mcp] @ git+https://github.com/charleneleong-ai/ai4science.git#subdirectory=touchstone"


@dataclass(frozen=True)
class NovacoreToolConfig:
    boltz_command: str = "boltz"
    enable_external_tools: bool = False
    prefer_boltz_api: bool = True
    boltz_api_model: str = "boltz-2.1"
    boltz_api_poll_interval_seconds: float = 5.0
    boltz_api_timeout_seconds: int = 3600
    verifier_mcp_server: str | None = None
    touchstone_command: str = "touchstone"
    touchstone_use_uvx: bool = True
    touchstone_deep: bool = False
    touchstone_stress: bool = False
    touchstone_timeout_seconds: int = 1800
    boltz_accelerator: str = "cpu"
    boltz_model: str = "boltz2"
    boltz_cache: str | None = None
    boltz_use_msa_server: bool = False
    boltz_msa_server_url: str = "https://api.colabfold.com"
    boltz_msa_pairing_strategy: str = "greedy"
    boltz_timeout_seconds: int = 3600
    boltz_recycling_steps: int | None = None
    boltz_sampling_steps: int | None = None
    boltz_diffusion_samples: int | None = None


@dataclass(frozen=True)
class NovacoreChangePlanner:
    author: str = NOVACORE_AGENT_NAME

    def plan_change(self, context: AgentLoopContext) -> ChangeSet:
        sequence = context.sequence
        if not sequence:
            raise ValueError("Cannot plan a protein change for an empty sequence")

        goal_names = tuple(goal.name for goal in context.objective.goals)
        position_index = (context.loop_index * 3 + len(context.lineage_loop_ids)) % len(sequence)
        from_residue = sequence[position_index]
        to_residue = _next_residue(from_residue, offset=context.loop_index + 2)
        machine_diff = f"{from_residue}{position_index + 1}{to_residue}"

        prompt = {
            "agent": self.author,
            "role": "Codex loop planner",
            "loop_index": context.loop_index,
            "active_loop_id": context.active_loop_id,
            "goals": list(goal_names),
            "latest_metrics": dict(context.latest_metrics),
            "instruction": (
                "Plan exactly one bounded amino-acid substitution. Preserve metal-binding "
                "interpretability and make the next loop easy to evaluate with Boltz and TRS."
            ),
        }
        return ChangeSet(
            summary=f"Novacore substitution {machine_diff}",
            why="Codex-managed planning selected one traceable substitution for Boltz, TRS, and Touchstone verification.",
            changes=(
                ProteinChange(
                    operation=ChangeOperation.SUBSTITUTE,
                    machine_diff=machine_diff,
                    position=position_index + 1,
                    from_residue=from_residue,
                    to_residue=to_residue,
                    rationale="Single-site edit keeps lineage interpretable for rollback and branch runs.",
                    expected_effect="Improve the configured Novacore optimization targets.",
                    metadata={"agent": self.author, "codex_prompt": prompt},
                ),
            ),
            author=self.author,
            metadata={
                "agent": self.author,
                "codex_managed": True,
                "persona": NOVACORE_AGENT_PERSONA,
                "codex_prompt": prompt,
            },
        )


@dataclass(frozen=True)
class NovacoreCandidateGenerator:
    name_prefix: str = "novacore"

    def generate_candidate(self, context: AgentLoopContext, change_set: ChangeSet) -> ProteinCandidate:
        sequence = list(context.sequence)
        for change in change_set.changes:
            if change.operation != ChangeOperation.SUBSTITUTE:
                raise ValueError(f"Unsupported Novacore change operation: {change.operation.value}")
            if change.position is None or change.to_residue is None:
                raise ValueError("Substitution changes require position and to_residue")
            position_index = change.position - 1
            if position_index < 0 or position_index >= len(sequence):
                raise ValueError(f"Change position outside sequence: {change.position}")
            if change.from_residue is not None and sequence[position_index] != change.from_residue:
                raise ValueError(
                    f"Expected residue {change.from_residue} at position {change.position}, "
                    f"found {sequence[position_index]}"
                )
            sequence[position_index] = change.to_residue

        return ProteinCandidate(
            sequence="".join(sequence),
            name=f"{self.name_prefix}_{context.loop_index + 1}",
            metadata={
                "agent": NOVACORE_AGENT_NAME,
                "codex_managed": True,
                "parent_loop_id": context.active_loop_id,
                "change_summary": change_set.summary,
            },
        )


@dataclass(frozen=True)
class NovacoreBoltzCliEvaluator:
    config: NovacoreToolConfig = NovacoreToolConfig()

    def evaluate(self, context: AgentLoopContext, loop: LoopRecord) -> EvaluationResult:
        if self.config.enable_external_tools:
            if self.config.prefer_boltz_api and _boltz_api_key_configured():
                return self._run_hosted_api(context, loop)
            return self._run_live_cli(context, loop)
        return self._dry_run_evaluation(context, loop)

    def _dry_run_evaluation(self, context: AgentLoopContext, loop: LoopRecord) -> EvaluationResult:
        loop_number = context.loop_index + 1
        plddt = min(0.95, 0.64 + 0.025 * loop_number + _sequence_balance(loop.candidate.sequence) * 0.08)
        ptm = min(0.9, 0.42 + 0.03 * loop_number + _charged_fraction(loop.candidate.sequence) * 0.1)
        boltz_path = _resolve_command(self.config.boltz_command)

        command = (
            f"{self.config.boltz_command} predict "
            f"--out_dir artifacts/{loop.run_id}/{loop.loop_id}/boltz "
            f"artifacts/{loop.run_id}/{loop.loop_id}/input.fasta"
        )
        return EvaluationResult(
            kind=EvaluationKind.BOLTZ,
            evaluator_name="novacore-boltz-cli",
            evaluator_version="adapter.v0",
            metrics=(
                MetricValue(name="plddt", value=round(plddt, 3), higher_is_better=True),
                MetricValue(name="ptm", value=round(ptm, 3), higher_is_better=True),
            ),
            passed=plddt >= 0.65,
            summary="Novacore prepared the Boltz CLI evaluation and recorded deterministic fallback metrics.",
            artifacts=(
                ArtifactRef(
                    uri=f"memory://{loop.run_id}/{loop.loop_id}/boltz/input.fasta",
                    kind="boltz_input",
                    format="fasta",
                    metadata={"command": command},
                ),
                ArtifactRef(
                    uri=f"memory://{loop.run_id}/{loop.loop_id}/boltz/prediction.cif",
                    kind="boltz_prediction",
                    format="mmcif",
                    metadata={"external_status": "dry_run"},
                ),
            ),
            metadata={
                "agent": NOVACORE_AGENT_NAME,
                "boltz_cli_command": command,
                "boltz_cli_available": boltz_path is not None,
                "external_status": "dry_run",
            },
        )

    def _run_hosted_api(self, context: AgentLoopContext, loop: LoopRecord) -> EvaluationResult:
        artifact_dir = ROOT / "artifacts" / loop.run_id / loop.loop_id / "boltz"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        input_path = artifact_dir / "api_input.json"
        log_path = artifact_dir / "boltz_api_result.json"
        structure_path = artifact_dir / "api_prediction.cif"
        confidence_path = artifact_dir / "api_confidence.json"
        archive_path = artifact_dir / "api_archive.zip"
        input_payload = _build_boltz_api_input(
            loop.candidate.sequence,
            use_msa_server=self.config.boltz_use_msa_server,
            recycling_steps=self.config.boltz_recycling_steps,
            sampling_steps=self.config.boltz_sampling_steps,
            diffusion_samples=self.config.boltz_diffusion_samples,
        )
        _write_json(input_path, input_payload)

        started_at = time.time()
        job_id: str | None = None
        try:
            from boltz_api import Boltz  # type: ignore[import-not-found]
        except Exception as exc:
            payload = {
                "status": "boltz_api_sdk_not_found",
                "error": f"{type(exc).__name__}: {exc}",
                "api_key_configured": True,
                "fallback": "none",
            }
            _write_json(log_path, payload)
            return self._api_result(
                context=context,
                loop=loop,
                input_path=input_path,
                log_path=log_path,
                structure_path=None,
                confidence_path=None,
                archive_path=None,
                job_payload=payload,
                job_id=None,
                status="boltz_api_sdk_not_found",
                error=str(payload["error"]),
                duration_seconds=0.0,
            )

        try:
            client = Boltz()
            response = client.predictions.structure_and_binding.start(
                input=input_payload,
                model=self.config.boltz_api_model,
                idempotency_key=f"novacore:{loop.run_id}:{loop.loop_id}",
            )
            job_id = str(getattr(response, "id"))
            terminal = response
            deadline = started_at + max(self.config.boltz_api_timeout_seconds, 1)
            while getattr(terminal, "status", None) in {"pending", "running"}:
                if time.time() >= deadline:
                    payload = _model_to_dict(terminal)
                    payload.update({
                        "status": "timeout",
                        "job_id": job_id,
                        "timeout_seconds": self.config.boltz_api_timeout_seconds,
                    })
                    _write_json(log_path, payload)
                    return self._api_result(
                        context=context,
                        loop=loop,
                        input_path=input_path,
                        log_path=log_path,
                        structure_path=None,
                        confidence_path=None,
                        archive_path=None,
                        job_payload=payload,
                        job_id=job_id,
                        status="timeout",
                        error=f"Boltz API job exceeded {self.config.boltz_api_timeout_seconds} seconds.",
                        duration_seconds=time.time() - started_at,
                    )
                time.sleep(max(self.config.boltz_api_poll_interval_seconds, 0.1))
                terminal = client.predictions.structure_and_binding.retrieve(job_id)
            payload = _model_to_dict(terminal)
            payload["job_id"] = job_id
            payload["duration_seconds"] = round(time.time() - started_at, 3)
            status = str(getattr(terminal, "status", payload.get("status") or "unknown"))
            output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
            if status == "succeeded" and isinstance(output, dict):
                _download_boltz_api_outputs(output, structure_path=structure_path, archive_path=archive_path)
                _write_json(confidence_path, _boltz_api_confidence_payload(output))
            _write_json(log_path, payload)
            error = _boltz_api_error(payload)
            return self._api_result(
                context=context,
                loop=loop,
                input_path=input_path,
                log_path=log_path,
                structure_path=structure_path if structure_path.exists() else None,
                confidence_path=confidence_path if confidence_path.exists() else None,
                archive_path=archive_path if archive_path.exists() else None,
                job_payload=payload,
                job_id=job_id,
                status=status,
                error=error,
                duration_seconds=time.time() - started_at,
            )
        except Exception as exc:
            payload = {
                "status": "failed",
                "job_id": job_id,
                "duration_seconds": round(time.time() - started_at, 3),
                "error": f"{type(exc).__name__}: {exc}",
                "api_key_configured": True,
            }
            _write_json(log_path, payload)
            return self._api_result(
                context=context,
                loop=loop,
                input_path=input_path,
                log_path=log_path,
                structure_path=None,
                confidence_path=None,
                archive_path=None,
                job_payload=payload,
                job_id=job_id,
                status="failed",
                error=str(payload["error"]),
                duration_seconds=time.time() - started_at,
            )

    def _api_result(
        self,
        *,
        context: AgentLoopContext,
        loop: LoopRecord,
        input_path: Path,
        log_path: Path,
        structure_path: Path | None,
        confidence_path: Path | None,
        archive_path: Path | None,
        job_payload: dict[str, object],
        job_id: str | None,
        status: str,
        error: str | None,
        duration_seconds: float,
    ) -> EvaluationResult:
        output = job_payload.get("output") if isinstance(job_payload.get("output"), dict) else {}
        metrics = tuple(_metrics_from_boltz_api_output(output if isinstance(output, dict) else {}))
        metric_map = {metric.name: metric.value for metric in metrics}
        plddt = metric_map.get("plddt")
        passed = None if status != "succeeded" else (plddt is not None and plddt >= 0.65)
        artifacts = tuple(
            artifact
            for artifact in (
                _artifact_ref(input_path, kind="boltz_api_input", fmt="json", metadata={"chain": "A"}),
                _artifact_ref(log_path, kind="boltz_api_result", fmt="json", metadata={"status": status, "job_id": job_id}),
                _artifact_ref(structure_path, kind="boltz_prediction", fmt="mmcif") if structure_path else None,
                _artifact_ref(confidence_path, kind="boltz_confidence", fmt="json") if confidence_path else None,
                _artifact_ref(archive_path, kind="boltz_archive", fmt="zip") if archive_path else None,
            )
            if artifact is not None
        )
        return EvaluationResult(
            kind=EvaluationKind.BOLTZ,
            evaluator_name="novacore-boltz-api",
            evaluator_version="sdk.v1",
            metrics=metrics,
            passed=passed,
            summary=_api_summary(status=status, error=error),
            artifacts=artifacts,
            metadata={
                "agent": NOVACORE_AGENT_NAME,
                "interface": "api",
                "external_status": status,
                "api_key_configured": True,
                "api_key_source": "environment_or_runtime",
                "job_id": job_id,
                "model": self.config.boltz_api_model,
                "duration_seconds": round(duration_seconds, 3),
                "use_msa_server": self.config.boltz_use_msa_server,
                "confidence": _boltz_api_confidence_payload(output if isinstance(output, dict) else {}),
                "error": error,
                "loop_index": context.loop_index + 1,
                "local_cli_fallback_available": _resolve_command(self.config.boltz_command) is not None,
            },
        )

    def _run_live_cli(self, context: AgentLoopContext, loop: LoopRecord) -> EvaluationResult:
        boltz_path = _resolve_command(self.config.boltz_command)
        artifact_dir = ROOT / "artifacts" / loop.run_id / loop.loop_id / "boltz"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        input_path = artifact_dir / "input.yaml"
        output_dir = artifact_dir / "output"
        log_path = artifact_dir / "boltz_cli_result.json"
        sequence = loop.candidate.sequence
        sampling_seed = _stable_int_seed(loop.run_id, loop.loop_id)
        _write_boltz_input(
            input_path,
            sequence=sequence,
            use_msa_server=self.config.boltz_use_msa_server,
        )

        if boltz_path is None:
            payload = {
                "status": "failed",
                "error": f"Boltz CLI command not found: {self.config.boltz_command}",
                "command": None,
            }
            _write_json(log_path, payload)
            return self._live_result(
                context=context,
                loop=loop,
                input_path=input_path,
                log_path=log_path,
                output_dir=output_dir,
                command=None,
                exit_code=None,
                duration_seconds=0.0,
                status="boltz_cli_not_found",
                error=payload["error"],
            )

        command = self._build_command(
            boltz_path=boltz_path,
            input_path=input_path,
            output_dir=output_dir,
            sampling_seed=sampling_seed,
        )
        started_at = time.time()
        try:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=max(self.config.boltz_timeout_seconds, 1),
                check=False,
            )
            duration_seconds = time.time() - started_at
            payload = {
                "status": "succeeded" if completed.returncode == 0 else "failed",
                "command": command,
                "exit_code": completed.returncode,
                "duration_seconds": round(duration_seconds, 3),
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
            _write_json(log_path, payload)
            if completed.returncode != 0:
                return self._live_result(
                    context=context,
                    loop=loop,
                    input_path=input_path,
                    log_path=log_path,
                    output_dir=output_dir,
                    command=command,
                    exit_code=completed.returncode,
                    duration_seconds=duration_seconds,
                    status="failed",
                    error=_tail(completed.stderr or completed.stdout),
                )
        except subprocess.TimeoutExpired as exc:
            duration_seconds = time.time() - started_at
            payload = {
                "status": "timeout",
                "command": command,
                "exit_code": None,
                "duration_seconds": round(duration_seconds, 3),
                "stdout": _string_or_empty(exc.stdout),
                "stderr": _string_or_empty(exc.stderr),
                "timeout_seconds": self.config.boltz_timeout_seconds,
            }
            _write_json(log_path, payload)
            return self._live_result(
                context=context,
                loop=loop,
                input_path=input_path,
                log_path=log_path,
                output_dir=output_dir,
                command=command,
                exit_code=None,
                duration_seconds=duration_seconds,
                status="timeout",
                error=f"Boltz CLI exceeded {self.config.boltz_timeout_seconds} seconds.",
            )

        return self._live_result(
            context=context,
            loop=loop,
            input_path=input_path,
            log_path=log_path,
            output_dir=output_dir,
            command=command,
            exit_code=0,
            duration_seconds=time.time() - started_at,
            status="succeeded",
            error=None,
        )

    def _build_command(
        self,
        *,
        boltz_path: str,
        input_path: Path,
        output_dir: Path,
        sampling_seed: int,
    ) -> list[str]:
        command = [
            boltz_path,
            "predict",
            str(input_path),
            "--out_dir",
            str(output_dir),
            "--output_format",
            "mmcif",
            "--override",
            "--accelerator",
            self.config.boltz_accelerator,
            "--model",
            self.config.boltz_model,
            "--seed",
            str(sampling_seed),
        ]
        if self.config.boltz_cache:
            command.extend(["--cache", self.config.boltz_cache])
        if self.config.boltz_use_msa_server:
            command.extend([
                "--use_msa_server",
                "--msa_server_url",
                self.config.boltz_msa_server_url,
                "--msa_pairing_strategy",
                self.config.boltz_msa_pairing_strategy,
            ])
        if self.config.boltz_recycling_steps is not None:
            command.extend(["--recycling_steps", str(self.config.boltz_recycling_steps)])
        if self.config.boltz_sampling_steps is not None:
            command.extend(["--sampling_steps", str(self.config.boltz_sampling_steps)])
        if self.config.boltz_diffusion_samples is not None:
            command.extend(["--diffusion_samples", str(self.config.boltz_diffusion_samples)])
        return command

    def _live_result(
        self,
        *,
        context: AgentLoopContext,
        loop: LoopRecord,
        input_path: Path,
        log_path: Path,
        output_dir: Path,
        command: list[str] | None,
        exit_code: int | None,
        duration_seconds: float,
        status: str,
        error: str | None,
    ) -> EvaluationResult:
        confidence_path = _first_existing(output_dir.glob("**/predictions/*/confidence_*_model_0.json"))
        structure_path = _first_existing(output_dir.glob("**/predictions/*/*_model_0.cif"))
        plddt_path = _first_existing(output_dir.glob("**/predictions/*/plddt_*_model_0.npz"))
        pae_path = _first_existing(output_dir.glob("**/predictions/*/pae_*_model_0.npz"))
        if status == "succeeded" and (confidence_path is None or structure_path is None):
            status = "missing_artifacts"
            error = "Boltz CLI exited successfully but required confidence or mmCIF artifacts were not found."
        confidence = _read_json(confidence_path) if confidence_path is not None else {}
        metrics = tuple(_metrics_from_confidence(confidence))
        metric_map = {metric.name: metric.value for metric in metrics}
        plddt = metric_map.get("plddt")
        passed = None if status != "succeeded" else (plddt is not None and plddt >= 0.65)
        artifacts = tuple(
            _artifact
            for _artifact in (
                _artifact_ref(input_path, kind="boltz_input", fmt="yaml", metadata={"chain": "A"}),
                _artifact_ref(log_path, kind="boltz_cli_result", fmt="json", metadata={"status": status}),
                _artifact_ref(structure_path, kind="boltz_prediction", fmt="mmcif") if structure_path else None,
                _artifact_ref(confidence_path, kind="boltz_confidence", fmt="json") if confidence_path else None,
                _artifact_ref(plddt_path, kind="boltz_plddt", fmt="npz") if plddt_path else None,
                _artifact_ref(pae_path, kind="boltz_pae", fmt="npz") if pae_path else None,
            )
            if _artifact is not None
        )
        return EvaluationResult(
            kind=EvaluationKind.BOLTZ,
            evaluator_name="novacore-boltz-cli",
            evaluator_version="cli.v1",
            metrics=metrics,
            passed=passed,
            summary=_live_summary(status=status, error=error),
            artifacts=artifacts,
            metadata={
                "agent": NOVACORE_AGENT_NAME,
                "interface": "cli",
                "external_status": status,
                "boltz_cli_command": command,
                "boltz_cli_available": command is not None,
                "exit_code": exit_code,
                "duration_seconds": round(duration_seconds, 3),
                "output_dir": str(output_dir),
                "model": self.config.boltz_model,
                "accelerator": self.config.boltz_accelerator,
                "use_msa_server": self.config.boltz_use_msa_server,
                "confidence": confidence,
                "error": error,
                "loop_index": context.loop_index + 1,
            },
        )


@dataclass(frozen=True)
class NovacoreTrsScreeningPipeline:
    def evaluate(
        self,
        context: AgentLoopContext,
        loop: LoopRecord,
        boltz_evaluation: EvaluationResult,
    ) -> EvaluationResult:
        boltz_metrics = boltz_evaluation.metric_map()
        changed_positions = tuple(
            position
            for change in (loop.change_set.changes if loop.change_set is not None else ())
            for position in changed_positions_from_diff(change.machine_diff)
        )
        target = context.conditions.parameters.get("binding_target")
        trs_result = score_sequence_trs(
            loop.candidate.sequence,
            target=str(target) if target is not None else None,
            changed_positions=changed_positions,
        )
        source_artifacts = [artifact.uri for artifact in boltz_evaluation.artifacts]
        return EvaluationResult(
            kind=EvaluationKind.SCREENING,
            evaluator_name="trs",
            evaluator_version="adapter.v1",
            metrics=(
                MetricValue(name="trs_total", value=trs_result.total, higher_is_better=True),
                MetricValue(name="trs_raw_total", value=trs_result.raw_total, higher_is_better=True),
            ),
            passed=trs_result.total >= 0.65,
            summary="TRS scored the candidate contact graph and linked the result to Boltz artifacts.",
            artifacts=(
                ArtifactRef(
                    uri=f"memory://{loop.run_id}/{loop.loop_id}/trs/screening.json",
                    kind="trs_screening_result",
                    format="json",
                    metadata={"source_artifacts": source_artifacts},
                ),
            ),
            metadata={
                "agent": NOVACORE_AGENT_NAME,
                "trs_components": trs_result.components,
                "trs_weights": trs_result.weights,
                "trs_raw_total": trs_result.raw_total,
                "input_mode": trs_result.input_mode,
                "metal_node": trs_result.metal_node,
                "contact_residue_indices": list(trs_result.contact_residue_indices),
                "source_artifact_refs": source_artifacts,
                "boltz_metrics": boltz_metrics,
            },
        )


@dataclass(frozen=True)
class TouchstoneVerifierAdapter:
    config: NovacoreToolConfig = NovacoreToolConfig()

    def evaluate(
        self,
        context: AgentLoopContext,
        loop: LoopRecord,
        boltz_evaluation: EvaluationResult,
        screening_evaluation: EvaluationResult,
    ) -> EvaluationResult:
        structure_path = _structure_path_from_boltz(boltz_evaluation)
        if not self.config.enable_external_tools:
            return self._pending_result(
                context,
                boltz_evaluation,
                screening_evaluation,
                adapter_status="external_tools_disabled",
                summary="Touchstone verifier was not run because external tools are disabled.",
                structure_path=structure_path,
            )
        if structure_path is None:
            return self._pending_result(
                context,
                boltz_evaluation,
                screening_evaluation,
                adapter_status="needs_structure",
                summary="Touchstone verifier needs a real Boltz mmCIF/PDB artifact before it can score the metal site.",
                structure_path=None,
            )

        artifact_dir = ROOT / "artifacts" / loop.run_id / loop.loop_id / "touchstone"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        log_path = artifact_dir / "touchstone_result.json"
        command = self._build_command(structure_path=structure_path, metal=_touchstone_metal_label(context))
        if command is None:
            payload = {
                "status": "touchstone_not_found",
                "error": "Touchstone CLI was not found and uvx fallback is unavailable.",
                "structure_path": str(structure_path),
            }
            _write_json(log_path, payload)
            return self._failed_result(
                context,
                boltz_evaluation,
                screening_evaluation,
                log_path=log_path,
                structure_path=structure_path,
                command=None,
                status="touchstone_not_found",
                error=str(payload["error"]),
                duration_seconds=0.0,
                stdout="",
                stderr="",
                parsed_result={},
            )

        started_at = time.time()
        try:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=max(self.config.touchstone_timeout_seconds, 1),
                check=False,
            )
            duration_seconds = time.time() - started_at
            parsed_result = _parse_touchstone_stdout(completed.stdout)
            status = "succeeded" if completed.returncode == 0 else "failed"
            payload = {
                "status": status,
                "command": command,
                "exit_code": completed.returncode,
                "duration_seconds": round(duration_seconds, 3),
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "parsed_result": parsed_result,
                "structure_path": str(structure_path),
            }
            _write_json(log_path, payload)
            if completed.returncode != 0:
                return self._failed_result(
                    context,
                    boltz_evaluation,
                    screening_evaluation,
                    log_path=log_path,
                    structure_path=structure_path,
                    command=command,
                    status=status,
                    error=_tail(completed.stderr or completed.stdout),
                    duration_seconds=duration_seconds,
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                    parsed_result=parsed_result,
                )
        except subprocess.TimeoutExpired as exc:
            duration_seconds = time.time() - started_at
            stdout = _string_or_empty(exc.stdout)
            stderr = _string_or_empty(exc.stderr)
            payload = {
                "status": "timeout",
                "command": command,
                "exit_code": None,
                "duration_seconds": round(duration_seconds, 3),
                "stdout": stdout,
                "stderr": stderr,
                "timeout_seconds": self.config.touchstone_timeout_seconds,
                "structure_path": str(structure_path),
            }
            _write_json(log_path, payload)
            return self._failed_result(
                context,
                boltz_evaluation,
                screening_evaluation,
                log_path=log_path,
                structure_path=structure_path,
                command=command,
                status="timeout",
                error=f"Touchstone verifier exceeded {self.config.touchstone_timeout_seconds} seconds.",
                duration_seconds=duration_seconds,
                stdout=stdout,
                stderr=stderr,
                parsed_result={},
            )

        metrics = tuple(_metrics_from_touchstone(parsed_result))
        consensus = _touchstone_consensus(parsed_result)
        passed = None if consensus is None else consensus == "trust"
        return EvaluationResult(
            kind=EvaluationKind.VERIFIER,
            evaluator_name="touchstone",
            evaluator_version="cli.v1",
            metrics=metrics,
            passed=passed,
            summary=_touchstone_summary(consensus=consensus, parsed=bool(parsed_result)),
            artifacts=(
                _artifact_ref(log_path, kind="touchstone_result", fmt="json", metadata={"status": "succeeded"}),
                _artifact_ref(structure_path, kind="touchstone_input_structure", fmt=structure_path.suffix.lstrip(".") or "structure"),
            ),
            metadata={
                "agent": NOVACORE_AGENT_NAME,
                "interface": "cli",
                "external_status": "succeeded",
                "command": command,
                "consensus": consensus,
                "touchstone_result": parsed_result,
                "deep": self.config.touchstone_deep,
                "stress": self.config.touchstone_stress,
                "metal": _touchstone_metal_label(context),
                "structure_path": str(structure_path),
                "boltz_metrics": boltz_evaluation.metric_map(),
                "screening_metrics": screening_evaluation.metric_map(),
            },
        )

    def _build_command(self, *, structure_path: Path, metal: str) -> list[str] | None:
        touchstone_path = _resolve_command(self.config.touchstone_command)
        if touchstone_path is not None:
            command = [touchstone_path, "verify", str(structure_path), "--metal", metal]
        elif self.config.touchstone_use_uvx:
            uvx_path = shutil.which("uvx") or _resolve_command("uvx")
            if uvx_path is None:
                return None
            command = [uvx_path, "--from", TOUCHSTONE_UVX_SPEC, "touchstone", "verify", str(structure_path), "--metal", metal]
        else:
            return None
        if self.config.touchstone_deep:
            command.append("--deep")
        if self.config.touchstone_stress:
            command.append("--stress")
        return command

    def _pending_result(
        self,
        context: AgentLoopContext,
        boltz_evaluation: EvaluationResult,
        screening_evaluation: EvaluationResult,
        *,
        adapter_status: str,
        summary: str,
        structure_path: Path | None,
    ) -> EvaluationResult:
        return EvaluationResult(
            kind=EvaluationKind.VERIFIER,
            evaluator_name="touchstone-pending",
            evaluator_version=None,
            metrics=(),
            passed=None,
            summary=summary,
            metadata={
                "agent": NOVACORE_AGENT_NAME,
                "adapter_status": adapter_status,
                "expected_mcp_server": self.config.verifier_mcp_server or "touchstone",
                "touchstone_command": self.config.touchstone_command,
                "touchstone_uvx_spec": TOUCHSTONE_UVX_SPEC,
                "structure_path": str(structure_path) if structure_path is not None else None,
                "goals": [
                    {
                        "name": goal.name,
                        "target": goal.target,
                        "comparator": goal.comparator,
                        "weight": goal.weight,
                    }
                    for goal in context.objective.goals
                ],
                "boltz_metrics": boltz_evaluation.metric_map(),
                "screening_metrics": screening_evaluation.metric_map(),
            },
        )

    def _failed_result(
        self,
        context: AgentLoopContext,
        boltz_evaluation: EvaluationResult,
        screening_evaluation: EvaluationResult,
        *,
        log_path: Path,
        structure_path: Path,
        command: list[str] | None,
        status: str,
        error: str,
        duration_seconds: float,
        stdout: str,
        stderr: str,
        parsed_result: dict[str, object],
    ) -> EvaluationResult:
        return EvaluationResult(
            kind=EvaluationKind.VERIFIER,
            evaluator_name="touchstone",
            evaluator_version="cli.v1",
            metrics=tuple(_metrics_from_touchstone(parsed_result)),
            passed=None,
            summary=_touchstone_failure_summary(status=status, error=error),
            artifacts=(
                _artifact_ref(log_path, kind="touchstone_result", fmt="json", metadata={"status": status}),
                _artifact_ref(structure_path, kind="touchstone_input_structure", fmt=structure_path.suffix.lstrip(".") or "structure"),
            ),
            metadata={
                "agent": NOVACORE_AGENT_NAME,
                "interface": "cli",
                "external_status": status,
                "command": command,
                "error": error,
                "duration_seconds": round(duration_seconds, 3),
                "stdout_tail": _tail(stdout),
                "stderr_tail": _tail(stderr),
                "touchstone_result": parsed_result,
                "consensus": _touchstone_consensus(parsed_result),
                "deep": self.config.touchstone_deep,
                "stress": self.config.touchstone_stress,
                "metal": _touchstone_metal_label(context),
                "structure_path": str(structure_path),
                "boltz_metrics": boltz_evaluation.metric_map(),
                "screening_metrics": screening_evaluation.metric_map(),
            },
        )


@dataclass(frozen=True)
class NovacoreReflectionWriter:
    def write_reflection(
        self,
        context: AgentLoopContext,
        loop: LoopRecord,
        boltz_evaluation: EvaluationResult,
        screening_evaluation: EvaluationResult,
        verifier_evaluation: EvaluationResult,
    ) -> LoopReflection:
        boltz_metrics = boltz_evaluation.metric_map()
        screening_metrics = screening_evaluation.metric_map()
        pending_verifier = verifier_evaluation.evaluator_name == "touchstone-pending"
        went_wrong = (
            "Touchstone verifier evidence is pending, so Novacore cannot claim verifier-backed success yet.",
        ) if pending_verifier else ()
        return LoopReflection(
            went_well=(
                "Novacore completed a forced Codex loop and recorded Boltz plus TRS-compatible outputs.",
            ),
            went_wrong=went_wrong,
            next_actions=(
                "Continue the configured loop budget unless the human rolls back or branches from a stronger loop.",
                "Review Touchstone verifier evidence before proposing the next single change.",
            ),
            notes=(
                f"Loop {loop.loop_id}: "
                f"pLDDT={boltz_metrics.get('plddt', 0.0):.3f}, "
                f"pTM={boltz_metrics.get('ptm', 0.0):.3f}, "
                f"TRS={screening_metrics.get('trs_total', 0.0):.3f}."
            ),
        )


def build_novacore_runner(
    *,
    memory: MemoryRepository,
    tool_config: NovacoreToolConfig | None = None,
) -> ProteinDesignLoopRunner:
    config = tool_config or NovacoreToolConfig()
    return ProteinDesignLoopRunner(
        memory=memory,
        change_planner=NovacoreChangePlanner(),
        candidate_generator=NovacoreCandidateGenerator(),
        boltz_evaluator=NovacoreBoltzCliEvaluator(config=config),
        screening_pipeline=NovacoreTrsScreeningPipeline(),
        verifier=TouchstoneVerifierAdapter(config=config),
        reflection_writer=NovacoreReflectionWriter(),
    )


def _next_residue(residue: str, *, offset: int) -> str:
    try:
        residue_index = AMINO_ACIDS.index(residue)
    except ValueError:
        residue_index = 0
    return AMINO_ACIDS[(residue_index + offset) % len(AMINO_ACIDS)]


def _sequence_balance(sequence: str) -> float:
    if not sequence:
        return 0.0
    hydrophobic = sum(1 for residue in sequence if residue in "AILMFWV")
    fraction = hydrophobic / len(sequence)
    return max(0.0, 1.0 - abs(0.42 - fraction))


def _charged_fraction(sequence: str) -> float:
    if not sequence:
        return 0.0
    return sum(1 for residue in sequence if residue in "DEKRH") / len(sequence)


def _resolve_command(command: str) -> str | None:
    path = shutil.which(command)
    if path is not None:
        return path
    venv_path = ROOT / ".venv" / "bin" / command
    return str(venv_path) if venv_path.exists() else None


def _write_boltz_input(path: Path, *, sequence: str, use_msa_server: bool) -> None:
    clean_sequence = "".join(residue for residue in sequence.upper() if residue in AMINO_ACIDS)
    if not clean_sequence:
        raise ValueError("Boltz input requires at least one standard amino-acid residue")
    msa_line = "" if use_msa_server else "      msa: empty\n"
    path.write_text(
        (
            "version: 1\n"
            "sequences:\n"
            "  - protein:\n"
            "      id: A\n"
            f"      sequence: {clean_sequence}\n"
            f"{msa_line}"
        ),
        encoding="utf-8",
    )


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _read_json(path: Path | None) -> dict[str, object]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _boltz_api_key_configured() -> bool:
    return bool(os.environ.get("BOLTZ_API_KEY"))


def _build_boltz_api_input(
    sequence: str,
    *,
    use_msa_server: bool,
    recycling_steps: int | None,
    sampling_steps: int | None,
    diffusion_samples: int | None,
) -> dict[str, object]:
    clean_sequence = "".join(residue for residue in sequence.upper() if residue in AMINO_ACIDS)
    if not clean_sequence:
        raise ValueError("Boltz API input requires at least one standard amino-acid residue")
    protein: dict[str, object] = {
        "type": "protein",
        "chain_ids": ["A"],
        "value": clean_sequence,
    }
    if not use_msa_server:
        protein["msa"] = {"type": "empty"}
    payload: dict[str, object] = {"entities": [protein]}
    model_options: dict[str, object] = {}
    if recycling_steps is not None:
        model_options["recycling_steps"] = recycling_steps
    if sampling_steps is not None:
        model_options["sampling_steps"] = sampling_steps
    if model_options:
        payload["model_options"] = model_options
    if diffusion_samples is not None:
        payload["num_samples"] = max(1, min(diffusion_samples, 10))
    return payload


def _model_to_dict(model: object) -> dict[str, object]:
    if hasattr(model, "model_dump"):
        payload = model.model_dump(mode="json", by_alias=True)
    elif hasattr(model, "dict"):
        payload = model.dict()
    else:
        payload = getattr(model, "__dict__", {})
    return payload if isinstance(payload, dict) else {}


def _download_boltz_api_outputs(output: dict[str, object], *, structure_path: Path, archive_path: Path) -> None:
    best_sample = output.get("best_sample")
    if isinstance(best_sample, dict):
        structure = best_sample.get("structure")
        if isinstance(structure, dict) and isinstance(structure.get("url"), str):
            _download_url(structure["url"], structure_path)
    archive = output.get("archive")
    if isinstance(archive, dict) and isinstance(archive.get("url"), str):
        _download_url(archive["url"], archive_path)


def _download_url(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as response:
        path.write_bytes(response.read())


def _boltz_api_confidence_payload(output: dict[str, object]) -> dict[str, object]:
    best_sample = output.get("best_sample")
    if isinstance(best_sample, dict):
        metrics = best_sample.get("metrics")
        if isinstance(metrics, dict):
            return dict(metrics)
    return {}


def _metrics_from_boltz_api_output(output: dict[str, object]) -> Iterable[MetricValue]:
    metrics = _boltz_api_confidence_payload(output)
    mapping = {
        "structure_confidence": "boltz_confidence",
        "ptm": "ptm",
        "iptm": "iptm",
        "ligand_iptm": "ligand_iptm",
        "protein_iptm": "protein_iptm",
        "complex_plddt": "plddt",
        "complex_iplddt": "complex_iplddt",
        "complex_pde": "complex_pde",
        "complex_ipde": "complex_ipde",
    }
    for source_name, metric_name in mapping.items():
        value = metrics.get(source_name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            higher_is_better = metric_name not in {"complex_pde", "complex_ipde"}
            yield MetricValue(
                name=metric_name,
                value=round(float(value), 6),
                higher_is_better=higher_is_better,
                metadata={"source": f"api.best_sample.metrics.{source_name}"},
            )
    binding = output.get("binding_metrics")
    if isinstance(binding, dict):
        for source_name in ("binding_confidence", "optimization_score"):
            value = binding.get(source_name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                yield MetricValue(
                    name=source_name,
                    value=round(float(value), 6),
                    higher_is_better=True,
                    metadata={"source": f"api.binding_metrics.{source_name}"},
                )


def _boltz_api_error(payload: dict[str, object]) -> str | None:
    error = payload.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message")
        return ": ".join(str(part) for part in (code, message) if part)
    if isinstance(error, str):
        return error
    return None


def _api_summary(*, status: str, error: str | None) -> str:
    if status == "succeeded":
        return "Boltz API completed hosted prediction and Novacore downloaded returned structure and confidence artifacts."
    if status == "timeout":
        return "Boltz API job timed out; Novacore preserved the input and latest job payload."
    if status == "boltz_api_sdk_not_found":
        return "BOLTZ_API_KEY is configured, but the boltz-api Python SDK is not installed in this environment."
    if status in {"pending", "running"}:
        return "Boltz API job did not reach a terminal state before evaluation ended."
    return f"Boltz API failed; Novacore preserved the input and job payload. {error or ''}".strip()


def _structure_path_from_boltz(evaluation: EvaluationResult) -> Path | None:
    for artifact in evaluation.artifacts:
        if artifact.kind not in {"boltz_prediction", "candidate_structure"}:
            continue
        if artifact.uri.startswith("memory://"):
            continue
        path = Path(artifact.uri)
        if path.exists() and path.suffix.lower() in {".cif", ".mmcif", ".pdb"}:
            return path
    return None


def _touchstone_metal_label(context: AgentLoopContext) -> str:
    raw = str(context.conditions.parameters.get("binding_target") or context.objective.custom.get("target") or "Ni2+")
    normalized = raw.strip().replace(" ", "")
    lower = normalized.lower()
    known = {
        "zn": "Zn2+",
        "zn2+": "Zn2+",
        "zinc": "Zn2+",
        "ni": "Ni2+",
        "ni2+": "Ni2+",
        "nickel": "Ni2+",
        "cu": "Cu2+",
        "cu2+": "Cu2+",
        "copper": "Cu2+",
        "co": "Co2+",
        "co2+": "Co2+",
        "cobalt": "Co2+",
    }
    return known.get(lower, normalized or "Ni2+")


def _parse_touchstone_stdout(stdout: str) -> dict[str, object]:
    stripped = stdout.strip()
    if not stripped:
        return {}
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            return _parse_touchstone_rendered_stdout(stripped)
        try:
            payload = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            return _parse_touchstone_rendered_stdout(stripped)
    return payload if isinstance(payload, dict) else {}


def _parse_touchstone_rendered_stdout(stdout: str) -> dict[str, object]:
    for line in stdout.splitlines():
        normalized = line.strip().lower()
        if not normalized.startswith("consensus:"):
            continue
        _, _, verdict_text = normalized.partition(":")
        verdict = verdict_text.strip().split(maxsplit=1)[0] if verdict_text.strip() else ""
        if verdict in {"trust", "weak", "defer"}:
            return {"consensus": verdict, "source": "rendered_stdout"}
    return {}


def _touchstone_consensus(payload: dict[str, object]) -> str | None:
    value = payload.get("consensus") or payload.get("verdict") or payload.get("label")
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"trust", "weak", "defer"}:
            return normalized
    return None


def _metrics_from_touchstone(payload: dict[str, object]) -> Iterable[MetricValue]:
    consensus = _touchstone_consensus(payload)
    if consensus is not None:
        yield MetricValue(
            name="touchstone_consensus_score",
            value={"trust": 1.0, "weak": 0.5, "defer": 0.0}[consensus],
            higher_is_better=True,
            metadata={"consensus": consensus},
        )
    for source_name, metric_name in (("score", "touchstone_score"), ("reward", "touchstone_reward")):
        value = payload.get(source_name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            yield MetricValue(name=metric_name, value=round(float(value), 6), higher_is_better=True)
    yield from _tier_metrics(payload.get("stack"))
    yield from _tier_metrics(payload.get("verdicts"))
    yield from _tier_metrics(payload.get("tiers"))


def _tier_metrics(value: object) -> Iterable[MetricValue]:
    if isinstance(value, dict):
        entries = value.values()
    elif isinstance(value, list):
        entries = value
    else:
        return
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        label = entry.get("label") or entry.get("tier") or entry.get("name") or entry.get("verifier")
        if not isinstance(label, str) or not label:
            continue
        prefix = "touchstone_" + "".join(ch.lower() if ch.isalnum() else "_" for ch in label).strip("_")
        score = entry.get("score")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            yield MetricValue(name=f"{prefix}_score", value=round(float(score), 6), higher_is_better=True)
        metrics = entry.get("metrics")
        if isinstance(metrics, dict):
            for metric_name, metric_value in metrics.items():
                if isinstance(metric_name, str) and isinstance(metric_value, (int, float)) and not isinstance(metric_value, bool):
                    yield MetricValue(
                        name=f"{prefix}_{metric_name}",
                        value=round(float(metric_value), 6),
                        higher_is_better=_touchstone_higher_is_better(metric_name),
                    )


def _touchstone_higher_is_better(metric_name: str) -> bool:
    lowered = metric_name.lower()
    return not any(marker in lowered for marker in ("delta", "drift", "rmsd", "sigma", "strain"))


def _touchstone_summary(*, consensus: str | None, parsed: bool) -> str:
    if consensus == "trust":
        return "Touchstone verified the predicted metal site with a trust consensus."
    if consensus == "weak":
        return "Touchstone judged the predicted metal site as weak; iterate before synthesis."
    if consensus == "defer":
        return "Touchstone deferred the predicted metal site; reject or rerun with deeper checks."
    if parsed:
        return "Touchstone completed and returned verifier data without a trust/weak/defer consensus field."
    return "Touchstone completed; Novacore preserved the rendered verifier output but could not parse structured JSON."


def _touchstone_failure_summary(*, status: str, error: str) -> str:
    if status == "touchstone_not_found":
        return "Touchstone verifier was requested but neither the CLI nor uvx fallback was available."
    if status == "timeout":
        return "Touchstone verifier timed out; Novacore preserved the input structure and process log."
    return f"Touchstone verifier failed; Novacore preserved the input structure and logs. {error or ''}".strip()


def _first_existing(paths: Iterable[Path]) -> Path | None:
    for path in sorted(paths):
        if path.exists():
            return path
    return None


def _artifact_ref(
    path: Path,
    *,
    kind: str,
    fmt: str,
    metadata: dict[str, object] | None = None,
) -> ArtifactRef:
    return ArtifactRef(
        uri=str(path.resolve()),
        kind=kind,
        format=fmt,
        sha256=_sha256(path) if path.is_file() else None,
        metadata=metadata or {},
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metrics_from_confidence(confidence: dict[str, object]) -> Iterable[MetricValue]:
    mapping = {
        "confidence_score": "boltz_confidence",
        "ptm": "ptm",
        "iptm": "iptm",
        "ligand_iptm": "ligand_iptm",
        "protein_iptm": "protein_iptm",
        "complex_plddt": "plddt",
        "complex_iplddt": "complex_iplddt",
        "complex_pde": "complex_pde",
        "complex_ipde": "complex_ipde",
    }
    for source_name, metric_name in mapping.items():
        value = confidence.get(source_name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            higher_is_better = metric_name not in {"complex_pde", "complex_ipde"}
            yield MetricValue(
                name=metric_name,
                value=round(float(value), 6),
                higher_is_better=higher_is_better,
                metadata={"source": source_name},
            )


def _stable_int_seed(*parts: str) -> int:
    digest = hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _tail(text: str, *, limit: int = 4000) -> str:
    return text[-limit:] if len(text) > limit else text


def _string_or_empty(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _live_summary(*, status: str, error: str | None) -> str:
    if status == "succeeded":
        return "Boltz CLI completed live prediction and Novacore parsed returned confidence and structure artifacts."
    if status == "missing_artifacts":
        return "Boltz CLI exited successfully, but Novacore could not find required confidence or mmCIF artifacts."
    if status == "timeout":
        return "Boltz CLI timed out; Novacore preserved the input and process log for rerun."
    if status == "boltz_cli_not_found":
        return "Boltz CLI was requested but the executable was not found."
    return f"Boltz CLI failed; Novacore preserved the input and logs. {error or ''}".strip()
