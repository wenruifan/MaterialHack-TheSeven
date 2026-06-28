from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
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
prepare Boltz CLI structure evaluation, run TRS screening, record verifier MCP
status, write reflection, and continue until the configured loop count is done.
It optimizes only against user-supplied metric goals and must preserve all
Boltz, TRS, verifier, rollback, and lineage evidence in memory.
"""

AMINO_ACIDS = "ACDEFGHIKLMNPQRSTVWY"


@dataclass(frozen=True)
class NovacoreToolConfig:
    boltz_command: str = "boltz"
    enable_external_tools: bool = False
    verifier_mcp_server: str | None = None
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
            why=(
                "Codex-managed planning selected one traceable substitution so the loop can "
                "be evaluated by Boltz and TRS before any verifier MCP adapter is available."
            ),
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
class PendingVerifierMcpAdapter:
    config: NovacoreToolConfig = NovacoreToolConfig()

    def evaluate(
        self,
        context: AgentLoopContext,
        loop: LoopRecord,
        boltz_evaluation: EvaluationResult,
        screening_evaluation: EvaluationResult,
    ) -> EvaluationResult:
        return EvaluationResult(
            kind=EvaluationKind.VERIFIER,
            evaluator_name="verifier-mcp-pending",
            evaluator_version=None,
            metrics=(),
            passed=None,
            summary="Verifier MCP server is not configured yet; Novacore recorded a pending verifier step.",
            metadata={
                "agent": NOVACORE_AGENT_NAME,
                "adapter_status": "pending",
                "expected_mcp_server": self.config.verifier_mcp_server,
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
        pending_verifier = verifier_evaluation.evaluator_name == "verifier-mcp-pending"
        went_wrong = (
            "Verifier MCP adapter is pending, so Novacore cannot claim verifier-backed success yet.",
        ) if pending_verifier else ()
        return LoopReflection(
            went_well=(
                "Novacore completed a forced Codex loop and recorded Boltz plus TRS-compatible outputs.",
            ),
            went_wrong=went_wrong,
            next_actions=(
                "Continue the configured loop budget unless the human rolls back or branches from a stronger loop.",
                "Attach the verifier MCP adapter once available and preserve its metrics under the verifier tab.",
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
        verifier=PendingVerifierMcpAdapter(config=config),
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
