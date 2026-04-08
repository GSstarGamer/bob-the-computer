from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError, BadRequestError, RateLimitError
from pydantic import BaseModel
from typer.testing import CliRunner

from bob.cli import app
from bob.llm.openai_client import OpenAIConfigurationError, OpenAIRequestError, OpenAIResponsesClient
from bob.stage1.models import (
    IssueComment,
    IssueContext,
    PlannerResult,
    RunLedger,
    Stage1Input,
    Stage1Status,
    TaskBrief,
)
from bob.stage1.orchestrator import Stage1Orchestrator
from bob.stage1.planner import PlannerArtifacts, PlannerNormalizationError, normalize_planner_payload
from bob.stage1.storage import RunStore


class FakeGitHubReader:
    def get_issue(self, repo: str, issue_number: int) -> IssueContext:
        return IssueContext(
            number=issue_number,
            title="Planner issue title",
            body="Issue-backed planning body.",
            url=f"https://example.com/{repo}/issues/{issue_number}",
            state="open",
            author="alice",
            labels=["planner"],
        )

    def get_issue_comments(self, repo: str, issue_number: int) -> list[IssueComment]:
        return [
            IssueComment(
                author="bob",
                body="Issue comment context.",
                created_at="2026-04-07T12:00:00Z",
                url=f"https://example.com/{repo}/issues/{issue_number}#comment",
            )
        ]


class FakePlanner:
    def __init__(self) -> None:
        self.llm_client = SimpleNamespace(model="gpt-5.4-mini", timeout_seconds=60, max_retries=3)

    def create_plan(self, *, run_id: str, task_brief: TaskBrief, repo_snapshot_text: str) -> PlannerArtifacts:
        planner_result = normalize_planner_payload(
            {
                "task_summary": task_brief.summary or task_brief.title,
                "constraints": ["Keep the work scoped to the touched files."],
                "assumptions": [{"text": "The repo snapshot is current.", "rationale": "The checkout is clean."}],
                "risks": [{"text": "The file list may broaden after inspection.", "mitigation": "Re-check the repo before coding."}],
                "stages": [
                    {
                        "name": "Inspect current implementation",
                        "goal": "Confirm the exact edit surface before coding begins.",
                        "files_or_modules": ["README.md"],
                        "expected_output": "A confirmed implementation target.",
                    },
                    {
                        "name": "Prepare execution packet",
                        "goal": "Hand off the approved work as a bounded execution step.",
                        "files_or_modules": ["README.md"],
                        "expected_output": "A saved approved plan ready for Stage 3.",
                    },
                ],
                "approval_notes": "Approve this plan before any coding begins.",
            },
            fallback_task_summary=task_brief.summary or task_brief.title,
            model=self.llm_client.model,
            response_id=f"resp_{run_id}",
            raw_output_text='{"ok":true}',
        )
        return PlannerArtifacts(
            prompt_markdown="# Planner Prompt\n\nFixture prompt.\n",
            raw_response={"id": planner_result.response_id, "model": planner_result.model, "repo_snapshot": repo_snapshot_text},
            planner_result=planner_result,
        )


class ExamplePlan(BaseModel):
    title: str


class FakeParsedResponse:
    def __init__(self, parsed: BaseModel, response_id: str = "resp_test") -> None:
        self.id = response_id
        self.model = "gpt-5.4-mini"
        self.output_parsed = parsed
        self.output_text = parsed.model_dump_json()

    def model_dump(self, mode: str = "json") -> dict:
        return {
            "id": self.id,
            "model": self.model,
            "output_text": self.output_text,
            "output_parsed": self.output_parsed.model_dump(mode=mode),
        }


class FakeResponsesEndpoint:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    def parse(self, **kwargs):
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make_repo(root: Path) -> Path:
    repo_path = root / "repo"
    repo_path.mkdir(parents=True, exist_ok=True)
    run(["git", "init"], repo_path)
    run(["git", "config", "user.email", "bob@example.com"], repo_path)
    run(["git", "config", "user.name", "Bob"], repo_path)
    (repo_path / "README.md").write_text("# Fixture Repo\n", encoding="utf-8")
    run(["git", "add", "README.md"], repo_path)
    run(["git", "commit", "-m", "initial"], repo_path)
    return repo_path


def run(command: list[str], cwd: Path) -> None:
    completed = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout)


def build_orchestrator(tmp_path: Path) -> Stage1Orchestrator:
    return Stage1Orchestrator(
        run_store=RunStore(tmp_path / "runs"),
        planner=FakePlanner(),
        github_reader=FakeGitHubReader(),
    )


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/responses")


def _response(status_code: int) -> httpx.Response:
    return httpx.Response(status_code, request=_request())


def test_openai_client_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(OpenAIConfigurationError):
        OpenAIResponsesClient()


def test_openai_client_retries_transient_failures() -> None:
    sleeps: list[float] = []
    fake_client = SimpleNamespace(
        responses=FakeResponsesEndpoint(
            [
                RateLimitError("try again", response=_response(429), body={"error": "rate"}),
                APIConnectionError(message="network", request=_request()),
                FakeParsedResponse(ExamplePlan(title="ok")),
            ]
        )
    )
    client = OpenAIResponsesClient(
        api_key="sk-test",
        client=fake_client,
        max_retries=3,
        sleep=sleeps.append,
    )

    result = client.parse_structured_output(
        instructions="Return a plan.",
        prompt="hello",
        output_type=ExamplePlan,
    )

    assert result.parsed.title == "ok"
    assert result.response_id == "resp_test"
    assert fake_client.responses.calls == 3
    assert sleeps == [2.0, 4.0]


def test_openai_client_surfaces_non_retryable_api_error() -> None:
    fake_client = SimpleNamespace(
        responses=FakeResponsesEndpoint(
            [BadRequestError("bad request", response=_response(400), body={"error": "bad"})]
        )
    )
    client = OpenAIResponsesClient(api_key="sk-test", client=fake_client, max_retries=3)

    with pytest.raises(OpenAIRequestError):
        client.parse_structured_output(
            instructions="Return a plan.",
            prompt="hello",
            output_type=ExamplePlan,
        )


def test_normalize_planner_payload_coerces_common_messy_shapes() -> None:
    result = normalize_planner_payload(
        {
            "task_summary": "Ship planner flow",
            "constraints": "Keep the API adapter small.",
            "assumptions": ["The repo snapshot is enough for planning."],
            "risks": [{"text": "Planner output may omit files.", "mitigation": "Review the stage list manually."}],
            "stages": [
                "Inspect the current orchestrator flow.",
                {"name": "Persist plan", "goal": "Save artifacts", "files": "src/bob/stage1/storage.py", "output": "Saved planner artifacts."},
            ],
            "approval_notes": "Pause for review before coding.",
        },
        fallback_task_summary="Fallback summary",
        model="gpt-5.4-mini",
        response_id="resp_norm",
    )

    assert result.plan.constraints == ["Keep the API adapter small."]
    assert result.plan.assumptions[0].text == "The repo snapshot is enough for planning."
    assert len(result.plan.stages) == 2
    assert any("Normalized constraints from a string to a list." == warning for warning in result.normalization_warnings)


def test_normalize_planner_payload_rejects_missing_stages() -> None:
    with pytest.raises(PlannerNormalizationError):
        normalize_planner_payload(
            {"task_summary": "No stages here", "stages": []},
            fallback_task_summary="Fallback summary",
            model="gpt-5.4-mini",
            response_id="resp_empty",
        )


def test_start_creates_run_and_waits_for_plan_approval(tmp_path: Path) -> None:
    repo_path = make_repo(tmp_path)
    orchestrator = build_orchestrator(tmp_path)

    ledger, run_dir = orchestrator.start(
        Stage1Input(repo="owner/repo", repo_path=str(repo_path), task="Create a planner-only Bob flow.")
    )

    assert ledger.status == Stage1Status.AWAITING_PLAN_APPROVAL
    assert (run_dir / "task_brief.json").exists()
    assert (run_dir / "planner_prompt.md").exists()
    assert (run_dir / "planner_response.json").exists()
    assert (run_dir / "planner_result.json").exists()
    assert ledger.model_settings["planner_provider"] == "openai"
    assert ledger.model_settings["planner_model"] == "gpt-5.4-mini"


def test_issue_mode_start_persists_issue_context(tmp_path: Path) -> None:
    repo_path = make_repo(tmp_path)
    orchestrator = build_orchestrator(tmp_path)

    ledger, run_dir = orchestrator.start(
        Stage1Input(repo="owner/repo", repo_path=str(repo_path), issue_number=123)
    )

    task_brief = json.loads((run_dir / "task_brief.json").read_text(encoding="utf-8"))
    assert ledger.status == Stage1Status.AWAITING_PLAN_APPROVAL
    assert task_brief["issue"]["number"] == 123
    assert task_brief["issue_comments"][0]["body"] == "Issue comment context."


def test_resume_without_approve_plan_is_read_only(tmp_path: Path) -> None:
    repo_path = make_repo(tmp_path)
    orchestrator = build_orchestrator(tmp_path)
    started_ledger, run_dir = orchestrator.start(
        Stage1Input(repo="owner/repo", repo_path=str(repo_path), task="Create a planner-only Bob flow.")
    )
    before = (run_dir / "ledger.json").read_text(encoding="utf-8")

    resumed_ledger, _ = orchestrator.resume(run_id=started_ledger.run_id, approve_plan=False)
    after = (run_dir / "ledger.json").read_text(encoding="utf-8")

    assert resumed_ledger.status == Stage1Status.AWAITING_PLAN_APPROVAL
    assert resumed_ledger.approvals.plan_approved is False
    assert before == after
    assert (repo_path / "README.md").read_text(encoding="utf-8") == "# Fixture Repo\n"


def test_resume_with_approve_plan_records_approval(tmp_path: Path) -> None:
    repo_path = make_repo(tmp_path)
    orchestrator = build_orchestrator(tmp_path)
    started_ledger, _ = orchestrator.start(
        Stage1Input(repo="owner/repo", repo_path=str(repo_path), task="Create a planner-only Bob flow.")
    )

    resumed_ledger, _ = orchestrator.resume(run_id=started_ledger.run_id, approve_plan=True)

    assert resumed_ledger.status == Stage1Status.PLAN_APPROVED
    assert resumed_ledger.approvals.plan_approved is True
    assert resumed_ledger.approvals.plan_approved_at is not None
    assert (repo_path / "README.md").read_text(encoding="utf-8") == "# Fixture Repo\n"


def test_dirty_repo_fails_before_planning(tmp_path: Path) -> None:
    repo_path = make_repo(tmp_path)
    (repo_path / "README.md").write_text("# Fixture Repo\nDirty change\n", encoding="utf-8")
    orchestrator = build_orchestrator(tmp_path)

    with pytest.raises(Exception):
        orchestrator.start(
            Stage1Input(repo="owner/repo", repo_path=str(repo_path), task="Attempt work in a dirty repo.")
        )

    run_dirs = list((tmp_path / "runs").iterdir())
    assert len(run_dirs) == 1
    ledger = RunLedger.model_validate_json((run_dirs[0] / "ledger.json").read_text(encoding="utf-8"))
    assert ledger.status == Stage1Status.FAILED


def test_cli_run_and_resume_display_saved_plan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo_path = make_repo(tmp_path)
    orchestrator = build_orchestrator(tmp_path)
    runner = CliRunner()

    monkeypatch.setattr("bob.cli.build_default_stage1_orchestrator", lambda: orchestrator)

    run_result = runner.invoke(
        app,
        ["stage1", "run", "--repo", "owner/repo", "--path", str(repo_path), "--task", "Create a planner-only Bob flow."],
    )
    assert run_result.exit_code == 0, run_result.output
    assert "status=AWAITING_PLAN_APPROVAL" in run_result.output
    assert "model=gpt-5.4-mini" in run_result.output
    assert "next_command=bob stage1 resume --run" in run_result.output
    assert "--approve-plan" in run_result.output

    run_id = next(line.split("=", 1)[1] for line in run_result.output.splitlines() if line.startswith("run_id="))

    review_result = runner.invoke(app, ["stage1", "resume", "--run", run_id])
    assert review_result.exit_code == 0, review_result.output
    assert "status=AWAITING_PLAN_APPROVAL" in review_result.output
    assert f"next_command=bob stage1 resume --run {run_id} --approve-plan" in review_result.output

    approve_result = runner.invoke(app, ["stage1", "resume", "--run", run_id, "--approve-plan"])
    assert approve_result.exit_code == 0, approve_result.output
    assert "status=PLAN_APPROVED" in approve_result.output
    assert "Plan approved and saved. Stage 3 execution is not implemented yet." in approve_result.output
