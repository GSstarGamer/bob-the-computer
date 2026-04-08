from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bob.runtime.evaluator import TaskEvaluator
from bob.runtime.github_adapter import GhCliGitHubIssueReader, GitHubIssueReader
from bob.runtime.llm_config import build_responses_client, resolve_endpoint_config
from bob.runtime.models import (
    BobInput,
    CandidateSourceType,
    CandidateTask,
    CandidateTaskCollection,
    InputMode,
    IssueContext,
    LLMEndpointConfig,
    LLMProvider,
    PlannerResult,
    SelectionResult,
    SessionLedger,
    SessionResult,
    SessionStatus,
    TaskEvaluation,
    TaskEvaluationCollection,
)
from bob.runtime.planner import TaskPlanner
from bob.runtime.repo_tools import build_repo_snapshot_text, snapshot_repo
from bob.runtime.selection import select_best_candidate
from bob.runtime.storage import SessionStore, generate_session_id


class CandidateFileError(RuntimeError):
    """Raised when a candidate file cannot be loaded."""


class BobOrchestrator:
    def __init__(
        self,
        *,
        session_store: SessionStore,
        evaluator: TaskEvaluator,
        planner: TaskPlanner,
        github_reader: GitHubIssueReader,
        evaluator_config: LLMEndpointConfig | None = None,
        planner_config: LLMEndpointConfig | None = None,
    ) -> None:
        self.session_store = session_store
        self.evaluator = evaluator
        self.planner = planner
        self.github_reader = github_reader
        self.evaluator_config = evaluator_config
        self.planner_config = planner_config

    def run(self, session_input: BobInput) -> SessionResult:
        session_id = generate_session_id()
        ledger = SessionLedger(
            session_id=session_id,
            repo=session_input.repo,
            repo_path=str(Path(session_input.repo_path).resolve()),
            input_mode=session_input.input_mode,
            status=SessionStatus.INTAKE,
            model_settings=self._build_model_settings(),
        )
        self.session_store.create(ledger)
        try:
            snapshot = snapshot_repo(Path(session_input.repo_path))
            ledger.git_snapshot = snapshot
            self.session_store.save_ledger(ledger)

            candidates = self._build_candidates(session_input, snapshot.repo_root)
            self.session_store.save_model(
                session_id,
                ledger.artifacts.candidate_tasks,
                CandidateTaskCollection(candidates),
            )

            ledger.mark_status(SessionStatus.SCORING)
            self.session_store.save_ledger(ledger)

            repo_snapshot_text = build_repo_snapshot_text(snapshot)
            evaluator_artifacts = self.evaluator.evaluate_candidates(
                session_id=session_id,
                candidates=candidates,
                repo_snapshot_text=repo_snapshot_text,
            )
            if evaluator_artifacts.response_id:
                ledger.response_ids["scoring"] = evaluator_artifacts.response_id

            selection_outcome = select_best_candidate(evaluator_artifacts.evaluations)
            self.session_store.save_model(
                session_id,
                ledger.artifacts.task_evaluations,
                TaskEvaluationCollection(selection_outcome.evaluations),
            )
            self.session_store.save_model(
                session_id,
                ledger.artifacts.selection_result,
                selection_outcome.selection_result,
            )

            selected_task = selection_outcome.selection_result.selected_task
            if selected_task is None:
                ledger.mark_status(SessionStatus.REJECTED)
                self.session_store.save_ledger(ledger)
                return self._build_session_result(
                    session_id=session_id,
                    ledger=ledger,
                    evaluations=selection_outcome.evaluations,
                    selection_result=selection_outcome.selection_result,
                    planner_result=None,
                    model=evaluator_artifacts.model,
                )

            selected_evaluation = self._find_selected_evaluation(
                selection_outcome.evaluations, selection_outcome.selection_result
            )
            ledger.mark_status(SessionStatus.PLANNING)
            self.session_store.save_ledger(ledger)

            planner_artifacts = self.planner.create_plan(
                session_id=session_id,
                selected_task=selected_task,
                evaluation=selected_evaluation,
                repo_snapshot_text=repo_snapshot_text,
            )
            self.session_store.save_text(session_id, ledger.artifacts.planner_prompt, planner_artifacts.prompt_markdown)
            self.session_store.save_json(session_id, ledger.artifacts.planner_response, planner_artifacts.raw_response)
            self.session_store.save_model(session_id, ledger.artifacts.planner_result, planner_artifacts.planner_result)
            if planner_artifacts.planner_result.response_id:
                ledger.response_ids["planning"] = planner_artifacts.planner_result.response_id

            ledger.mark_status(SessionStatus.READY_FOR_EXECUTION)
            self.session_store.save_ledger(ledger)
            return self._build_session_result(
                session_id=session_id,
                ledger=ledger,
                evaluations=selection_outcome.evaluations,
                selection_result=selection_outcome.selection_result,
                planner_result=planner_artifacts.planner_result,
                model=planner_artifacts.planner_result.model,
            )
        except Exception as exc:
            ledger.mark_status(SessionStatus.FAILED, latest_error=str(exc))
            self.session_store.save_ledger(ledger)
            raise

    def resume(self, session_id: str) -> SessionResult:
        ledger = self.session_store.load_ledger(session_id)
        evaluations_collection = self.session_store.load_optional_model(
            session_id, ledger.artifacts.task_evaluations, TaskEvaluationCollection
        )
        selection_result = self.session_store.load_optional_model(
            session_id, ledger.artifacts.selection_result, SelectionResult
        )
        planner_result = self.session_store.load_optional_model(
            session_id, ledger.artifacts.planner_result, PlannerResult
        )
        model = planner_result.model if planner_result else ledger.model_settings.get("evaluator_model")
        return self._build_session_result(
            session_id=session_id,
            ledger=ledger,
            evaluations=list(evaluations_collection.root) if evaluations_collection else [],
            selection_result=selection_result,
            planner_result=planner_result,
            model=model,
        )

    def load_plan(self, session_id: str) -> PlannerResult:
        ledger = self.session_store.load_ledger(session_id)
        planner_result = self.session_store.load_optional_model(
            session_id, ledger.artifacts.planner_result, PlannerResult
        )
        if planner_result is None:
            raise ValueError(f"Session {session_id} does not have a saved implementation plan.")
        return planner_result

    def _build_candidates(self, session_input: BobInput, repo_root: str) -> list[CandidateTask]:
        if session_input.input_mode == InputMode.TASK:
            task_text = (session_input.task or "").strip()
            title = task_text.splitlines()[0][:120] if task_text else "Ad hoc task"
            return [
                CandidateTask(
                    candidate_id="candidate-001",
                    source_type=CandidateSourceType.TASK_INPUT,
                    source_id="task-input",
                    title=title,
                    summary=task_text or title,
                    details=task_text or title,
                    repo_path=str(Path(session_input.repo_path).resolve()),
                    repo_root=repo_root,
                )
            ]

        if session_input.input_mode == InputMode.ISSUE:
            issue_number = session_input.issue_number or 0
            issue = self.github_reader.get_issue(session_input.repo, issue_number)
            comments = self.github_reader.get_issue_comments(session_input.repo, issue_number)
            details = "\n\n".join(
                [
                    issue.body or issue.title,
                    *[
                        f"Comment by {comment.author or 'unknown'}: {comment.body}"
                        for comment in comments
                        if comment.body.strip()
                    ],
                ]
            ).strip()
            return [
                CandidateTask(
                    candidate_id=f"issue-{issue.number}",
                    source_type=CandidateSourceType.GITHUB_ISSUE,
                    source_id=f"issue:{issue.number}",
                    title=issue.title,
                    summary=issue.body or issue.title,
                    details=details or issue.title,
                    repo_path=str(Path(session_input.repo_path).resolve()),
                    repo_root=repo_root,
                    issue=issue,
                    issue_comments=comments,
                    labels=issue.labels,
                )
            ]

        candidate_path = Path(session_input.candidate_file or "").resolve()
        return self._load_candidates_from_file(candidate_path, session_input.repo_path, repo_root)

    def _load_candidates_from_file(
        self,
        candidate_path: Path,
        repo_path: str,
        repo_root: str,
    ) -> list[CandidateTask]:
        if not candidate_path.exists():
            raise CandidateFileError(f"Candidate file does not exist: {candidate_path}")
        try:
            payload = json.loads(candidate_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CandidateFileError(f"Candidate file is not valid JSON: {exc}") from exc

        raw_candidates = payload.get("candidates") if isinstance(payload, dict) else payload
        if not isinstance(raw_candidates, list) or not raw_candidates:
            raise CandidateFileError("Candidate file must contain a non-empty JSON array or {\"candidates\": [...]} object.")

        candidates: list[CandidateTask] = []
        for index, raw_candidate in enumerate(raw_candidates, start=1):
            if not isinstance(raw_candidate, dict):
                raise CandidateFileError("Every candidate entry must be a JSON object.")
            issue = _parse_issue_context(raw_candidate.get("issue"))
            candidate_id = _clean_text(raw_candidate.get("candidate_id")) or f"candidate-{index:03d}"
            title = _clean_text(raw_candidate.get("title"))
            summary = _clean_text(raw_candidate.get("summary")) or title
            details = _clean_text(raw_candidate.get("details")) or summary
            if not title or not summary:
                raise CandidateFileError("Each candidate must include at least a title and summary.")
            source_type_value = _clean_text(raw_candidate.get("source_type")) or CandidateSourceType.CANDIDATE_FILE.value
            try:
                source_type = CandidateSourceType(source_type_value)
            except ValueError as exc:
                raise CandidateFileError(f"Unsupported candidate source_type: {source_type_value}") from exc
            labels = raw_candidate.get("labels")
            if labels is None:
                label_list: list[str] = []
            elif isinstance(labels, list):
                label_list = [str(label).strip() for label in labels if str(label).strip()]
            else:
                raise CandidateFileError("Candidate labels must be a list of strings when provided.")
            candidates.append(
                CandidateTask(
                    candidate_id=candidate_id,
                    source_type=source_type,
                    source_id=_clean_text(raw_candidate.get("source_id")) or candidate_id,
                    title=title,
                    summary=summary,
                    details=details,
                    repo_path=str(Path(repo_path).resolve()),
                    repo_root=repo_root,
                    issue=issue,
                    labels=label_list,
                )
            )
        return candidates

    @staticmethod
    def _find_selected_evaluation(
        evaluations: list[TaskEvaluation],
        selection_result: SelectionResult,
    ) -> TaskEvaluation:
        selected_candidate_id = selection_result.selected_candidate_id
        if selected_candidate_id is None:
            raise ValueError("No selected candidate is available for planning.")
        for evaluation in evaluations:
            if evaluation.candidate.candidate_id == selected_candidate_id:
                return evaluation
        raise ValueError(f"Selected candidate {selected_candidate_id} was not found in the evaluation set.")

    def _build_session_result(
        self,
        *,
        session_id: str,
        ledger: SessionLedger,
        evaluations: list[TaskEvaluation],
        selection_result: SelectionResult | None,
        planner_result: PlannerResult | None,
        model: str | None,
    ) -> SessionResult:
        return SessionResult(
            session_id=session_id,
            status=ledger.status,
            session_dir=str(self.session_store.paths_for(session_id).session_dir),
            selected_task=selection_result.selected_task if selection_result else None,
            evaluations=evaluations,
            selection_result=selection_result,
            planner_result=planner_result,
            model=model,
        )

    def _build_model_settings(self) -> dict[str, str]:
        settings: dict[str, str] = {}
        settings.update(self._endpoint_settings(prefix="evaluator", config=self.evaluator_config, llm_client=self.evaluator.llm_client))
        settings.update(self._endpoint_settings(prefix="planner", config=self.planner_config, llm_client=self.planner.llm_client))
        return settings

    @staticmethod
    def _endpoint_settings(
        *,
        prefix: str,
        config: LLMEndpointConfig | None,
        llm_client: Any,
    ) -> dict[str, str]:
        provider = (
            config.provider.value
            if config is not None
            else (
                LLMProvider.OPENAI_COMPATIBLE.value
                if getattr(llm_client, "base_url", None)
                else LLMProvider.OPENAI.value
            )
        )
        settings = {
            f"{prefix}_provider": provider,
            f"{prefix}_model": str(config.model if config is not None else getattr(llm_client, "model", "")),
            f"{prefix}_timeout_seconds": str(
                config.timeout_seconds if config is not None else getattr(llm_client, "timeout_seconds", "")
            ),
            f"{prefix}_max_retries": str(
                config.max_retries if config is not None else getattr(llm_client, "max_retries", "")
            ),
        }
        base_url = config.base_url if config is not None else getattr(llm_client, "base_url", None)
        api_key_env = config.api_key_env if config is not None else getattr(llm_client, "api_key_env_var", None)
        if base_url:
            settings[f"{prefix}_base_url"] = str(base_url)
        if api_key_env:
            settings[f"{prefix}_api_key_env"] = str(api_key_env)
        return settings


def build_default_bob_orchestrator(
    *,
    provider: str | None = None,
    base_url: str | None = None,
    api_key_env: str | None = None,
    model: str | None = None,
    evaluator_model: str | None = None,
    planner_model: str | None = None,
) -> BobOrchestrator:
    evaluator_config = resolve_endpoint_config(
        role="evaluator",
        provider_override=provider,
        base_url_override=base_url,
        api_key_env_override=api_key_env,
        shared_model_override=model,
        role_model_override=evaluator_model,
    )
    planner_config = resolve_endpoint_config(
        role="planner",
        provider_override=provider,
        base_url_override=base_url,
        api_key_env_override=api_key_env,
        shared_model_override=model,
        role_model_override=planner_model,
    )
    return BobOrchestrator(
        session_store=SessionStore(),
        evaluator=TaskEvaluator(llm_client=build_responses_client(evaluator_config)),
        planner=TaskPlanner(llm_client=build_responses_client(planner_config)),
        github_reader=GhCliGitHubIssueReader(),
        evaluator_config=evaluator_config,
        planner_config=planner_config,
    )


def _parse_issue_context(value: Any) -> IssueContext | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise CandidateFileError("Candidate issue context must be an object when provided.")
    return IssueContext.model_validate(value)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split()).strip()
    return str(value).strip()
