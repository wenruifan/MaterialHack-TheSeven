from materialhack_agent import MaterialHackAgentApp
from materialhack_memory import CandidateOrigin


def test_agent_app_creates_loop_0_and_runs_optimization_loops():
    app = MaterialHackAgentApp()

    result = app.run(
        "design a protein that binds Zn2+ at pH 5 and can polymerize",
        seed_count=4,
        loop_count=2,
        target_score=0.95,
        rng_seed=11,
    )

    assert result.seed_flow.run.seed_selection_decision == result.seed_flow.decision
    assert result.seed_flow.selected_seed.seed_candidate_id == result.seed_flow.decision.selected_seed_candidate_id
    assert {candidate.origin for candidate in result.seed_flow.pool.candidates} == {
        CandidateOrigin.CCDC_CSD,
        CandidateOrigin.DE_NOVO,
    }
    assert result.runner_result is not None
    assert result.runner_result.loops_completed == 2
    assert result.snapshot.active_loop_id == result.runner_result.active_loop_id
    assert len(result.snapshot.nodes) == 3
    assert [node.index for node in result.snapshot.nodes] == [0, 1, 2]
    assert all("trs_total" in node.latest_metrics for node in result.snapshot.nodes)
    assert all("trs_raw_total" in node.latest_metrics for node in result.snapshot.nodes)


def test_agent_app_can_stop_after_seed_handoff():
    app = MaterialHackAgentApp()

    result = app.run(
        "design a 45 residue protein that binds collagen at pH 6.5",
        seed_count=3,
        loop_count=0,
        rng_seed=3,
    )

    assert result.runner_result is None
    assert len(result.snapshot.nodes) == 1
    assert result.snapshot.nodes[0].index == 0
    assert result.snapshot.seed_selection_decision == result.seed_flow.decision
