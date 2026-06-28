from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
import os
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from materialhack_loop_runner import LoopRunnerResult
from materialhack_memory import (
    CandidateOrigin,
    ChangeSet,
    HumanInput,
    InvalidLoopOperation,
    LoopRecord,
    LoopReflection,
    LoopStatus,
    MemoryRepository,
    MetricGoal,
    ProteinCandidate,
    to_jsonable,
)

from materialhack_agent.novacore import (
    NOVACORE_AGENT_NAME,
    NovacoreBoltzCliEvaluator,
    NovacoreToolConfig,
    NovacoreTrsScreeningPipeline,
    TouchstoneVerifierAdapter,
    build_novacore_runner,
)
from materialhack_agent.observable_memory import ObservableInMemoryProteinMemoryRepository
from materialhack_agent.seed_flow import (
    ParsedObjective,
    SeedFlowConfig,
    create_seeded_run,
    parse_objective,
)
from materialhack_agent.workbench_events import EventHub, utc_now_iso


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class WorkbenchJob:
    job_id: str
    run_id: str
    action: str
    status: JobState = JobState.QUEUED
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    result: dict | None = None

    def to_payload(self) -> dict:
        return to_jsonable(self)


@dataclass(frozen=True)
class ObjectiveParameters:
    target: str
    ph: float
    functions: tuple[str, ...]
    length: int
    seed_count: int = 5
    seed_sources: tuple[str, ...] = ("ccdc_csd", "de_novo")
    target_score: float = 0.8
    loop_count: int = 2
    optimization_targets: tuple[MetricGoal, ...] = (
        MetricGoal(
            name="trs_total",
            target=0.8,
            comparator="gte",
            weight=1.0,
            description="TRS total screening score.",
        ),
        MetricGoal(
            name="plddt",
            target=0.7,
            comparator="gte",
            weight=0.5,
            description="Boltz confidence score.",
        ),
    )


@dataclass
class WorkbenchService:
    event_hub: EventHub = field(default_factory=EventHub)

    def __post_init__(self) -> None:
        self.memory: MemoryRepository = ObservableInMemoryProteinMemoryRepository(self.event_hub)
        self._jobs: dict[str, WorkbenchJob] = {}
        self._job_loop_ids: dict[str, str] = {}
        self._latest_run_id: str | None = None
        self._initial_boltz_api_key = os.environ.get("BOLTZ_API_KEY")
        self._runtime_boltz_api_key_set = False
        self._lock = threading.RLock()

    def parse_objective_parameters(self, objective: str) -> ObjectiveParameters:
        parsed = parse_objective(objective)
        return ObjectiveParameters(
            target=parsed.target,
            ph=parsed.ph,
            functions=parsed.functions,
            length=parsed.length,
        )

    def create_run(
        self,
        *,
        objective: str,
        target: str,
        ph: float,
        functions: Iterable[str],
        length: int,
        seed_count: int,
        seed_sources: Iterable[str],
        target_score: float,
        loop_count: int,
        optimization_targets: Iterable[MetricGoal] | None = None,
        run_mode: str = "seed_and_loop",
        rng_seed: int = 7,
    ) -> tuple[str, str]:
        if loop_count < 0:
            raise ValueError("loop_count must be greater than or equal to zero")
        if run_mode not in {"seed_and_loop", "seed_only"}:
            raise ValueError("run_mode must be seed_and_loop or seed_only")

        requested_loop_count = loop_count if run_mode == "seed_and_loop" else 0
        goals = tuple(optimization_targets or ()) or self._default_optimization_targets(target_score)

        parsed = ParsedObjective(
            target=target,
            ph=ph,
            functions=tuple(function for function in functions if function),
            length=length,
        )
        config = SeedFlowConfig(
            seed_count=seed_count,
            target_score=target_score,
            max_loops=loop_count,
            rng_seed=rng_seed,
            seed_sources=self._candidate_origins(seed_sources),
            optimization_goals=goals,
            ccdc_ligand_zip_path=str(self._ligand_archive_path()),
        )
        seed_flow = create_seeded_run(self.memory, objective, config=config, parsed=parsed)
        run_id = seed_flow.run.run_id
        job = self._create_job(run_id=run_id, action="create_run")
        self._set_latest_run(run_id)

        self.event_hub.publish(
            "run_started",
            run_id=run_id,
            job_id=job.job_id,
            loop_id=seed_flow.run.root_loop_id,
            data={
                "objective": objective,
                "seed_pool_id": seed_flow.pool.pool_id,
                "selected_seed_candidate_id": seed_flow.selected_seed.seed_candidate_id,
                "requested_loops": requested_loop_count,
                "run_mode": run_mode,
                "agent": NOVACORE_AGENT_NAME,
                "optimization_targets": goals,
            },
        )
        self.event_hub.publish(
            "loop_finalized",
            run_id=run_id,
            job_id=job.job_id,
            loop_id=seed_flow.run.root_loop_id,
            data={
                "status": "active",
                "index": 0,
                "latest_metrics": self.memory.get_loop(
                    run_id=run_id,
                    loop_id=seed_flow.run.root_loop_id,
                ).latest_metric_map(),
            },
        )
        self._start_loop_job(job, loop_count=requested_loop_count, start_loop_id=None)
        return run_id, job.job_id

    def run_loops(self, *, run_id: str, loop_count: int, start_loop_id: str | None = None) -> str:
        if loop_count < 0:
            raise ValueError("loop_count must be greater than or equal to zero")
        self.memory.get_run(run_id)
        if start_loop_id is not None:
            self.memory.get_loop(run_id=run_id, loop_id=start_loop_id)
        job = self._create_job(run_id=run_id, action="run_loops")
        self._set_latest_run(run_id)
        self.event_hub.publish(
            "run_started",
            run_id=run_id,
            job_id=job.job_id,
            loop_id=start_loop_id,
            data={"requested_loops": loop_count, "start_loop_id": start_loop_id},
        )
        self._start_loop_job(job, loop_count=loop_count, start_loop_id=start_loop_id)
        return job.job_id

    def agent_context(self, *, run_id: str, loop_id: str | None = None) -> dict:
        if loop_id is not None:
            context = self.memory.get_loop_context(run_id=run_id, loop_id=loop_id)
        else:
            context = self.memory.get_active_context(run_id)
        return context.to_agent_payload()

    def propose_chat_loop(
        self,
        *,
        run_id: str,
        sequence: str,
        change_set: ChangeSet,
        author: str = "Codex",
        parent_loop_id: str | None = None,
        candidate_name: str | None = None,
        branch_label: str | None = None,
        planning_note: str | None = None,
        advisor_reports: Iterable[HumanInput] = (),
        candidate_metadata: dict | None = None,
        loop_metadata: dict | None = None,
    ) -> LoopRecord:
        run = self.memory.get_run(run_id)
        pending_loop_ids = self._pending_loop_ids(run_id)
        if pending_loop_ids:
            pending = ", ".join(pending_loop_ids)
            raise InvalidLoopOperation(
                f"Run {run_id} already has unresolved pending loop(s): {pending}. "
                "Evaluate, reject, or finalize the pending loop before proposing another candidate."
            )

        parent_loop_id = parent_loop_id or run.active_loop_id
        parent = self.memory.get_loop(run_id=run_id, loop_id=parent_loop_id)
        if parent_loop_id != run.active_loop_id and not branch_label:
            raise InvalidLoopOperation(
                "Branching from a non-active loop requires an explicit branch_label."
            )
        if parent.status not in {LoopStatus.ACTIVE, LoopStatus.AVAILABLE, LoopStatus.ABANDONED}:
            raise InvalidLoopOperation(f"Cannot branch from a loop with status {parent.status.value}.")

        sanitized_sequence = "".join(sequence.strip().upper().split())
        if not sanitized_sequence:
            raise ValueError("Chat-proposed candidate sequence cannot be empty")

        loop = self.memory.append_loop(
            run_id=run_id,
            parent_loop_id=parent_loop_id,
            candidate=ProteinCandidate(
                sequence=sanitized_sequence,
                origin=CandidateOrigin.DERIVED,
                name=candidate_name,
                metadata={
                    **(candidate_metadata or {}),
                    "agent": author,
                    "chat_operated": True,
                },
            ),
            change_set=change_set,
            branch_label=branch_label,
            metadata={
                **(loop_metadata or {}),
                "agent": author,
                "chat_operated": True,
            },
        )
        if planning_note:
            loop = self.memory.record_human_input(
                run_id=run_id,
                loop_id=loop.loop_id,
                human_input=HumanInput(
                    author=author,
                    note=planning_note,
                    requested_changes=tuple(change.machine_diff for change in change_set.changes),
                    metadata={
                        "action": "chat_plan",
                        "chat_operated": True,
                    },
                ),
            )
        for report in advisor_reports:
            loop = self.memory.record_human_input(
                run_id=run_id,
                loop_id=loop.loop_id,
                human_input=report,
            )
        self._set_latest_run(run_id)
        return loop

    def evaluate_chat_loop(self, *, run_id: str, loop_id: str) -> str:
        loop = self.memory.get_loop(run_id=run_id, loop_id=loop_id)
        if loop.status != LoopStatus.PENDING:
            raise InvalidLoopOperation(f"Only pending loops can be evaluated; loop {loop_id} is {loop.status.value}.")
        running_job_id = self._running_evaluation_job_id(loop_id)
        if running_job_id is not None:
            raise InvalidLoopOperation(f"Loop {loop_id} already has running evaluation job {running_job_id}.")

        job = self._create_job(run_id=run_id, action="evaluate_chat_loop")
        with self._lock:
            self._job_loop_ids[job.job_id] = loop_id
        self._set_latest_run(run_id)
        self.event_hub.publish(
            "run_started",
            run_id=run_id,
            job_id=job.job_id,
            loop_id=loop_id,
            data={
                "action": "evaluate_chat_loop",
                "chat_operated": True,
            },
        )
        thread = threading.Thread(
            target=self._run_chat_evaluation_job,
            args=(job.job_id, loop_id),
            daemon=True,
        )
        thread.start()
        return job.job_id

    def write_chat_reflection(
        self,
        *,
        run_id: str,
        loop_id: str,
        reflection: LoopReflection,
        advisor_reports: Iterable[HumanInput] = (),
    ) -> LoopRecord:
        self._set_latest_run(run_id)
        loop = self.memory.set_loop_reflection(run_id=run_id, loop_id=loop_id, reflection=reflection)
        for report in advisor_reports:
            loop = self.memory.record_human_input(
                run_id=run_id,
                loop_id=loop.loop_id,
                human_input=report,
            )
        return loop

    def finalize_chat_loop(self, *, run_id: str, loop_id: str) -> LoopRecord:
        self._set_latest_run(run_id)
        return self.memory.finalize_loop(run_id=run_id, loop_id=loop_id)

    def reject_chat_loop(self, *, run_id: str, loop_id: str, actor: str, reason: str) -> LoopRecord:
        self._set_latest_run(run_id)
        running_job_id = self._running_evaluation_job_id(loop_id)
        if running_job_id is not None:
            raise InvalidLoopOperation(f"Cannot reject loop {loop_id} while evaluation job {running_job_id} is running.")
        return self.memory.reject_loop(run_id=run_id, loop_id=loop_id, actor=actor, reason=reason)

    def rollback(self, *, run_id: str, loop_id: str, actor: str, reason: str) -> LoopRecord:
        self._set_latest_run(run_id)
        return self.memory.rollback_to_loop(run_id=run_id, loop_id=loop_id, actor=actor, reason=reason)

    def latest_run_id(self) -> str | None:
        with self._lock:
            return self._latest_run_id

    def get_job(self, job_id: str) -> WorkbenchJob:
        with self._lock:
            return self._jobs[job_id]

    def list_jobs(self) -> tuple[WorkbenchJob, ...]:
        with self._lock:
            return tuple(self._jobs.values())

    def _pending_loop_ids(self, run_id: str) -> tuple[str, ...]:
        snapshot = self.memory.get_run_visualization(run_id)
        return tuple(
            node.loop_id
            for node in snapshot.nodes
            if node.status == LoopStatus.PENDING
        )

    def _running_evaluation_job_id(self, loop_id: str) -> str | None:
        with self._lock:
            for job_id, job in self._jobs.items():
                if (
                    job.action == "evaluate_chat_loop"
                    and job.status in {JobState.QUEUED, JobState.RUNNING}
                    and self._job_loop_ids.get(job_id) == loop_id
                ):
                    return job_id
        return None

    def boltz_api_auth_status(self) -> dict[str, str | bool | None]:
        key = os.environ.get("BOLTZ_API_KEY")
        source = None
        if key:
            source = "runtime" if self._runtime_boltz_api_key_set else "environment"
        return {
            "configured": bool(key),
            "source": source,
            "masked_key": _mask_secret(key) if key else None,
        }

    def set_boltz_api_key(self, api_key: str) -> dict[str, str | bool | None]:
        sanitized = api_key.strip()
        if not sanitized:
            raise ValueError("Boltz API key cannot be empty")
        os.environ["BOLTZ_API_KEY"] = sanitized
        with self._lock:
            self._runtime_boltz_api_key_set = True
        return self.boltz_api_auth_status()

    def clear_boltz_api_key(self) -> dict[str, str | bool | None]:
        with self._lock:
            self._runtime_boltz_api_key_set = False
        if self._initial_boltz_api_key:
            os.environ["BOLTZ_API_KEY"] = self._initial_boltz_api_key
        else:
            os.environ.pop("BOLTZ_API_KEY", None)
        return self.boltz_api_auth_status()

    def _create_job(self, *, run_id: str, action: str) -> WorkbenchJob:
        job = WorkbenchJob(job_id=f"job_{uuid4().hex}", run_id=run_id, action=action)
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def _set_latest_run(self, run_id: str) -> None:
        with self._lock:
            self._latest_run_id = run_id

    def _start_loop_job(self, job: WorkbenchJob, *, loop_count: int, start_loop_id: str | None) -> None:
        thread = threading.Thread(
            target=self._run_loop_job,
            args=(job.job_id, loop_count, start_loop_id),
            daemon=True,
        )
        thread.start()

    def _run_loop_job(self, job_id: str, loop_count: int, start_loop_id: str | None) -> None:
        job = self.get_job(job_id)
        self._mark_job(job_id, status=JobState.RUNNING, started_at=utc_now_iso())
        try:
            result: LoopRunnerResult | None = None
            if loop_count > 0:
                runner = build_novacore_runner(memory=self.memory, tool_config=self._tool_config())
                if start_loop_id:
                    result = runner.continue_from_loop(job.run_id, start_loop_id, loop_count=loop_count)
                else:
                    result = runner.run_for_loops(job.run_id, loop_count)
            result_payload = result.__dict__ if result is not None else {"loops_completed": 0}
            self._mark_job(
                job_id,
                status=JobState.SUCCEEDED,
                completed_at=utc_now_iso(),
                result=to_jsonable(result_payload),
            )
            self.event_hub.publish(
                "run_finished",
                run_id=job.run_id,
                job_id=job_id,
                data={"result": result_payload},
            )
        except Exception as exc:
            self._mark_job(
                job_id,
                status=JobState.FAILED,
                completed_at=utc_now_iso(),
                error=str(exc),
            )
            self.event_hub.publish(
                "run_failed",
                run_id=job.run_id,
                job_id=job_id,
                data={"error": str(exc), "error_type": type(exc).__name__},
            )

    def _run_chat_evaluation_job(self, job_id: str, loop_id: str) -> None:
        job = self.get_job(job_id)
        self._mark_job(job_id, status=JobState.RUNNING, started_at=utc_now_iso())
        try:
            loop = self.memory.get_loop(run_id=job.run_id, loop_id=loop_id)
            if loop.parent_loop_id is None:
                raise ValueError("Chat-operated evaluation requires a derived loop with a parent")
            context = self.memory.get_loop_context(run_id=job.run_id, loop_id=loop.parent_loop_id)
            tool_config = self._tool_config()

            boltz_evaluation = NovacoreBoltzCliEvaluator(config=tool_config).evaluate(context, loop)
            self.memory.attach_evaluation(
                run_id=job.run_id,
                loop_id=loop.loop_id,
                evaluation=boltz_evaluation,
            )
            screening_evaluation = NovacoreTrsScreeningPipeline().evaluate(context, loop, boltz_evaluation)
            self.memory.attach_evaluation(
                run_id=job.run_id,
                loop_id=loop.loop_id,
                evaluation=screening_evaluation,
            )
            verifier_evaluation = TouchstoneVerifierAdapter(config=tool_config).evaluate(
                context,
                loop,
                boltz_evaluation,
                screening_evaluation,
            )
            self.memory.attach_evaluation(
                run_id=job.run_id,
                loop_id=loop.loop_id,
                evaluation=verifier_evaluation,
            )
            evaluated_loop = self.memory.get_loop(run_id=job.run_id, loop_id=loop.loop_id)
            result_payload = {
                "loop_id": loop.loop_id,
                "latest_metrics": evaluated_loop.latest_metric_map(),
                "evaluation_kinds": [evaluation.kind.value for evaluation in evaluated_loop.evaluations],
                "chat_operated": True,
            }
            self._mark_job(
                job_id,
                status=JobState.SUCCEEDED,
                completed_at=utc_now_iso(),
                result=to_jsonable(result_payload),
            )
            self.event_hub.publish(
                "run_finished",
                run_id=job.run_id,
                job_id=job_id,
                loop_id=loop.loop_id,
                data={"result": result_payload},
            )
        except Exception as exc:
            self._mark_job(
                job_id,
                status=JobState.FAILED,
                completed_at=utc_now_iso(),
                error=str(exc),
            )
            self.event_hub.publish(
                "run_failed",
                run_id=job.run_id,
                job_id=job_id,
                loop_id=loop_id,
                data={"error": str(exc), "error_type": type(exc).__name__},
            )

    def _mark_job(self, job_id: str, **updates) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in updates.items():
                setattr(job, key, value)

    @staticmethod
    def _candidate_origins(seed_sources: Iterable[str]) -> tuple[CandidateOrigin, ...]:
        mapping = {
            "ccdc_csd": CandidateOrigin.CCDC_CSD,
            "de_novo": CandidateOrigin.DE_NOVO,
        }
        origins = tuple(mapping[source] for source in seed_sources if source in mapping)
        if not origins:
            raise ValueError("seed_sources must include ccdc_csd, de_novo, or both")
        return origins

    @staticmethod
    def _default_optimization_targets(target_score: float) -> tuple[MetricGoal, ...]:
        return (
            MetricGoal(
                name="trs_total",
                target=target_score,
                comparator="gte",
                weight=1.0,
                description="TRS total screening score.",
            ),
            MetricGoal(
                name="plddt",
                target=0.7,
                comparator="gte",
                weight=0.5,
                description="Boltz confidence score.",
            ),
        )

    @staticmethod
    def _ligand_archive_path() -> Path:
        return Path(__file__).resolve().parents[3] / "ligands_10000.zip"

    @staticmethod
    def _tool_config() -> NovacoreToolConfig:
        return NovacoreToolConfig(
            boltz_command=os.environ.get("NOVACORE_BOLTZ_COMMAND", "boltz"),
            enable_external_tools=os.environ.get("NOVACORE_ENABLE_EXTERNAL_TOOLS", "").lower()
            in {"1", "true", "yes"},
            prefer_boltz_api=os.environ.get("NOVACORE_PREFER_BOLTZ_API", "1").lower()
            in {"1", "true", "yes"},
            boltz_api_model=os.environ.get("NOVACORE_BOLTZ_API_MODEL", "boltz-2.1"),
            boltz_api_poll_interval_seconds=_env_float("NOVACORE_BOLTZ_API_POLL_INTERVAL_SECONDS", 5.0),
            boltz_api_timeout_seconds=_env_int("NOVACORE_BOLTZ_API_TIMEOUT_SECONDS", 3600),
            verifier_mcp_server=os.environ.get("NOVACORE_VERIFIER_MCP_SERVER"),
            touchstone_command=os.environ.get("NOVACORE_TOUCHSTONE_COMMAND", "touchstone"),
            touchstone_use_uvx=os.environ.get("NOVACORE_TOUCHSTONE_USE_UVX", "1").lower()
            in {"1", "true", "yes"},
            touchstone_deep=os.environ.get("NOVACORE_TOUCHSTONE_DEEP", "").lower()
            in {"1", "true", "yes"},
            touchstone_stress=os.environ.get("NOVACORE_TOUCHSTONE_STRESS", "").lower()
            in {"1", "true", "yes"},
            touchstone_timeout_seconds=_env_int("NOVACORE_TOUCHSTONE_TIMEOUT_SECONDS", 1800),
            boltz_accelerator=os.environ.get("NOVACORE_BOLTZ_ACCELERATOR", "cpu"),
            boltz_model=os.environ.get("NOVACORE_BOLTZ_MODEL", "boltz2"),
            boltz_cache=os.environ.get("NOVACORE_BOLTZ_CACHE") or os.environ.get("BOLTZ_CACHE"),
            boltz_use_msa_server=os.environ.get("NOVACORE_BOLTZ_USE_MSA_SERVER", "").lower()
            in {"1", "true", "yes"},
            boltz_msa_server_url=os.environ.get("NOVACORE_BOLTZ_MSA_SERVER_URL", "https://api.colabfold.com"),
            boltz_msa_pairing_strategy=os.environ.get("NOVACORE_BOLTZ_MSA_PAIRING_STRATEGY", "greedy"),
            boltz_timeout_seconds=_env_int("NOVACORE_BOLTZ_TIMEOUT_SECONDS", 3600),
            boltz_recycling_steps=_env_optional_int("NOVACORE_BOLTZ_RECYCLING_STEPS"),
            boltz_sampling_steps=_env_optional_int("NOVACORE_BOLTZ_SAMPLING_STEPS"),
            boltz_diffusion_samples=_env_optional_int("NOVACORE_BOLTZ_DIFFUSION_SAMPLES"),
        )


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value else default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value else default


def _env_optional_int(name: str) -> int | None:
    value = os.environ.get(name)
    return int(value) if value else None


def _mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}...{value[-4:]}"
