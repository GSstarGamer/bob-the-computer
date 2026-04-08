from __future__ import annotations

from bob.runtime.models import PlannerResult, SessionResult, SessionStatus, TaskEvaluation


def render_session_summary(session: SessionResult) -> str:
    lines = [
        f"session_id={session.session_id}",
        f"status={session.status.value}",
        f"session_dir={session.session_dir}",
    ]
    if session.model:
        lines.append(f"model={session.model}")
    lines.append(f"ready_for_execution={'yes' if session.status == SessionStatus.READY_FOR_EXECUTION else 'no'}")
    if session.selected_task is not None:
        lines.append(f"selected_candidate={session.selected_task.candidate_id}")
        lines.append(f"selected_title={session.selected_task.title}")
    if session.selection_result is not None:
        lines.append(f"selection_reason={session.selection_result.selection_reason}")
    lines.extend(["", "Candidate Scores"])
    if not session.evaluations:
        lines.append("- None")
    else:
        for evaluation in _sorted_evaluations(session.evaluations):
            status = "viable" if evaluation.is_viable else "rejected"
            lines.append(
                (
                    f"- {evaluation.candidate.candidate_id}: total={evaluation.weighted_total_score:.2f}, "
                    f"usefulness={evaluation.usefulness:.2f}, feasibility={evaluation.feasibility:.2f}, "
                    f"risk={evaluation.implementation_risk:.2f}, status={status}"
                )
            )
            if evaluation.rejection_reasons:
                lines.append(f"  reasons: {'; '.join(evaluation.rejection_reasons)}")
    return "\n".join(lines)


def render_plan_text(planner_result: PlannerResult) -> str:
    plan = planner_result.plan
    lines = [
        "Implementation Plan",
        plan.title,
        "",
        f"task_id={plan.task_id}",
        f"source_id={plan.source_id}",
        f"difficulty={plan.difficulty}",
        f"estimated_size={plan.estimated_implementation_size}",
        f"confidence={plan.confidence:.2f}",
        f"priority_score={plan.priority_score:.2f}",
        "",
        "Summary",
        plan.short_summary,
        "",
        "Why It Matters",
        plan.why_it_is_useful,
        "",
        "Scope",
        plan.scope,
        "",
        "Dependencies",
        _render_bullet_block(plan.dependencies),
        "",
        "Blockers",
        _render_bullet_block(plan.blockers),
        "",
        "Acceptance Criteria",
        _render_numbered_block(plan.acceptance_criteria),
        "",
        "Risks",
        _render_bullet_block(plan.risks),
        "",
        "Edge Cases",
        _render_bullet_block(plan.edge_cases),
        "",
        "Implementation Steps",
        _render_numbered_block(plan.implementation_steps),
        "",
        "Recommended Files Or Modules",
        _render_bullet_block(plan.recommended_files_or_modules),
    ]
    if planner_result.normalization_warnings:
        lines.extend(["", "Normalization Warnings", _render_bullet_block(planner_result.normalization_warnings)])
    return "\n".join(lines)


def render_plan_markdown(planner_result: PlannerResult) -> str:
    plan = planner_result.plan
    lines = [
        f"# Implementation Plan: {plan.title}",
        "",
        f"- Task ID: `{plan.task_id}`",
        f"- Source ID: `{plan.source_id}`",
        f"- Difficulty: `{plan.difficulty}`",
        f"- Estimated Size: `{plan.estimated_implementation_size}`",
        f"- Confidence: `{plan.confidence:.2f}`",
        f"- Priority Score: `{plan.priority_score:.2f}`",
        "",
        "## Summary",
        plan.short_summary,
        "",
        "## Why It Matters",
        plan.why_it_is_useful,
        "",
        "## Scope",
        plan.scope,
        "",
        "## Dependencies",
        _render_markdown_bullets(plan.dependencies),
        "",
        "## Blockers",
        _render_markdown_bullets(plan.blockers),
        "",
        "## Acceptance Criteria",
        _render_markdown_numbers(plan.acceptance_criteria),
        "",
        "## Risks",
        _render_markdown_bullets(plan.risks),
        "",
        "## Edge Cases",
        _render_markdown_bullets(plan.edge_cases),
        "",
        "## Implementation Steps",
        _render_markdown_numbers(plan.implementation_steps),
        "",
        "## Recommended Files Or Modules",
        _render_markdown_bullets(plan.recommended_files_or_modules),
    ]
    if planner_result.normalization_warnings:
        lines.extend(["", "## Normalization Warnings", _render_markdown_bullets(planner_result.normalization_warnings)])
    return "\n".join(lines)


def render_plan_json(planner_result: PlannerResult) -> str:
    return planner_result.model_dump_json(indent=2)


def _sorted_evaluations(evaluations: list[TaskEvaluation]) -> list[TaskEvaluation]:
    return sorted(
        evaluations,
        key=lambda item: (
            -item.weighted_total_score,
            -item.usefulness,
            -item.feasibility,
            item.candidate.candidate_id,
        ),
    )


def _render_bullet_block(items: list[str]) -> str:
    if not items:
        return "- None"
    return "\n".join(f"- {item}" for item in items)


def _render_numbered_block(items: list[str]) -> str:
    if not items:
        return "1. None"
    return "\n".join(f"{index}. {item}" for index, item in enumerate(items, start=1))


def _render_markdown_bullets(items: list[str]) -> str:
    return _render_bullet_block(items)


def _render_markdown_numbers(items: list[str]) -> str:
    return _render_numbered_block(items)
