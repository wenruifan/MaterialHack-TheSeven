import unittest

from materialhack_loop_runner import (
    DeterministicVerifier,
    LoopRunnerConfigError,
    ProteinDesignLoopRunner,
    StopReason,
)
from materialhack_memory import (
    CandidateOrigin,
    ConditionSet,
    DesignObjective,
    EvaluationKind,
    EvaluationResult,
    InMemoryProteinMemoryRepository,
    LoopStatus,
    MetricGoal,
    MetricValue,
    ProteinCandidate,
)


class FailingScreeningPipeline:
    def evaluate(self, context, loop, boltz_evaluation):
        raise RuntimeError("screening service unavailable")


class LoopRunnerTests(unittest.TestCase):
    def _create_repo(
        self,
        *,
        seed_score: float = 0.1,
        target: float = 0.8,
        max_loops: int | None = 4,
    ) -> InMemoryProteinMemoryRepository:
        repo = InMemoryProteinMemoryRepository()
        objective = DesignObjective(
            description="Optimize binder until verifier target is met.",
            goals=(MetricGoal(name="binding_score", target=target, comparator="gte"),),
            max_loops=max_loops,
        )
        repo.create_run(
            objective=objective,
            seed_candidate=ProteinCandidate(
                sequence="ACDEFGHIKLMNPQRSTVWY",
                origin=CandidateOrigin.CCDC_CSD,
                name="seed",
            ),
            conditions=ConditionSet.common(binding_target="target-x", ph=7.4),
            seed_evaluations=(
                EvaluationResult(
                    kind=EvaluationKind.VERIFIER,
                    evaluator_name="seed-verifier",
                    metrics=(MetricValue(name="binding_score", value=seed_score),),
                ),
            ),
            run_id="run_test",
            loop_id="loop_0",
        )
        return repo

    def test_fixed_count_run_creates_and_finalizes_requested_loops(self) -> None:
        repo = self._create_repo(target=2.0)
        runner = ProteinDesignLoopRunner(memory=repo)

        result = runner.run_for_loops(run_id="run_test", loop_count=3)

        self.assertEqual(result.stop_reason, StopReason.FIXED_LOOP_COUNT_COMPLETE)
        self.assertEqual(result.start_loop_id, "loop_0")
        self.assertEqual(result.loops_completed, 3)
        loops = repo.list_loops("run_test")
        self.assertEqual(len(loops), 4)
        self.assertEqual(loops[-1].status, LoopStatus.ACTIVE)
        for loop in loops[1:]:
            self.assertTrue(repo.validate_loop_complete(run_id="run_test", loop_id=loop.loop_id).is_complete)
            self.assertEqual(
                {evaluation.kind for evaluation in loop.evaluations},
                {EvaluationKind.BOLTZ, EvaluationKind.SCREENING, EvaluationKind.VERIFIER},
            )

    def test_threshold_run_stops_immediately_when_seed_meets_goals(self) -> None:
        repo = self._create_repo(seed_score=0.91, target=0.85, max_loops=5)
        runner = ProteinDesignLoopRunner(memory=repo)

        result = runner.run_until_stop(run_id="run_test")

        self.assertEqual(result.stop_reason, StopReason.GOALS_MET)
        self.assertEqual(result.loops_completed, 0)
        self.assertEqual(len(repo.list_loops("run_test")), 1)
        self.assertEqual(result.active_loop_id, "loop_0")

    def test_threshold_run_stops_after_generated_loop_when_verifier_goals_are_met(self) -> None:
        repo = self._create_repo(seed_score=0.1, target=0.3, max_loops=5)
        runner = ProteinDesignLoopRunner(
            memory=repo,
            verifier=DeterministicVerifier(metric_step=0.25),
        )

        result = runner.run_until_stop(run_id="run_test")

        self.assertEqual(result.stop_reason, StopReason.GOALS_MET)
        self.assertEqual(result.loops_completed, 1)
        self.assertGreaterEqual(result.final_metrics["binding_score"], 0.3)
        self.assertEqual(len(repo.list_loops("run_test")), 2)

    def test_budget_exhaustion_stops_when_goals_are_not_met(self) -> None:
        repo = self._create_repo(seed_score=0.1, target=0.95, max_loops=2)
        runner = ProteinDesignLoopRunner(
            memory=repo,
            verifier=DeterministicVerifier(metric_step=0.1),
        )

        result = runner.run_until_stop(run_id="run_test")

        self.assertEqual(result.stop_reason, StopReason.LOOP_BUDGET_EXHAUSTED)
        self.assertEqual(result.loops_completed, 2)
        self.assertFalse(result.goals_satisfied)
        self.assertEqual(len(repo.list_loops("run_test")), 3)

    def test_continue_from_loop_branches_and_preserves_existing_history(self) -> None:
        repo = self._create_repo(target=2.0)
        runner = ProteinDesignLoopRunner(memory=repo)
        runner.run_for_loops(run_id="run_test", loop_count=2)
        loops = repo.list_loops("run_test")
        branch_parent_id = loops[1].loop_id
        original_active_id = loops[2].loop_id

        result = runner.continue_from_loop(run_id="run_test", loop_id=branch_parent_id, loop_count=1)

        self.assertEqual(result.stop_reason, StopReason.FIXED_LOOP_COUNT_COMPLETE)
        self.assertEqual(result.loops_completed, 1)
        self.assertEqual(repo.get_loop(run_id="run_test", loop_id=original_active_id).status, LoopStatus.ABANDONED)
        active_loop = repo.get_loop(run_id="run_test", loop_id=result.active_loop_id)
        self.assertEqual(active_loop.parent_loop_id, branch_parent_id)
        self.assertEqual(
            [loop.loop_id for loop in repo.get_lineage(run_id="run_test", loop_id=active_loop.loop_id)],
            ["loop_0", branch_parent_id, active_loop.loop_id],
        )

    def test_adapter_failure_leaves_pending_loop_and_records_error_note(self) -> None:
        repo = self._create_repo(target=2.0)
        runner = ProteinDesignLoopRunner(
            memory=repo,
            screening_pipeline=FailingScreeningPipeline(),
        )

        result = runner.run_for_loops(run_id="run_test", loop_count=1)

        self.assertEqual(result.stop_reason, StopReason.ADAPTER_FAILURE)
        self.assertEqual(result.failed_stage, "run_screening")
        self.assertIsNotNone(result.pending_loop_id)
        self.assertEqual(result.active_loop_id, "loop_0")
        pending_loop = repo.get_loop(run_id="run_test", loop_id=result.pending_loop_id)
        self.assertEqual(pending_loop.status, LoopStatus.PENDING)
        self.assertEqual(
            {evaluation.kind for evaluation in pending_loop.evaluations},
            {EvaluationKind.BOLTZ},
        )
        self.assertEqual(pending_loop.human_inputs[-1].author, "loop_runner")
        self.assertIn("screening service unavailable", pending_loop.human_inputs[-1].note)

    def test_run_until_stop_requires_finite_loop_cap(self) -> None:
        repo = self._create_repo(max_loops=None)
        runner = ProteinDesignLoopRunner(memory=repo)

        with self.assertRaises(LoopRunnerConfigError) as error:
            runner.run_until_stop(run_id="run_test")

        self.assertIn("requires max_loops", str(error.exception))


if __name__ == "__main__":
    unittest.main()
