from __future__ import annotations

import os
import time

from fastapi.testclient import TestClient

import materialhack_agent.workbench_api as api_module
from materialhack_agent.workbench_service import JobState, WorkbenchService


def _client() -> TestClient:
    api_module.service = WorkbenchService()
    return TestClient(api_module.app)


def _wait_for_job(client: TestClient, job_id: str) -> dict:
    deadline = time.time() + 5
    while time.time() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        response.raise_for_status()
        payload = response.json()
        if payload["status"] in {JobState.SUCCEEDED.value, JobState.FAILED.value}:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"job did not finish: {job_id}")


def _create_seed_only_run(client: TestClient) -> dict:
    response = client.post(
        "/api/runs",
        json={
            "objective": "design a protein that binds Zn2+ at pH 5",
            "target": "ZN2+",
            "ph": 5.0,
            "functions": ["bind"],
            "length": 60,
            "seed_count": 3,
            "seed_sources": ["ccdc_csd", "de_novo"],
            "target_score": 0.95,
            "loop_count": 2,
            "run_mode": "seed_only",
        },
    )
    response.raise_for_status()
    run = response.json()
    _wait_for_job(client, run["job_id"])
    return run


def _propose_simple_agent_loop(
    client: TestClient,
    run_id: str,
    *,
    parent_loop_id: str | None = None,
    branch_label: str | None = None,
) -> dict:
    context_response = client.get(f"/api/runs/{run_id}/agent-context")
    context_response.raise_for_status()
    context = context_response.json()
    parent_sequence = context["sequence"]
    from_residue = parent_sequence[0]
    to_residue = "A" if from_residue != "A" else "C"
    candidate_sequence = to_residue + parent_sequence[1:]
    machine_diff = f"{from_residue}1{to_residue}"
    payload = {
        "author": "Codex",
        "parent_loop_id": parent_loop_id or context["active_loop_id"],
        "sequence": candidate_sequence,
        "candidate_name": "codex_guardrail_loop",
        "planning_note": "Test one guarded chat-operated candidate.",
        "change_set": {
            "summary": f"Codex substitution {machine_diff}",
            "why": "Exercise chat loop lifecycle guardrails.",
            "author": "Codex",
            "changes": [
                {
                    "operation": "substitute",
                    "machine_diff": machine_diff,
                    "position": 1,
                    "from_residue": from_residue,
                    "to_residue": to_residue,
                    "rationale": "Bounded single-site edit supplied by the chat agent.",
                    "expected_effect": "Exercise guarded chat-owned loop planning memory.",
                }
            ],
        },
    }
    if branch_label is not None:
        payload["branch_label"] = branch_label
    response = client.post(f"/api/runs/{run_id}/agent-loops", json=payload)
    response.raise_for_status()
    return response.json()


def test_health_endpoint_identifies_workbench():
    client = _client()

    response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "novacore-workbench"
    assert payload["status"] == "ok"
    assert payload["latest_run_id"] is None


def test_parse_objective_returns_editable_defaults():
    client = _client()

    response = client.post(
        "/api/objectives/parse",
        json={"objective": "design a 72 residue protein that binds Zn2+ at pH 5 and can polymerize"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["target"] == "ZN2+"
    assert payload["ph"] == 5.0
    assert payload["length"] == 72
    assert payload["seed_sources"] == ["ccdc_csd", "de_novo"]
    assert [target["name"] for target in payload["optimization_targets"]] == ["trs_total", "plddt"]


def test_boltz_api_auth_can_set_runtime_key_without_echoing_secret(monkeypatch):
    monkeypatch.delenv("BOLTZ_API_KEY", raising=False)
    client = _client()

    missing = client.get("/api/boltz-api/auth")
    missing.raise_for_status()
    assert missing.json() == {"configured": False, "source": None, "masked_key": None}

    response = client.post("/api/boltz-api/auth", json={"api_key": "bapi_test_123456"})
    response.raise_for_status()
    payload = response.json()

    assert payload == {"configured": True, "source": "runtime", "masked_key": "bapi...3456"}
    assert os.environ["BOLTZ_API_KEY"] == "bapi_test_123456"
    assert "bapi_test_123456" not in response.text

    cleared = client.delete("/api/boltz-api/auth")
    cleared.raise_for_status()
    assert cleared.json() == {"configured": False, "source": None, "masked_key": None}
    assert "BOLTZ_API_KEY" not in os.environ


def test_boltz_api_auth_clear_restores_environment_key(monkeypatch):
    monkeypatch.setenv("BOLTZ_API_KEY", "env_key_abcdef")
    client = _client()

    initial = client.get("/api/boltz-api/auth")
    initial.raise_for_status()
    assert initial.json() == {"configured": True, "source": "environment", "masked_key": "env_...cdef"}

    override = client.post("/api/boltz-api/auth", json={"api_key": "runtime_key_9999"})
    override.raise_for_status()
    assert override.json() == {"configured": True, "source": "runtime", "masked_key": "runt...9999"}

    cleared = client.delete("/api/boltz-api/auth")
    cleared.raise_for_status()
    assert cleared.json() == {"configured": True, "source": "environment", "masked_key": "env_...cdef"}
    assert os.environ["BOLTZ_API_KEY"] == "env_key_abcdef"


def test_create_run_builds_seed_and_requested_loops():
    client = _client()

    response = client.post(
        "/api/runs",
        json={
            "objective": "design a protein that binds Zn2+ at pH 5",
            "target": "ZN2+",
            "ph": 5.0,
            "functions": ["bind"],
            "length": 60,
            "seed_count": 4,
            "seed_sources": ["ccdc_csd"],
            "target_score": 0.95,
            "loop_count": 2,
            "optimization_targets": [
                {"name": "trs_total", "target": 0.75, "comparator": "gte", "weight": 1.0},
                {"name": "plddt", "target": 0.7, "comparator": "gte", "weight": 0.5},
            ],
        },
    )
    response.raise_for_status()
    payload = response.json()
    job = _wait_for_job(client, payload["job_id"])

    assert job["status"] == "succeeded"
    memory = client.get(f"/api/runs/{payload['run_id']}/memory").json()
    assert len(memory["nodes"]) == 3
    assert [node["index"] for node in memory["nodes"]] == [0, 1, 2]
    assert any("verifier" in node["evaluation_kinds"] for node in memory["nodes"])
    assert [goal["name"] for goal in memory["objective"]["goals"]] == ["trs_total", "plddt"]
    loop_0 = client.get(f"/api/runs/{payload['run_id']}/loops/{memory['root_loop_id']}").json()
    artifact_uris = [
        artifact["uri"]
        for artifact in loop_0["loop"]["candidate"]["structure_artifacts"]
    ]
    assert any(uri.startswith("zip://ligands_10000.zip!/ligands_10000/Zn/") for uri in artifact_uris)
    assert client.get("/api/runs/latest").json()["run_id"] == payload["run_id"]


def test_seed_only_run_mode_stops_after_loop_zero():
    client = _client()

    response = client.post(
        "/api/runs",
        json={
            "objective": "design a protein that binds Zn2+ at pH 5",
            "target": "ZN2+",
            "ph": 5.0,
            "functions": ["bind"],
            "length": 60,
            "seed_count": 4,
            "seed_sources": ["ccdc_csd", "de_novo"],
            "target_score": 0.95,
            "loop_count": 2,
            "run_mode": "seed_only",
        },
    )
    response.raise_for_status()
    payload = response.json()
    job = _wait_for_job(client, payload["job_id"])

    assert job["status"] == "succeeded"
    assert job["result"]["loops_completed"] == 0
    memory = client.get(f"/api/runs/{payload['run_id']}/memory").json()
    assert len(memory["nodes"]) == 1
    assert memory["nodes"][0]["index"] == 0
    assert memory["objective"]["max_loops"] == 2


def test_chat_agent_can_own_loop_plan_reflection_and_finalization():
    client = _client()
    response = client.post(
        "/api/runs",
        json={
            "objective": "design a protein that binds Zn2+ at pH 5",
            "target": "ZN2+",
            "ph": 5.0,
            "functions": ["bind"],
            "length": 60,
            "seed_count": 3,
            "seed_sources": ["ccdc_csd", "de_novo"],
            "target_score": 0.95,
            "loop_count": 2,
            "run_mode": "seed_only",
        },
    )
    response.raise_for_status()
    run = response.json()
    _wait_for_job(client, run["job_id"])

    context_response = client.get(f"/api/runs/{run['run_id']}/agent-context")
    context_response.raise_for_status()
    context = context_response.json()
    parent_loop_id = context["active_loop_id"]
    parent_sequence = context["sequence"]
    from_residue = parent_sequence[0]
    to_residue = "A" if from_residue != "A" else "C"
    candidate_sequence = to_residue + parent_sequence[1:]
    machine_diff = f"{from_residue}1{to_residue}"

    proposed = client.post(
        f"/api/runs/{run['run_id']}/agent-loops",
        json={
            "author": "Codex",
            "parent_loop_id": parent_loop_id,
            "sequence": candidate_sequence,
            "candidate_name": "codex_loop_1",
            "planning_note": "Codex selected one interpretable N-terminal substitution after reading loop_0 evidence.",
            "advisor_reports": [
                {
                    "advisor_name": "mutation-planner",
                    "role": "mutation_planner",
                    "summary": "Suggest a single conservative N-terminal substitution.",
                    "recommendations": [machine_diff],
                    "concerns": ["This does not yet prove zinc-binding improvement."],
                    "confidence": 0.62,
                },
                {
                    "advisor_name": "skeptic",
                    "role": "skeptic",
                    "summary": "The proposed mutation is only a harness exercise unless metrics improve.",
                    "concerns": ["Do not finalize based on plan quality alone."],
                },
            ],
            "change_set": {
                "summary": f"Codex substitution {machine_diff}",
                "why": "Test chat planner records explicit rationale before harness evaluation.",
                "author": "Codex",
                "changes": [
                    {
                        "operation": "substitute",
                        "machine_diff": machine_diff,
                        "position": 1,
                        "from_residue": from_residue,
                        "to_residue": to_residue,
                        "rationale": "Bounded single-site edit supplied by the chat agent.",
                        "expected_effect": "Exercise chat-owned loop planning memory.",
                    }
                ],
            },
        },
    )
    proposed.raise_for_status()
    pending_loop = proposed.json()
    loop_id = pending_loop["loop_id"]

    memory = client.get(f"/api/runs/{run['run_id']}/memory").json()
    nodes = {node["loop_id"]: node for node in memory["nodes"]}
    assert nodes[loop_id]["status"] == "pending"
    assert nodes[loop_id]["change_diffs"] == [machine_diff]
    human_inputs = nodes[loop_id]["human_inputs"]
    actions = [item["metadata"]["action"] for item in human_inputs]
    assert actions == ["chat_plan", "advisor_report", "advisor_report"]
    assert human_inputs[1]["metadata"]["advisor_role"] == "mutation_planner"
    assert human_inputs[1]["metadata"]["advisor_stage"] == "planning"
    assert human_inputs[1]["metadata"]["recorded_by"] == "Codex"
    assert human_inputs[1]["metadata"]["read_only_advisory"] is True
    assert human_inputs[1]["metadata"]["main_agent_owns_decision"] is True

    evaluation = client.post(f"/api/runs/{run['run_id']}/agent-loops/{loop_id}/evaluations")
    evaluation.raise_for_status()
    job = _wait_for_job(client, evaluation.json()["job_id"])
    assert job["status"] == "succeeded"
    assert job["result"]["chat_operated"] is True

    premature_finalize = client.post(f"/api/runs/{run['run_id']}/agent-loops/{loop_id}/finalize")
    assert premature_finalize.status_code == 400
    assert "reflection" in premature_finalize.json()["detail"]

    reflection = client.post(
        f"/api/runs/{run['run_id']}/agent-loops/{loop_id}/reflection",
        json={
            "went_well": ["Harness evaluations were attached after the chat-authored plan."],
            "went_wrong": ["Verifier MCP is still pending."],
            "next_actions": ["Read the evaluated metrics before proposing the next mutation."],
            "notes": "Codex owns this reflection rather than the deterministic runner.",
            "advisor_reports": [
                {
                    "advisor_name": "structure-metric-critic",
                    "role": "structure_metric_critic",
                    "summary": "Evaluation evidence exists, but the main agent must decide whether it is sufficient.",
                    "recommendations": ["Compare TRS and pLDDT against objective before the next loop."],
                    "concerns": ["Pending verifier evidence remains a production gap."],
                    "confidence": 0.7,
                }
            ],
        },
    )
    reflection.raise_for_status()

    finalized = client.post(f"/api/runs/{run['run_id']}/agent-loops/{loop_id}/finalize")
    finalized.raise_for_status()
    finalized_loop = finalized.json()
    assert finalized_loop["status"] == "active"

    updated = client.get(f"/api/runs/{run['run_id']}/memory").json()
    assert updated["active_loop_id"] == loop_id
    active = {node["loop_id"]: node for node in updated["nodes"]}[loop_id]
    assert active["evaluation_kinds"] == ["boltz", "screening", "verifier"]
    assert active["reflection"]["next_actions"] == [
        "Read the evaluated metrics before proposing the next mutation."
    ]
    advisor_inputs = [
        item for item in active["human_inputs"]
        if item["metadata"]["action"] == "advisor_report"
    ]
    assert [item["metadata"]["advisor_stage"] for item in advisor_inputs] == [
        "planning",
        "planning",
        "post_evaluation",
    ]


def test_chat_agent_rejects_pending_loop_and_blocks_stacked_candidates():
    client = _client()
    run = _create_seed_only_run(client)
    run_id = run["run_id"]

    first = _propose_simple_agent_loop(client, run_id)

    blocked = client.post(
        f"/api/runs/{run_id}/agent-loops",
        json={
            "author": "Codex",
            "parent_loop_id": first["parent_loop_id"],
            "sequence": first["candidate"]["sequence"],
            "candidate_name": "codex_second_pending",
            "planning_note": "This should be blocked while the first pending loop is unresolved.",
            "change_set": {
                "summary": "Blocked duplicate pending candidate",
                "why": "The service should enforce one unresolved pending loop at a time.",
                "author": "Codex",
                "changes": [
                    {
                        "operation": "substitute",
                        "machine_diff": first["change_set"]["changes"][0]["machine_diff"],
                        "position": 1,
                        "rationale": "Duplicate candidate should not be accepted.",
                    }
                ],
            },
        },
    )
    assert blocked.status_code == 400
    assert "unresolved pending loop" in blocked.json()["detail"]

    rejected = client.post(
        f"/api/runs/{run_id}/agent-loops/{first['loop_id']}/reject",
        json={"actor": "Codex", "reason": "Reject unresolved candidate during lifecycle guardrail test."},
    )
    rejected.raise_for_status()
    assert rejected.json()["status"] == "rejected"

    memory = client.get(f"/api/runs/{run_id}/memory").json()
    nodes = {node["loop_id"]: node for node in memory["nodes"]}
    assert nodes[first["loop_id"]]["status"] == "rejected"
    assert nodes[first["loop_id"]]["human_inputs"][-1]["metadata"]["action"] == "reject"

    second = _propose_simple_agent_loop(client, run_id)
    assert second["status"] == "pending"
    assert second["loop_id"] != first["loop_id"]


def test_chat_agent_branch_from_non_active_loop_requires_branch_label():
    client = _client()
    run = client.post(
        "/api/runs",
        json={
            "objective": "design a protein that binds Zn2+ at pH 5",
            "target": "ZN2+",
            "ph": 5.0,
            "functions": ["bind"],
            "length": 60,
            "seed_count": 2,
            "seed_sources": ["ccdc_csd", "de_novo"],
            "target_score": 0.95,
            "loop_count": 1,
        },
    ).json()
    _wait_for_job(client, run["job_id"])
    memory = client.get(f"/api/runs/{run['run_id']}/memory").json()
    root_loop_id = memory["root_loop_id"]

    context = client.get(f"/api/runs/{run['run_id']}/agent-context").json()
    sequence = context["sequence"]
    from_residue = sequence[0]
    to_residue = "A" if from_residue != "A" else "C"
    candidate_sequence = to_residue + sequence[1:]
    machine_diff = f"{from_residue}1{to_residue}"
    blocked = client.post(
        f"/api/runs/{run['run_id']}/agent-loops",
        json={
            "author": "Codex",
            "parent_loop_id": root_loop_id,
            "sequence": candidate_sequence,
            "candidate_name": "codex_missing_branch_label",
            "planning_note": "Branching from a non-active loop should be explicit.",
            "change_set": {
                "summary": f"Codex substitution {machine_diff}",
                "why": "Exercise branch guardrails.",
                "author": "Codex",
                "changes": [
                    {
                        "operation": "substitute",
                        "machine_diff": machine_diff,
                        "position": 1,
                        "from_residue": from_residue,
                        "to_residue": to_residue,
                        "rationale": "Bounded branch test.",
                    }
                ],
            },
        },
    )
    assert blocked.status_code == 400
    assert "branch_label" in blocked.json()["detail"]

    allowed = _propose_simple_agent_loop(
        client,
        run["run_id"],
        parent_loop_id=root_loop_id,
        branch_label="explicit_non_active_branch",
    )
    assert allowed["parent_loop_id"] == root_loop_id
    assert allowed["branch_label"] == "explicit_non_active_branch"


def test_event_log_replays_as_sse_payloads():
    client = _client()
    run = client.post(
        "/api/runs",
        json={
            "objective": "design a protein that binds Zn2+ at pH 5",
            "target": "ZN2+",
            "ph": 5.0,
            "functions": ["bind"],
            "length": 60,
            "seed_count": 2,
            "seed_sources": ["ccdc_csd", "de_novo"],
            "target_score": 0.95,
            "loop_count": 1,
        },
    ).json()
    _wait_for_job(client, run["job_id"])

    sse_payloads = [
        api_module._format_sse(event.event_type, event.to_payload())
        for event in api_module.service.event_hub.events_for_run(run["run_id"])
    ]

    assert any(payload.startswith("event: evaluation_attached") for payload in sse_payloads)
    assert any('"evaluation_kind": "screening"' in payload for payload in sse_payloads)
    assert any('"evaluation_kind": "verifier"' in payload for payload in sse_payloads)
    assert any('"evaluator_name": "trs"' in payload for payload in sse_payloads)


def test_rollback_marks_descendants_abandoned_and_emits_event():
    client = _client()
    run = client.post(
        "/api/runs",
        json={
            "objective": "design a protein that binds Zn2+ at pH 5",
            "target": "ZN2+",
            "ph": 5.0,
            "functions": ["bind"],
            "length": 60,
            "seed_count": 2,
            "seed_sources": ["ccdc_csd", "de_novo"],
            "target_score": 0.95,
            "loop_count": 2,
        },
    ).json()
    _wait_for_job(client, run["job_id"])
    memory = client.get(f"/api/runs/{run['run_id']}/memory").json()
    loop_1 = memory["nodes"][1]["loop_id"]
    loop_2 = memory["nodes"][2]["loop_id"]

    rollback = client.post(
        f"/api/runs/{run['run_id']}/rollback",
        json={"loop_id": loop_1, "actor": "human", "reason": "Loop 2 reduced useful verifier confidence."},
    )
    rollback.raise_for_status()

    updated = client.get(f"/api/runs/{run['run_id']}/memory").json()
    nodes = {node["loop_id"]: node for node in updated["nodes"]}
    assert nodes[loop_1]["status"] == "active"
    assert nodes[loop_2]["status"] == "abandoned"
    assert nodes[loop_1]["human_inputs"][-1]["metadata"]["action"] == "rollback"

    event_types = [
        event.event_type
        for event in api_module.service.event_hub.events_for_run(run["run_id"])
    ]
    assert "rollback_recorded" in event_types


def test_continue_from_earlier_loop_creates_branch():
    client = _client()
    run = client.post(
        "/api/runs",
        json={
            "objective": "design a protein that binds Zn2+ at pH 5",
            "target": "ZN2+",
            "ph": 5.0,
            "functions": ["bind"],
            "length": 60,
            "seed_count": 2,
            "seed_sources": ["ccdc_csd", "de_novo"],
            "target_score": 0.95,
            "loop_count": 2,
        },
    ).json()
    _wait_for_job(client, run["job_id"])
    memory = client.get(f"/api/runs/{run['run_id']}/memory").json()
    loop_1 = memory["nodes"][1]["loop_id"]
    old_active = memory["active_loop_id"]

    branch = client.post(
        f"/api/runs/{run['run_id']}/loops",
        json={"loop_count": 1, "start_loop_id": loop_1},
    ).json()
    _wait_for_job(client, branch["job_id"])

    updated = client.get(f"/api/runs/{run['run_id']}/memory").json()
    nodes = {node["loop_id"]: node for node in updated["nodes"]}
    assert nodes[old_active]["status"] == "abandoned"
    assert nodes[updated["active_loop_id"]]["parent_loop_id"] == loop_1
