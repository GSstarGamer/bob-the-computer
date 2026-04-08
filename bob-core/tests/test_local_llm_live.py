from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from openai import OpenAI
from typer.testing import CliRunner

from bob.cli import app
from bob.runtime.llm_config import build_responses_client, resolve_endpoint_config
from bob.runtime.models import CandidateSourceType, CandidateTask, TaskEvaluation
from bob.runtime.planner import TaskPlanner

pytestmark = pytest.mark.local_llm


def _require_env(*names: str) -> None:
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        pytest.skip(f"Missing local LLM live-test configuration: {', '.join(missing)}")


def _run(command: list[str], cwd: Path) -> None:
    completed = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout)


def _make_repo(root: Path) -> Path:
    repo_path = root / "repo"
    repo_path.mkdir(parents=True, exist_ok=True)
    _run(["git", "init"], repo_path)
    _run(["git", "config", "user.email", "bob@example.com"], repo_path)
    _run(["git", "config", "user.name", "Bob"], repo_path)
    (repo_path / "README.md").write_text("# Fixture Repo\n", encoding="utf-8")
    _run(["git", "add", "README.md"], repo_path)
    _run(["git", "commit", "-m", "initial"], repo_path)
    return repo_path


def test_live_llm_probe_roundtrip() -> None:
    _require_env("BOB_LOCAL_LLM_LIVE", "BOB_LLM_BASE_URL")

    endpoint = resolve_endpoint_config(role="planner", provider_override="openai_compatible")
    probe = build_responses_client(endpoint).probe()

    assert probe.parsed.message.strip()


def test_live_planner_roundtrip() -> None:
    _require_env("BOB_LOCAL_LLM_LIVE", "BOB_LLM_BASE_URL")

    endpoint = resolve_endpoint_config(role="planner", provider_override="openai_compatible")
    planner = TaskPlanner(llm_client=build_responses_client(endpoint))
    task = CandidateTask(
        candidate_id="candidate-live",
        source_type=CandidateSourceType.TASK_INPUT,
        source_id="live-task",
        title="Add a live planner smoke test",
        summary="Verify that the remote local model can produce a structured plan.",
        details="Keep the scope limited to a smoke test and persisted output expectations.",
        repo_path="C:/repo",
        repo_root="C:/repo",
    )
    evaluation = TaskEvaluation(
        candidate=task,
        usefulness=0.85,
        simplicity=0.72,
        feasibility=0.80,
        implementation_risk=0.22,
        value=0.82,
        evaluator_summary="This is a compact but realistic local-planner validation task.",
        weighted_total_score=0.80,
        is_viable=True,
    )

    result = planner.create_plan(
        session_id="live-session",
        selected_task=task,
        evaluation=evaluation,
        repo_snapshot_text="repo_root=C:/repo\ntracked_file_count=1\ntracked_files_sample=[README.md]",
    )

    assert result.planner_result.plan.implementation_steps
    assert result.planner_result.plan.acceptance_criteria


def test_live_bob_run_against_local_endpoint(tmp_path: Path) -> None:
    _require_env("BOB_LOCAL_LLM_LIVE", "BOB_LLM_BASE_URL")

    repo_path = _make_repo(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run",
            "--repo",
            "owner/repo",
            "--path",
            str(repo_path),
            "--task",
            "Prepare an implementation plan for a small Bob runtime improvement.",
            "--provider",
            "openai_compatible",
            "--base-url",
            os.environ["BOB_LLM_BASE_URL"],
        ],
    )

    assert result.exit_code == 0, result.output
    assert "status=READY_FOR_EXECUTION" in result.output


def test_live_coder_endpoint_tool_call_smoke() -> None:
    _require_env("BOB_LOCAL_LLM_LIVE", "BOB_CODER_BASE_URL", "BOB_CODER_MODEL")

    client = OpenAI(
        api_key=os.environ.get("BOB_LLM_API_KEY") or "",
        base_url=os.environ["BOB_CODER_BASE_URL"],
        timeout=120.0,
        max_retries=0,
    )
    response = client.chat.completions.create(
        model=os.environ["BOB_CODER_MODEL"],
        messages=[
            {
                "role": "user",
                "content": "Call the noop tool with value set to ready, or explain briefly why you cannot.",
            }
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "noop",
                    "description": "Accepts a ready value for a smoke test.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "value": {"type": "string"},
                        },
                        "required": ["value"],
                    },
                },
            }
        ],
        tool_choice="auto",
    )

    message = response.choices[0].message
    assert message.tool_calls or message.content
