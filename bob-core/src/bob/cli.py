from __future__ import annotations

import sys
from pathlib import Path

import typer

from bob.runtime.llm_config import build_responses_client, resolve_endpoint_config
from bob.runtime.formatters import render_plan_json, render_plan_markdown, render_plan_text, render_session_summary
from bob.runtime.models import BobInput
from bob.runtime.orchestrator import build_default_bob_orchestrator

app = typer.Typer(help="Bob command line interface.")


def _write_or_echo(content: str, output: Path | None) -> None:
    if output is None:
        typer.echo(_safe_terminal_text(content))
        return
    output.write_text(content, encoding="utf-8")
    typer.echo(f"saved_output={output}")


def _safe_terminal_text(content: str) -> str:
    encoding = sys.stdout.encoding or "utf-8"
    return content.encode(encoding, errors="replace").decode(encoding, errors="replace")


def _summarize_probe_message(message: str) -> str:
    compact = " ".join(message.split()).strip()
    if len(compact) <= 200:
        return compact
    return f"{compact[:197]}..."


@app.command("run")
def run(
    repo: str = typer.Option(..., "--repo", help="Repository slug in owner/name format."),
    path: Path = typer.Option(..., "--path", exists=True, file_okay=False, resolve_path=True),
    issue: int | None = typer.Option(None, "--issue", min=1, help="GitHub issue number."),
    task: str | None = typer.Option(None, "--task", help="Free-form task text."),
    candidate_file: Path | None = typer.Option(
        None,
        "--candidate-file",
        exists=True,
        dir_okay=False,
        resolve_path=True,
        help="JSON file containing candidate tasks.",
    ),
    provider: str | None = typer.Option(None, "--provider", help="LLM provider: openai or openai_compatible."),
    base_url: str | None = typer.Option(None, "--base-url", help="OpenAI-compatible base URL."),
    api_key_env: str | None = typer.Option(
        None,
        "--api-key-env",
        help="Environment variable name containing the API key for the configured endpoint.",
    ),
    model: str | None = typer.Option(None, "--model", help="Shared model override for evaluator and planner."),
    evaluator_model: str | None = typer.Option(None, "--evaluator-model", help="Evaluator model override."),
    planner_model: str | None = typer.Option(None, "--planner-model", help="Planner model override."),
) -> None:
    """Run Bob's unified scoring and planning session."""

    orchestrator = build_default_bob_orchestrator(
        provider=provider,
        base_url=base_url,
        api_key_env=api_key_env,
        model=model,
        evaluator_model=evaluator_model,
        planner_model=planner_model,
    )
    session_input = BobInput(
        repo=repo,
        repo_path=str(path),
        issue_number=issue,
        task=task,
        candidate_file=str(candidate_file) if candidate_file else None,
    )
    session = orchestrator.run(session_input)
    typer.echo(_safe_terminal_text(render_session_summary(session)))


@app.command("resume")
def resume(
    session: str = typer.Option(..., "--session", help="Saved Bob session identifier."),
) -> None:
    """Re-display a saved Bob session without changing state."""

    orchestrator = build_default_bob_orchestrator()
    result = orchestrator.resume(session)
    typer.echo(_safe_terminal_text(render_session_summary(result)))


@app.command("show-plan")
def show_plan(
    session: str = typer.Option(..., "--session", help="Saved Bob session identifier."),
    format: str = typer.Option("text", "--format", help="One of: text, json, markdown."),
    output: Path | None = typer.Option(None, "--output", resolve_path=True, help="Optional export path."),
) -> None:
    """Display or export the saved implementation plan for a session."""

    orchestrator = build_default_bob_orchestrator()
    planner_result = orchestrator.load_plan(session)
    normalized_format = format.strip().lower()
    if normalized_format == "text":
        content = render_plan_text(planner_result)
    elif normalized_format == "json":
        content = render_plan_json(planner_result)
    elif normalized_format == "markdown":
        content = render_plan_markdown(planner_result)
    else:
        raise typer.BadParameter("format must be one of: text, json, markdown")
    _write_or_echo(content, output)


@app.command("llm-probe")
def llm_probe(
    provider: str | None = typer.Option(None, "--provider", help="LLM provider: openai or openai_compatible."),
    base_url: str | None = typer.Option(None, "--base-url", help="OpenAI-compatible base URL."),
    api_key_env: str | None = typer.Option(
        None,
        "--api-key-env",
        help="Environment variable name containing the API key for the configured endpoint.",
    ),
    model: str | None = typer.Option(None, "--model", help="Model override for the probe request."),
) -> None:
    """Validate the configured LLM backend with a small structured probe."""

    endpoint = resolve_endpoint_config(
        role="planner",
        provider_override=provider,
        base_url_override=base_url,
        api_key_env_override=api_key_env,
        shared_model_override=model,
    )
    probe = build_responses_client(endpoint).probe()
    lines = [
        f"provider={endpoint.provider.value}",
        f"model={endpoint.model}",
    ]
    if endpoint.base_url:
        lines.append(f"base_url={endpoint.base_url}")
    if endpoint.api_key_env:
        lines.append(f"api_key_env={endpoint.api_key_env}")
    lines.extend(
        [
            f"response_id={probe.response_id or ''}",
            f"message={_summarize_probe_message(probe.parsed.message)}",
        ]
    )
    typer.echo(_safe_terminal_text("\n".join(lines)))
