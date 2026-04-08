from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError, BadRequestError, RateLimitError
from pydantic import BaseModel, ValidationError
from typer.testing import CliRunner

from bob.cli import app
from bob.llm.openai_client import OpenAIConfigurationError, OpenAIRequestError, OpenAIResponsesClient
from bob.runtime.evaluator import EvaluatorArtifacts
from bob.runtime.llm_config import resolve_endpoint_config
from bob.runtime.models import (
    BobInput,
    CandidateSourceType,
    CandidateTask,
    ImplementationPlan,
    LLMEndpointConfig,
    LLMProvider,
    IssueComment,
    IssueContext,
    PlannerResult,
    SessionLedger,
    SessionStatus,
    TaskEvaluation,
)
from bob.runtime.orchestrator import BobOrchestrator
from bob.runtime.planner import PlannerArtifacts, PlannerNormalizationError, normalize_planner_payload
from bob.runtime.selection import select_best_candidate
from bob.runtime.storage import SessionStore


class FakeGitHubReader:
    def get_issue(self, repo: str, issue_number: int) -> IssueContext:
        return IssueContext(
            number=issue_number,
            title="Runtime issue title",
            body="Issue-backed planning body.",
            url=f"https://example.com/{repo}/issues/{issue_number}",
            state="open",
            author="alice",
            labels=["planning"],
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


class FakeEvaluator:
    def __init__(self, score_overrides: dict[str, dict[str, float | str]] | None = None) -> None:
        self.llm_client = SimpleNamespace(
            model="gpt-5.4-mini",
            timeout_seconds=60,
            max_retries=3,
            base_url=None,
            api_key_env_var="OPENAI_API_KEY",
        )
        self.score_overrides = score_overrides or {}
        self.calls = 0

    def evaluate_candidates(
        self,
        *,
        session_id: str,
        candidates: list[CandidateTask],
        repo_snapshot_text: str,
    ) -> EvaluatorArtifacts:
        self.calls += 1
        evaluations: list[TaskEvaluation] = []
        for candidate in candidates:
            override = self.score_overrides.get(candidate.candidate_id, {})
            evaluations.append(
                TaskEvaluation(
                    candidate=candidate,
                    usefulness=float(override.get("usefulness", 0.80)),
                    simplicity=float(override.get("simplicity", 0.65)),
                    feasibility=float(override.get("feasibility", 0.78)),
                    implementation_risk=float(override.get("implementation_risk", 0.25)),
                    value=float(override.get("value", 0.75)),
                    evaluator_summary=str(
                        override.get("evaluator_summary", f"{candidate.title} is worth planning next.")
                    ),
                    blockers=[],
                    rejection_reasons=[],
                )
            )
        return EvaluatorArtifacts(
            evaluations=evaluations,
            model=self.llm_client.model,
            response_id=f"resp_eval_{session_id}",
            raw_output_text='{"ok":true}',
            raw_response={"id": f"resp_eval_{session_id}", "repo_snapshot": repo_snapshot_text},
        )


class FakePlanner:
    def __init__(self) -> None:
        self.llm_client = SimpleNamespace(
            model="gpt-5.4-mini",
            timeout_seconds=60,
            max_retries=3,
            base_url=None,
            api_key_env_var="OPENAI_API_KEY",
        )
        self.calls = 0

    def create_plan(
        self,
        *,
        session_id: str,
        selected_task: CandidateTask,
        evaluation: TaskEvaluation,
        repo_snapshot_text: str,
    ) -> PlannerArtifacts:
        self.calls += 1
        planner_result = normalize_planner_payload(
            {
                "title": selected_task.title,
                "short_summary": selected_task.summary,
                "why_it_is_useful": evaluation.evaluator_summary,
                "scope": "Keep the work limited to the current repository goal.",
                "difficulty": "medium",
                "estimated_implementation_size": "medium",
                "dependencies": ["OpenAI API access"],
                "blockers": [],
                "acceptance_criteria": ["The plan is persisted in a typed JSON artifact."],
                "risks": ["The selected file list may broaden after deeper inspection."],
                "edge_cases": ["Malformed model output should fail safely."],
                "implementation_steps": [
                    "Normalize the selected task into a stable internal payload.",
                    "Persist the planning result for a later execution step.",
                ],
                "recommended_files_or_modules": ["src/bob/runtime/orchestrator.py", "src/bob/runtime/planner.py"],
                "confidence": evaluation.feasibility,
                "priority_score": evaluation.weighted_total_score or 0.70,
            },
            selected_task=selected_task,
            evaluation=evaluation,
            model=self.llm_client.model,
            response_id=f"resp_plan_{session_id}",
            raw_output_text='{"ok":true}',
        )
        return PlannerArtifacts(
            prompt_markdown="# Planner Prompt\n\nFixture prompt.\n",
            raw_response={"id": planner_result.response_id, "repo_snapshot": repo_snapshot_text},
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


def build_orchestrator(
    tmp_path: Path,
    *,
    score_overrides: dict[str, dict[str, float | str]] | None = None,
) -> tuple[BobOrchestrator, FakeEvaluator, FakePlanner]:
    evaluator = FakeEvaluator(score_overrides=score_overrides)
    planner = FakePlanner()
    orchestrator = BobOrchestrator(
        session_store=SessionStore(tmp_path / "runs"),
        evaluator=evaluator,
        planner=planner,
        github_reader=FakeGitHubReader(),
    )
    return orchestrator, evaluator, planner


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/responses")


def _response(status_code: int) -> httpx.Response:
    return httpx.Response(status_code, request=_request())


def _make_candidate(candidate_id: str, *, title: str = "Candidate") -> CandidateTask:
    return CandidateTask(
        candidate_id=candidate_id,
        source_type=CandidateSourceType.TASK_INPUT,
        source_id=candidate_id,
        title=title,
        summary=f"{title} summary",
        details=f"{title} details",
        repo_path="C:/repo",
        repo_root="C:/repo",
    )


def _make_evaluation(
    candidate_id: str,
    *,
    usefulness: float,
    simplicity: float,
    feasibility: float,
    implementation_risk: float,
    value: float,
) -> TaskEvaluation:
    return TaskEvaluation(
        candidate=_make_candidate(candidate_id),
        usefulness=usefulness,
        simplicity=simplicity,
        feasibility=feasibility,
        implementation_risk=implementation_risk,
        value=value,
        evaluator_summary="Looks reasonable.",
    )


def test_openai_client_requires_api_key_without_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(OpenAIConfigurationError):
        OpenAIResponsesClient()


def test_openai_client_allows_keyless_openai_compatible_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class CapturingOpenAI:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)
            self.responses = FakeResponsesEndpoint([FakeParsedResponse(ExamplePlan(title="ok"))])

    monkeypatch.setattr("bob.llm.openai_client.OpenAI", CapturingOpenAI)

    client = OpenAIResponsesClient(base_url="https://llm.example.test/v1", api_key_env_var=None)
    result = client.parse_structured_output(
        instructions="Return a plan.",
        prompt="hello",
        output_type=ExamplePlan,
    )

    assert captured["api_key"] == ""
    assert captured["base_url"] == "https://llm.example.test/v1"
    assert result.parsed.title == "ok"


def test_openai_client_passes_base_url_to_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class CapturingOpenAI:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)
            self.responses = FakeResponsesEndpoint([FakeParsedResponse(ExamplePlan(title="ok"))])

    monkeypatch.setattr("bob.llm.openai_client.OpenAI", CapturingOpenAI)

    client = OpenAIResponsesClient(api_key="sk-test", base_url="https://llm.example.test/v1")
    result = client.parse_structured_output(
        instructions="Return a plan.",
        prompt="hello",
        output_type=ExamplePlan,
    )

    assert captured["base_url"] == "https://llm.example.test/v1"
    assert captured["api_key"] == "sk-test"
    assert captured["default_headers"] == {"User-Agent": "BobRuntime/0.1"}
    assert result.parsed.title == "ok"


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


def test_runtime_models_validate_sources_and_forbid_extra_keys() -> None:
    with pytest.raises(ValidationError):
        BobInput(repo="owner/repo", repo_path="C:/repo", task="one", issue_number=1)

    with pytest.raises(ValidationError):
        CandidateTask.model_validate(
            {
                "candidate_id": "candidate-001",
                "source_type": "task_input",
                "source_id": "task-input",
                "title": "Task",
                "summary": "Summary",
                "details": "Details",
                "repo_path": "C:/repo",
                "unexpected": True,
            }
        )

    with pytest.raises(ValidationError):
        ImplementationPlan.model_validate(
            {
                "task_id": "candidate-001",
                "source_id": "task-input",
                "source_type": "task_input",
                "title": "Task",
                "short_summary": "Summary",
                "why_it_is_useful": "Useful",
                "scope": "Scoped",
                "difficulty": "medium",
                "estimated_implementation_size": "small",
                "confidence": 0.8,
                "priority_score": 0.8,
                "unexpected": True,
            }
        )


def test_resolve_endpoint_config_prefers_overrides_and_role_specific_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOB_LLM_PROVIDER", "openai")
    monkeypatch.setenv("BOB_LLM_MODEL", "shared-env-model")
    monkeypatch.setenv("BOB_EVALUATOR_MODEL", "env-evaluator-model")
    monkeypatch.setenv("BOB_LLM_BASE_URL", "https://shared.example/v1")
    monkeypatch.setenv("BOB_EVALUATOR_BASE_URL", "https://env-evaluator.example/v1")
    monkeypatch.setenv("CUSTOM_LLM_KEY", "sk-local")

    config = resolve_endpoint_config(
        role="evaluator",
        provider_override="openai_compatible",
        base_url_override="https://override.example/v1",
        api_key_env_override="CUSTOM_LLM_KEY",
        shared_model_override="shared-cli-model",
        role_model_override="cli-evaluator-model",
    )

    assert config.provider == LLMProvider.OPENAI_COMPATIBLE
    assert config.model == "cli-evaluator-model"
    assert config.base_url == "https://override.example/v1"
    assert config.api_key_env == "CUSTOM_LLM_KEY"
    assert config.api_key == "sk-local"


def test_resolve_endpoint_config_uses_role_specific_env_before_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOB_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("BOB_LLM_MODEL", "shared-model")
    monkeypatch.setenv("BOB_PLANNER_MODEL", "planner-model")
    monkeypatch.setenv("BOB_LLM_BASE_URL", "https://shared.example/v1")
    monkeypatch.setenv("BOB_PLANNER_BASE_URL", "https://planner.example/v1")
    monkeypatch.setenv("BOB_LLM_API_KEY", "sk-local")

    config = resolve_endpoint_config(role="planner")

    assert config.provider == LLMProvider.OPENAI_COMPATIBLE
    assert config.model == "planner-model"
    assert config.base_url == "https://planner.example/v1"
    assert config.api_key == "sk-local"


def test_resolve_endpoint_config_defaults_to_bob_local_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOB_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("BOB_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("BOB_LLM_MODEL", raising=False)
    monkeypatch.delenv("BOB_PLANNER_MODEL", raising=False)
    monkeypatch.delenv("BOB_PLANNER_BASE_URL", raising=False)
    monkeypatch.delenv("BOB_OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("BOB_LLM_API_KEY", raising=False)

    config = resolve_endpoint_config(role="planner")

    assert config.provider == LLMProvider.OPENAI_COMPATIBLE
    assert config.model == "gpt-oss-20b"
    assert config.base_url == "https://llm.rionnag.net/gpt-oss/v1"
    assert config.api_key is None
    assert config.api_key_env is None


def test_selection_helper_marks_viability_and_ranks_candidates() -> None:
    outcome = select_best_candidate(
        [
            _make_evaluation(
                "candidate-low-risk",
                usefulness=0.82,
                simplicity=0.70,
                feasibility=0.80,
                implementation_risk=0.20,
                value=0.74,
            ),
            _make_evaluation(
                "candidate-high-risk",
                usefulness=0.90,
                simplicity=0.55,
                feasibility=0.60,
                implementation_risk=0.82,
                value=0.88,
            ),
        ]
    )

    assert outcome.selection_result.selected_candidate_id == "candidate-low-risk"
    assert outcome.selection_result.ranked_candidate_ids[0] == "candidate-low-risk"
    rejected = next(item for item in outcome.evaluations if item.candidate.candidate_id == "candidate-high-risk")
    assert rejected.is_viable is False
    assert any("Implementation risk" in reason for reason in rejected.rejection_reasons)


def test_selection_helper_rejects_all_when_thresholds_fail() -> None:
    outcome = select_best_candidate(
        [
            _make_evaluation(
                "candidate-001",
                usefulness=0.40,
                simplicity=0.50,
                feasibility=0.35,
                implementation_risk=0.40,
                value=0.45,
            )
        ]
    )

    assert outcome.selection_result.selected_candidate_id is None
    assert outcome.selection_result.rejected_candidate_ids == ["candidate-001"]
    assert outcome.evaluations[0].is_viable is False


def test_normalize_planner_payload_coerces_common_messy_shapes() -> None:
    selected_task = _make_candidate("candidate-001", title="Ship planner flow")
    evaluation = _make_evaluation(
        "candidate-001",
        usefulness=0.80,
        simplicity=0.70,
        feasibility=0.82,
        implementation_risk=0.20,
        value=0.75,
    ).model_copy(update={"weighted_total_score": 0.79})

    result = normalize_planner_payload(
        {
            "title": "Ship planner flow",
            "short_summary": "Create a structured implementation plan.",
            "why_it_is_useful": "It makes Bob ready for later execution.",
            "scope": "Keep the API adapter small.",
            "difficulty": "medium",
            "estimated_implementation_size": "medium",
            "dependencies": "OpenAI API access",
            "acceptance_criteria": "Planner result persists clean JSON.",
            "risks": ["Planner output may omit files."],
            "edge_cases": ["Missing model fields."],
            "implementation_steps": [
                "Inspect the current runtime flow.",
                "Persist the plan artifact.",
            ],
            "recommended_files_or_modules": "src/bob/runtime/planner.py",
            "confidence": "0.81",
            "priority_score": "0.79",
        },
        selected_task=selected_task,
        evaluation=evaluation,
        model="gpt-5.4-mini",
        response_id="resp_norm",
    )

    assert result.plan.dependencies == ["OpenAI API access"]
    assert result.plan.acceptance_criteria == ["Planner result persists clean JSON."]
    assert result.plan.recommended_files_or_modules == ["src/bob/runtime/planner.py"]
    assert any(
        "Normalized dependencies from a string to a list." == warning for warning in result.normalization_warnings
    )


def test_normalize_planner_payload_rejects_missing_implementation_steps() -> None:
    selected_task = _make_candidate("candidate-001")
    evaluation = _make_evaluation(
        "candidate-001",
        usefulness=0.80,
        simplicity=0.70,
        feasibility=0.82,
        implementation_risk=0.20,
        value=0.75,
    )

    with pytest.raises(PlannerNormalizationError):
        normalize_planner_payload(
            {"title": "Incomplete", "acceptance_criteria": ["Still missing steps"]},
            selected_task=selected_task,
            evaluation=evaluation,
            model="gpt-5.4-mini",
            response_id="resp_empty",
        )


def test_run_with_task_input_persists_session_and_plan(tmp_path: Path) -> None:
    repo_path = make_repo(tmp_path)
    orchestrator, evaluator, planner = build_orchestrator(tmp_path)

    session = orchestrator.run(
        BobInput(repo="owner/repo", repo_path=str(repo_path), task="Build the unified Bob planning flow.")
    )

    session_dir = Path(session.session_dir)
    assert session.status == SessionStatus.READY_FOR_EXECUTION
    assert evaluator.calls == 1
    assert planner.calls == 1
    assert (session_dir / "candidate_tasks.json").exists()
    assert (session_dir / "task_evaluations.json").exists()
    assert (session_dir / "selection_result.json").exists()
    assert (session_dir / "planner_prompt.md").exists()
    assert (session_dir / "planner_response.json").exists()
    assert (session_dir / "planner_result.json").exists()
    ledger = SessionLedger.model_validate_json((session_dir / "ledger.json").read_text(encoding="utf-8"))
    assert ledger.status == SessionStatus.READY_FOR_EXECUTION
    assert ledger.model_settings["evaluator_model"] == "gpt-5.4-mini"


def test_run_persists_openai_compatible_endpoint_settings(tmp_path: Path) -> None:
    repo_path = make_repo(tmp_path)
    evaluator = FakeEvaluator()
    planner = FakePlanner()
    evaluator_config = LLMEndpointConfig(
        provider=LLMProvider.OPENAI_COMPATIBLE,
        model="gpt-oss-20b",
        base_url="https://llm.example.test/gpt-oss/v1",
        api_key=None,
        api_key_env=None,
        timeout_seconds=90,
        max_retries=4,
    )
    planner_config = LLMEndpointConfig(
        provider=LLMProvider.OPENAI_COMPATIBLE,
        model="gpt-oss-20b",
        base_url="https://llm.example.test/gpt-oss/v1",
        api_key=None,
        api_key_env=None,
        timeout_seconds=90,
        max_retries=4,
    )
    orchestrator = BobOrchestrator(
        session_store=SessionStore(tmp_path / "runs"),
        evaluator=evaluator,
        planner=planner,
        github_reader=FakeGitHubReader(),
        evaluator_config=evaluator_config,
        planner_config=planner_config,
    )

    session = orchestrator.run(
        BobInput(repo="owner/repo", repo_path=str(repo_path), task="Plan against a local OpenAI-compatible endpoint.")
    )

    ledger = SessionLedger.model_validate_json((Path(session.session_dir) / "ledger.json").read_text(encoding="utf-8"))
    assert ledger.model_settings["evaluator_provider"] == "openai_compatible"
    assert ledger.model_settings["evaluator_model"] == "gpt-oss-20b"
    assert ledger.model_settings["evaluator_base_url"] == "https://llm.example.test/gpt-oss/v1"
    assert "evaluator_api_key_env" not in ledger.model_settings
    assert ledger.model_settings["planner_provider"] == "openai_compatible"


def test_run_with_issue_input_persists_issue_backed_candidate(tmp_path: Path) -> None:
    repo_path = make_repo(tmp_path)
    orchestrator, _, _ = build_orchestrator(tmp_path)

    session = orchestrator.run(BobInput(repo="owner/repo", repo_path=str(repo_path), issue_number=123))

    candidates = json.loads((Path(session.session_dir) / "candidate_tasks.json").read_text(encoding="utf-8"))
    assert session.status == SessionStatus.READY_FOR_EXECUTION
    assert candidates[0]["issue"]["number"] == 123
    assert candidates[0]["issue_comments"][0]["body"] == "Issue comment context."


def test_run_with_candidate_file_only_plans_top_viable_candidate(tmp_path: Path) -> None:
    repo_path = make_repo(tmp_path)
    candidate_file = tmp_path / "candidates.json"
    candidate_file.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "candidate_id": "candidate-low",
                        "title": "Risky task",
                        "summary": "Might not be feasible.",
                        "details": "This one should be rejected.",
                    },
                    {
                        "candidate_id": "candidate-high",
                        "title": "Useful task",
                        "summary": "A solid next step.",
                        "details": "This one should be selected.",
                    },
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    orchestrator, _, planner = build_orchestrator(
        tmp_path,
        score_overrides={
            "candidate-low": {
                "usefulness": 0.40,
                "simplicity": 0.40,
                "feasibility": 0.30,
                "implementation_risk": 0.80,
                "value": 0.35,
            },
            "candidate-high": {
                "usefulness": 0.88,
                "simplicity": 0.72,
                "feasibility": 0.84,
                "implementation_risk": 0.18,
                "value": 0.86,
            },
        },
    )

    session = orchestrator.run(
        BobInput(repo="owner/repo", repo_path=str(repo_path), candidate_file=str(candidate_file))
    )

    selection = json.loads((Path(session.session_dir) / "selection_result.json").read_text(encoding="utf-8"))
    assert session.status == SessionStatus.READY_FOR_EXECUTION
    assert session.selected_task is not None
    assert session.selected_task.candidate_id == "candidate-high"
    assert selection["selected_candidate_id"] == "candidate-high"
    assert planner.calls == 1


def test_run_marks_session_rejected_when_no_viable_candidate(tmp_path: Path) -> None:
    repo_path = make_repo(tmp_path)
    orchestrator, _, planner = build_orchestrator(
        tmp_path,
        score_overrides={
            "candidate-001": {
                "usefulness": 0.30,
                "simplicity": 0.30,
                "feasibility": 0.20,
                "implementation_risk": 0.90,
                "value": 0.25,
            }
        },
    )

    session = orchestrator.run(
        BobInput(repo="owner/repo", repo_path=str(repo_path), task="A poor candidate that should be rejected.")
    )

    session_dir = Path(session.session_dir)
    assert session.status == SessionStatus.REJECTED
    assert session.selected_task is None
    assert planner.calls == 0
    assert (session_dir / "candidate_tasks.json").exists()
    assert (session_dir / "task_evaluations.json").exists()
    assert (session_dir / "selection_result.json").exists()
    assert not (session_dir / "planner_result.json").exists()


def test_resume_is_read_only(tmp_path: Path) -> None:
    repo_path = make_repo(tmp_path)
    orchestrator, _, _ = build_orchestrator(tmp_path)
    started = orchestrator.run(
        BobInput(repo="owner/repo", repo_path=str(repo_path), task="Build the unified Bob planning flow.")
    )
    ledger_path = Path(started.session_dir) / "ledger.json"
    before = ledger_path.read_text(encoding="utf-8")

    resumed = orchestrator.resume(started.session_id)
    after = ledger_path.read_text(encoding="utf-8")

    assert resumed.status == SessionStatus.READY_FOR_EXECUTION
    assert before == after
    assert (repo_path / "README.md").read_text(encoding="utf-8") == "# Fixture Repo\n"


def test_cli_run_resume_and_show_plan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo_path = make_repo(tmp_path)
    orchestrator, _, _ = build_orchestrator(tmp_path)
    runner = CliRunner()

    monkeypatch.setattr("bob.cli.build_default_bob_orchestrator", lambda **kwargs: orchestrator)

    run_result = runner.invoke(
        app,
        ["run", "--repo", "owner/repo", "--path", str(repo_path), "--task", "Build the unified Bob planning flow."],
    )
    assert run_result.exit_code == 0, run_result.output
    assert "status=READY_FOR_EXECUTION" in run_result.output
    assert "ready_for_execution=yes" in run_result.output
    assert "selected_candidate=candidate-001" in run_result.output
    assert "approve-plan" not in run_result.output

    session_id = next(line.split("=", 1)[1] for line in run_result.output.splitlines() if line.startswith("session_id="))

    resume_result = runner.invoke(app, ["resume", "--session", session_id])
    assert resume_result.exit_code == 0, resume_result.output
    assert "status=READY_FOR_EXECUTION" in resume_result.output

    show_text_result = runner.invoke(app, ["show-plan", "--session", session_id])
    assert show_text_result.exit_code == 0, show_text_result.output
    assert "Implementation Plan" in show_text_result.output
    assert "Acceptance Criteria" in show_text_result.output

    export_path = tmp_path / "plan.json"
    show_json_result = runner.invoke(
        app,
        ["show-plan", "--session", session_id, "--format", "json", "--output", str(export_path)],
    )
    assert show_json_result.exit_code == 0, show_json_result.output
    assert export_path.exists()
    exported = json.loads(export_path.read_text(encoding="utf-8"))
    assert exported["plan"]["task_id"] == "candidate-001"


def test_cli_llm_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    endpoint = LLMEndpointConfig(
        provider=LLMProvider.OPENAI_COMPATIBLE,
        model="gpt-oss-20b",
        base_url="https://llm.example.test/gpt-oss/v1",
        api_key=None,
        api_key_env=None,
        timeout_seconds=60,
        max_retries=3,
    )

    monkeypatch.setattr("bob.cli.resolve_endpoint_config", lambda **kwargs: endpoint)
    monkeypatch.setattr(
        "bob.cli.build_responses_client",
        lambda config: SimpleNamespace(
            probe=lambda: SimpleNamespace(
                response_id="resp_probe",
                parsed=SimpleNamespace(message="pong"),
            )
        ),
    )

    result = runner.invoke(app, ["llm-probe"])

    assert result.exit_code == 0, result.output
    assert "provider=openai_compatible" in result.output
    assert "model=gpt-oss-20b" in result.output
    assert "base_url=https://llm.example.test/gpt-oss/v1" in result.output
    assert "api_key_env=" not in result.output
    assert "message=pong" in result.output
