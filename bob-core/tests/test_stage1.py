from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bob.stage1.codex_adapter import CodexCliClient
from bob.stage1.models import (
    ChangeReport,
    FinalSummary,
    GitSnapshot,
    PlanPacket,
    ResearchPacket,
    RunLedger,
    Stage1Input,
    Stage1Status,
    TaskBrief,
    VerificationReport,
)
from bob.stage1.orchestrator import Stage1Orchestrator
from bob.stage1.storage import RunStore


class FakeGitHubReader:
    def get_issue(self, repo: str, issue_number: int):  # pragma: no cover - not used in task-mode tests
        raise AssertionError("Issue lookup should not be called in task mode")

    def get_issue_comments(self, repo: str, issue_number: int):  # pragma: no cover - not used in task mode tests
        raise AssertionError("Issue comments should not be called in task mode")


class FakeAgentsClient:
    llm_model = "gpt-5.4-mini"

    def research(
        self,
        *,
        run_id: str,
        task_brief: TaskBrief,
        repo_snapshot_text: str,
        github_reader,
        codex_client,
    ):
        packet = ResearchPacket(
            summary="Investigated the repo and narrowed the likely edit surface.",
            likely_files=["README.md"],
            repo_findings=["The repo is intentionally minimal in this test fixture."],
            constraints=["Keep the change scoped to one file."],
            candidate_checks=["python -m pytest"],
            risks=["Very small fixture coverage."],
            codex_summary="Codex confirmed the file choice from the snapshot.",
        )
        return packet, f"{run_id}-research-trace", "codex-research-session", "# Codex Research\n\nFixture research.\n"

    def plan(self, *, run_id: str, task_brief: TaskBrief, research_packet: ResearchPacket):
        packet = PlanPacket(
            summary="Update the README with the requested line.",
            implementation_steps=["Append a short line to README.md."],
            acceptance_criteria=["README.md contains the approved Stage 1 note."],
            verification_plan=["python -m pytest"],
            risks=["No extra risks in the fixture."],
        )
        return packet, f"{run_id}-plan-trace"

    def verify(
        self,
        *,
        run_id: str,
        repo_path: Path,
        task_brief: TaskBrief,
        plan_packet: PlanPacket,
        change_report_json: str,
    ):
        packet = VerificationReport(
            passed=True,
            summary="Verification passed.",
            checks_run=["python -m pytest"],
            warnings=[],
            diff_summary=["README.md | 1 +"],
            retry_guidance=[],
        )
        return packet, f"{run_id}-verify-trace"

    def summarize(
        self,
        *,
        run_id: str,
        task_brief: TaskBrief,
        plan_packet: PlanPacket,
        change_report_json: str,
        verification_report: VerificationReport,
    ):
        packet = FinalSummary(
            ready_for_pr=True,
            overview="The requested README change is complete and verified.",
            pr_title="docs: add stage 1 note",
            pr_summary="Adds the approved Stage 1 note to README.md.",
            changed_files=["README.md"],
            verification_notes=["python -m pytest passed"],
            follow_ups=[],
        )
        return packet, f"{run_id}-summary-trace"


class FakeCodexClient:
    provider = "openai"
    research_model = "gpt-5.4-mini"
    write_model = "gpt-5.4-mini"

    def run_research(self, task_brief: TaskBrief, repo_snapshot_text: str):  # pragma: no cover - handled by FakeAgentsClient
        raise AssertionError("Research should be routed through the fake Agents client")

    def run_write(self, work_order):
        readme_path = Path(work_order.repo_path) / "README.md"
        readme_path.write_text(readme_path.read_text(encoding="utf-8") + "\nApproved Stage 1 note.\n", encoding="utf-8")
        return ChangeReport(
            summary="Appended the approved note to README.md.",
            changed_files=["README.md"],
            checks_run=["python -m pytest"],
            follow_up=[],
            notes=["The change is intentionally tiny for the fixture."],
            codex_session_id="codex-write-session",
        )


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
        agents_client=FakeAgentsClient(),
        github_reader=FakeGitHubReader(),
        codex_client=FakeCodexClient(),
    )


def test_start_creates_run_and_waits_for_write_approval(tmp_path: Path) -> None:
    repo_path = make_repo(tmp_path)
    orchestrator = build_orchestrator(tmp_path)

    ledger, run_dir = orchestrator.start(
        Stage1Input(repo="owner/repo", repo_path=str(repo_path), task="Add the approved Stage 1 note to the README.")
    )

    assert ledger.status == Stage1Status.AWAITING_WRITE_APPROVAL
    assert (run_dir / "task_brief.json").exists()
    assert (run_dir / "research_packet.json").exists()
    assert (run_dir / "plan_packet.json").exists()
    assert (run_dir / "codex_research.md").exists()
    assert ledger.model_settings["codex_provider"] == "openai"
    assert ledger.model_settings["codex_write_model"] == "gpt-5.4-mini"


def test_resume_runs_coding_verification_and_summary(tmp_path: Path) -> None:
    repo_path = make_repo(tmp_path)
    orchestrator = build_orchestrator(tmp_path)
    started_ledger, run_dir = orchestrator.start(
        Stage1Input(repo="owner/repo", repo_path=str(repo_path), task="Add the approved Stage 1 note to the README.")
    )

    resumed_ledger, _ = orchestrator.resume(run_id=started_ledger.run_id, approve_write=True)

    assert resumed_ledger.status == Stage1Status.AWAITING_PUBLISH_APPROVAL
    assert resumed_ledger.approvals.write_approved is True
    assert (run_dir / "codex_change_report.json").exists()
    assert (run_dir / "verification_report.json").exists()
    assert (run_dir / "final_summary.md").exists()
    assert "Approved Stage 1 note." in (repo_path / "README.md").read_text(encoding="utf-8")


def test_dirty_repo_fails_before_research(tmp_path: Path) -> None:
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


def test_codex_cli_defaults_are_explicit_and_credit_safe() -> None:
    client = CodexCliClient()
    assert client.provider == "openai"
    assert client.research_model == "gpt-5.4-mini"
    assert client.write_model == "gpt-5.4-mini"
