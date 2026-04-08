from __future__ import annotations

from dataclasses import dataclass

from bob.runtime.models import (
    SelectionResult,
    SelectionThresholds,
    SelectionWeights,
    TaskEvaluation,
    clamp_score,
)

DEFAULT_SELECTION_WEIGHTS = SelectionWeights()
DEFAULT_SELECTION_THRESHOLDS = SelectionThresholds()


@dataclass(frozen=True)
class SelectionOutcome:
    evaluations: list[TaskEvaluation]
    selection_result: SelectionResult


def compute_weighted_total(
    evaluation: TaskEvaluation,
    weights: SelectionWeights = DEFAULT_SELECTION_WEIGHTS,
) -> float:
    inverse_risk = 1.0 - evaluation.implementation_risk
    return clamp_score(
        (evaluation.usefulness * weights.usefulness)
        + (evaluation.feasibility * weights.feasibility)
        + (evaluation.simplicity * weights.simplicity)
        + (evaluation.value * weights.value)
        + (inverse_risk * weights.inverse_risk)
    )


def select_best_candidate(
    evaluations: list[TaskEvaluation],
    *,
    weights: SelectionWeights = DEFAULT_SELECTION_WEIGHTS,
    thresholds: SelectionThresholds = DEFAULT_SELECTION_THRESHOLDS,
) -> SelectionOutcome:
    updated: list[TaskEvaluation] = []
    for evaluation in evaluations:
        rejection_reasons = list(dict.fromkeys(evaluation.rejection_reasons))
        weighted_total = compute_weighted_total(evaluation, weights)
        if evaluation.usefulness < thresholds.minimum_usefulness:
            rejection_reasons.append(
                f"Usefulness score {evaluation.usefulness:.2f} is below {thresholds.minimum_usefulness:.2f}."
            )
        if evaluation.feasibility < thresholds.minimum_feasibility:
            rejection_reasons.append(
                f"Feasibility score {evaluation.feasibility:.2f} is below {thresholds.minimum_feasibility:.2f}."
            )
        if evaluation.implementation_risk > thresholds.maximum_implementation_risk:
            rejection_reasons.append(
                f"Implementation risk {evaluation.implementation_risk:.2f} exceeds {thresholds.maximum_implementation_risk:.2f}."
            )
        if weighted_total < thresholds.minimum_weighted_total:
            rejection_reasons.append(
                f"Weighted total {weighted_total:.2f} is below {thresholds.minimum_weighted_total:.2f}."
            )
        updated.append(
            evaluation.model_copy(
                update={
                    "weighted_total_score": weighted_total,
                    "is_viable": not rejection_reasons,
                    "rejection_reasons": rejection_reasons,
                }
            )
        )

    ranked = sorted(
        updated,
        key=lambda item: (
            -item.weighted_total_score,
            -item.usefulness,
            -item.feasibility,
            item.candidate.candidate_id,
        ),
    )
    selected = next((item for item in ranked if item.is_viable), None)
    if selected is None:
        selection_result = SelectionResult(
            selected_candidate_id=None,
            selected_task=None,
            ranked_candidate_ids=[item.candidate.candidate_id for item in ranked],
            rejected_candidate_ids=[item.candidate.candidate_id for item in ranked],
            selection_reason="No candidate met the current viability thresholds.",
            weights=weights,
            thresholds=thresholds,
        )
    else:
        selection_result = SelectionResult(
            selected_candidate_id=selected.candidate.candidate_id,
            selected_task=selected.candidate,
            ranked_candidate_ids=[item.candidate.candidate_id for item in ranked],
            rejected_candidate_ids=[item.candidate.candidate_id for item in ranked if not item.is_viable],
            selection_reason=(
                f"Selected {selected.candidate.candidate_id} with weighted score "
                f"{selected.weighted_total_score:.2f}."
            ),
            weights=weights,
            thresholds=thresholds,
        )
    return SelectionOutcome(evaluations=updated, selection_result=selection_result)
