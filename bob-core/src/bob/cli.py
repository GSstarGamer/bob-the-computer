from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from bob.stage1.models import PlannerResult, Stage1Input, Stage1Status
from bob.stage1.orchestrator import build_default_stage1_orchestrator

app = typer.Typer(help="Bob command line interface.")
stage1_app = typer.Typer(help="Stage 1 planner and approval flow.")
app.add_typer(stage1_app, name="stage1")


def _print_status(run_id: str, status: str, run_dir: Path, model: str) -> None:
    typer.echo(f"run_id={run_id}")
    typer.echo(f"status={status}")
    typer.echo(f"run_dir={run_dir}")
    typer.echo(f"model={model}")


def _render_next_command(run_id: str, status: Stage1Status) -> str:
    if status == Stage1Status.AWAITING_PLAN_APPROVAL:
        return f"bob stage1 resume --run {run_id} --approve-plan"
    if status == Stage1Status.PLAN_APPROVED:
        return "Plan approved and saved. Stage 3 execution is not implemented yet."
    return f"bob stage1 resume --run {run_id}"


def _print_planner_result(run_id: str, status: Stage1Status, planner_result: PlannerResult) -> None:
    plan = planner_result.plan
    typer.echo("")
    typer.echo("Plan Summary")
    typer.echo(plan.task_summary)
    typer.echo("")
    typer.echo(f"approval_required={'yes' if status == Stage1Status.AWAITING_PLAN_APPROVAL else 'no'}")
    typer.echo("")
    typer.echo("Constraints")
    _print_list(plan.constraints)
    typer.echo("")
    typer.echo("Assumptions")
    if not plan.assumptions:
        typer.echo("- None")
    else:
        for assumption in plan.assumptions:
            line = assumption.text
            if assumption.rationale:
                line = f"{line} ({assumption.rationale})"
            typer.echo(f"- {line}")
    typer.echo("")
    typer.echo("Risks")
    if not plan.risks:
        typer.echo("- None")
    else:
        for risk in plan.risks:
            line = risk.text
            if risk.mitigation:
                line = f"{line} (mitigation: {risk.mitigation})"
            typer.echo(f"- {line}")
    typer.echo("")
    typer.echo("Stages")
    for index, stage in enumerate(plan.stages, start=1):
        typer.echo(f"{index}. {stage.name}")
        typer.echo(f"   goal: {stage.goal}")
        typer.echo(f"   files/modules: {', '.join(stage.files_or_modules) if stage.files_or_modules else 'none specified'}")
        typer.echo(f"   expected output: {stage.expected_output}")
    typer.echo("")
    typer.echo("Approval Notes")
    typer.echo(plan.approval_notes)
    if planner_result.normalization_warnings:
        typer.echo("")
        typer.echo("Normalization Warnings")
        _print_list(planner_result.normalization_warnings)
    typer.echo("")
    typer.echo(f"next_command={_render_next_command(run_id, status)}")


def _print_list(items: list[str]) -> None:
    if not items:
        typer.echo("- None")
        return
    for item in items:
        typer.echo(f"- {item}")


@stage1_app.command("run")
def stage1_run(
    repo: str = typer.Option(..., "--repo", help="Repository slug in owner/name format."),
    path: Path = typer.Option(..., "--path", exists=True, file_okay=False, resolve_path=True),
    issue: Optional[int] = typer.Option(None, "--issue", min=1, help="GitHub issue number."),
    task: Optional[str] = typer.Option(None, "--task", help="Free-form task text."),
) -> None:
    """Start a Stage 1 planner run and wait for plan approval."""

    orchestrator = build_default_stage1_orchestrator()
    stage1_input = Stage1Input(repo=repo, repo_path=str(path), issue_number=issue, task=task)
    ledger, run_dir = orchestrator.start(stage1_input)
    planner_result = orchestrator.load_planner_result(ledger.run_id)
    _print_status(ledger.run_id, ledger.status.value, run_dir, planner_result.model)
    _print_planner_result(ledger.run_id, ledger.status, planner_result)


@stage1_app.command("resume")
def stage1_resume(
    run: str = typer.Option(..., "--run", help="Run identifier to resume."),
    approve_plan: bool = typer.Option(
        False,
        "--approve-plan",
        help="Approve the saved plan so later stages can execute it.",
    ),
) -> None:
    """Review or approve the saved Stage 1 plan."""

    orchestrator = build_default_stage1_orchestrator()
    ledger, run_dir = orchestrator.resume(run_id=run, approve_plan=approve_plan)
    planner_result = orchestrator.load_planner_result(ledger.run_id)
    _print_status(ledger.run_id, ledger.status.value, run_dir, planner_result.model)
    _print_planner_result(ledger.run_id, ledger.status, planner_result)
