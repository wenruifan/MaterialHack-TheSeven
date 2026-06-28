from __future__ import annotations

import json
from dataclasses import replace
from typing import Protocol
from uuid import uuid4

from materialhack_memory.models import (
    AgentLoopContext,
    ChangeSet,
    ConditionSet,
    DesignObjective,
    DesignRun,
    EvaluationKind,
    EvaluationResult,
    HumanInput,
    LoopCompletenessReport,
    LoopCompletenessRequirements,
    LoopGraphEdge,
    LoopGraphNode,
    LoopRecord,
    LoopReflection,
    LoopStatus,
    ProteinCandidate,
    RunVisualizationSnapshot,
    SeedCandidate,
    SeedCandidatePool,
    SeedSelectionDecision,
    from_jsonable,
    to_jsonable,
    utc_now_iso,
)


class MemoryRepositoryError(RuntimeError):
    pass


class RunNotFound(MemoryRepositoryError):
    pass


class LoopNotFound(MemoryRepositoryError):
    pass


class LoopIncomplete(MemoryRepositoryError):
    pass


class SeedPoolNotFound(MemoryRepositoryError):
    pass


class SeedCandidateNotFound(MemoryRepositoryError):
    pass


class SeedSelectionNotFound(MemoryRepositoryError):
    pass


class InvalidLoopOperation(MemoryRepositoryError):
    pass


class MemoryRepository(Protocol):
    """Storage contract to implement with TuringDB later."""

    def create_run(
        self,
        *,
        objective: DesignObjective,
        seed_candidate: ProteinCandidate,
        conditions: ConditionSet,
        seed_evaluations: tuple[EvaluationResult, ...] = (),
        seed_reflection: LoopReflection | None = None,
        run_id: str | None = None,
        loop_id: str | None = None,
        metadata: dict | None = None,
        seed_selection_decision: SeedSelectionDecision | None = None,
    ) -> DesignRun:
        ...

    def create_seed_pool(
        self,
        *,
        objective: DesignObjective,
        conditions: ConditionSet,
        candidates: tuple[SeedCandidate, ...] = (),
        created_by: str = "WF",
        pool_id: str | None = None,
        metadata: dict | None = None,
    ) -> SeedCandidatePool:
        ...

    def add_seed_candidate(self, *, pool_id: str, candidate: SeedCandidate) -> SeedCandidatePool:
        ...

    def record_seed_selection_decision(
        self,
        *,
        pool_id: str,
        selected_seed_candidate_id: str,
        rationale: str,
        selected_by: str = "WF",
        selection_method: str = "screening_verifier_rank",
        ranked_seed_candidate_ids: tuple[str, ...] = (),
        human_override: bool = False,
        decision_id: str | None = None,
        metadata: dict | None = None,
    ) -> SeedSelectionDecision:
        ...

    def create_run_from_seed_selection(
        self,
        *,
        decision_id: str,
        run_id: str | None = None,
        loop_id: str | None = None,
        seed_reflection: LoopReflection | None = None,
        metadata: dict | None = None,
    ) -> DesignRun:
        ...

    def append_loop(
        self,
        *,
        run_id: str,
        candidate: ProteinCandidate,
        change_set: ChangeSet,
        evaluations: tuple[EvaluationResult, ...] = (),
        reflection: LoopReflection | None = None,
        parent_loop_id: str | None = None,
        human_inputs: tuple[HumanInput, ...] = (),
        loop_id: str | None = None,
        branch_label: str | None = None,
        metadata: dict | None = None,
        make_active: bool = False,
        completeness_requirements: LoopCompletenessRequirements | None = None,
    ) -> LoopRecord:
        ...

    def rollback_to_loop(self, *, run_id: str, loop_id: str, actor: str, reason: str) -> LoopRecord:
        ...

    def reject_loop(self, *, run_id: str, loop_id: str, actor: str, reason: str) -> LoopRecord:
        ...

    def record_human_input(self, *, run_id: str, loop_id: str, human_input: HumanInput) -> LoopRecord:
        ...

    def add_loop_comment(
        self,
        *,
        run_id: str,
        loop_id: str,
        author: str,
        note: str,
        requested_changes: tuple[str, ...] = (),
        metadata: dict | None = None,
    ) -> LoopRecord:
        ...

    def attach_evaluation(self, *, run_id: str, loop_id: str, evaluation: EvaluationResult) -> LoopRecord:
        ...

    def set_loop_reflection(self, *, run_id: str, loop_id: str, reflection: LoopReflection) -> LoopRecord:
        ...

    def validate_loop_complete(
        self,
        *,
        run_id: str,
        loop_id: str,
        requirements: LoopCompletenessRequirements | None = None,
    ) -> LoopCompletenessReport:
        ...

    def finalize_loop(
        self,
        *,
        run_id: str,
        loop_id: str,
        requirements: LoopCompletenessRequirements | None = None,
        make_active: bool = True,
    ) -> LoopRecord:
        ...

    def get_seed_pool(self, pool_id: str) -> SeedCandidatePool:
        ...

    def get_seed_candidate(self, *, pool_id: str, seed_candidate_id: str) -> SeedCandidate:
        ...

    def get_seed_selection_decision(self, decision_id: str) -> SeedSelectionDecision:
        ...

    def get_active_context(self, run_id: str) -> AgentLoopContext:
        ...

    def get_loop_context(self, *, run_id: str, loop_id: str) -> AgentLoopContext:
        ...

    def get_run_visualization(self, run_id: str) -> RunVisualizationSnapshot:
        ...

    def extract_sequence(self, *, run_id: str, loop_id: str | None = None) -> str:
        ...


class InMemoryProteinMemoryRepository:
    """Reference implementation for tests and local agent-loop development."""

    def __init__(self) -> None:
        self._runs: dict[str, DesignRun] = {}
        self._loops: dict[str, dict[str, LoopRecord]] = {}
        self._seed_pools: dict[str, SeedCandidatePool] = {}
        self._seed_selection_decisions: dict[str, SeedSelectionDecision] = {}

    def create_run(
        self,
        *,
        objective: DesignObjective,
        seed_candidate: ProteinCandidate,
        conditions: ConditionSet,
        seed_evaluations: tuple[EvaluationResult, ...] = (),
        seed_reflection: LoopReflection | None = None,
        run_id: str | None = None,
        loop_id: str | None = None,
        metadata: dict | None = None,
        seed_selection_decision: SeedSelectionDecision | None = None,
    ) -> DesignRun:
        run_id = run_id or self._new_id("run")
        loop_id = loop_id or self._new_id("loop")
        if run_id in self._runs:
            raise InvalidLoopOperation(f"Run already exists: {run_id}")

        seed_loop = LoopRecord(
            run_id=run_id,
            loop_id=loop_id,
            index=0,
            parent_loop_id=None,
            candidate=seed_candidate,
            conditions=conditions,
            evaluations=seed_evaluations,
            reflection=seed_reflection or LoopReflection(),
            status=LoopStatus.ACTIVE,
            metadata={
                **(metadata or {}),
                **(
                    {"seed_selection_decision_id": seed_selection_decision.decision_id}
                    if seed_selection_decision is not None
                    else {}
                ),
            },
        )
        run = DesignRun(
            run_id=run_id,
            objective=objective,
            root_loop_id=loop_id,
            active_loop_id=loop_id,
            seed_selection_decision=seed_selection_decision,
            metadata=metadata or {},
        )
        self._runs[run_id] = run
        self._loops[run_id] = {loop_id: seed_loop}
        return run

    def create_seed_pool(
        self,
        *,
        objective: DesignObjective,
        conditions: ConditionSet,
        candidates: tuple[SeedCandidate, ...] = (),
        created_by: str = "WF",
        pool_id: str | None = None,
        metadata: dict | None = None,
    ) -> SeedCandidatePool:
        pool_id = pool_id or self._new_id("seed_pool")
        if pool_id in self._seed_pools:
            raise InvalidLoopOperation(f"Seed pool already exists: {pool_id}")
        self._validate_unique_seed_candidates(candidates)
        pool = SeedCandidatePool(
            pool_id=pool_id,
            objective=objective,
            conditions=conditions,
            candidates=candidates,
            created_by=created_by,
            metadata=metadata or {},
        )
        self._seed_pools[pool_id] = pool
        return pool

    def add_seed_candidate(self, *, pool_id: str, candidate: SeedCandidate) -> SeedCandidatePool:
        pool = self.get_seed_pool(pool_id)
        if any(existing.seed_candidate_id == candidate.seed_candidate_id for existing in pool.candidates):
            raise InvalidLoopOperation(
                f"Seed candidate already exists in pool {pool_id}: {candidate.seed_candidate_id}"
            )
        updated = replace(pool, candidates=pool.candidates + (candidate,))
        self._seed_pools[pool_id] = updated
        return updated

    def record_seed_selection_decision(
        self,
        *,
        pool_id: str,
        selected_seed_candidate_id: str,
        rationale: str,
        selected_by: str = "WF",
        selection_method: str = "screening_verifier_rank",
        ranked_seed_candidate_ids: tuple[str, ...] = (),
        human_override: bool = False,
        decision_id: str | None = None,
        metadata: dict | None = None,
    ) -> SeedSelectionDecision:
        self.get_seed_candidate(pool_id=pool_id, seed_candidate_id=selected_seed_candidate_id)
        for seed_candidate_id in ranked_seed_candidate_ids:
            self.get_seed_candidate(pool_id=pool_id, seed_candidate_id=seed_candidate_id)
        if ranked_seed_candidate_ids:
            if selected_seed_candidate_id not in ranked_seed_candidate_ids:
                raise InvalidLoopOperation(
                    "Selected seed candidate must be included in ranked_seed_candidate_ids"
                )
            if len(set(ranked_seed_candidate_ids)) != len(ranked_seed_candidate_ids):
                raise InvalidLoopOperation("ranked_seed_candidate_ids cannot contain duplicates")

        decision_id = decision_id or self._new_id("seed_selection")
        if decision_id in self._seed_selection_decisions:
            raise InvalidLoopOperation(f"Seed selection decision already exists: {decision_id}")

        decision = SeedSelectionDecision(
            decision_id=decision_id,
            pool_id=pool_id,
            selected_seed_candidate_id=selected_seed_candidate_id,
            rationale=rationale,
            selected_by=selected_by,
            selection_method=selection_method,
            ranked_seed_candidate_ids=ranked_seed_candidate_ids or (selected_seed_candidate_id,),
            human_override=human_override,
            metadata=metadata or {},
        )
        self._seed_selection_decisions[decision_id] = decision
        return decision

    def create_run_from_seed_selection(
        self,
        *,
        decision_id: str,
        run_id: str | None = None,
        loop_id: str | None = None,
        seed_reflection: LoopReflection | None = None,
        metadata: dict | None = None,
    ) -> DesignRun:
        decision = self.get_seed_selection_decision(decision_id)
        pool = self.get_seed_pool(decision.pool_id)
        seed = self.get_seed_candidate(
            pool_id=decision.pool_id,
            seed_candidate_id=decision.selected_seed_candidate_id,
        )
        run_metadata = {
            **(metadata or {}),
            "seed_pool_id": pool.pool_id,
            "seed_selection_decision_id": decision.decision_id,
            "selected_seed_candidate_id": seed.seed_candidate_id,
        }
        return self.create_run(
            objective=pool.objective,
            seed_candidate=seed.to_protein_candidate(),
            conditions=pool.conditions,
            seed_evaluations=seed.evaluations,
            seed_reflection=seed_reflection,
            run_id=run_id,
            loop_id=loop_id,
            metadata=run_metadata,
            seed_selection_decision=decision,
        )

    def append_loop(
        self,
        *,
        run_id: str,
        candidate: ProteinCandidate,
        change_set: ChangeSet,
        evaluations: tuple[EvaluationResult, ...] = (),
        reflection: LoopReflection | None = None,
        parent_loop_id: str | None = None,
        human_inputs: tuple[HumanInput, ...] = (),
        loop_id: str | None = None,
        branch_label: str | None = None,
        metadata: dict | None = None,
        make_active: bool = False,
        completeness_requirements: LoopCompletenessRequirements | None = None,
    ) -> LoopRecord:
        run = self.get_run(run_id)
        parent_loop_id = parent_loop_id or run.active_loop_id
        parent = self.get_loop(run_id=run_id, loop_id=parent_loop_id)
        loop_id = loop_id or self._new_id("loop")
        if loop_id in self._loops[run_id]:
            raise InvalidLoopOperation(f"Loop already exists in run {run_id}: {loop_id}")

        loop = LoopRecord(
            run_id=run_id,
            loop_id=loop_id,
            index=parent.index + 1,
            parent_loop_id=parent_loop_id,
            candidate=candidate,
            conditions=parent.conditions,
            change_set=change_set,
            evaluations=evaluations,
            reflection=reflection or LoopReflection(),
            human_inputs=human_inputs,
            status=LoopStatus.PENDING,
            branch_label=branch_label,
            metadata=metadata or {},
        )
        self._loops[run_id][loop_id] = loop

        if make_active:
            return self.finalize_loop(
                run_id=run_id,
                loop_id=loop_id,
                requirements=completeness_requirements,
                make_active=True,
            )
        return loop

    def rollback_to_loop(self, *, run_id: str, loop_id: str, actor: str, reason: str) -> LoopRecord:
        run = self.get_run(run_id)
        target = self.get_loop(run_id=run_id, loop_id=loop_id)
        if target.status == LoopStatus.PENDING:
            raise InvalidLoopOperation("Cannot roll back to a pending loop; finalize or reject it first")
        if target.status in {LoopStatus.REJECTED, LoopStatus.TERMINAL}:
            raise InvalidLoopOperation(f"Cannot roll back to a loop with status {target.status.value}")
        old_active_id = run.active_loop_id

        if old_active_id != loop_id and self._is_ancestor(run_id, ancestor_id=loop_id, loop_id=old_active_id):
            for descendant_id in self._descendant_ids(run_id, loop_id):
                descendant = self._loops[run_id][descendant_id]
                if descendant.status not in {LoopStatus.REJECTED, LoopStatus.TERMINAL}:
                    self._loops[run_id][descendant_id] = replace(descendant, status=LoopStatus.ABANDONED)
        elif old_active_id != loop_id:
            old_active = self._loops[run_id][old_active_id]
            if old_active.status not in {LoopStatus.REJECTED, LoopStatus.TERMINAL}:
                self._loops[run_id][old_active_id] = replace(old_active, status=LoopStatus.ABANDONED)

        rollback_note = HumanInput(author=actor, note=f"Rollback selected this loop: {reason}")
        target = replace(target, human_inputs=target.human_inputs + (rollback_note,))
        self._loops[run_id][loop_id] = target
        self._activate_loop(run_id=run_id, loop_id=loop_id, abandon_previous=False)
        return self.get_loop(run_id=run_id, loop_id=loop_id)

    def reject_loop(self, *, run_id: str, loop_id: str, actor: str, reason: str) -> LoopRecord:
        run = self.get_run(run_id)
        loop = self.get_loop(run_id=run_id, loop_id=loop_id)
        if loop.status == LoopStatus.ACTIVE or loop.loop_id == run.active_loop_id:
            raise InvalidLoopOperation("Cannot reject the active loop; roll back to another loop instead")
        if loop.status in {LoopStatus.REJECTED, LoopStatus.TERMINAL}:
            raise InvalidLoopOperation(f"Cannot reject a loop with status {loop.status.value}")

        rejection_note = HumanInput(
            author=actor,
            note=f"Rejected this loop: {reason}",
            metadata={
                "action": "reject",
                "actor": actor,
                "active_loop_id": run.active_loop_id,
            },
        )
        rejected = replace(
            loop,
            status=LoopStatus.REJECTED,
            human_inputs=loop.human_inputs + (rejection_note,),
        )
        self._loops[run_id][loop_id] = rejected
        return rejected

    def record_human_input(self, *, run_id: str, loop_id: str, human_input: HumanInput) -> LoopRecord:
        loop = self.get_loop(run_id=run_id, loop_id=loop_id)
        updated = replace(loop, human_inputs=loop.human_inputs + (human_input,))
        self._loops[run_id][loop_id] = updated
        return updated

    def add_loop_comment(
        self,
        *,
        run_id: str,
        loop_id: str,
        author: str,
        note: str,
        requested_changes: tuple[str, ...] = (),
        metadata: dict | None = None,
    ) -> LoopRecord:
        return self.record_human_input(
            run_id=run_id,
            loop_id=loop_id,
            human_input=HumanInput(
                author=author,
                note=note,
                requested_changes=requested_changes,
                metadata=metadata or {},
            ),
        )

    def attach_evaluation(self, *, run_id: str, loop_id: str, evaluation: EvaluationResult) -> LoopRecord:
        loop = self.get_loop(run_id=run_id, loop_id=loop_id)
        if loop.status in {LoopStatus.REJECTED, LoopStatus.TERMINAL}:
            raise InvalidLoopOperation(f"Cannot attach evaluation to loop with status {loop.status.value}")
        updated = replace(loop, evaluations=loop.evaluations + (evaluation,))
        self._loops[run_id][loop_id] = updated
        return updated

    def set_loop_reflection(self, *, run_id: str, loop_id: str, reflection: LoopReflection) -> LoopRecord:
        loop = self.get_loop(run_id=run_id, loop_id=loop_id)
        if loop.status in {LoopStatus.REJECTED, LoopStatus.TERMINAL}:
            raise InvalidLoopOperation(f"Cannot set reflection on loop with status {loop.status.value}")
        updated = replace(loop, reflection=reflection)
        self._loops[run_id][loop_id] = updated
        return updated

    def validate_loop_complete(
        self,
        *,
        run_id: str,
        loop_id: str,
        requirements: LoopCompletenessRequirements | None = None,
    ) -> LoopCompletenessReport:
        loop = self.get_loop(run_id=run_id, loop_id=loop_id)
        requirements = requirements or LoopCompletenessRequirements.optimization_loop()
        missing: list[str] = []

        if not loop.candidate.sequence:
            missing.append("candidate.sequence")
        if loop.index > 0 and loop.parent_loop_id is None:
            missing.append("parent_loop_id")

        if requirements.require_change_set:
            if loop.change_set is None:
                missing.append("change_set")
            elif not loop.change_set.changes:
                missing.append("change_set.changes")

        if requirements.require_change_rationale and loop.change_set is not None:
            if not loop.change_set.why:
                missing.append("change_set.why")
            for index, change in enumerate(loop.change_set.changes):
                if not change.rationale:
                    missing.append(f"change_set.changes[{index}].rationale")

        if requirements.require_boltz and not self._has_boltz_output(loop):
            missing.append("boltz_evaluation_or_artifact")
        if requirements.require_screening and not self._has_evaluation_kind(loop, EvaluationKind.SCREENING):
            missing.append("screening_evaluation")
        if requirements.require_verifier and not self._has_evaluation_kind(loop, EvaluationKind.VERIFIER):
            missing.append("verifier_evaluation")

        if requirements.require_reflection and not self._has_reflection(loop.reflection):
            missing.append("reflection")
        if requirements.require_next_actions and not loop.reflection.next_actions:
            missing.append("reflection.next_actions")

        return LoopCompletenessReport(
            loop_id=loop_id,
            is_complete=not missing,
            missing_requirements=tuple(missing),
        )

    def finalize_loop(
        self,
        *,
        run_id: str,
        loop_id: str,
        requirements: LoopCompletenessRequirements | None = None,
        make_active: bool = True,
    ) -> LoopRecord:
        loop = self.get_loop(run_id=run_id, loop_id=loop_id)
        report = self.validate_loop_complete(
            run_id=run_id,
            loop_id=loop_id,
            requirements=requirements,
        )
        if not report.is_complete:
            missing = ", ".join(report.missing_requirements)
            raise LoopIncomplete(f"Loop {loop_id} is incomplete: {missing}")

        if make_active:
            run = self.get_run(run_id)
            abandon_previous = loop.parent_loop_id is not None and loop.parent_loop_id != run.active_loop_id
            self._activate_loop(run_id=run_id, loop_id=loop_id, abandon_previous=abandon_previous)
            return self.get_loop(run_id=run_id, loop_id=loop_id)

        finalized = replace(loop, status=LoopStatus.AVAILABLE)
        self._loops[run_id][loop_id] = finalized
        return finalized

    def get_run(self, run_id: str) -> DesignRun:
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise RunNotFound(run_id) from exc

    def get_loop(self, *, run_id: str, loop_id: str) -> LoopRecord:
        self.get_run(run_id)
        try:
            return self._loops[run_id][loop_id]
        except KeyError as exc:
            raise LoopNotFound(f"{run_id}:{loop_id}") from exc

    def get_seed_pool(self, pool_id: str) -> SeedCandidatePool:
        try:
            return self._seed_pools[pool_id]
        except KeyError as exc:
            raise SeedPoolNotFound(pool_id) from exc

    def get_seed_candidate(self, *, pool_id: str, seed_candidate_id: str) -> SeedCandidate:
        pool = self.get_seed_pool(pool_id)
        for candidate in pool.candidates:
            if candidate.seed_candidate_id == seed_candidate_id:
                return candidate
        raise SeedCandidateNotFound(f"{pool_id}:{seed_candidate_id}")

    def get_seed_selection_decision(self, decision_id: str) -> SeedSelectionDecision:
        try:
            return self._seed_selection_decisions[decision_id]
        except KeyError as exc:
            raise SeedSelectionNotFound(decision_id) from exc

    def list_loops(self, run_id: str) -> tuple[LoopRecord, ...]:
        self.get_run(run_id)
        return tuple(sorted(self._loops[run_id].values(), key=lambda loop: (loop.index, loop.created_at)))

    def get_lineage(self, *, run_id: str, loop_id: str) -> tuple[LoopRecord, ...]:
        lineage: list[LoopRecord] = []
        current = self.get_loop(run_id=run_id, loop_id=loop_id)
        while True:
            lineage.append(current)
            if current.parent_loop_id is None:
                break
            current = self.get_loop(run_id=run_id, loop_id=current.parent_loop_id)
        return tuple(reversed(lineage))

    def get_active_context(self, run_id: str) -> AgentLoopContext:
        run = self.get_run(run_id)
        return self.get_loop_context(run_id=run_id, loop_id=run.active_loop_id)

    def get_loop_context(self, *, run_id: str, loop_id: str) -> AgentLoopContext:
        run = self.get_run(run_id)
        loop = self.get_loop(run_id=run_id, loop_id=loop_id)
        lineage = self.get_lineage(run_id=run_id, loop_id=loop.loop_id)
        previous_change_sets = tuple(loop.change_set for loop in lineage if loop.change_set is not None)
        human_inputs: list[HumanInput] = []
        for lineage_loop in lineage:
            human_inputs.extend(lineage_loop.human_inputs)

        return AgentLoopContext(
            run_id=run_id,
            active_loop_id=loop.loop_id,
            parent_loop_id=loop.parent_loop_id,
            loop_index=loop.index,
            sequence=loop.candidate.sequence,
            conditions=loop.conditions,
            objective=run.objective,
            latest_metrics=loop.latest_metric_map(),
            lineage_loop_ids=tuple(loop.loop_id for loop in lineage),
            previous_change_sets=previous_change_sets,
            evaluations=loop.evaluations,
            reflection=loop.reflection,
            human_inputs=tuple(human_inputs),
            seed_selection_decision=run.seed_selection_decision,
        )

    def get_run_visualization(self, run_id: str) -> RunVisualizationSnapshot:
        run = self.get_run(run_id)
        loops = self.list_loops(run_id)
        nodes = tuple(self._to_loop_graph_node(run=run, loop=loop) for loop in loops)
        edges = tuple(
            LoopGraphEdge(parent_loop_id=loop.parent_loop_id, child_loop_id=loop.loop_id)
            for loop in loops
            if loop.parent_loop_id is not None
        )
        root_loop = self.get_loop(run_id=run_id, loop_id=run.root_loop_id)
        return RunVisualizationSnapshot(
            run_id=run.run_id,
            root_loop_id=run.root_loop_id,
            active_loop_id=run.active_loop_id,
            objective=run.objective,
            conditions=root_loop.conditions,
            nodes=nodes,
            edges=edges,
            seed_selection_decision=run.seed_selection_decision,
            created_at=run.created_at,
        )

    def extract_sequence(self, *, run_id: str, loop_id: str | None = None) -> str:
        run = self.get_run(run_id)
        loop = self.get_loop(run_id=run_id, loop_id=loop_id or run.active_loop_id)
        return loop.candidate.sequence

    def _activate_loop(self, *, run_id: str, loop_id: str, abandon_previous: bool) -> None:
        run = self.get_run(run_id)
        current_active_id = run.active_loop_id
        if current_active_id != loop_id:
            current = self._loops[run_id][current_active_id]
            if current.status == LoopStatus.ACTIVE:
                self._loops[run_id][current_active_id] = replace(
                    current,
                    status=LoopStatus.ABANDONED if abandon_previous else LoopStatus.AVAILABLE,
                )

        target = self.get_loop(run_id=run_id, loop_id=loop_id)
        self._loops[run_id][loop_id] = replace(target, status=LoopStatus.ACTIVE)
        self._runs[run_id] = replace(run, active_loop_id=loop_id)

    def _descendant_ids(self, run_id: str, loop_id: str) -> tuple[str, ...]:
        descendants: list[str] = []
        children = [loop for loop in self._loops[run_id].values() if loop.parent_loop_id == loop_id]
        for child in children:
            descendants.append(child.loop_id)
            descendants.extend(self._descendant_ids(run_id, child.loop_id))
        return tuple(descendants)

    def _is_ancestor(self, run_id: str, *, ancestor_id: str, loop_id: str) -> bool:
        current = self.get_loop(run_id=run_id, loop_id=loop_id)
        while current.parent_loop_id is not None:
            if current.parent_loop_id == ancestor_id:
                return True
            current = self.get_loop(run_id=run_id, loop_id=current.parent_loop_id)
        return False

    @staticmethod
    def _validate_unique_seed_candidates(candidates: tuple[SeedCandidate, ...]) -> None:
        seen: set[str] = set()
        for candidate in candidates:
            if candidate.seed_candidate_id in seen:
                raise InvalidLoopOperation(f"Duplicate seed candidate id: {candidate.seed_candidate_id}")
            seen.add(candidate.seed_candidate_id)

    @staticmethod
    def _has_evaluation_kind(loop: LoopRecord, kind: EvaluationKind) -> bool:
        return any(evaluation.kind == kind for evaluation in loop.evaluations)

    @classmethod
    def _has_boltz_output(cls, loop: LoopRecord) -> bool:
        return bool(loop.candidate.boltz_artifacts) or cls._has_evaluation_kind(loop, EvaluationKind.BOLTZ)

    @staticmethod
    def _has_reflection(reflection: LoopReflection) -> bool:
        return bool(
            reflection.went_well
            or reflection.went_wrong
            or reflection.next_actions
            or reflection.notes
        )

    def _to_loop_graph_node(self, *, run: DesignRun, loop: LoopRecord) -> LoopGraphNode:
        completeness = (
            self.validate_loop_complete(run_id=run.run_id, loop_id=loop.loop_id)
            if loop.status == LoopStatus.PENDING
            else None
        )
        return LoopGraphNode(
            loop_id=loop.loop_id,
            parent_loop_id=loop.parent_loop_id,
            index=loop.index,
            status=loop.status,
            is_active=loop.loop_id == run.active_loop_id,
            can_branch_from=loop.status in {LoopStatus.ACTIVE, LoopStatus.AVAILABLE, LoopStatus.ABANDONED},
            branch_label=loop.branch_label,
            sequence_length=len(loop.candidate.sequence),
            sequence_preview=self._sequence_preview(loop.candidate.sequence),
            change_summary=loop.change_set.summary if loop.change_set is not None else None,
            change_rationale=loop.change_set.why if loop.change_set is not None else None,
            change_diffs=tuple(
                change.machine_diff
                for change in loop.change_set.changes
            ) if loop.change_set is not None else (),
            latest_metrics=loop.latest_metric_map(),
            evaluation_kinds=tuple(evaluation.kind for evaluation in loop.evaluations),
            human_input_count=len(loop.human_inputs),
            human_inputs=loop.human_inputs,
            reflection=loop.reflection,
            completeness=completeness,
            created_at=loop.created_at,
        )

    @staticmethod
    def _sequence_preview(sequence: str, *, prefix: int = 12, suffix: int = 8) -> str:
        if len(sequence) <= prefix + suffix + 3:
            return sequence
        return f"{sequence[:prefix]}...{sequence[-suffix:]}"

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{uuid4().hex}"


class TuringDbMemoryRepository(InMemoryProteinMemoryRepository):
    """TuringDB-backed memory repository.

    Records are stored as JSON payloads on graph nodes. TuringDB supplies the
    versioned storage and rollback-capable history; this repository keeps the
    domain behavior identical to the in-memory reference implementation.
    """

    node_label = "MaterialHackMemoryRecord"

    def __init__(
        self,
        client: object | None = None,
        *,
        graph_name: str = "materialhack_memory",
        client_kwargs: dict | None = None,
        create_graph: bool = True,
        load_existing: bool = True,
    ) -> None:
        super().__init__()
        self.client = client or self._create_default_client(client_kwargs or {})
        self.graph_name = graph_name
        self._initialize_graph(create_graph=create_graph)
        if load_existing:
            self._load_existing_records()

    def create_run(
        self,
        *,
        objective: DesignObjective,
        seed_candidate: ProteinCandidate,
        conditions: ConditionSet,
        seed_evaluations: tuple[EvaluationResult, ...] = (),
        seed_reflection: LoopReflection | None = None,
        run_id: str | None = None,
        loop_id: str | None = None,
        metadata: dict | None = None,
        seed_selection_decision: SeedSelectionDecision | None = None,
    ) -> DesignRun:
        run = super().create_run(
            objective=objective,
            seed_candidate=seed_candidate,
            conditions=conditions,
            seed_evaluations=seed_evaluations,
            seed_reflection=seed_reflection,
            run_id=run_id,
            loop_id=loop_id,
            metadata=metadata,
            seed_selection_decision=seed_selection_decision,
        )
        loop = self.get_loop(run_id=run.run_id, loop_id=run.root_loop_id)
        records: list[tuple[str, str, object]] = [
            ("design_run", self._run_record_id(run.run_id), run),
            ("loop_record", self._loop_record_id(run.run_id, loop.loop_id), loop),
        ]
        if seed_selection_decision is not None:
            records.append(
                (
                    "seed_selection",
                    self._seed_selection_record_id(seed_selection_decision.decision_id),
                    seed_selection_decision,
                )
            )
        self._persist_records(records)
        return run

    def create_seed_pool(
        self,
        *,
        objective: DesignObjective,
        conditions: ConditionSet,
        candidates: tuple[SeedCandidate, ...] = (),
        created_by: str = "WF",
        pool_id: str | None = None,
        metadata: dict | None = None,
    ) -> SeedCandidatePool:
        pool = super().create_seed_pool(
            objective=objective,
            conditions=conditions,
            candidates=candidates,
            created_by=created_by,
            pool_id=pool_id,
            metadata=metadata,
        )
        self._persist_records([("seed_pool", self._seed_pool_record_id(pool.pool_id), pool)])
        return pool

    def add_seed_candidate(self, *, pool_id: str, candidate: SeedCandidate) -> SeedCandidatePool:
        pool = super().add_seed_candidate(pool_id=pool_id, candidate=candidate)
        self._persist_records([("seed_pool", self._seed_pool_record_id(pool.pool_id), pool)])
        return pool

    def record_seed_selection_decision(
        self,
        *,
        pool_id: str,
        selected_seed_candidate_id: str,
        rationale: str,
        selected_by: str = "WF",
        selection_method: str = "screening_verifier_rank",
        ranked_seed_candidate_ids: tuple[str, ...] = (),
        human_override: bool = False,
        decision_id: str | None = None,
        metadata: dict | None = None,
    ) -> SeedSelectionDecision:
        decision = super().record_seed_selection_decision(
            pool_id=pool_id,
            selected_seed_candidate_id=selected_seed_candidate_id,
            rationale=rationale,
            selected_by=selected_by,
            selection_method=selection_method,
            ranked_seed_candidate_ids=ranked_seed_candidate_ids,
            human_override=human_override,
            decision_id=decision_id,
            metadata=metadata,
        )
        self._persist_records(
            [("seed_selection", self._seed_selection_record_id(decision.decision_id), decision)]
        )
        return decision

    def append_loop(
        self,
        *,
        run_id: str,
        candidate: ProteinCandidate,
        change_set: ChangeSet,
        evaluations: tuple[EvaluationResult, ...] = (),
        reflection: LoopReflection | None = None,
        parent_loop_id: str | None = None,
        human_inputs: tuple[HumanInput, ...] = (),
        loop_id: str | None = None,
        branch_label: str | None = None,
        metadata: dict | None = None,
        make_active: bool = False,
        completeness_requirements: LoopCompletenessRequirements | None = None,
    ) -> LoopRecord:
        loop = super().append_loop(
            run_id=run_id,
            candidate=candidate,
            change_set=change_set,
            evaluations=evaluations,
            reflection=reflection,
            parent_loop_id=parent_loop_id,
            human_inputs=human_inputs,
            loop_id=loop_id,
            branch_label=branch_label,
            metadata=metadata,
            make_active=make_active,
            completeness_requirements=completeness_requirements,
        )
        records: list[tuple[str, str, object]] = [
            ("loop_record", self._loop_record_id(run_id, loop.loop_id), loop)
        ]
        if make_active:
            records.append(("design_run", self._run_record_id(run_id), self.get_run(run_id)))
            if loop.parent_loop_id is not None:
                records.append(
                    (
                        "loop_record",
                        self._loop_record_id(run_id, loop.parent_loop_id),
                        self.get_loop(run_id=run_id, loop_id=loop.parent_loop_id),
                    )
                )
        self._persist_records(records)
        return loop

    def rollback_to_loop(self, *, run_id: str, loop_id: str, actor: str, reason: str) -> LoopRecord:
        loop = super().rollback_to_loop(run_id=run_id, loop_id=loop_id, actor=actor, reason=reason)
        self._persist_run_and_loops(run_id)
        return loop

    def reject_loop(self, *, run_id: str, loop_id: str, actor: str, reason: str) -> LoopRecord:
        loop = super().reject_loop(run_id=run_id, loop_id=loop_id, actor=actor, reason=reason)
        self._persist_records([("loop_record", self._loop_record_id(run_id, loop_id), loop)])
        return loop

    def record_human_input(self, *, run_id: str, loop_id: str, human_input: HumanInput) -> LoopRecord:
        loop = super().record_human_input(run_id=run_id, loop_id=loop_id, human_input=human_input)
        self._persist_records([("loop_record", self._loop_record_id(run_id, loop_id), loop)])
        return loop

    def attach_evaluation(self, *, run_id: str, loop_id: str, evaluation: EvaluationResult) -> LoopRecord:
        loop = super().attach_evaluation(run_id=run_id, loop_id=loop_id, evaluation=evaluation)
        self._persist_records([("loop_record", self._loop_record_id(run_id, loop_id), loop)])
        return loop

    def set_loop_reflection(self, *, run_id: str, loop_id: str, reflection: LoopReflection) -> LoopRecord:
        loop = super().set_loop_reflection(run_id=run_id, loop_id=loop_id, reflection=reflection)
        self._persist_records([("loop_record", self._loop_record_id(run_id, loop_id), loop)])
        return loop

    def finalize_loop(
        self,
        *,
        run_id: str,
        loop_id: str,
        requirements: LoopCompletenessRequirements | None = None,
        make_active: bool = True,
    ) -> LoopRecord:
        loop = super().finalize_loop(
            run_id=run_id,
            loop_id=loop_id,
            requirements=requirements,
            make_active=make_active,
        )
        records: list[tuple[str, str, object]] = [
            ("loop_record", self._loop_record_id(run_id, loop.loop_id), loop)
        ]
        if make_active:
            records.append(("design_run", self._run_record_id(run_id), self.get_run(run_id)))
            if loop.parent_loop_id is not None:
                records.append(
                    (
                        "loop_record",
                        self._loop_record_id(run_id, loop.parent_loop_id),
                        self.get_loop(run_id=run_id, loop_id=loop.parent_loop_id),
                    )
                )
        self._persist_records(records)
        return loop

    @staticmethod
    def _create_default_client(client_kwargs: dict) -> object:
        try:
            from turingdb import TuringDB
        except ImportError as exc:
            raise ImportError(
                "Install the TuringDB SDK to use TuringDbMemoryRepository, "
                "for example: pip install 'materialhack-memory[turingdb]'"
            ) from exc
        return TuringDB(**client_kwargs)

    def _initialize_graph(self, *, create_graph: bool) -> None:
        if create_graph:
            try:
                self.client.create_graph(self.graph_name)
            except Exception:
                # Existing graph is acceptable; set_graph below verifies access.
                pass
        self.client.set_graph(self.graph_name)

    def _load_existing_records(self) -> None:
        query = (
            f"MATCH (n:{self.node_label}) "
            "RETURN n.record_id AS record_id, n.record_type AS record_type, "
            "n.payload_json AS payload_json"
        )
        rows = self._query_rows(query)
        for row in rows:
            record_type = row["record_type"]
            payload = json.loads(row["payload_json"])
            self._load_record_payload(record_type=record_type, payload=payload)

    def _load_record_payload(self, *, record_type: str, payload: dict) -> None:
        if record_type == "design_run":
            run = from_jsonable(DesignRun, payload)
            self._runs[run.run_id] = run
            return
        if record_type == "loop_record":
            loop = from_jsonable(LoopRecord, payload)
            self._loops.setdefault(loop.run_id, {})[loop.loop_id] = loop
            return
        if record_type == "seed_pool":
            pool = from_jsonable(SeedCandidatePool, payload)
            self._seed_pools[pool.pool_id] = pool
            return
        if record_type == "seed_selection":
            decision = from_jsonable(SeedSelectionDecision, payload)
            self._seed_selection_decisions[decision.decision_id] = decision

    def _persist_run_and_loops(self, run_id: str) -> None:
        records: list[tuple[str, str, object]] = [
            ("design_run", self._run_record_id(run_id), self.get_run(run_id))
        ]
        for loop in self.list_loops(run_id):
            records.append(("loop_record", self._loop_record_id(run_id, loop.loop_id), loop))
        self._persist_records(records)

    def _persist_records(self, records: list[tuple[str, str, object]]) -> None:
        if not records:
            return
        change = self.client.new_change()
        self.client.checkout(change=change)
        try:
            for record_type, record_id, payload in records:
                self._upsert_record(record_type=record_type, record_id=record_id, payload=payload)
            self.client.query("COMMIT")
            self.client.query("CHANGE SUBMIT")
        finally:
            self.client.checkout()

    def _upsert_record(self, *, record_type: str, record_id: str, payload: object) -> None:
        exists = self._record_exists(record_id)
        payload_json = json.dumps(to_jsonable(payload), sort_keys=True)
        updated_at = utc_now_iso()
        if exists:
            query = (
                f"MATCH (n:{self.node_label} "
                f"{{record_id: {self._cypher_string(record_id)}}}) "
                f"SET n.record_type = {self._cypher_string(record_type)}, "
                f"n.payload_json = {self._cypher_string(payload_json)}, "
                f"n.updated_at = {self._cypher_string(updated_at)}"
            )
        else:
            query = (
                f"CREATE (:{self.node_label} "
                "{"
                f"record_id: {self._cypher_string(record_id)}, "
                f"record_type: {self._cypher_string(record_type)}, "
                f"payload_json: {self._cypher_string(payload_json)}, "
                f"updated_at: {self._cypher_string(updated_at)}"
                "})"
            )
        self.client.query(query)

    def _record_exists(self, record_id: str) -> bool:
        query = (
            f"MATCH (n:{self.node_label} "
            f"{{record_id: {self._cypher_string(record_id)}}}) "
            "RETURN n.record_id AS record_id"
        )
        return bool(self._query_rows(query))

    def _query_rows(self, query: str) -> list[dict]:
        result = self.client.query(query)
        if hasattr(result, "to_dict"):
            return result.to_dict("records")
        if isinstance(result, list):
            return result
        if result is None:
            return []
        return list(result)

    @staticmethod
    def _cypher_string(value: str) -> str:
        escaped = (
            value.replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )
        return f"'{escaped}'"

    @staticmethod
    def _run_record_id(run_id: str) -> str:
        return f"run:{run_id}"

    @staticmethod
    def _loop_record_id(run_id: str, loop_id: str) -> str:
        return f"loop:{run_id}:{loop_id}"

    @staticmethod
    def _seed_pool_record_id(pool_id: str) -> str:
        return f"seed_pool:{pool_id}"

    @staticmethod
    def _seed_selection_record_id(decision_id: str) -> str:
        return f"seed_selection:{decision_id}"
