import unittest

from materialhack_memory import (
    CandidateOrigin,
    ChangeOperation,
    ChangeSet,
    ConditionSet,
    DesignObjective,
    EvaluationKind,
    EvaluationResult,
    InMemoryProteinMemoryRepository,
    InvalidLoopOperation,
    LoopIncomplete,
    LoopReflection,
    LoopStatus,
    MetricGoal,
    MetricValue,
    ProteinCandidate,
    ProteinChange,
    SeedCandidate,
    SeedCandidateNotFound,
)


class MemoryRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = InMemoryProteinMemoryRepository()
        self.objective = DesignObjective(
            description="Optimize binder until verifier affinity and stability targets are met.",
            goals=(
                MetricGoal(name="binding_score", target=0.85, comparator="gte"),
                MetricGoal(name="instability_index", target=35.0, comparator="lte"),
            ),
            max_loops=12,
        )
        self.conditions = ConditionSet.common(
            binding_target="target-protein-x",
            ligand="ligand-a",
            ph=7.4,
            temperature_c=37,
            solvent="aqueous",
        )
        self.run = self.repo.create_run(
            objective=self.objective,
            seed_candidate=ProteinCandidate(
                sequence="ACDEFGHIKLMNPQRSTVWY",
                origin=CandidateOrigin.CCDC_CSD,
                name="seed_from_csd",
            ),
            conditions=self.conditions,
            seed_evaluations=(
                EvaluationResult(
                    kind=EvaluationKind.BOLTZ,
                    evaluator_name="boltz",
                    evaluator_version="placeholder",
                    metrics=(MetricValue(name="binding_score", value=0.41),),
                    summary="Seed binds weakly but has a plausible pocket.",
                ),
            ),
            run_id="run_test",
            loop_id="loop_0",
        )

    def _complete_evaluations(self) -> tuple[EvaluationResult, ...]:
        return (
            EvaluationResult(
                kind=EvaluationKind.BOLTZ,
                evaluator_name="boltz",
                metrics=(MetricValue(name="plddt", value=0.82),),
            ),
            EvaluationResult(
                kind=EvaluationKind.SCREENING,
                evaluator_name="screening-model",
                metrics=(MetricValue(name="screen_score", value=0.64),),
            ),
            EvaluationResult(
                kind=EvaluationKind.VERIFIER,
                evaluator_name="binding-verifier",
                metrics=(MetricValue(name="binding_score", value=0.57),),
            ),
        )

    def _complete_reflection(self) -> LoopReflection:
        return LoopReflection(
            went_well=("Binding score improved from the seed.",),
            went_wrong=("Stability target is still unknown.",),
            next_actions=("Probe nearby charged substitutions without disrupting the pocket.",),
        )

    def _append_complete_loop(
        self,
        *,
        loop_id: str,
        sequence: str,
        machine_diff: str,
        parent_loop_id: str | None = None,
        branch_label: str | None = None,
    ):
        return self.repo.append_loop(
            run_id="run_test",
            loop_id=loop_id,
            parent_loop_id=parent_loop_id,
            branch_label=branch_label,
            candidate=ProteinCandidate(sequence=sequence, origin=CandidateOrigin.DERIVED),
            change_set=ChangeSet(
                summary=f"Apply {machine_diff}",
                why="Use verifier feedback to improve binding.",
                changes=(
                    ProteinChange(
                        operation=ChangeOperation.SUBSTITUTE,
                        machine_diff=machine_diff,
                        rationale="Test a targeted local sequence change.",
                    ),
                ),
            ),
            evaluations=self._complete_evaluations(),
            reflection=self._complete_reflection(),
            make_active=True,
        )

    def test_active_context_exposes_seed_sequence_conditions_and_metrics(self) -> None:
        context = self.repo.get_active_context("run_test")

        self.assertEqual(context.active_loop_id, "loop_0")
        self.assertEqual(context.sequence, "ACDEFGHIKLMNPQRSTVWY")
        self.assertEqual(context.conditions.parameters["ph"], 7.4)
        self.assertEqual(context.latest_metrics["binding_score"], 0.41)

    def test_append_loop_records_one_change_set_rationale_and_feedback(self) -> None:
        loop = self.repo.append_loop(
            run_id="run_test",
            loop_id="loop_1",
            candidate=ProteinCandidate(sequence="ACDEYGHIKLMNPQRSTVWY", origin=CandidateOrigin.DERIVED),
            change_set=ChangeSet(
                summary="Increase hydrophobic contact near predicted pocket.",
                why="Boltz suggested the pocket was under-packed around residue 5.",
                changes=(
                    ProteinChange(
                        operation=ChangeOperation.SUBSTITUTE,
                        machine_diff="F5Y",
                        position=5,
                        from_residue="F",
                        to_residue="Y",
                        rationale="Add a polar aromatic contact while keeping local packing.",
                    ),
                ),
            ),
        )

        self.assertEqual(loop.status, LoopStatus.PENDING)
        report = self.repo.validate_loop_complete(run_id="run_test", loop_id="loop_1")
        self.assertFalse(report.is_complete)
        self.assertIn("boltz_evaluation_or_artifact", report.missing_requirements)
        self.assertIn("screening_evaluation", report.missing_requirements)
        self.assertIn("verifier_evaluation", report.missing_requirements)
        self.assertIn("reflection", report.missing_requirements)

        with self.assertRaises(LoopIncomplete):
            self.repo.finalize_loop(run_id="run_test", loop_id="loop_1")

        for evaluation in self._complete_evaluations():
            self.repo.attach_evaluation(run_id="run_test", loop_id="loop_1", evaluation=evaluation)
        self.repo.set_loop_reflection(
            run_id="run_test",
            loop_id="loop_1",
            reflection=self._complete_reflection(),
        )
        loop = self.repo.finalize_loop(run_id="run_test", loop_id="loop_1")
        context = self.repo.get_active_context("run_test")
        self.assertEqual(loop.status, LoopStatus.ACTIVE)
        self.assertEqual(context.sequence, "ACDEYGHIKLMNPQRSTVWY")
        self.assertEqual(context.previous_change_sets[0].changes[0].machine_diff, "F5Y")
        self.assertEqual(context.reflection.next_actions[0], "Probe nearby charged substitutions without disrupting the pocket.")

    def test_rollback_preserves_abandoned_branch_and_can_branch_from_prior_loop(self) -> None:
        self._append_complete_loop(
            loop_id="loop_1",
            sequence="ACDEYGHIKLMNPQRSTVWY",
            machine_diff="F5Y",
        )
        self._append_complete_loop(
            loop_id="loop_2",
            sequence="ACDEYGHIKLMNPQKSTVWY",
            machine_diff="R15K",
        )

        active = self.repo.rollback_to_loop(
            run_id="run_test",
            loop_id="loop_1",
            actor="human",
            reason="Loop 2 hurt the verifier score.",
        )
        self.assertEqual(active.loop_id, "loop_1")
        self.assertEqual(self.repo.get_loop(run_id="run_test", loop_id="loop_2").status, LoopStatus.ABANDONED)

        branch = self._append_complete_loop(
            loop_id="loop_3",
            parent_loop_id="loop_1",
            branch_label="human_rollback_branch",
            sequence="ACDEYGHIKLMNPQRSTAWY",
            machine_diff="V18A",
        )

        self.assertEqual(branch.status, LoopStatus.ACTIVE)
        self.assertEqual(self.repo.extract_sequence(run_id="run_test"), "ACDEYGHIKLMNPQRSTAWY")
        self.assertEqual(
            [loop.loop_id for loop in self.repo.get_lineage(run_id="run_test", loop_id="loop_3")],
            ["loop_0", "loop_1", "loop_3"],
        )

    def test_rollback_cannot_select_pending_loop(self) -> None:
        self.repo.append_loop(
            run_id="run_test",
            loop_id="loop_pending",
            candidate=ProteinCandidate(sequence="ACDEYGHIKLMNPQRSTVWY", origin=CandidateOrigin.DERIVED),
            change_set=ChangeSet(
                summary="Pending change",
                why="Try a candidate before screening is available.",
                changes=(
                    ProteinChange(
                        operation=ChangeOperation.SUBSTITUTE,
                        machine_diff="F5Y",
                        rationale="Test aromatic contact.",
                    ),
                ),
            ),
        )

        with self.assertRaises(InvalidLoopOperation):
            self.repo.rollback_to_loop(
                run_id="run_test",
                loop_id="loop_pending",
                actor="human",
                reason="Should not activate incomplete memory.",
            )

    def test_reject_loop_records_decision_and_prevents_activation(self) -> None:
        self.repo.append_loop(
            run_id="run_test",
            loop_id="loop_reject",
            candidate=ProteinCandidate(sequence="ACDEYGHIKLMNPQRSTVWY", origin=CandidateOrigin.DERIVED),
            change_set=ChangeSet(
                summary="Candidate to reject",
                why="Try a candidate before accepting it.",
                changes=(
                    ProteinChange(
                        operation=ChangeOperation.SUBSTITUTE,
                        machine_diff="F5Y",
                        rationale="Test aromatic contact.",
                    ),
                ),
            ),
        )

        rejected = self.repo.reject_loop(
            run_id="run_test",
            loop_id="loop_reject",
            actor="Codex",
            reason="Boltz confidence regressed.",
        )

        self.assertEqual(rejected.status, LoopStatus.REJECTED)
        self.assertEqual(self.repo.get_active_context("run_test").active_loop_id, "loop_0")
        self.assertEqual(rejected.human_inputs[-1].metadata["action"], "reject")
        self.assertEqual(rejected.human_inputs[-1].metadata["actor"], "Codex")
        self.assertIn("Boltz confidence regressed", rejected.human_inputs[-1].note)

        with self.assertRaises(InvalidLoopOperation):
            self.repo.rollback_to_loop(
                run_id="run_test",
                loop_id="loop_reject",
                actor="human",
                reason="Rejected candidates cannot become active.",
            )
        with self.assertRaises(InvalidLoopOperation):
            self.repo.attach_evaluation(
                run_id="run_test",
                loop_id="loop_reject",
                evaluation=self._complete_evaluations()[0],
            )

    def test_run_visualization_snapshot_exposes_graph_metrics_and_comments(self) -> None:
        self._append_complete_loop(
            loop_id="loop_1",
            sequence="ACDEYGHIKLMNPQRSTVWY",
            machine_diff="F5Y",
        )
        self._append_complete_loop(
            loop_id="loop_2",
            sequence="ACDEYGHIKLMNPQKSTVWY",
            machine_diff="R15K",
        )
        self.repo.rollback_to_loop(
            run_id="run_test",
            loop_id="loop_1",
            actor="human",
            reason="Loop 2 reduced useful verifier confidence.",
        )
        self.repo.add_loop_comment(
            run_id="run_test",
            loop_id="loop_1",
            author="human",
            note="Continue from here but avoid shrinking the binding pocket.",
            requested_changes=("Preserve the loop_1 aromatic contact.",),
        )

        snapshot = self.repo.get_run_visualization("run_test")
        nodes = {node.loop_id: node for node in snapshot.nodes}

        self.assertEqual(snapshot.root_loop_id, "loop_0")
        self.assertEqual(snapshot.active_loop_id, "loop_1")
        self.assertEqual(
            {(edge.parent_loop_id, edge.child_loop_id) for edge in snapshot.edges},
            {("loop_0", "loop_1"), ("loop_1", "loop_2")},
        )
        self.assertTrue(nodes["loop_1"].is_active)
        self.assertTrue(nodes["loop_1"].can_branch_from)
        self.assertEqual(nodes["loop_1"].human_input_count, 2)
        self.assertEqual(nodes["loop_1"].human_inputs[-1].requested_changes[0], "Preserve the loop_1 aromatic contact.")
        self.assertEqual(nodes["loop_1"].latest_metrics["binding_score"], 0.57)
        self.assertIn(EvaluationKind.SCREENING, nodes["loop_1"].evaluation_kinds)
        self.assertEqual(nodes["loop_2"].status, LoopStatus.ABANDONED)
        self.assertTrue(nodes["loop_2"].can_branch_from)

        payload = snapshot.to_frontend_payload()
        self.assertEqual(payload["nodes"][1]["status"], "active")
        self.assertEqual(payload["nodes"][1]["human_inputs"][-1]["author"], "human")

    def test_loop_context_can_be_loaded_for_branching_from_non_active_loop(self) -> None:
        self._append_complete_loop(
            loop_id="loop_1",
            sequence="ACDEYGHIKLMNPQRSTVWY",
            machine_diff="F5Y",
        )
        self._append_complete_loop(
            loop_id="loop_2",
            sequence="ACDEYGHIKLMNPQKSTVWY",
            machine_diff="R15K",
        )
        self.repo.add_loop_comment(
            run_id="run_test",
            loop_id="loop_1",
            author="human",
            note="Use loop 1 as a branch point.",
            requested_changes=("Try a less disruptive second mutation.",),
        )

        context = self.repo.get_loop_context(run_id="run_test", loop_id="loop_1")

        self.assertEqual(context.active_loop_id, "loop_1")
        self.assertEqual(context.sequence, "ACDEYGHIKLMNPQRSTVWY")
        self.assertEqual(context.lineage_loop_ids, ("loop_0", "loop_1"))
        self.assertEqual(context.human_inputs[-1].note, "Use loop 1 as a branch point.")
        self.assertEqual(context.human_inputs[-1].requested_changes[0], "Try a less disruptive second mutation.")

    def test_create_run_from_wf_seed_selection_persists_evidence_and_context(self) -> None:
        pool = self.repo.create_seed_pool(
            pool_id="seed_pool_1",
            objective=self.objective,
            conditions=self.conditions,
            candidates=(
                SeedCandidate(
                    seed_candidate_id="seed_a",
                    sequence="AAAAAAAAAA",
                    origin=CandidateOrigin.CCDC_CSD,
                    source_database="CCDC/CSD",
                    source_id="CSD-0001",
                    evaluations=(
                        EvaluationResult(
                            kind=EvaluationKind.SCREENING,
                            evaluator_name="wf-screen",
                            metrics=(MetricValue(name="screen_score", value=0.62),),
                        ),
                    ),
                ),
                SeedCandidate(
                    seed_candidate_id="seed_b",
                    sequence="GGGGGGGGGG",
                    origin=CandidateOrigin.CCDC_CSD,
                    source_database="CCDC/CSD",
                    source_id="CSD-0002",
                    evaluations=(
                        EvaluationResult(
                            kind=EvaluationKind.SCREENING,
                            evaluator_name="wf-screen",
                            metrics=(MetricValue(name="screen_score", value=0.91),),
                        ),
                        EvaluationResult(
                            kind=EvaluationKind.VERIFIER,
                            evaluator_name="wf-verifier",
                            metrics=(MetricValue(name="binding_score", value=0.79),),
                        ),
                    ),
                ),
            ),
        )
        decision = self.repo.record_seed_selection_decision(
            decision_id="seed_decision_1",
            pool_id=pool.pool_id,
            selected_seed_candidate_id="seed_b",
            rationale="WF selected seed_b because it had the best screening and verifier profile.",
            selected_by="WF",
            ranked_seed_candidate_ids=("seed_b", "seed_a"),
        )

        run = self.repo.create_run_from_seed_selection(
            decision_id=decision.decision_id,
            run_id="run_from_seed",
            loop_id="loop_seed",
        )
        loop_0 = self.repo.get_loop(run_id=run.run_id, loop_id=run.root_loop_id)
        context = self.repo.get_active_context(run.run_id)

        self.assertEqual(loop_0.candidate.sequence, "GGGGGGGGGG")
        self.assertEqual(loop_0.candidate.metadata["seed_candidate_id"], "seed_b")
        self.assertEqual(loop_0.candidate.metadata["seed_source_id"], "CSD-0002")
        self.assertEqual(loop_0.latest_metric_map()["binding_score"], 0.79)
        self.assertEqual(context.seed_selection_decision.rationale, decision.rationale)
        self.assertEqual(context.seed_selection_decision.ranked_seed_candidate_ids, ("seed_b", "seed_a"))
        self.assertEqual(len(self.repo.get_seed_pool("seed_pool_1").candidates), 2)

    def test_human_override_seed_selection_is_recorded_without_memory_ranking_logic(self) -> None:
        self.repo.create_seed_pool(
            pool_id="seed_pool_override",
            objective=self.objective,
            conditions=self.conditions,
            candidates=(
                SeedCandidate(
                    seed_candidate_id="auto_best",
                    sequence="CCCCCCCCCC",
                    origin=CandidateOrigin.CCDC_CSD,
                    evaluations=(
                        EvaluationResult(
                            kind=EvaluationKind.VERIFIER,
                            evaluator_name="wf-verifier",
                            metrics=(MetricValue(name="binding_score", value=0.88),),
                        ),
                    ),
                ),
                SeedCandidate(
                    seed_candidate_id="human_choice",
                    sequence="DDDDDDDDDD",
                    origin=CandidateOrigin.CCDC_CSD,
                    evaluations=(
                        EvaluationResult(
                            kind=EvaluationKind.VERIFIER,
                            evaluator_name="wf-verifier",
                            metrics=(MetricValue(name="binding_score", value=0.71),),
                        ),
                    ),
                ),
            ),
        )
        decision = self.repo.record_seed_selection_decision(
            decision_id="human_override_decision",
            pool_id="seed_pool_override",
            selected_seed_candidate_id="human_choice",
            rationale="Human selected this seed because the alternate scaffold looked more editable.",
            selected_by="human",
            human_override=True,
            ranked_seed_candidate_ids=("auto_best", "human_choice"),
        )

        run = self.repo.create_run_from_seed_selection(
            decision_id=decision.decision_id,
            run_id="run_human_seed",
            loop_id="loop_0_human_seed",
        )

        self.assertTrue(decision.human_override)
        self.assertEqual(decision.selected_by, "human")
        self.assertEqual(self.repo.extract_sequence(run_id=run.run_id), "DDDDDDDDDD")
        self.assertEqual(run.seed_selection_decision.selected_seed_candidate_id, "human_choice")

    def test_seed_selection_rejects_unknown_selected_seed(self) -> None:
        self.repo.create_seed_pool(
            pool_id="seed_pool_missing",
            objective=self.objective,
            conditions=self.conditions,
            candidates=(
                SeedCandidate(
                    seed_candidate_id="known_seed",
                    sequence="EEEEEEEEEE",
                    origin=CandidateOrigin.CCDC_CSD,
                ),
            ),
        )

        with self.assertRaises(SeedCandidateNotFound):
            self.repo.record_seed_selection_decision(
                pool_id="seed_pool_missing",
                selected_seed_candidate_id="missing_seed",
                rationale="This should fail because memory only records known WF candidates.",
            )


if __name__ == "__main__":
    unittest.main()
