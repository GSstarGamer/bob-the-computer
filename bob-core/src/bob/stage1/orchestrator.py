from __future__ import annotations

import os
from pathlib import Path

from bob.llm import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    OpenAIResponsesClient,
)
from bob.stage1.git_tools import build_repo_snapshot_text, ensure_clean_repo, snapshot_repo
from bob.stage1.github_adapter import GhCliGitHubIssueReader, GitHubIssueReader
from bob.stage1.models import (
    InputMode,
    PlannerResult,
    RunLedger,
    Stage1Input,
    Stage1Status,
    TaskBrief,
    utc_now,
)
from bob.stage1.planner import Stage1Planner
from bob.stage1.storage import RunStore, generate_run_id


class Stage1Orchestrator:
    def __init__(
        self,
        *,
        run_store: RunStore,
        planner: Stage1Planner,
        github_reader: GitHubIssueReader,
    ) -> None:
        self.run_store = run_store
        self.planner = planner
        self.github_reader = github_reader

    def start(self, stage1_input: Stage1Input) -> tuple[RunLedger, Path]:
        run_id = generate_run_id()
        ledger = RunLedger(
            run_id=run_id,
            repo=stage1_input.repo,
            repo_path=str(Path(stage1_input.repo_path).resolve()),
            input_mode=stage1_input.input_mode,
            status=Stage1Status.INTAKE,
            model_settings={
                "planner_provider": "openai",
                "planner_model": self.planner.llm_client.model,
                "planner_timeout_seconds": str(self.planner.llm_client.timeout_seconds),
                "planner_max_retries": str(self.planner.llm_client.max_retries),
            },
        )
        paths = self.run_store.create(ledger)
        try:
            snapshot = snapshot_repo(Path(stage1_input.repo_path))
            ensure_clean_repo(snapshot)
            ledger.git_snapshot = snapshot
            self.run_store.save_ledger(ledger)

            task_brief = self._build_task_brief(ledger.run_id, stage1_input, snapshot)
            self.run_store.save_model(run_id, ledger.artifacts.task_brief, task_brief)

            ledger.mark_status(Stage1Status.PLANNING)
            ledger.increment_attempt("planning")
            self.run_store.save_ledger(ledger)

            repo_snapshot_text = build_repo_snapshot_text(Path(task_brief.repo_path), snapshot)
            planner_artifacts = self.planner.create_plan(
                run_id=run_id,
                task_brief=task_brief,
                repo_snapshot_text=repo_snapshot_text,
            )

            self.run_store.save_text(run_id, ledger.artifacts.planner_prompt, planner_artifacts.prompt_markdown)
            self.run_store.save_json(run_id, ledger.artifacts.planner_response, planner_artifacts.raw_response)
            self.run_store.save_model(run_id, ledger.artifacts.planner_result, planner_artifacts.planner_result)
            if planner_artifacts.planner_result.response_id:
                ledger.response_ids["planning"] = planner_artifacts.planner_result.response_id

            ledger.mark_status(Stage1Status.AWAITING_PLAN_APPROVAL)
            self.run_store.save_ledger(ledger)
            return ledger, paths.run_dir
        except Exception as exc:
            ledger.mark_status(Stage1Status.FAILED, latest_error=str(exc))
            self.run_store.save_ledger(ledger)
            raise

    def resume(self, *, run_id: str, approve_plan: bool) -> tuple[RunLedger, Path]:
        ledger = self.run_store.load_ledger(run_id)
        paths = self.run_store.paths_for(run_id)

        if ledger.status not in {Stage1Status.AWAITING_PLAN_APPROVAL, Stage1Status.PLAN_APPROVED}:
            raise ValueError(
                f"Run {run_id} is not awaiting plan approval. Current status: {ledger.status.value}"
            )

        self.load_planner_result(run_id)

        if ledger.status == Stage1Status.AWAITING_PLAN_APPROVAL:
            if approve_plan:
                ledger.approvals.plan_approved = True
                ledger.approvals.plan_approved_at = utc_now()
                ledger.mark_status(Stage1Status.PLAN_APPROVED)
                self.run_store.save_ledger(ledger)
            return ledger, paths.run_dir

        if ledger.status == Stage1Status.PLAN_APPROVED:
            return ledger, paths.run_dir

    def load_planner_result(self, run_id: str) -> PlannerResult:
        ledger = self.run_store.load_ledger(run_id)
        return self.run_store.load_model(run_id, ledger.artifacts.planner_result, PlannerResult)

    def _build_task_brief(self, run_id: str, stage1_input: Stage1Input, snapshot) -> TaskBrief:
        if stage1_input.input_mode == InputMode.TASK:
            task_text = (stage1_input.task or "").strip()
            title = task_text.splitlines()[0][:120] if task_text else "Ad hoc task"
            return TaskBrief(
                run_id=run_id,
                repo=stage1_input.repo,
                repo_path=str(Path(stage1_input.repo_path).resolve()),
                input_mode=InputMode.TASK,
                title=title,
                summary=task_text,
                task_text=task_text,
                repo_snapshot=snapshot,
            )

        issue = self.github_reader.get_issue(stage1_input.repo, stage1_input.issue_number or 0)
        comments = self.github_reader.get_issue_comments(stage1_input.repo, stage1_input.issue_number or 0)
        return TaskBrief(
            run_id=run_id,
            repo=stage1_input.repo,
            repo_path=str(Path(stage1_input.repo_path).resolve()),
            input_mode=InputMode.ISSUE,
            title=f"Issue #{issue.number}: {issue.title}",
            summary=issue.body or issue.title,
            issue=issue,
            issue_comments=comments,
            repo_snapshot=snapshot,
        )


def build_default_stage1_orchestrator() -> Stage1Orchestrator:
    llm_model = os.environ.get("BOB_OPENAI_MODEL") or os.environ.get("BOB_LLM_MODEL", DEFAULT_OPENAI_MODEL)
    timeout_seconds = int(os.environ.get("BOB_OPENAI_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))
    max_retries = int(os.environ.get("BOB_OPENAI_MAX_RETRIES", str(DEFAULT_MAX_RETRIES)))
    return Stage1Orchestrator(
        run_store=RunStore(),
        planner=Stage1Planner(
            llm_client=OpenAIResponsesClient(
                model=llm_model,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
            )
        ),
        github_reader=GhCliGitHubIssueReader(),
    )
