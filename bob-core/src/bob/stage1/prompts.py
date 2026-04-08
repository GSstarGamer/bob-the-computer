from __future__ import annotations

from bob.stage1.models import TaskBrief

PLANNER_PROMPT_VERSION = "stage2/v1"

PLANNER_INSTRUCTIONS = """
You are Bob the Computer's planning agent for a staged engineering workflow.

Produce a practical implementation plan for the current repository task.
The plan must be sequential, implementation-focused, and grounded only in the provided task brief and repository snapshot.

Rules:
- Do not assume code exists unless it appears in the provided context.
- Be explicit about uncertainty instead of inventing details.
- Keep each stage scoped, file-aware, and realistic for an engineer to execute later.
- Call out meaningful constraints, assumptions, and risks.
- Make the approval notes explain why this is the right next step before coding begins.
""".strip()


def build_planner_prompt(task_brief: TaskBrief, repo_snapshot_text: str) -> str:
    return "\n\n".join(
        [
            "Task brief:",
            task_brief.model_dump_json(indent=2),
            "Repository snapshot:",
            repo_snapshot_text,
            "Return a structured implementation plan for the next stage.",
        ]
    )


def render_saved_prompt(task_brief: TaskBrief, repo_snapshot_text: str) -> str:
    return "\n".join(
        [
            f"# Planner Prompt ({PLANNER_PROMPT_VERSION})",
            "",
            "## Instructions",
            PLANNER_INSTRUCTIONS,
            "",
            "## Task Brief",
            "```json",
            task_brief.model_dump_json(indent=2),
            "```",
            "",
            "## Repository Snapshot",
            "```text",
            repo_snapshot_text,
            "```",
        ]
    )
