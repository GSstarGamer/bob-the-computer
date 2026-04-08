from __future__ import annotations


class AgentsStage1Client:
    """Legacy placeholder for the removed OpenAI Agents SDK path."""

    def __init__(self, *args, **kwargs) -> None:  # pragma: no cover - compatibility guard
        raise RuntimeError(
            "AgentsStage1Client has been replaced by the direct OpenAI planner flow. "
            "Use Stage1Planner via bob.stage1.orchestrator.build_default_stage1_orchestrator()."
        )
