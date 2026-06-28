from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from langgraph.graph import END, StateGraph

from materialhack_memory import HumanInput, MemoryRepository

from materialhack_loop_runner.contracts import (
    BoltzEvaluator,
    CandidateGenerator,
    ChangePlanner,
    LoopRunnerConfigError,
    LoopRunnerResult,
    LoopRunnerState,
    ReflectionWriter,
    ScreeningPipeline,
    StopReason,
    Verifier,
)
from materialhack_loop_runner.stubs import (
    DeterministicBoltzEvaluator,
    DeterministicCandidateGenerator,
    DeterministicChangePlanner,
    DeterministicReflectionWriter,
    DeterministicScreeningPipeline,
    DeterministicVerifier,
)


Route = Literal["continue", "stop"]


@dataclass
class ProteinDesignLoopRunner:
    memory: MemoryRepository
    change_planner: ChangePlanner | None = None
    candidate_generator: CandidateGenerator | None = None
    boltz_evaluator: BoltzEvaluator | None = None
    screening_pipeline: ScreeningPipeline | None = None
    verifier: Verifier | None = None
    reflection_writer: ReflectionWriter | None = None

    def __post_init__(self) -> None:
        self.change_planner = self.change_planner or DeterministicChangePlanner()
        self.candidate_generator = self.candidate_generator or DeterministicCandidateGenerator()
        self.boltz_evaluator = self.boltz_evaluator or DeterministicBoltzEvaluator()
        self.screening_pipeline = self.screening_pipeline or DeterministicScreeningPipeline()
        self.verifier = self.verifier or DeterministicVerifier()
        self.reflection_writer = self.reflection_writer or DeterministicReflectionWriter()
        self._graph = self._build_graph()

    def run_until_stop(self, run_id: str, max_loops: int | None = None) -> LoopRunnerResult:
        context = self.memory.get_active_context(run_id)
        loop_cap = max_loops if max_loops is not None else context.objective.max_loops
        if loop_cap is None:
            raise LoopRunnerConfigError(
                "run_until_stop requires max_loops or DesignObjective.max_loops to prevent an infinite run"
            )
        return self._run(
            run_id=run_id,
            start_loop_id=None,
            loop_cap=loop_cap,
            check_thresholds=True,
            fixed_count_mode=False,
        )

    def run_for_loops(self, run_id: str, loop_count: int) -> LoopRunnerResult:
        return self._run(
            run_id=run_id,
            start_loop_id=None,
            loop_cap=loop_count,
            check_thresholds=False,
            fixed_count_mode=True,
        )

    def continue_from_loop(
        self,
        run_id: str,
        loop_id: str,
        loop_count: int | None = None,
    ) -> LoopRunnerResult:
        if loop_count is not None:
            return self._run(
                run_id=run_id,
                start_loop_id=loop_id,
                loop_cap=loop_count,
                check_thresholds=False,
                fixed_count_mode=True,
            )

        context = self.memory.get_loop_context(run_id=run_id, loop_id=loop_id)
        if context.objective.max_loops is None:
            raise LoopRunnerConfigError(
                "continue_from_loop without loop_count requires DesignObjective.max_loops"
            )
        return self._run(
            run_id=run_id,
            start_loop_id=loop_id,
            loop_cap=context.objective.max_loops,
            check_thresholds=True,
            fixed_count_mode=False,
        )

    def _run(
        self,
        *,
        run_id: str,
        start_loop_id: str | None,
        loop_cap: int,
        check_thresholds: bool,
        fixed_count_mode: bool,
    ) -> LoopRunnerResult:
        if loop_cap < 0:
            raise LoopRunnerConfigError("loop count must be greater than or equal to zero")

        initial_state: LoopRunnerState = {
            "run_id": run_id,
            "start_loop_id": start_loop_id,
            "loop_cap": loop_cap,
            "check_thresholds": check_thresholds,
            "fixed_count_mode": fixed_count_mode,
            "loops_completed": 0,
            "done": False,
            "pending_loop_id": None,
        }
        final_state = self._graph.invoke(initial_state, config={"recursion_limit": self._recursion_limit(loop_cap)})
        return self._to_result(final_state)

    def _build_graph(self):
        graph = StateGraph(LoopRunnerState)
        graph.add_node("load_context", self._load_context)
        graph.add_node("check_pre_loop_stop", self._check_pre_loop_stop)
        graph.add_node("plan_change", self._plan_change)
        graph.add_node("generate_candidate", self._generate_candidate)
        graph.add_node("append_pending_loop", self._append_pending_loop)
        graph.add_node("run_boltz", self._run_boltz)
        graph.add_node("run_screening", self._run_screening)
        graph.add_node("run_verifier", self._run_verifier)
        graph.add_node("write_reflection", self._write_reflection)
        graph.add_node("finalize_loop", self._finalize_loop)
        graph.add_node("check_post_loop_stop", self._check_post_loop_stop)

        graph.set_entry_point("load_context")
        graph.add_edge("load_context", "check_pre_loop_stop")
        graph.add_conditional_edges(
            "check_pre_loop_stop",
            self._route_done,
            {"continue": "plan_change", "stop": END},
        )
        graph.add_conditional_edges(
            "plan_change",
            self._route_done,
            {"continue": "generate_candidate", "stop": END},
        )
        graph.add_conditional_edges(
            "generate_candidate",
            self._route_done,
            {"continue": "append_pending_loop", "stop": END},
        )
        graph.add_conditional_edges(
            "append_pending_loop",
            self._route_done,
            {"continue": "run_boltz", "stop": END},
        )
        graph.add_conditional_edges(
            "run_boltz",
            self._route_done,
            {"continue": "run_screening", "stop": END},
        )
        graph.add_conditional_edges(
            "run_screening",
            self._route_done,
            {"continue": "run_verifier", "stop": END},
        )
        graph.add_conditional_edges(
            "run_verifier",
            self._route_done,
            {"continue": "write_reflection", "stop": END},
        )
        graph.add_conditional_edges(
            "write_reflection",
            self._route_done,
            {"continue": "finalize_loop", "stop": END},
        )
        graph.add_conditional_edges(
            "finalize_loop",
            self._route_done,
            {"continue": "check_post_loop_stop", "stop": END},
        )
        graph.add_conditional_edges(
            "check_post_loop_stop",
            self._route_done,
            {"continue": "load_context", "stop": END},
        )
        return graph.compile()

    def _load_context(self, state: LoopRunnerState) -> LoopRunnerState:
        if state.get("loops_completed", 0) == 0 and state.get("start_loop_id") is not None:
            context = self.memory.get_loop_context(run_id=state["run_id"], loop_id=state["start_loop_id"])
        else:
            context = self.memory.get_active_context(state["run_id"])
        updates: LoopRunnerState = {"context": context}
        if "initial_loop_id" not in state:
            updates["initial_loop_id"] = context.active_loop_id
        return updates

    def _check_pre_loop_stop(self, state: LoopRunnerState) -> LoopRunnerState:
        context = state["context"]
        if state.get("check_thresholds") and context.objective.goals_satisfied_by(context.latest_metrics):
            return {
                "done": True,
                "stop_reason": StopReason.GOALS_MET,
            }
        if state.get("loops_completed", 0) >= state["loop_cap"]:
            return {
                "done": True,
                "stop_reason": self._budget_stop_reason(state),
            }
        return {"done": False}

    def _plan_change(self, state: LoopRunnerState) -> LoopRunnerState:
        try:
            return {"change_set": self.change_planner.plan_change(state["context"])}
        except Exception as exc:
            return self._fail(state, stage="plan_change", exc=exc)

    def _generate_candidate(self, state: LoopRunnerState) -> LoopRunnerState:
        try:
            return {
                "candidate": self.candidate_generator.generate_candidate(
                    state["context"],
                    state["change_set"],
                )
            }
        except Exception as exc:
            return self._fail(state, stage="generate_candidate", exc=exc)

    def _append_pending_loop(self, state: LoopRunnerState) -> LoopRunnerState:
        try:
            loop = self.memory.append_loop(
                run_id=state["run_id"],
                parent_loop_id=state["context"].active_loop_id,
                candidate=state["candidate"],
                change_set=state["change_set"],
            )
            return {
                "pending_loop": loop,
                "pending_loop_id": loop.loop_id,
            }
        except Exception as exc:
            return self._fail(state, stage="append_pending_loop", exc=exc)

    def _run_boltz(self, state: LoopRunnerState) -> LoopRunnerState:
        try:
            evaluation = self.boltz_evaluator.evaluate(state["context"], state["pending_loop"])
            self.memory.attach_evaluation(
                run_id=state["run_id"],
                loop_id=state["pending_loop"].loop_id,
                evaluation=evaluation,
            )
            return {"boltz_evaluation": evaluation}
        except Exception as exc:
            return self._fail(state, stage="run_boltz", exc=exc)

    def _run_screening(self, state: LoopRunnerState) -> LoopRunnerState:
        try:
            evaluation = self.screening_pipeline.evaluate(
                state["context"],
                state["pending_loop"],
                state["boltz_evaluation"],
            )
            self.memory.attach_evaluation(
                run_id=state["run_id"],
                loop_id=state["pending_loop"].loop_id,
                evaluation=evaluation,
            )
            return {"screening_evaluation": evaluation}
        except Exception as exc:
            return self._fail(state, stage="run_screening", exc=exc)

    def _run_verifier(self, state: LoopRunnerState) -> LoopRunnerState:
        try:
            evaluation = self.verifier.evaluate(
                state["context"],
                state["pending_loop"],
                state["boltz_evaluation"],
                state["screening_evaluation"],
            )
            self.memory.attach_evaluation(
                run_id=state["run_id"],
                loop_id=state["pending_loop"].loop_id,
                evaluation=evaluation,
            )
            return {"verifier_evaluation": evaluation}
        except Exception as exc:
            return self._fail(state, stage="run_verifier", exc=exc)

    def _write_reflection(self, state: LoopRunnerState) -> LoopRunnerState:
        try:
            reflection = self.reflection_writer.write_reflection(
                state["context"],
                state["pending_loop"],
                state["boltz_evaluation"],
                state["screening_evaluation"],
                state["verifier_evaluation"],
            )
            self.memory.set_loop_reflection(
                run_id=state["run_id"],
                loop_id=state["pending_loop"].loop_id,
                reflection=reflection,
            )
            return {"reflection": reflection}
        except Exception as exc:
            return self._fail(state, stage="write_reflection", exc=exc)

    def _finalize_loop(self, state: LoopRunnerState) -> LoopRunnerState:
        try:
            loop = self.memory.finalize_loop(run_id=state["run_id"], loop_id=state["pending_loop"].loop_id)
            return {
                "pending_loop": loop,
                "loops_completed": state.get("loops_completed", 0) + 1,
            }
        except Exception as exc:
            return self._fail(state, stage="finalize_loop", exc=exc)

    def _check_post_loop_stop(self, state: LoopRunnerState) -> LoopRunnerState:
        context = self.memory.get_active_context(state["run_id"])
        updates: LoopRunnerState = {
            "context": context,
            "pending_loop_id": None,
        }
        if state.get("check_thresholds") and context.objective.goals_satisfied_by(context.latest_metrics):
            updates.update(
                {
                    "done": True,
                    "stop_reason": StopReason.GOALS_MET,
                }
            )
        elif state.get("loops_completed", 0) >= state["loop_cap"]:
            updates.update(
                {
                    "done": True,
                    "stop_reason": self._budget_stop_reason(state),
                }
            )
        else:
            updates["done"] = False
        return updates

    def _fail(self, state: LoopRunnerState, *, stage: str, exc: Exception) -> LoopRunnerState:
        error_message = f"{stage} failed: {exc}"
        pending_loop_id = state.get("pending_loop_id")
        if pending_loop_id is not None:
            try:
                self.memory.record_human_input(
                    run_id=state["run_id"],
                    loop_id=pending_loop_id,
                    human_input=HumanInput(
                        author="loop_runner",
                        note=error_message,
                        metadata={
                            "stage": stage,
                            "error_type": type(exc).__name__,
                        },
                    ),
                )
            except Exception as record_exc:
                error_message = f"{error_message}; failed to record error note: {record_exc}"

        return {
            "done": True,
            "stop_reason": StopReason.ADAPTER_FAILURE,
            "failed_stage": stage,
            "error_message": error_message,
            "pending_loop_id": pending_loop_id,
        }

    @staticmethod
    def _route_done(state: LoopRunnerState) -> Route:
        return "stop" if state.get("done") else "continue"

    @staticmethod
    def _budget_stop_reason(state: LoopRunnerState) -> StopReason:
        if state.get("fixed_count_mode"):
            return StopReason.FIXED_LOOP_COUNT_COMPLETE
        return StopReason.LOOP_BUDGET_EXHAUSTED

    @staticmethod
    def _recursion_limit(loop_cap: int) -> int:
        return max(25, 12 * (loop_cap + 1))

    def _to_result(self, state: LoopRunnerState) -> LoopRunnerResult:
        context = self.memory.get_active_context(state["run_id"])
        stop_reason = state.get("stop_reason")
        if stop_reason is None:
            stop_reason = self._budget_stop_reason(state)
        start_loop_id = state.get("start_loop_id") or state.get("initial_loop_id") or state["context"].active_loop_id
        return LoopRunnerResult(
            run_id=state["run_id"],
            start_loop_id=start_loop_id,
            active_loop_id=context.active_loop_id,
            loops_completed=state.get("loops_completed", 0),
            stop_reason=stop_reason,
            goals_satisfied=context.objective.goals_satisfied_by(context.latest_metrics),
            final_metrics=dict(context.latest_metrics),
            pending_loop_id=state.get("pending_loop_id"),
            failed_stage=state.get("failed_stage"),
            error_message=state.get("error_message"),
        )
