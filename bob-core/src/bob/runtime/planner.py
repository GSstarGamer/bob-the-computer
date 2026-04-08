from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from bob.llm import OpenAIResponsesClient
from bob.runtime.models import CandidateTask, ImplementationPlan, PlannerResult, TaskEvaluation, clamp_score
from bob.runtime.prompts import (
    PLANNER_INSTRUCTIONS,
    PLANNER_PROMPT_VERSION,
    build_planner_prompt,
    render_saved_planner_prompt,
)


class TaskPlannerError(RuntimeError):
    """Raised when Bob cannot create a usable implementation plan."""


class PlannerNormalizationError(TaskPlannerError):
    """Raised when planner output cannot be normalized into a usable plan."""


class _ImplementationPlanDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    short_summary: str | None = None
    why_it_is_useful: str | None = None
    scope: str | None = None
    difficulty: str | None = None
    estimated_implementation_size: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    edge_cases: list[str] = Field(default_factory=list)
    implementation_steps: list[str] = Field(default_factory=list)
    recommended_files_or_modules: list[str] = Field(default_factory=list)
    confidence: float | None = None
    priority_score: float | None = None


@dataclass(frozen=True)
class PlannerArtifacts:
    prompt_markdown: str
    raw_response: dict[str, Any]
    planner_result: PlannerResult


class TaskPlanner:
    def __init__(self, *, llm_client: OpenAIResponsesClient) -> None:
        self.llm_client = llm_client

    def create_plan(
        self,
        *,
        session_id: str,
        selected_task: CandidateTask,
        evaluation: TaskEvaluation,
        repo_snapshot_text: str,
    ) -> PlannerArtifacts:
        prompt = build_planner_prompt(selected_task, evaluation, repo_snapshot_text)
        response = self.llm_client.parse_structured_output(
            instructions=PLANNER_INSTRUCTIONS,
            prompt=prompt,
            output_type=_ImplementationPlanDraft,
            metadata={"session_id": session_id, "component": "task_planner"},
        )
        planner_result = normalize_planner_payload(
            response.parsed.model_dump(mode="json"),
            selected_task=selected_task,
            evaluation=evaluation,
            model=response.model,
            response_id=response.response_id,
            raw_output_text=response.output_text,
        )
        return PlannerArtifacts(
            prompt_markdown=render_saved_planner_prompt(selected_task, evaluation, repo_snapshot_text),
            raw_response=response.raw_payload,
            planner_result=planner_result,
        )


def normalize_planner_payload(
    payload: Mapping[str, Any] | BaseModel,
    *,
    selected_task: CandidateTask,
    evaluation: TaskEvaluation,
    model: str,
    response_id: str | None,
    raw_output_text: str | None = None,
) -> PlannerResult:
    source = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else dict(payload)
    warnings: list[str] = []

    title = _clean_text(source.get("title")) or selected_task.title
    short_summary = (
        _clean_text(source.get("short_summary"))
        or _clean_text(source.get("summary"))
        or selected_task.summary
    )
    why_it_is_useful = _clean_text(source.get("why_it_is_useful")) or evaluation.evaluator_summary
    scope = _clean_text(source.get("scope")) or "Implement the selected task using the current repository."
    difficulty = _clean_text(source.get("difficulty")) or _default_difficulty(evaluation)
    estimated_size = _clean_text(source.get("estimated_implementation_size")) or _default_size(evaluation)
    dependencies = _normalize_string_list(source.get("dependencies"), warnings, "dependencies")
    blockers = _normalize_string_list(source.get("blockers"), warnings, "blockers")
    acceptance_criteria = _normalize_string_list(
        source.get("acceptance_criteria"), warnings, "acceptance_criteria"
    )
    risks = _normalize_string_list(source.get("risks"), warnings, "risks")
    edge_cases = _normalize_string_list(source.get("edge_cases"), warnings, "edge_cases")
    implementation_steps = _normalize_string_list(
        source.get("implementation_steps"), warnings, "implementation_steps"
    )
    recommended_files = _normalize_string_list(
        source.get("recommended_files_or_modules")
        or source.get("files_or_modules")
        or source.get("recommended_files"),
        warnings,
        "recommended_files_or_modules",
    )
    confidence = _normalize_score(source.get("confidence"), evaluation.feasibility, warnings, "confidence")
    priority_score = _normalize_score(
        source.get("priority_score"), evaluation.weighted_total_score, warnings, "priority_score"
    )

    if not implementation_steps:
        raise PlannerNormalizationError("Planner output did not include any usable implementation steps.")
    if not acceptance_criteria:
        raise PlannerNormalizationError("Planner output did not include acceptance criteria.")

    return PlannerResult(
        plan=ImplementationPlan(
            task_id=selected_task.candidate_id,
            source_id=selected_task.source_id,
            source_type=selected_task.source_type,
            title=title,
            short_summary=short_summary,
            why_it_is_useful=why_it_is_useful,
            scope=scope,
            difficulty=difficulty,
            estimated_implementation_size=estimated_size,
            dependencies=dependencies,
            blockers=blockers,
            acceptance_criteria=acceptance_criteria,
            risks=risks,
            edge_cases=edge_cases,
            implementation_steps=implementation_steps,
            recommended_files_or_modules=recommended_files,
            confidence=confidence,
            priority_score=priority_score,
        ),
        model=model,
        response_id=response_id,
        prompt_version=PLANNER_PROMPT_VERSION,
        normalization_warnings=warnings,
        raw_output_text=raw_output_text,
    )


def _normalize_string_list(value: Any, warnings: list[str], field_name: str) -> list[str]:
    items: list[str] = []
    if isinstance(value, str):
        normalized = _clean_text(value)
        if normalized:
            warnings.append(f"Normalized {field_name} from a string to a list.")
            items.append(normalized)
        return items
    if isinstance(value, (list, tuple, set)):
        for item in value:
            text = _clean_text(item)
            if text:
                items.append(text)
    return list(dict.fromkeys(items))


def _normalize_score(value: Any, fallback: float, warnings: list[str], field_name: str) -> float:
    if value is None:
        return clamp_score(fallback)
    if isinstance(value, (int, float)):
        normalized = clamp_score(float(value))
        if normalized != float(value):
            warnings.append(f"Clamped {field_name} into the 0-1 range.")
        return normalized
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return clamp_score(fallback)
        try:
            normalized = clamp_score(float(text))
        except ValueError:
            warnings.append(f"Fell back to the selected task score for {field_name}.")
            return clamp_score(fallback)
        if normalized != float(text):
            warnings.append(f"Clamped {field_name} into the 0-1 range.")
        return normalized
    return clamp_score(fallback)


def _default_difficulty(evaluation: TaskEvaluation) -> str:
    if evaluation.implementation_risk >= 0.65 or evaluation.feasibility <= 0.40:
        return "high"
    if evaluation.weighted_total_score >= 0.75 and evaluation.simplicity >= 0.65:
        return "low"
    return "medium"


def _default_size(evaluation: TaskEvaluation) -> str:
    if evaluation.simplicity >= 0.70 and evaluation.implementation_risk <= 0.35:
        return "small"
    if evaluation.implementation_risk >= 0.65 or evaluation.feasibility <= 0.40:
        return "large"
    return "medium"


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split()).strip()
    return ""
