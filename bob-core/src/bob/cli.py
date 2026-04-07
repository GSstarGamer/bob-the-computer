from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from bob.stage1.models import Stage1Input
from bob.stage1.orchestrator import build_default_stage1_orchestrator

app = typer.Typer(help="Bob command line interface.")
stage1_app = typer.Typer(help="Stage 1 issue-to-summary pipeline.")
app.add_typer(stage1_app, name="stage1")


def _print_status(run_id: str, status: str, run_dir: Path) -> None:
    typer.echo(f"run_id={run_id}")
    typer.echo(f"status={status}")
    typer.echo(f"run_dir={run_dir}")


@stage1_app.command("run")
def stage1_run(
    repo: str = typer.Option(..., "--repo", help="Repository slug in owner/name format."),
    path: Path = typer.Option(..., "--path", exists=True, file_okay=False, resolve_path=True),
    issue: Optional[int] = typer.Option(None, "--issue", min=1, help="GitHub issue number."),
    task: Optional[str] = typer.Option(None, "--task", help="Free-form task text."),
) -> None:
    """Start a Stage 1 run and stop before any write action."""

    orchestrator = build_default_stage1_orchestrator()
    stage1_input = Stage1Input(repo=repo, repo_path=str(path), issue_number=issue, task=task)
    ledger, run_dir = orchestrator.start(stage1_input)
    _print_status(ledger.run_id, ledger.status.value, run_dir)


@stage1_app.command("resume")
def stage1_resume(
    run: str = typer.Option(..., "--run", help="Run identifier to resume."),
    approve_write: bool = typer.Option(
        False,
        "--approve-write",
        help="Required explicit approval before Codex is allowed to modify files.",
    ),
) -> None:
    """Resume a Stage 1 run after explicit write approval."""

    orchestrator = build_default_stage1_orchestrator()
    ledger, run_dir = orchestrator.resume(run_id=run, approve_write=approve_write)
    _print_status(ledger.run_id, ledger.status.value, run_dir)
