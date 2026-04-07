from __future__ import annotations

import os
from pathlib import Path

from bob.stage1.agents import AgentsStage1Client
from bob.stage1.codex_adapter import CodexCliClient, CodexClient
from bob.stage1.git_tools import build_repo_snapshot_text, ensure_clean_repo, snapshot_repo
from bob.stage1.github_adapter import GhCliGitHubIssueReader, GitHubIssueReader
from bob.stage1.models import (
    CodexWorkOrder,
    FinalSummary,
    InputMode,
    PlanPacket,
    ResearchPacket,
    RunLedger,
    Stage1Input,
    Stage1Status,
    TaskBrief,
    VerificationReport,
    utc_now,
)
from bob.stage1.storage import RunStore, generate_run_id, render_markdown_list


class Stage1Orchestrator:
    def __init__(
        self,
        *,
        run_store: RunStore,
        agents_client: AgentsStage1Client,
        github_reader: GitHubIssueReader,
        codex_client: CodexClient,
    ) -> None:
        self.run_store = run_store
        self.agents_client = agents_client
        self.github_reader = github_reader
        self.codex_client = codex_client

    def start(self, stage1_input: Stage1Input) -> tuple[RunLedger, Path]:
        run_id = generate_run_id()
        ledger = RunLedger(
            run_id=run_id,
            repo=stage1_input.repo,
            repo_path=str(Path(stage1_input.repo_path).resolve()),
            input_mode=stage1_input.input_mode,
            status=Stage1Status.INTAKE,
            model_settings={
                "manager": self.agents_client.llm_model,
                "researcher": self.agents_client.llm_model,
                "planner": self.agents_client.llm_model,
                "verifier": self.agents_client.llm_model,
                "codex_provider": getattr(self.codex_client, "provider", "openai"),
                "codex_research_model": getattr(self.codex_client, "research_model", "gpt-5.4-mini"),
                "codex_write_model": getattr(self.codex_client, "write_model", "gpt-5.4-mini"),
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

            ledger.mark_status(Stage1Status.RESEARCHING)
            ledger.increment_attempt("research")
            self.run_store.save_ledger(ledger)
            repo_snapshot_text = build_repo_snapshot_text(Path(task_brief.repo_path), snapshot)
            research_packet, research_trace_id, codex_session_id, codex_markdown = self.agents_client.research(
                run_id=run_id,
                task_brief=task_brief,
                repo_snapshot_text=repo_snapshot_text,
                github_reader=self.github_reader,
                codex_client=self.codex_client,
            )
            ledger.trace_ids["research"] = research_trace_id
            if codex_session_id:
                ledger.codex_session_ids["research"] = codex_session_id
            self.run_store.save_model(run_id, ledger.artifacts.research_packet, research_packet)
            self.run_store.save_text(run_id, ledger.artifacts.codex_research, codex_markdown)

            ledger.mark_status(Stage1Status.PLANNING)
            ledger.increment_attempt("planning")
            self.run_store.save_ledger(ledger)
            plan_packet, plan_trace_id = self.agents_client.plan(
                run_id=run_id,
                task_brief=task_brief,
                research_packet=research_packet,
            )
            ledger.trace_ids["planning"] = plan_trace_id
            self.run_store.save_model(run_id, ledger.artifacts.plan_packet, plan_packet)

            ledger.mark_status(Stage1Status.AWAITING_WRITE_APPROVAL)
            self.run_store.save_ledger(ledger)
            return ledger, paths.run_dir
        except Exception as exc:
            ledger.mark_status(Stage1Status.FAILED, latest_error=str(exc))
            self.run_store.save_ledger(ledger)
            raise

    def resume(self, *, run_id: str, approve_write: bool) -> tuple[RunLedger, Path]:
        if not approve_write:
            raise ValueError("--approve-write is required before Codex can modify files.")
        ledger = self.run_store.load_ledger(run_id)
        paths = self.run_store.paths_for(run_id)
        if ledger.status != Stage1Status.AWAITING_WRITE_APPROVAL:
            raise ValueError(f"Run {run_id} is not awaiting write approval. Current status: {ledger.status.value}")

        task_brief = self.run_store.load_model(run_id, ledger.artifacts.task_brief, TaskBrief)
        research_packet = self.run_store.load_model(run_id, ledger.artifacts.research_packet, ResearchPacket)
        plan_packet = self.run_store.load_model(run_id, ledger.artifacts.plan_packet, PlanPacket)

        try:
            snapshot = snapshot_repo(Path(ledger.repo_path))
            ensure_clean_repo(snapshot)
            ledger.approvals.write_approved = True
            ledger.approvals.write_approved_at = utc_now()
            ledger.mark_status(Stage1Status.CODING)
            ledger.increment_attempt("coding")
            self.run_store.save_ledger(ledger)

            work_order = self._build_work_order(run_id, task_brief, research_packet, plan_packet)
            change_report = self.codex_client.run_write(work_order)
            if change_report.codex_session_id:
                ledger.codex_session_ids["coding"] = change_report.codex_session_id
            self.run_store.save_model(run_id, ledger.artifacts.codex_change_report, change_report)

            ledger.mark_status(Stage1Status.VERIFYING)
            ledger.increment_attempt("verification")
            self.run_store.save_ledger(ledger)
            verification_report, verify_trace_id = self.agents_client.verify(
                run_id=run_id,
                repo_path=Path(task_brief.repo_path),
                task_brief=task_brief,
                plan_packet=plan_packet,
                change_report_json=change_report.model_dump_json(indent=2),
            )
            ledger.trace_ids["verification"] = verify_trace_id
            self.run_store.save_model(run_id, ledger.artifacts.verification_report, verification_report)

            final_summary, summary_trace_id = self.agents_client.summarize(
                run_id=run_id,
                task_brief=task_brief,
                plan_packet=plan_packet,
                change_report_json=change_report.model_dump_json(indent=2),
                verification_report=verification_report,
            )
            ledger.trace_ids["summary"] = summary_trace_id
            self.run_store.save_text(
                run_id,
                ledger.artifacts.final_summary,
                _render_final_summary(final_summary, verification_report),
            )

            if verification_report.passed:
                ledger.mark_status(Stage1Status.AWAITING_PUBLISH_APPROVAL)
            else:
                ledger.mark_status(Stage1Status.FAILED, latest_error="Verification failed")
            self.run_store.save_ledger(ledger)
            return ledger, paths.run_dir
        except Exception as exc:
            ledger.mark_status(Stage1Status.FAILED, latest_error=str(exc))
            self.run_store.save_ledger(ledger)
            raise

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

    @staticmethod
    def _build_work_order(
        run_id: str,
        task_brief: TaskBrief,
        research_packet: ResearchPacket,
        plan_packet: PlanPacket,
    ) -> CodexWorkOrder:
        objective = task_brief.summary if task_brief.input_mode == InputMode.TASK else task_brief.title
        return CodexWorkOrder(
            run_id=run_id,
            repo=task_brief.repo,
            repo_path=task_brief.repo_path,
            objective=objective,
            plan_summary=plan_packet.summary,
            implementation_steps=plan_packet.implementation_steps,
            acceptance_criteria=plan_packet.acceptance_criteria,
            verification_plan=plan_packet.verification_plan,
            constraints=list(dict.fromkeys([*research_packet.constraints, *plan_packet.risks])),
            forbidden_actions=[
                "Do not commit changes.",
                "Do not push branches.",
                "Do not open or edit pull requests.",
                "Do not comment on GitHub issues or pull requests.",
                "Do not modify files outside the provided repository.",
                "Do not revert unrelated user changes.",
            ],
        )


def build_default_stage1_orchestrator() -> Stage1Orchestrator:
    llm_model = os.environ.get("BOB_LLM_MODEL", "gpt-5.4-mini")
    codex_provider = os.environ.get("BOB_CODEX_PROVIDER", "openai")
    codex_model = os.environ.get("BOB_CODEX_MODEL", "gpt-5.4-mini")
    codex_research_model = os.environ.get("BOB_CODEX_RESEARCH_MODEL", codex_model)
    codex_write_model = os.environ.get("BOB_CODEX_WRITE_MODEL", codex_model)
    return Stage1Orchestrator(
        run_store=RunStore(),
        agents_client=AgentsStage1Client(llm_model=llm_model),
        github_reader=GhCliGitHubIssueReader(),
        codex_client=CodexCliClient(
            provider=codex_provider,
            research_model=codex_research_model,
            write_model=codex_write_model,
        ),
    )


def _render_final_summary(final_summary: FinalSummary, verification_report: VerificationReport) -> str:
    return "\n".join(
        [
            "# Final Summary",
            "",
            final_summary.overview,
            "",
            f"Ready for PR: {'yes' if final_summary.ready_for_pr else 'no'}",
            "",
            "## Suggested PR Title",
            final_summary.pr_title,
            "",
            "## Suggested PR Summary",
            final_summary.pr_summary,
            "",
            "## Changed Files",
            render_markdown_list(final_summary.changed_files),
            "",
            "## Verification Notes",
            render_markdown_list(final_summary.verification_notes or [verification_report.summary]),
            "",
            "## Follow Ups",
            render_markdown_list(final_summary.follow_ups),
        ]
    )
