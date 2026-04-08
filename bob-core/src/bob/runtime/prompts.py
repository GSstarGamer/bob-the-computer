from __future__ import annotations

from bob.runtime.models import CandidateTask, TaskEvaluation

EVALUATOR_PROMPT_VERSION = "runtime/evaluator/v1"
PLANNER_PROMPT_VERSION = "runtime/planner/v1"

EVALUATOR_INSTRUCTIONS = """
You are Bob the Computer's internal task evaluator.

Score each candidate task against the current repository snapshot.
Return practical normalized task summaries and 0-1 scores for:
- usefulness
- simplicity
- feasibility
- implementation_risk
- value

Rules:
- Be skeptical of vague or risky work.
- Prefer bounded, high-value tasks that fit the current repository.
- Do not invent files or hidden context.
- If a task looks unsafe or under-specified, reflect that in feasibility and risk.
""".strip()

PLANNER_INSTRUCTIONS = """
You are Bob the Computer's internal planning agent.

Turn the selected task into a concrete implementation plan that another runtime step can execute later.
Ground the plan in the provided task details, evaluation summary, and repository snapshot.

Rules:
- Keep the plan implementation-focused and file-aware.
- Be explicit about dependencies, blockers, risks, and edge cases.
- Do not assume code exists unless it appears in the provided context.
- Return a plan that is immediately useful for a later coding step.
""".strip()


def build_evaluator_prompt(candidates: list[CandidateTask], repo_snapshot_text: str) -> str:
    return "\n\n".join(
        [
            "Candidate tasks:",
            "\n".join(candidate.model_dump_json(indent=2) for candidate in candidates),
            "Repository snapshot:",
            repo_snapshot_text,
            "Return structured task evaluations for every candidate.",
        ]
    )


def build_planner_prompt(
    candidate: CandidateTask,
    evaluation: TaskEvaluation,
    repo_snapshot_text: str,
) -> str:
    return "\n\n".join(
        [
            "Selected candidate:",
            candidate.model_dump_json(indent=2),
            "Task evaluation:",
            evaluation.model_dump_json(indent=2),
            "Repository snapshot:",
            repo_snapshot_text,
            "Return a structured implementation plan for the selected candidate.",
        ]
    )


def render_saved_planner_prompt(
    candidate: CandidateTask,
    evaluation: TaskEvaluation,
    repo_snapshot_text: str,
) -> str:
    return "\n".join(
        [
            f"# Planner Prompt ({PLANNER_PROMPT_VERSION})",
            "",
            "## Instructions",
            PLANNER_INSTRUCTIONS,
            "",
            "## Selected Candidate",
            "```json",
            candidate.model_dump_json(indent=2),
            "```",
            "",
            "## Task Evaluation",
            "```json",
            evaluation.model_dump_json(indent=2),
            "```",
            "",
            "## Repository Snapshot",
            "```text",
            repo_snapshot_text,
            "```",
        ]
    )
