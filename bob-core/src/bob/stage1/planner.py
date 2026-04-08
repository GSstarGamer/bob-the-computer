from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from bob.llm import OpenAIResponsesClient
from bob.stage1.models import Plan, PlanAssumption, PlannerResult, PlanRisk, PlanStage, TaskBrief
from bob.stage1.prompts import PLANNER_INSTRUCTIONS, PLANNER_PROMPT_VERSION, build_planner_prompt, render_saved_prompt


class PlannerError(RuntimeError):
    """Raised when Bob cannot create a usable plan."""


class PlannerNormalizationError(PlannerError):
    """Raised when planner output cannot be normalized into a usable plan."""


class _PlanAssumptionDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str | None = None
    rationale: str | None = None


class _PlanRiskDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str | None = None
    mitigation: str | None = None


class _PlanStageDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    goal: str | None = None
    files_or_modules: list[str] = Field(default_factory=list)
    expected_output: str | None = None


class _PlannerDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_summary: str | None = None
    constraints: list[str] = Field(default_factory=list)
    assumptions: list[_PlanAssumptionDraft] = Field(default_factory=list)
    risks: list[_PlanRiskDraft] = Field(default_factory=list)
    stages: list[_PlanStageDraft] = Field(default_factory=list)
    approval_notes: str | None = None


@dataclass(frozen=True)
class PlannerArtifacts:
    prompt_markdown: str
    raw_response: dict[str, Any]
    planner_result: PlannerResult


class Stage1Planner:
    def __init__(self, *, llm_client: OpenAIResponsesClient) -> None:
        self.llm_client = llm_client

    def create_plan(self, *, run_id: str, task_brief: TaskBrief, repo_snapshot_text: str) -> PlannerArtifacts:
        prompt = build_planner_prompt(task_brief, repo_snapshot_text)
        response = self.llm_client.parse_structured_output(
            instructions=PLANNER_INSTRUCTIONS,
            prompt=prompt,
            output_type=_PlannerDraft,
            metadata={"run_id": run_id, "component": "planner"},
        )
        planner_result = normalize_planner_payload(
            response.parsed.model_dump(mode="json"),
            fallback_task_summary=task_brief.summary.strip() or task_brief.title,
            model=response.model,
            response_id=response.response_id,
            raw_output_text=response.output_text,
        )
        return PlannerArtifacts(
            prompt_markdown=render_saved_prompt(task_brief, repo_snapshot_text),
            raw_response=response.raw_payload,
            planner_result=planner_result,
        )


def normalize_planner_payload(
    payload: Mapping[str, Any] | BaseModel,
    *,
    fallback_task_summary: str,
    model: str,
    response_id: str | None,
    raw_output_text: str | None = None,
) -> PlannerResult:
    source = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else dict(payload)
    warnings: list[str] = []

    task_summary = _clean_text(source.get("task_summary")) or fallback_task_summary
    if not task_summary:
        raise PlannerNormalizationError("Planner output did not include a usable task summary.")

    constraints = _normalize_string_list(source.get("constraints"), warnings, "constraints")
    assumptions = _normalize_assumptions(source.get("assumptions"), warnings)
    risks = _normalize_risks(source.get("risks"), warnings)
    stages = _normalize_stages(source.get("stages"), warnings)
    approval_notes = _clean_text(source.get("approval_notes")) or (
        "Review the proposed stages, assumptions, and risks before approving execution."
    )

    if not stages:
        raise PlannerNormalizationError("Planner output did not include any usable stages.")

    return PlannerResult(
        plan=Plan(
            task_summary=task_summary,
            constraints=constraints,
            assumptions=assumptions,
            risks=risks,
            stages=stages,
            approval_notes=approval_notes,
        ),
        model=model,
        response_id=response_id,
        prompt_version=PLANNER_PROMPT_VERSION,
        normalization_warnings=warnings,
        raw_output_text=raw_output_text,
    )


def _normalize_assumptions(value: Any, warnings: list[str]) -> list[PlanAssumption]:
    assumptions: list[PlanAssumption] = []
    for item in _coerce_list(value):
        if isinstance(item, str):
            text = _clean_text(item)
            if text:
                warnings.append("Normalized a string assumption into an object.")
                assumptions.append(PlanAssumption(text=text))
            continue
        if not isinstance(item, Mapping):
            continue
        text = _clean_text(item.get("text")) or _clean_text(item.get("summary"))
        rationale = _clean_text(item.get("rationale")) or _clean_text(item.get("reason"))
        if text:
            assumptions.append(PlanAssumption(text=text, rationale=rationale))
    return assumptions


def _normalize_risks(value: Any, warnings: list[str]) -> list[PlanRisk]:
    risks: list[PlanRisk] = []
    for item in _coerce_list(value):
        if isinstance(item, str):
            text = _clean_text(item)
            if text:
                warnings.append("Normalized a string risk into an object.")
                risks.append(PlanRisk(text=text))
            continue
        if not isinstance(item, Mapping):
            continue
        text = _clean_text(item.get("text")) or _clean_text(item.get("summary"))
        mitigation = _clean_text(item.get("mitigation")) or _clean_text(item.get("response"))
        if text:
            risks.append(PlanRisk(text=text, mitigation=mitigation))
    return risks


def _normalize_stages(value: Any, warnings: list[str]) -> list[PlanStage]:
    stages: list[PlanStage] = []
    for index, item in enumerate(_coerce_list(value), start=1):
        if isinstance(item, str):
            goal = _clean_text(item)
            if not goal:
                continue
            warnings.append("Normalized a string stage into an object.")
            stages.append(
                PlanStage(
                    name=f"Stage {index}",
                    goal=goal,
                    files_or_modules=[],
                    expected_output="A completed implementation step aligned with the stated goal.",
                )
            )
            continue
        if not isinstance(item, Mapping):
            continue

        name = _clean_text(item.get("name")) or f"Stage {index}"
        goal = _clean_text(item.get("goal")) or _clean_text(item.get("summary")) or _clean_text(item.get("objective"))
        expected_output = _clean_text(item.get("expected_output")) or _clean_text(item.get("output"))
        files_or_modules = _normalize_string_list(
            item.get("files_or_modules") or item.get("files") or item.get("modules"),
            warnings,
            f"stages[{index}].files_or_modules",
        )
        if not goal or not expected_output:
            continue
        stages.append(
            PlanStage(
                name=name,
                goal=goal,
                files_or_modules=files_or_modules,
                expected_output=expected_output,
            )
        )
    return stages


def _normalize_string_list(value: Any, warnings: list[str], field_name: str) -> list[str]:
    items: list[str] = []
    if isinstance(value, str):
        normalized = _clean_text(value)
        if normalized:
            warnings.append(f"Normalized {field_name} from a string to a list.")
            items.append(normalized)
        return items
    for item in _coerce_list(value):
        text = _clean_text(item)
        if text:
            items.append(text)
    return list(dict.fromkeys(items))


def _coerce_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    return [value]


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split()).strip()
    return ""
