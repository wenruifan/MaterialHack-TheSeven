from __future__ import annotations

from dataclasses import dataclass

from materialhack_memory import (
    AgentLoopContext,
    ArtifactRef,
    CandidateOrigin,
    ChangeOperation,
    ChangeSet,
    EvaluationKind,
    EvaluationResult,
    LoopRecord,
    LoopReflection,
    MetricValue,
    ProteinCandidate,
    ProteinChange,
)


AMINO_ACIDS = "ACDEFGHIKLMNPQRSTVWY"


@dataclass(frozen=True)
class DeterministicChangePlanner:
    author: str = "loop_runner_stub"

    def plan_change(self, context: AgentLoopContext) -> ChangeSet:
        sequence = context.sequence
        if not sequence:
            raise ValueError("Cannot plan a protein change for an empty sequence")

        position_index = context.loop_index % len(sequence)
        from_residue = sequence[position_index]
        to_residue = _next_residue(from_residue, offset=context.loop_index + 1)
        machine_diff = f"{from_residue}{position_index + 1}{to_residue}"

        return ChangeSet(
            summary=f"Apply deterministic substitution {machine_diff}",
            why=(
                "Use the latest verifier and screening feedback to make one "
                "bounded substitution for the next optimization loop."
            ),
            changes=(
                ProteinChange(
                    operation=ChangeOperation.SUBSTITUTE,
                    machine_diff=machine_diff,
                    position=position_index + 1,
                    from_residue=from_residue,
                    to_residue=to_residue,
                    rationale=(
                        "A single conservative substitution gives the runner a "
                        "traceable hypothesis to evaluate."
                    ),
                    expected_effect="Improve verifier-aligned binding without changing loop scope.",
                ),
            ),
            author=self.author,
        )


@dataclass(frozen=True)
class DeterministicCandidateGenerator:
    name_prefix: str = "derived"

    def generate_candidate(self, context: AgentLoopContext, change_set: ChangeSet) -> ProteinCandidate:
        sequence = list(context.sequence)
        for change in change_set.changes:
            if change.operation != ChangeOperation.SUBSTITUTE:
                raise ValueError(f"Unsupported deterministic change operation: {change.operation.value}")
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
            origin=CandidateOrigin.DERIVED,
            name=f"{self.name_prefix}_{context.loop_index + 1}",
            metadata={
                "parent_loop_id": context.active_loop_id,
                "change_summary": change_set.summary,
            },
        )


@dataclass(frozen=True)
class DeterministicBoltzEvaluator:
    evaluator_name: str = "deterministic-boltz-stub"

    def evaluate(self, context: AgentLoopContext, loop: LoopRecord) -> EvaluationResult:
        loop_number = context.loop_index + 1
        plddt = min(0.95, 0.62 + 0.03 * loop_number)
        ptm = min(0.9, 0.45 + 0.025 * loop_number)
        return EvaluationResult(
            kind=EvaluationKind.BOLTZ,
            evaluator_name=self.evaluator_name,
            evaluator_version="stub.v1",
            metrics=(
                MetricValue(name="plddt", value=round(plddt, 3), higher_is_better=True),
                MetricValue(name="ptm", value=round(ptm, 3), higher_is_better=True),
            ),
            passed=True,
            summary="Deterministic Boltz placeholder produced a plausible structure-confidence signal.",
            artifacts=(
                ArtifactRef(
                    uri=f"memory://{loop.run_id}/{loop.loop_id}/boltz_prediction.json",
                    kind="boltz_prediction",
                    format="json",
                ),
            ),
        )


@dataclass(frozen=True)
class DeterministicScreeningPipeline:
    evaluator_name: str = "deterministic-screening-stub"

    def evaluate(
        self,
        context: AgentLoopContext,
        loop: LoopRecord,
        boltz_evaluation: EvaluationResult,
    ) -> EvaluationResult:
        score = min(1.0, 0.35 + 0.08 * (context.loop_index + 1))
        return EvaluationResult(
            kind=EvaluationKind.SCREENING,
            evaluator_name=self.evaluator_name,
            evaluator_version="stub.v1",
            metrics=(MetricValue(name="screen_score", value=round(score, 3), higher_is_better=True),),
            passed=score >= 0.4,
            summary="Deterministic screening placeholder scored the generated candidate.",
            metadata={"boltz_metrics": boltz_evaluation.metric_map()},
        )


@dataclass(frozen=True)
class DeterministicVerifier:
    evaluator_name: str = "deterministic-verifier-stub"
    metric_step: float = 0.2

    def evaluate(
        self,
        context: AgentLoopContext,
        loop: LoopRecord,
        boltz_evaluation: EvaluationResult,
        screening_evaluation: EvaluationResult,
    ) -> EvaluationResult:
        metrics: list[MetricValue] = []
        for goal in context.objective.goals:
            current = context.latest_metrics.get(goal.name)
            if current is None:
                current = goal.target - self.metric_step if goal.comparator == "gte" else goal.target + self.metric_step
            if goal.comparator == "gte":
                value = min(1.0, current + self.metric_step)
                higher_is_better = True
            elif goal.comparator == "lte":
                value = max(0.0, current - self.metric_step)
                higher_is_better = False
            elif goal.comparator == "eq":
                value = goal.target
                higher_is_better = None
            else:
                raise ValueError(f"Unsupported goal comparator: {goal.comparator}")
            metrics.append(
                MetricValue(
                    name=goal.name,
                    value=round(value, 3),
                    unit=goal.unit,
                    higher_is_better=higher_is_better,
                )
            )

        if not metrics:
            metrics.append(MetricValue(name="verifier_score", value=0.5, higher_is_better=True))

        return EvaluationResult(
            kind=EvaluationKind.VERIFIER,
            evaluator_name=self.evaluator_name,
            evaluator_version="stub.v1",
            metrics=tuple(metrics),
            passed=context.objective.goals_satisfied_by({metric.name: metric.value for metric in metrics}),
            summary="Deterministic verifier placeholder produced objective-aligned metrics.",
            metadata={
                "boltz_metrics": boltz_evaluation.metric_map(),
                "screening_metrics": screening_evaluation.metric_map(),
            },
        )


@dataclass(frozen=True)
class DeterministicReflectionWriter:
    def write_reflection(
        self,
        context: AgentLoopContext,
        loop: LoopRecord,
        boltz_evaluation: EvaluationResult,
        screening_evaluation: EvaluationResult,
        verifier_evaluation: EvaluationResult,
    ) -> LoopReflection:
        verifier_metrics = verifier_evaluation.metric_map()
        return LoopReflection(
            went_well=(
                "The runner completed one bounded change and collected Boltz, screening, and verifier outputs.",
            ),
            went_wrong=(
                "This is a deterministic placeholder, so model uncertainty is not represented yet.",
            ),
            next_actions=(
                "Use the verifier deltas and any human comments before proposing the next single change.",
            ),
            notes=(
                f"Loop {loop.loop_id} evaluated metrics: "
                + ", ".join(f"{name}={value:.3f}" for name, value in sorted(verifier_metrics.items()))
            ),
        )


def _next_residue(residue: str, *, offset: int) -> str:
    try:
        residue_index = AMINO_ACIDS.index(residue)
    except ValueError:
        residue_index = 0
    return AMINO_ACIDS[(residue_index + offset) % len(AMINO_ACIDS)]
