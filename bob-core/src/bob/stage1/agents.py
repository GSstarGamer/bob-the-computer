from __future__ import annotations

import subprocess
from pathlib import Path

from agents import Agent, ModelSettings, RunConfig, Runner, function_tool

from bob.stage1.codex_adapter import CodexClient
from bob.stage1.git_tools import diff_name_only, diff_summary
from bob.stage1.github_adapter import GitHubIssueReader
from bob.stage1.models import FinalSummary, PlanPacket, ResearchPacket, TaskBrief, VerificationReport

SAFE_COMMAND_PREFIXES = (
    "python -m pytest",
    "pytest",
    "python -m unittest",
    "uv run pytest",
    "ruff check",
    "npm test",
    "npm run test",
    "npm run lint",
    "pnpm test",
    "pnpm lint",
    "cargo test",
    "go test",
)


class AgentsStage1Client:
    def __init__(self, llm_model: str = "gpt-5.4-mini") -> None:
        self.llm_model = llm_model
        self.model_settings = ModelSettings(temperature=0, verbosity="low")

    def research(
        self,
        *,
        run_id: str,
        task_brief: TaskBrief,
        repo_snapshot_text: str,
        github_reader: GitHubIssueReader,
        codex_client: CodexClient,
    ) -> tuple[ResearchPacket, str, str | None, str]:
        captured: dict[str, str | None] = {"codex_session_id": None, "codex_markdown": None}

        @function_tool
        def get_issue(repo: str, issue_number: int) -> dict:
            """Load a single GitHub issue for the active repository."""

            return github_reader.get_issue(repo, issue_number).model_dump(mode="json")

        @function_tool
        def get_issue_comments(repo: str, issue_number: int) -> list[dict]:
            """Load the comments for a single GitHub issue."""

            comments = github_reader.get_issue_comments(repo, issue_number)
            return [comment.model_dump(mode="json") for comment in comments]

        @function_tool
        def get_repo_snapshot(repo_path: str) -> str:
            """Return a compact repository snapshot for planning and research."""

            if Path(repo_path).resolve() != Path(task_brief.repo_path).resolve():
                raise ValueError("Research is limited to the run repository path.")
            return repo_snapshot_text

        @function_tool
        def launch_codex_research(repo_path: str) -> dict:
            """Ask Codex to analyze the current task using the prepared repo snapshot only."""

            if Path(repo_path).resolve() != Path(task_brief.repo_path).resolve():
                raise ValueError("Codex research is limited to the run repository path.")
            packet, markdown, session_id = codex_client.run_research(task_brief, repo_snapshot_text)
            captured["codex_session_id"] = session_id
            captured["codex_markdown"] = markdown
            return packet.model_dump(mode="json")

        agent = Agent(
            name="Bob Researcher",
            handoff_description="Collect issue-specific repo context for one scoped implementation task.",
            instructions=(
                "You are Bob's Stage 1 researcher. Keep the output practical and implementation-oriented. "
                "If the run is issue-backed, load the issue details and comments if needed. "
                "Always inspect the repo snapshot and call launch_codex_research exactly once. "
                "Return a concise ResearchPacket."
            ),
            model=self.llm_model,
            model_settings=self.model_settings,
            tools=[get_issue, get_issue_comments, get_repo_snapshot, launch_codex_research],
            output_type=ResearchPacket,
        )
        trace_id = f"{run_id}-research"
        prompt = (
            "Task brief:\n"
            f"{task_brief.model_dump_json(indent=2)}\n\n"
            "Use the available tools to gather the final Stage 1 research packet."
        )
        result = Runner.run_sync(
            agent,
            prompt,
            max_turns=10,
            run_config=RunConfig(
                workflow_name="Bob Stage 1 Research",
                trace_id=trace_id,
                group_id=run_id,
            ),
        )
        return (
            result.final_output_as(ResearchPacket),
            trace_id,
            captured["codex_session_id"],
            captured["codex_markdown"] or "# Codex Research\n\nNo Codex research captured.\n",
        )

    def plan(
        self,
        *,
        run_id: str,
        task_brief: TaskBrief,
        research_packet: ResearchPacket,
    ) -> tuple[PlanPacket, str]:
        agent = Agent(
            name="Bob Planner",
            handoff_description="Turn a research packet into a short implementation plan.",
            instructions=(
                "You are Bob's Stage 1 planner. Produce a short implementation packet with specific steps, "
                "clear acceptance criteria, and a runnable verification plan when possible."
            ),
            model=self.llm_model,
            model_settings=self.model_settings,
            output_type=PlanPacket,
        )
        trace_id = f"{run_id}-plan"
        prompt = "\n\n".join(
            [
                "Task brief:",
                task_brief.model_dump_json(indent=2),
                "Research packet:",
                research_packet.model_dump_json(indent=2),
            ]
        )
        result = Runner.run_sync(
            agent,
            prompt,
            max_turns=6,
            run_config=RunConfig(
                workflow_name="Bob Stage 1 Planning",
                trace_id=trace_id,
                group_id=run_id,
            ),
        )
        return result.final_output_as(PlanPacket), trace_id

    def verify(
        self,
        *,
        run_id: str,
        repo_path: Path,
        task_brief: TaskBrief,
        plan_packet: PlanPacket,
        change_report_json: str,
    ) -> tuple[VerificationReport, str]:
        @function_tool
        def run_checks(checks: list[str]) -> list[dict]:
            """Run safe local verification commands in the target repository."""

            results: list[dict] = []
            for command in checks:
                if not _is_safe_command(command):
                    results.append({"command": command, "ok": False, "stdout": "", "stderr": "command not allowed"})
                    continue
                completed = subprocess.run(
                    command,
                    cwd=str(repo_path),
                    shell=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
                results.append(
                    {
                        "command": command,
                        "ok": completed.returncode == 0,
                        "stdout": completed.stdout[-4000:],
                        "stderr": completed.stderr[-4000:],
                    }
                )
            return results

        @function_tool
        def summarize_diff() -> dict:
            """Summarize the current git diff for the target repository."""

            return {
                "changed_files": diff_name_only(repo_path),
                "diff_stat": diff_summary(repo_path),
            }

        agent = Agent(
            name="Bob Verifier",
            handoff_description="Validate code changes against the plan and acceptance criteria.",
            instructions=(
                "You are Bob's Stage 1 verifier. Run the planned checks when they are safe, inspect the git diff, "
                "and decide whether the work is ready for a PR or needs another coding pass."
            ),
            model=self.llm_model,
            model_settings=self.model_settings,
            tools=[run_checks, summarize_diff],
            output_type=VerificationReport,
        )
        trace_id = f"{run_id}-verify"
        prompt = "\n\n".join(
            [
                "Task brief:",
                task_brief.model_dump_json(indent=2),
                "Plan packet:",
                plan_packet.model_dump_json(indent=2),
                "Change report:",
                change_report_json,
                "Use summarize_diff and run_checks(plan_packet.verification_plan) before deciding.",
            ]
        )
        result = Runner.run_sync(
            agent,
            prompt,
            max_turns=8,
            run_config=RunConfig(
                workflow_name="Bob Stage 1 Verification",
                trace_id=trace_id,
                group_id=run_id,
            ),
        )
        return result.final_output_as(VerificationReport), trace_id

    def summarize(
        self,
        *,
        run_id: str,
        task_brief: TaskBrief,
        plan_packet: PlanPacket,
        change_report_json: str,
        verification_report: VerificationReport,
    ) -> tuple[FinalSummary, str]:
        agent = Agent(
            name="Bob Manager",
            handoff_description="Render the final Stage 1 summary and PR-ready handoff.",
            instructions=(
                "You are Bob's Stage 1 manager. Produce a concise final summary with a suggested PR title, "
                "a short PR summary, the changed files, and verification notes. If verification failed, "
                "mark the work as not ready for a PR."
            ),
            model=self.llm_model,
            model_settings=self.model_settings,
            output_type=FinalSummary,
        )
        trace_id = f"{run_id}-summary"
        prompt = "\n\n".join(
            [
                "Task brief:",
                task_brief.model_dump_json(indent=2),
                "Plan packet:",
                plan_packet.model_dump_json(indent=2),
                "Change report:",
                change_report_json,
                "Verification report:",
                verification_report.model_dump_json(indent=2),
            ]
        )
        result = Runner.run_sync(
            agent,
            prompt,
            max_turns=6,
            run_config=RunConfig(
                workflow_name="Bob Stage 1 Summary",
                trace_id=trace_id,
                group_id=run_id,
            ),
        )
        return result.final_output_as(FinalSummary), trace_id


def _is_safe_command(command: str) -> bool:
    normalized = command.strip().lower()
    if any(char in normalized for char in "&|;><`"):
        return False
    return normalized.startswith(SAFE_COMMAND_PREFIXES)
