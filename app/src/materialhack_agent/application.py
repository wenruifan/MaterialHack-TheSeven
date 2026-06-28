from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from materialhack_loop_runner import LoopRunnerResult
from materialhack_memory import (
    CandidateOrigin,
    InMemoryProteinMemoryRepository,
    MemoryRepository,
    MetricGoal,
    RunVisualizationSnapshot,
)

from materialhack_agent.novacore import NovacoreToolConfig, build_novacore_runner
from materialhack_agent.seed_flow import SeedFlowConfig, SeedFlowResult, create_seeded_run


@dataclass(frozen=True)
class AgentAppResult:
    seed_flow: SeedFlowResult
    runner_result: LoopRunnerResult | None
    snapshot: RunVisualizationSnapshot

    @property
    def run_id(self) -> str:
        return self.seed_flow.run.run_id


@dataclass
class MaterialHackAgentApp:
    """Runnable Novacore composition of seed flow, durable memory, and loop runner."""

    memory: MemoryRepository | None = None

    def __post_init__(self) -> None:
        if self.memory is None:
            self.memory = InMemoryProteinMemoryRepository()

    def run(
        self,
        objective: str,
        *,
        seed_count: int = 5,
        loop_count: int = 2,
        target_score: float = 0.8,
        max_loops: int | None = None,
        rng_seed: int = 7,
        seed_sources: tuple[CandidateOrigin, ...] | None = None,
        optimization_goals: tuple[MetricGoal, ...] | None = None,
        boltz_command: str = "boltz",
        enable_external_tools: bool = False,
        prefer_boltz_api: bool = True,
        boltz_api_model: str = "boltz-2.1",
        boltz_api_poll_interval_seconds: float = 5.0,
        boltz_api_timeout_seconds: int = 3600,
        verifier_mcp_server: str | None = None,
        touchstone_command: str = "touchstone",
        touchstone_use_uvx: bool = True,
        touchstone_deep: bool = False,
        touchstone_stress: bool = False,
        touchstone_timeout_seconds: int = 1800,
        boltz_accelerator: str = "cpu",
        boltz_model: str = "boltz2",
        boltz_cache: str | None = None,
        boltz_use_msa_server: bool = False,
        boltz_msa_server_url: str = "https://api.colabfold.com",
        boltz_msa_pairing_strategy: str = "greedy",
        boltz_timeout_seconds: int = 3600,
        boltz_recycling_steps: int | None = None,
        boltz_sampling_steps: int | None = None,
        boltz_diffusion_samples: int | None = None,
    ) -> AgentAppResult:
        if loop_count < 0:
            raise ValueError("loop_count must be greater than or equal to zero")

        config = SeedFlowConfig(
            seed_count=seed_count,
            target_score=target_score,
            max_loops=max_loops if max_loops is not None else loop_count,
            rng_seed=rng_seed,
            seed_sources=seed_sources or (CandidateOrigin.CCDC_CSD, CandidateOrigin.DE_NOVO),
            optimization_goals=optimization_goals,
            ccdc_ligand_zip_path=str(Path(__file__).resolve().parents[3] / "ligands_10000.zip"),
        )
        seed_flow = create_seeded_run(self.memory, objective, config=config)

        runner_result: LoopRunnerResult | None = None
        if loop_count:
            runner = build_novacore_runner(
                memory=self.memory,
                tool_config=NovacoreToolConfig(
                    boltz_command=boltz_command,
                    enable_external_tools=enable_external_tools,
                    prefer_boltz_api=prefer_boltz_api,
                    boltz_api_model=boltz_api_model,
                    boltz_api_poll_interval_seconds=boltz_api_poll_interval_seconds,
                    boltz_api_timeout_seconds=boltz_api_timeout_seconds,
                    verifier_mcp_server=verifier_mcp_server,
                    touchstone_command=touchstone_command,
                    touchstone_use_uvx=touchstone_use_uvx,
                    touchstone_deep=touchstone_deep,
                    touchstone_stress=touchstone_stress,
                    touchstone_timeout_seconds=touchstone_timeout_seconds,
                    boltz_accelerator=boltz_accelerator,
                    boltz_model=boltz_model,
                    boltz_cache=boltz_cache,
                    boltz_use_msa_server=boltz_use_msa_server,
                    boltz_msa_server_url=boltz_msa_server_url,
                    boltz_msa_pairing_strategy=boltz_msa_pairing_strategy,
                    boltz_timeout_seconds=boltz_timeout_seconds,
                    boltz_recycling_steps=boltz_recycling_steps,
                    boltz_sampling_steps=boltz_sampling_steps,
                    boltz_diffusion_samples=boltz_diffusion_samples,
                ),
            )
            runner_result = runner.run_for_loops(seed_flow.run.run_id, loop_count)

        snapshot = self.memory.get_run_visualization(seed_flow.run.run_id)
        return AgentAppResult(
            seed_flow=seed_flow,
            runner_result=runner_result,
            snapshot=snapshot,
        )
