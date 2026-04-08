from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from bob.llm import OpenAIResponsesClient
from bob.runtime.models import CandidateTask, TaskEvaluation, clamp_score
from bob.runtime.prompts import EVALUATOR_INSTRUCTIONS, build_evaluator_prompt


class TaskEvaluationError(RuntimeError):
    """Raised when Bob cannot produce usable task evaluations."""


class EvaluationNormalizationError(TaskEvaluationError):
    """Raised when evaluation output cannot be normalized into usable scores."""


class _CandidateEvaluationDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    candidate_id: str | None = None
    title: str | None = None
    summary: str | None = None
    details: str | None = None
    usefulness: float | None = None
    simplicity: float | None = None
    feasibility: float | None = None
    implementation_risk: float | None = None
    value: float | None = None
    evaluator_summary: str | None = None
    blockers: list[str] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)


class _EvaluationBatchDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    evaluations: list[_CandidateEvaluationDraft] = Field(default_factory=list)


@dataclass(frozen=True)
class EvaluatorArtifacts:
    evaluations: list[TaskEvaluation]
    model: str
    response_id: str | None
    raw_output_text: str | None
    raw_response: dict[str, Any]


class TaskEvaluator:
    def __init__(self, *, llm_client: OpenAIResponsesClient) -> None:
        self.llm_client = llm_client

    def evaluate_candidates(
        self,
        *,
        session_id: str,
        candidates: list[CandidateTask],
        repo_snapshot_text: str,
    ) -> EvaluatorArtifacts:
        prompt = build_evaluator_prompt(candidates, repo_snapshot_text)
        response = self.llm_client.parse_structured_output(
            instructions=EVALUATOR_INSTRUCTIONS,
            prompt=prompt,
            output_type=_EvaluationBatchDraft,
            metadata={"session_id": session_id, "component": "task_evaluator"},
        )
        evaluations = normalize_evaluation_payload(
            response.parsed.model_dump(mode="json"),
            candidates=candidates,
        )
        return EvaluatorArtifacts(
            evaluations=evaluations,
            model=response.model,
            response_id=response.response_id,
            raw_output_text=response.output_text,
            raw_response=response.raw_payload,
        )


def normalize_evaluation_payload(
    payload: Mapping[str, Any] | BaseModel,
    *,
    candidates: list[CandidateTask],
) -> list[TaskEvaluation]:
    source = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else dict(payload)
    raw_evaluations = source.get("evaluations")
    if isinstance(raw_evaluations, dict):
        raw_evaluations = raw_evaluations.get("items")
    if not isinstance(raw_evaluations, list):
        raise EvaluationNormalizationError("Evaluator output did not include an evaluations list.")

    candidate_map = {candidate.candidate_id: candidate for candidate in candidates}
    evaluations_by_id: dict[str, TaskEvaluation] = {}

    for index, raw_item in enumerate(raw_evaluations, start=1):
        if not isinstance(raw_item, Mapping):
            continue
        warnings: list[str] = []
        candidate = _resolve_candidate(raw_item, candidates, candidate_map, index)
        normalized_candidate = candidate.model_copy(
            update={
                "title": _clean_text(raw_item.get("title")) or candidate.title,
                "summary": _clean_text(raw_item.get("summary")) or candidate.summary,
                "details": _clean_text(raw_item.get("details")) or candidate.details,
            }
        )
        evaluations_by_id[normalized_candidate.candidate_id] = TaskEvaluation(
            candidate=normalized_candidate,
            usefulness=_normalize_score(raw_item.get("usefulness"), warnings, "usefulness"),
            simplicity=_normalize_score(raw_item.get("simplicity"), warnings, "simplicity"),
            feasibility=_normalize_score(raw_item.get("feasibility"), warnings, "feasibility"),
            implementation_risk=_normalize_score(
                raw_item.get("implementation_risk"), warnings, "implementation_risk"
            ),
            value=_normalize_score(raw_item.get("value"), warnings, "value"),
            evaluator_summary=(
                _clean_text(raw_item.get("evaluator_summary"))
                or _clean_text(raw_item.get("summary"))
                or normalized_candidate.summary
            ),
            blockers=_normalize_string_list(raw_item.get("blockers")),
            rejection_reasons=_normalize_string_list(raw_item.get("rejection_reasons")),
            normalization_warnings=warnings,
        )

    if not evaluations_by_id:
        raise EvaluationNormalizationError("Evaluator output did not contain any usable candidate evaluations.")

    for candidate in candidates:
        if candidate.candidate_id in evaluations_by_id:
            continue
        evaluations_by_id[candidate.candidate_id] = TaskEvaluation(
            candidate=candidate,
            usefulness=0.0,
            simplicity=0.0,
            feasibility=0.0,
            implementation_risk=1.0,
            value=0.0,
            evaluator_summary="The evaluator omitted this candidate from the structured response.",
            rejection_reasons=["The evaluator omitted this candidate from the structured response."],
            normalization_warnings=["Synthesized a fallback evaluation because the model omitted this candidate."],
        )

    return [evaluations_by_id[candidate.candidate_id] for candidate in candidates]


def _resolve_candidate(
    raw_item: Mapping[str, Any],
    candidates: list[CandidateTask],
    candidate_map: dict[str, CandidateTask],
    index: int,
) -> CandidateTask:
    candidate_id = _clean_text(raw_item.get("candidate_id"))
    if candidate_id and candidate_id in candidate_map:
        return candidate_map[candidate_id]
    if 1 <= index <= len(candidates):
        return candidates[index - 1]
    raise EvaluationNormalizationError("Evaluator output referenced an unknown candidate.")


def _normalize_score(value: Any, warnings: list[str], field_name: str) -> float:
    if isinstance(value, bool):
        warnings.append(f"Normalized boolean {field_name} to a numeric score.")
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        normalized = clamp_score(float(value))
        if normalized != float(value):
            warnings.append(f"Clamped {field_name} into the 0-1 range.")
        return normalized
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0.0
        try:
            normalized = clamp_score(float(text))
        except ValueError:
            warnings.append(f"Normalized non-numeric {field_name} to 0.0.")
            return 0.0
        if normalized != float(text):
            warnings.append(f"Clamped {field_name} into the 0-1 range.")
        return normalized
    return 0.0


def _normalize_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = _clean_text(value)
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        items = [_clean_text(item) for item in value]
        return [item for item in items if item]
    return []


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split()).strip()
    return ""
