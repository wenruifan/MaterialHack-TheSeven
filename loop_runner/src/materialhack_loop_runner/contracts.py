from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, TypedDict

from materialhack_memory import (
    AgentLoopContext,
    ChangeSet,
    EvaluationResult,
    LoopRecord,
    LoopReflection,
    ProteinCandidate,
)
from materialhack_memory.models import JsonValue


class StopReason(str, Enum):
    GOALS_MET = "goals_met"
    FIXED_LOOP_COUNT_COMPLETE = "fixed_loop_count_complete"
    LOOP_BUDGET_EXHAUSTED = "loop_budget_exhausted"
    ADAPTER_FAILURE = "adapter_failure"


class LoopRunnerError(RuntimeError):
    pass


class LoopRunnerConfigError(LoopRunnerError):
    pass


@dataclass(frozen=True)
class LoopRunnerResult:
    run_id: str
    start_loop_id: str
    active_loop_id: str
    loops_completed: int
    stop_reason: StopReason
    goals_satisfied: bool
    final_metrics: dict[str, float] = field(default_factory=dict)
    pending_loop_id: str | None = None
    failed_stage: str | None = None
    error_message: str | None = None


class LoopRunnerState(TypedDict, total=False):
    run_id: str
    start_loop_id: str | None
    initial_loop_id: str
    loop_cap: int
    check_thresholds: bool
    fixed_count_mode: bool
    loops_completed: int
    done: bool
    stop_reason: StopReason
    error_message: str | None
    failed_stage: str | None
    context: AgentLoopContext
    change_set: ChangeSet
    candidate: ProteinCandidate
    pending_loop: LoopRecord
    pending_loop_id: str | None
    boltz_evaluation: EvaluationResult
    screening_evaluation: EvaluationResult
    verifier_evaluation: EvaluationResult
    reflection: LoopReflection
    metadata: dict[str, JsonValue]


class ChangePlanner(Protocol):
    def plan_change(self, context: AgentLoopContext) -> ChangeSet:
        ...


class CandidateGenerator(Protocol):
    def generate_candidate(self, context: AgentLoopContext, change_set: ChangeSet) -> ProteinCandidate:
        ...


class BoltzEvaluator(Protocol):
    def evaluate(self, context: AgentLoopContext, loop: LoopRecord) -> EvaluationResult:
        ...


class ScreeningPipeline(Protocol):
    def evaluate(
        self,
        context: AgentLoopContext,
        loop: LoopRecord,
        boltz_evaluation: EvaluationResult,
    ) -> EvaluationResult:
        ...


class Verifier(Protocol):
    def evaluate(
        self,
        context: AgentLoopContext,
        loop: LoopRecord,
        boltz_evaluation: EvaluationResult,
        screening_evaluation: EvaluationResult,
    ) -> EvaluationResult:
        ...


class ReflectionWriter(Protocol):
    def write_reflection(
        self,
        context: AgentLoopContext,
        loop: LoopRecord,
        boltz_evaluation: EvaluationResult,
        screening_evaluation: EvaluationResult,
        verifier_evaluation: EvaluationResult,
    ) -> LoopReflection:
        ...
