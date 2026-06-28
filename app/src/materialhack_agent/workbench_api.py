from __future__ import annotations

import asyncio
import json
import queue
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from materialhack_memory import (
    ChangeOperation,
    ChangeSet,
    HumanInput,
    LoopReflection,
    MetricGoal,
    ProteinChange,
    to_jsonable,
)

from materialhack_agent.workbench_service import WorkbenchService


class ParseObjectiveRequest(BaseModel):
    objective: str = Field(min_length=1)


class OptimizationTargetPayload(BaseModel):
    name: str = Field(min_length=1)
    target: float
    comparator: Literal["gte", "lte", "eq"] = "gte"
    weight: float = 1.0
    unit: str | None = None
    description: str | None = None

    def to_metric_goal(self) -> MetricGoal:
        return MetricGoal(
            name=self.name,
            target=self.target,
            comparator=self.comparator,
            weight=self.weight,
            unit=self.unit,
            description=self.description,
        )


class ParseObjectiveResponse(BaseModel):
    target: str
    ph: float
    functions: list[str]
    length: int
    seed_count: int
    seed_sources: list[str]
    target_score: float
    loop_count: int
    optimization_targets: list[OptimizationTargetPayload]


class CreateRunRequest(BaseModel):
    objective: str = Field(min_length=1)
    target: str = Field(min_length=1)
    ph: float = 7.0
    functions: list[str] = Field(default_factory=lambda: ["bind"])
    length: int = Field(default=60, ge=1)
    seed_count: int = Field(default=5, ge=1)
    seed_sources: list[Literal["ccdc_csd", "de_novo"]] = Field(default_factory=lambda: ["ccdc_csd", "de_novo"])
    target_score: float = Field(default=0.8, ge=0.0, le=1.0)
    loop_count: int = Field(default=2, ge=0)
    optimization_targets: list[OptimizationTargetPayload] = Field(default_factory=list)
    run_mode: Literal["seed_and_loop", "seed_only"] = "seed_and_loop"
    rng_seed: int = 7


class CreateRunResponse(BaseModel):
    run_id: str
    job_id: str
    status: str


class RunLoopsRequest(BaseModel):
    loop_count: int = Field(default=1, ge=0)
    start_loop_id: str | None = None


class RunLoopsResponse(BaseModel):
    run_id: str
    job_id: str
    status: str


class ProteinChangePayload(BaseModel):
    operation: Literal["substitute", "insert", "delete", "structural_edit", "constraint_edit", "other"] = "substitute"
    machine_diff: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    position: int | None = Field(default=None, ge=1)
    from_residue: str | None = None
    to_residue: str | None = None
    expected_effect: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_change(self) -> ProteinChange:
        return ProteinChange(
            operation=ChangeOperation(self.operation),
            machine_diff=self.machine_diff,
            rationale=self.rationale,
            position=self.position,
            from_residue=self.from_residue,
            to_residue=self.to_residue,
            expected_effect=self.expected_effect,
            metadata=self.metadata,
        )


class ChangeSetPayload(BaseModel):
    summary: str = Field(min_length=1)
    why: str = Field(min_length=1)
    changes: list[ProteinChangePayload] = Field(min_length=1)
    author: str = "Codex"
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_change_set(self) -> ChangeSet:
        return ChangeSet(
            summary=self.summary,
            why=self.why,
            changes=tuple(change.to_change() for change in self.changes),
            author=self.author,
            metadata={
                **self.metadata,
                "chat_operated": True,
            },
        )


class AdvisorReportPayload(BaseModel):
    advisor_name: str = Field(min_length=1)
    role: Literal[
        "mutation_planner",
        "structure_metric_critic",
        "skeptic",
        "safety_reviewer",
        "other",
    ] = "other"
    summary: str = Field(min_length=1)
    recommendations: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_human_input(self, *, recorded_by: str, stage: Literal["planning", "post_evaluation"]) -> HumanInput:
        note_lines = [self.summary]
        if self.recommendations:
            note_lines.append("Recommendations:")
            note_lines.extend(f"- {item}" for item in self.recommendations if item)
        if self.concerns:
            note_lines.append("Concerns:")
            note_lines.extend(f"- {item}" for item in self.concerns if item)
        return HumanInput(
            author=self.advisor_name,
            note="\n".join(note_lines),
            requested_changes=tuple(item for item in self.recommendations if item),
            metadata={
                **self.metadata,
                "action": "advisor_report",
                "advisor_role": self.role,
                "advisor_stage": stage,
                "recorded_by": recorded_by,
                "read_only_advisory": True,
                "main_agent_owns_decision": True,
                "confidence": self.confidence,
            },
        )


class ProposeAgentLoopRequest(BaseModel):
    sequence: str = Field(min_length=1)
    change_set: ChangeSetPayload
    author: str = "Codex"
    parent_loop_id: str | None = None
    candidate_name: str | None = None
    branch_label: str | None = None
    planning_note: str | None = None
    advisor_reports: list[AdvisorReportPayload] = Field(default_factory=list)
    candidate_metadata: dict[str, Any] = Field(default_factory=dict)
    loop_metadata: dict[str, Any] = Field(default_factory=dict)


class AgentReflectionRequest(BaseModel):
    author: str = "Codex"
    went_well: list[str] = Field(default_factory=list)
    went_wrong: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    notes: str | None = None
    advisor_reports: list[AdvisorReportPayload] = Field(default_factory=list)

    def to_reflection(self) -> LoopReflection:
        return LoopReflection(
            went_well=tuple(item for item in self.went_well if item),
            went_wrong=tuple(item for item in self.went_wrong if item),
            next_actions=tuple(item for item in self.next_actions if item),
            notes=self.notes,
        )


class RollbackRequest(BaseModel):
    loop_id: str
    actor: str = "human"
    reason: str = Field(min_length=1)


class RejectLoopRequest(BaseModel):
    actor: str = "Codex"
    reason: str = Field(min_length=1)


class LatestRunResponse(BaseModel):
    run_id: str | None


class BoltzApiAuthStatus(BaseModel):
    configured: bool
    source: Literal["environment", "runtime"] | None = None
    masked_key: str | None = None


class BoltzApiAuthRequest(BaseModel):
    api_key: str = Field(min_length=1, max_length=4096)


service = WorkbenchService()
app = FastAPI(title="Novacore API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health_endpoint():
    return {
        "service": "novacore-workbench",
        "status": "ok",
        "latest_run_id": service.latest_run_id(),
    }


@app.post("/api/objectives/parse", response_model=ParseObjectiveResponse)
def parse_objective_endpoint(request: ParseObjectiveRequest) -> ParseObjectiveResponse:
    parameters = service.parse_objective_parameters(request.objective)
    return ParseObjectiveResponse(
        target=parameters.target,
        ph=parameters.ph,
        functions=list(parameters.functions),
        length=parameters.length,
        seed_count=parameters.seed_count,
        seed_sources=list(parameters.seed_sources),
        target_score=parameters.target_score,
        loop_count=parameters.loop_count,
        optimization_targets=[OptimizationTargetPayload(**to_jsonable(target)) for target in parameters.optimization_targets],
    )


@app.post("/api/runs", response_model=CreateRunResponse)
def create_run_endpoint(request: CreateRunRequest) -> CreateRunResponse:
    try:
        payload = request.model_dump(exclude={"optimization_targets"})
        run_id, job_id = service.create_run(
            **payload,
            optimization_targets=tuple(target.to_metric_goal() for target in request.optimization_targets),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CreateRunResponse(run_id=run_id, job_id=job_id, status=service.get_job(job_id).status.value)


@app.post("/api/runs/{run_id}/loops", response_model=RunLoopsResponse)
def run_loops_endpoint(run_id: str, request: RunLoopsRequest) -> RunLoopsResponse:
    try:
        job_id = service.run_loops(
            run_id=run_id,
            loop_count=request.loop_count,
            start_loop_id=request.start_loop_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RunLoopsResponse(run_id=run_id, job_id=job_id, status=service.get_job(job_id).status.value)


@app.get("/api/runs/{run_id}/agent-context")
def agent_context_endpoint(run_id: str, loop_id: str | None = None):
    try:
        return service.agent_context(run_id=run_id, loop_id=loop_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/runs/{run_id}/agent-loops")
def propose_agent_loop_endpoint(run_id: str, request: ProposeAgentLoopRequest):
    try:
        loop = service.propose_chat_loop(
            run_id=run_id,
            sequence=request.sequence,
            change_set=request.change_set.to_change_set(),
            author=request.author,
            parent_loop_id=request.parent_loop_id,
            candidate_name=request.candidate_name,
            branch_label=request.branch_label,
            planning_note=request.planning_note,
            advisor_reports=tuple(
                report.to_human_input(recorded_by=request.author, stage="planning")
                for report in request.advisor_reports
            ),
            candidate_metadata=request.candidate_metadata,
            loop_metadata=request.loop_metadata,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_jsonable(loop)


@app.post("/api/runs/{run_id}/agent-loops/{loop_id}/evaluations", response_model=RunLoopsResponse)
def evaluate_agent_loop_endpoint(run_id: str, loop_id: str) -> RunLoopsResponse:
    try:
        job_id = service.evaluate_chat_loop(run_id=run_id, loop_id=loop_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RunLoopsResponse(run_id=run_id, job_id=job_id, status=service.get_job(job_id).status.value)


@app.post("/api/runs/{run_id}/agent-loops/{loop_id}/reflection")
def write_agent_reflection_endpoint(run_id: str, loop_id: str, request: AgentReflectionRequest):
    try:
        loop = service.write_chat_reflection(
            run_id=run_id,
            loop_id=loop_id,
            reflection=request.to_reflection(),
            advisor_reports=tuple(
                report.to_human_input(recorded_by=request.author, stage="post_evaluation")
                for report in request.advisor_reports
            ),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_jsonable(loop)


@app.post("/api/runs/{run_id}/agent-loops/{loop_id}/finalize")
def finalize_agent_loop_endpoint(run_id: str, loop_id: str):
    try:
        loop = service.finalize_chat_loop(run_id=run_id, loop_id=loop_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_jsonable(loop)


@app.post("/api/runs/{run_id}/agent-loops/{loop_id}/reject")
def reject_agent_loop_endpoint(run_id: str, loop_id: str, request: RejectLoopRequest):
    try:
        loop = service.reject_chat_loop(
            run_id=run_id,
            loop_id=loop_id,
            actor=request.actor,
            reason=request.reason,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_jsonable(loop)


@app.post("/api/runs/{run_id}/rollback")
def rollback_endpoint(run_id: str, request: RollbackRequest):
    try:
        loop = service.rollback(
            run_id=run_id,
            loop_id=request.loop_id,
            actor=request.actor,
            reason=request.reason,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return to_jsonable(loop)


@app.get("/api/runs/{run_id}/memory")
def memory_endpoint(run_id: str):
    try:
        return service.memory.get_run_visualization(run_id).to_frontend_payload()
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/latest", response_model=LatestRunResponse)
def latest_run_endpoint() -> LatestRunResponse:
    return LatestRunResponse(run_id=service.latest_run_id())


@app.get("/api/boltz-api/auth", response_model=BoltzApiAuthStatus)
def boltz_api_auth_endpoint() -> BoltzApiAuthStatus:
    return BoltzApiAuthStatus(**service.boltz_api_auth_status())


@app.post("/api/boltz-api/auth", response_model=BoltzApiAuthStatus)
def set_boltz_api_auth_endpoint(request: BoltzApiAuthRequest) -> BoltzApiAuthStatus:
    try:
        return BoltzApiAuthStatus(**service.set_boltz_api_key(request.api_key))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/boltz-api/auth", response_model=BoltzApiAuthStatus)
def clear_boltz_api_auth_endpoint() -> BoltzApiAuthStatus:
    return BoltzApiAuthStatus(**service.clear_boltz_api_key())


@app.get("/api/runs/{run_id}/loops/{loop_id}")
def loop_detail_endpoint(run_id: str, loop_id: str):
    try:
        loop = service.memory.get_loop(run_id=run_id, loop_id=loop_id)
        lineage = service.memory.get_lineage(run_id=run_id, loop_id=loop_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "loop": to_jsonable(loop),
        "lineage_loop_ids": [item.loop_id for item in lineage],
    }


@app.get("/api/jobs/{job_id}")
def job_endpoint(job_id: str):
    try:
        return service.get_job(job_id).to_payload()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=job_id) from exc


@app.get("/api/runs/{run_id}/events")
async def events_endpoint(run_id: str):
    async def stream():
        subscriber = service.event_hub.subscribe(run_id)
        try:
            for event in service.event_hub.events_for_run(run_id):
                yield _format_sse(event.event_type, event.to_payload())
            while True:
                try:
                    event = await asyncio.to_thread(subscriber.get, True, 15)
                    yield _format_sse(event.event_type, event.to_payload())
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            service.event_hub.unsubscribe(run_id, subscriber)

    return StreamingResponse(stream(), media_type="text/event-stream")


def _format_sse(event_type: str, payload: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"


def run() -> None:
    import uvicorn

    uvicorn.run("materialhack_agent.workbench_api:app", host="127.0.0.1", port=8000, reload=False)
