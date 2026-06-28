from __future__ import annotations

import random
import re
from dataclasses import dataclass
from pathlib import Path

from materialhack_memory import (
    ArtifactRef,
    CandidateOrigin,
    ConditionSet,
    DesignObjective,
    DesignRun,
    EvaluationKind,
    EvaluationResult,
    MemoryRepository,
    MetricGoal,
    MetricValue,
    SeedCandidate,
    SeedCandidatePool,
    SeedSelectionDecision,
)

from materialhack_agent.ligand_catalog import select_ligand_model
from materialhack_agent.trs_adapter import score_sequence_trs


AMINO_ACIDS = "ACDEFGHIKLMNPQRSTVWY"


@dataclass(frozen=True)
class ParsedObjective:
    target: str
    ph: float
    functions: tuple[str, ...]
    length: int


@dataclass(frozen=True)
class SeedFlowConfig:
    seed_count: int = 5
    target_score: float = 0.8
    max_loops: int | None = 2
    rng_seed: int = 7
    created_by: str = "Novacore"
    seed_sources: tuple[CandidateOrigin, ...] = (CandidateOrigin.CCDC_CSD, CandidateOrigin.DE_NOVO)
    optimization_goals: tuple[MetricGoal, ...] | None = None
    ccdc_ligand_zip_path: str | None = None


@dataclass(frozen=True)
class SeedFlowResult:
    objective: DesignObjective
    conditions: ConditionSet
    pool: SeedCandidatePool
    decision: SeedSelectionDecision
    selected_seed: SeedCandidate
    run: DesignRun


def create_seeded_run(
    repository: MemoryRepository,
    objective_text: str,
    *,
    config: SeedFlowConfig | None = None,
    parsed: ParsedObjective | None = None,
) -> SeedFlowResult:
    """Run the WF-owned pre-loop handoff and create durable `loop_0`."""

    config = config or SeedFlowConfig()
    if config.seed_count <= 0:
        raise ValueError("seed_count must be greater than zero")
    if not config.seed_sources:
        raise ValueError("seed_sources must include at least one source")

    parsed = parsed or parse_objective(objective_text)
    objective = to_design_objective(
        objective_text,
        parsed=parsed,
        target_score=config.target_score,
        max_loops=config.max_loops,
        goals=config.optimization_goals,
    )
    conditions = to_conditions(parsed)
    candidates = generate_seed_candidates(parsed, config=config)
    ranked_seed_candidate_ids = rank_seed_candidates(candidates)

    pool = repository.create_seed_pool(
        objective=objective,
        conditions=conditions,
        candidates=candidates,
        created_by=config.created_by,
        metadata={
            "source": "materialhack_agent.seed_flow",
            "stub": True,
            "rng_seed": config.rng_seed,
        },
    )
    selected_seed_candidate_id = ranked_seed_candidate_ids[0]
    decision = repository.record_seed_selection_decision(
        pool_id=pool.pool_id,
        selected_seed_candidate_id=selected_seed_candidate_id,
        rationale=(
            "Selected the top ranked Novacore pre-loop seed by TRS score, screening score, "
            "then pLDDT and pTM as deterministic tie breakers."
        ),
        selected_by=config.created_by,
        selection_method="novacore_screening_boltz_rank",
        ranked_seed_candidate_ids=ranked_seed_candidate_ids,
        metadata={
            "stub": True,
            "agent": "Novacore",
            "ranking_metric_order": ["trs_total", "screen_score", "plddt", "ptm"],
        },
    )
    run = repository.create_run_from_seed_selection(decision_id=decision.decision_id)
    selected_seed = repository.get_seed_candidate(
        pool_id=pool.pool_id,
        seed_candidate_id=selected_seed_candidate_id,
    )

    return SeedFlowResult(
        objective=objective,
        conditions=conditions,
        pool=pool,
        decision=decision,
        selected_seed=selected_seed,
        run=run,
    )


def parse_objective(objective_text: str) -> ParsedObjective:
    text = objective_text.lower()
    ph_match = re.search(r"ph\s*([\d.]+)", text)
    ph = float(ph_match.group(1)) if ph_match else 7.0

    length_match = re.search(r"(\d+)\s*(?:aa|residues?|amino acids?)", text)
    length = int(length_match.group(1)) if length_match else 60

    target = _parse_target(text)
    functions = tuple(
        function
        for marker, function in _FUNCTION_KEYWORDS.items()
        if marker in text
    ) or ("bind",)

    return ParsedObjective(target=target, ph=ph, functions=functions, length=length)


def to_design_objective(
    objective_text: str,
    *,
    parsed: ParsedObjective,
    target_score: float,
    max_loops: int | None,
    goals: tuple[MetricGoal, ...] | None = None,
) -> DesignObjective:
    return DesignObjective(
        description=objective_text,
        goals=goals or _default_goals(target_score),
        max_loops=max_loops,
        custom={
            "target": parsed.target,
            "ph": parsed.ph,
            "functions": list(parsed.functions),
            "length": parsed.length,
        },
    )


def to_conditions(parsed: ParsedObjective) -> ConditionSet:
    return ConditionSet.common(
        binding_target=parsed.target,
        ph=parsed.ph,
        custom={
            "functions": list(parsed.functions),
            "requested_length": parsed.length,
        },
    )


def generate_seed_candidates(parsed: ParsedObjective, *, config: SeedFlowConfig) -> tuple[SeedCandidate, ...]:
    rng = random.Random(config.rng_seed)
    candidates: list[SeedCandidate] = []
    for index in range(config.seed_count):
        seed_id = f"seed_{index + 1:03d}"
        origin = config.seed_sources[index % len(config.seed_sources)]
        ligand_ref = (
            select_ligand_model(parsed.target, archive_path=config.ccdc_ligand_zip_path, index=index)
            if origin == CandidateOrigin.CCDC_CSD
            else None
        )
        length = max(20, parsed.length + rng.randint(-5, 5))
        sequence = _generate_sequence(rng, length)
        plddt = round(rng.uniform(0.55, 0.92), 3)
        ptm = round(rng.uniform(0.35, 0.88), 3)
        screen_score = round(0.55 * plddt + 0.45 * ptm, 3)
        trs_result = score_sequence_trs(sequence, target=parsed.target)

        candidates.append(
            SeedCandidate(
                seed_candidate_id=seed_id,
                sequence=sequence,
                origin=origin,
                source_database="CCDC/CSD" if origin == CandidateOrigin.CCDC_CSD else None,
                source_id=ligand_ref.source_id if ligand_ref is not None else (
                    f"CSD-STUB-{index + 1:04d}" if origin == CandidateOrigin.CCDC_CSD else None
                ),
                name=f"{parsed.target}_seed_{index + 1}",
                structure_artifacts=(
                    *(
                        (
                            ArtifactRef(
                                uri=ligand_ref.uri,
                                kind="ccdc_ligand_model",
                                format="mol2",
                                metadata={
                                    "archive": Path(config.ccdc_ligand_zip_path).name
                                    if config.ccdc_ligand_zip_path
                                    else None,
                                    "entry_name": ligand_ref.entry_name,
                                    "metal": ligand_ref.metal,
                                },
                            ),
                        )
                        if ligand_ref is not None
                        else ()
                    ),
                    ArtifactRef(
                        uri=f"memory://preloop/{seed_id}/structure.cif",
                        kind="candidate_structure",
                        format="mmcif",
                        metadata={"stub": True},
                    ),
                ),
                boltz_artifacts=(
                    ArtifactRef(
                        uri=f"memory://preloop/{seed_id}/boltz_metrics.json",
                        kind="boltz_metrics",
                        format="json",
                        metadata={"stub": True},
                    ),
                ),
                evaluations=(
                    EvaluationResult(
                        kind=EvaluationKind.BOLTZ,
                        evaluator_name="wf-preloop-boltz-stub",
                        evaluator_version="stub.v1",
                        metrics=(
                            MetricValue(name="plddt", value=plddt, higher_is_better=True),
                            MetricValue(name="ptm", value=ptm, higher_is_better=True),
                        ),
                        passed=plddt >= 0.55,
                        summary="Deterministic pre-loop Boltz placeholder; replace with real Boltz adapter.",
                        artifacts=(
                            ArtifactRef(
                                uri=f"memory://preloop/{seed_id}/boltz_result.json",
                                kind="boltz_result",
                                format="json",
                                metadata={"stub": True},
                            ),
                        ),
                        metadata={"stub": True},
                    ),
                    EvaluationResult(
                        kind=EvaluationKind.SCREENING,
                        evaluator_name="wf-preloop-screening-stub",
                        evaluator_version="stub.v1",
                        metrics=(MetricValue(name="screen_score", value=screen_score, higher_is_better=True),),
                        passed=screen_score >= 0.5,
                        summary="Deterministic seed screening placeholder.",
                        metadata={"stub": True},
                    ),
                    EvaluationResult(
                        kind=EvaluationKind.SCREENING,
                        evaluator_name="trs",
                        evaluator_version="adapter.v1",
                        metrics=(
                            MetricValue(name="trs_total", value=trs_result.total, higher_is_better=True),
                            MetricValue(name="trs_raw_total", value=trs_result.raw_total, higher_is_better=True),
                        ),
                        passed=trs_result.total >= config.target_score,
                        summary="TRS scored the seed candidate contact graph before durable loop_0 selection.",
                        artifacts=(
                            ArtifactRef(
                                uri=f"memory://preloop/{seed_id}/trs/screening.json",
                                kind="trs_screening_result",
                                format="json",
                                metadata={"input_mode": trs_result.input_mode},
                            ),
                        ),
                        metadata={
                            "agent": "Novacore",
                            "trs_components": trs_result.components,
                            "trs_weights": trs_result.weights,
                            "trs_raw_total": trs_result.raw_total,
                            "input_mode": trs_result.input_mode,
                            "metal_node": trs_result.metal_node,
                            "contact_residue_indices": list(trs_result.contact_residue_indices),
                        },
                    ),
                    EvaluationResult(
                        kind=EvaluationKind.VERIFIER,
                        evaluator_name="touchstone-pending",
                        evaluator_version=None,
                        metrics=(),
                        passed=None,
                        summary="Touchstone verification needs a generated structure; no seed-stage verifier score was produced.",
                        metadata={"adapter_status": "needs_structure", "agent": "Novacore", "expected_verifier": "touchstone"},
                    ),
                ),
                metadata={
                    "target": parsed.target,
                    "ph": parsed.ph,
                    "functions": list(parsed.functions),
                    "agent": "Novacore",
                    "ccdc_ligand_model": ligand_ref.uri if ligand_ref is not None else None,
                    "stub": True,
                },
            )
        )
    return tuple(candidates)


def rank_seed_candidates(candidates: tuple[SeedCandidate, ...]) -> tuple[str, ...]:
    ranked = sorted(
        candidates,
        key=lambda candidate: (
            _metric(candidate, "trs_total"),
            _metric(candidate, "screen_score"),
            _metric(candidate, "plddt"),
            _metric(candidate, "ptm"),
        ),
        reverse=True,
    )
    return tuple(candidate.seed_candidate_id for candidate in ranked)


def _default_goals(target_score: float) -> tuple[MetricGoal, ...]:
    return (
        MetricGoal(
            name="trs_total",
            target=target_score,
            comparator="gte",
            weight=1.0,
            description="TRS total screening score for metal-binding topology.",
        ),
        MetricGoal(
            name="plddt",
            target=0.7,
            comparator="gte",
            weight=0.5,
            description="Boltz structure confidence target.",
        ),
    )


_KNOWN_TARGETS = [
    "zn2+",
    "ca2+",
    "mg2+",
    "fe2+",
    "fe3+",
    "cu2+",
    "ni2+",
    "mn2+",
    "atp",
    "adp",
    "dna",
    "rna",
    "lipid",
    "collagen",
    "heparin",
]

_FUNCTION_KEYWORDS = {
    "bind": "bind",
    "polymeriz": "polymerize",
    "catalyz": "catalyze",
    "cleave": "cleave",
    "fold": "fold",
    "fluoresc": "fluoresce",
    "transport": "transport",
    "inhibit": "inhibit",
    "stabiliz": "stabilize",
    "dimeriz": "dimerize",
    "aggregat": "aggregate",
    "sens": "sense",
}


def _parse_target(text: str) -> str:
    target = next((item.upper() for item in _KNOWN_TARGETS if item in text), None)
    if target is not None:
        return target

    bind_match = re.search(r"binds?\s+(?:to\s+)?([a-z0-9+\-]+)", text)
    return bind_match.group(1).upper() if bind_match else "unknown"


def _generate_sequence(rng: random.Random, length: int) -> str:
    return "".join(rng.choice(AMINO_ACIDS) for _ in range(length))


def _metric(candidate: SeedCandidate, name: str) -> float:
    for evaluation in candidate.evaluations:
        metric_map = evaluation.metric_map()
        if name in metric_map:
            return metric_map[name]
    return 0.0
