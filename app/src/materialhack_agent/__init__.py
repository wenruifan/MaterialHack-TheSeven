"""Application composition for the MaterialHack protein-design agent."""

__all__ = [
    "AgentAppResult",
    "MaterialHackAgentApp",
    "SeedFlowConfig",
    "SeedFlowResult",
    "create_seeded_run",
]


def __getattr__(name: str):
    if name in {"AgentAppResult", "MaterialHackAgentApp"}:
        from materialhack_agent.application import AgentAppResult, MaterialHackAgentApp

        return {
            "AgentAppResult": AgentAppResult,
            "MaterialHackAgentApp": MaterialHackAgentApp,
        }[name]
    if name in {"SeedFlowConfig", "SeedFlowResult", "create_seeded_run"}:
        from materialhack_agent.seed_flow import SeedFlowConfig, SeedFlowResult, create_seeded_run

        return {
            "SeedFlowConfig": SeedFlowConfig,
            "SeedFlowResult": SeedFlowResult,
            "create_seeded_run": create_seeded_run,
        }[name]
    raise AttributeError(name)
