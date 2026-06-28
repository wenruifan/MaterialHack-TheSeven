from __future__ import annotations

from dataclasses import replace

from materialhack_memory import (
    EvaluationResult,
    HumanInput,
    InMemoryProteinMemoryRepository,
    LoopRecord,
)

from materialhack_agent.workbench_events import EventHub


class ObservableInMemoryProteinMemoryRepository(InMemoryProteinMemoryRepository):
    """In-memory memory repository that emits workbench events after writes."""

    def __init__(self, event_hub: EventHub) -> None:
        super().__init__()
        self.event_hub = event_hub

    def append_loop(self, **kwargs) -> LoopRecord:
        loop = super().append_loop(**kwargs)
        self.event_hub.publish(
            "loop_appended",
            run_id=loop.run_id,
            loop_id=loop.loop_id,
            data={
                "parent_loop_id": loop.parent_loop_id,
                "index": loop.index,
                "status": loop.status,
            },
        )
        return loop

    def attach_evaluation(self, *, run_id: str, loop_id: str, evaluation: EvaluationResult) -> LoopRecord:
        loop = super().attach_evaluation(run_id=run_id, loop_id=loop_id, evaluation=evaluation)
        self.event_hub.publish(
            "evaluation_attached",
            run_id=run_id,
            loop_id=loop_id,
            data={
                "evaluation": evaluation,
                "evaluation_kind": evaluation.kind,
                "evaluator_name": evaluation.evaluator_name,
            },
        )
        return loop

    def set_loop_reflection(self, *, run_id: str, loop_id: str, reflection) -> LoopRecord:
        loop = super().set_loop_reflection(run_id=run_id, loop_id=loop_id, reflection=reflection)
        self.event_hub.publish(
            "reflection_written",
            run_id=run_id,
            loop_id=loop_id,
            data={"reflection": reflection},
        )
        return loop

    def finalize_loop(self, *, run_id: str, loop_id: str, requirements=None, make_active: bool = True) -> LoopRecord:
        loop = super().finalize_loop(
            run_id=run_id,
            loop_id=loop_id,
            requirements=requirements,
            make_active=make_active,
        )
        self.event_hub.publish(
            "loop_finalized",
            run_id=run_id,
            loop_id=loop_id,
            data={
                "status": loop.status,
                "make_active": make_active,
                "latest_metrics": loop.latest_metric_map(),
            },
        )
        return loop

    def rollback_to_loop(self, *, run_id: str, loop_id: str, actor: str, reason: str) -> LoopRecord:
        previous_active_loop_id = self.get_run(run_id).active_loop_id
        loop = super().rollback_to_loop(run_id=run_id, loop_id=loop_id, actor=actor, reason=reason)
        if loop.human_inputs:
            last_input = loop.human_inputs[-1]
            annotated_input = replace(
                last_input,
                metadata={
                    **dict(last_input.metadata),
                    "action": "rollback",
                    "actor": actor,
                    "previous_active_loop_id": previous_active_loop_id,
                },
            )
            loop = replace(loop, human_inputs=loop.human_inputs[:-1] + (annotated_input,))
            self._loops[run_id][loop_id] = loop

        self.event_hub.publish(
            "rollback_recorded",
            run_id=run_id,
            loop_id=loop_id,
            data={
                "actor": actor,
                "reason": reason,
                "previous_active_loop_id": previous_active_loop_id,
                "active_loop_id": loop.loop_id,
            },
        )
        return loop

    def reject_loop(self, *, run_id: str, loop_id: str, actor: str, reason: str) -> LoopRecord:
        loop = super().reject_loop(run_id=run_id, loop_id=loop_id, actor=actor, reason=reason)
        self.event_hub.publish(
            "loop_rejected",
            run_id=run_id,
            loop_id=loop_id,
            data={
                "actor": actor,
                "reason": reason,
                "status": loop.status,
                "active_loop_id": self.get_run(run_id).active_loop_id,
            },
        )
        return loop

    def record_human_input(self, *, run_id: str, loop_id: str, human_input: HumanInput) -> LoopRecord:
        loop = super().record_human_input(run_id=run_id, loop_id=loop_id, human_input=human_input)
        self.event_hub.publish(
            "human_input_recorded",
            run_id=run_id,
            loop_id=loop_id,
            data={"human_input": human_input},
        )
        return loop
