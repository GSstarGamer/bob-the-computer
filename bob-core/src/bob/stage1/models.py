from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Stage1Status(str, Enum):
    INTAKE = "INTAKE"
    RESEARCHING = "RESEARCHING"
    PLANNING = "PLANNING"
    AWAITING_WRITE_APPROVAL = "AWAITING_WRITE_APPROVAL"
    CODING = "CODING"
    VERIFYING = "VERIFYING"
    AWAITING_PUBLISH_APPROVAL = "AWAITING_PUBLISH_APPROVAL"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


class InputMode(str, Enum):
    ISSUE = "issue"
    TASK = "task"


class GitRemote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    url: str


class GitSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_root: str
    branch: str
    head_sha: str
    is_dirty: bool
    status_lines: list[str] = Field(default_factory=list)
    remotes: list[GitRemote] = Field(default_factory=list)
    top_level_entries: list[str] = Field(default_factory=list)
    tracked_file_count: int = 0
    tracked_files_sample: list[str] = Field(default_factory=list)


class IssueContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int
    title: str
    body: str
    url: str | None = None
    state: str | None = None
    author: str | None = None
    labels: list[str] = Field(default_factory=list)


class IssueComment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    author: str | None = None
    body: str
    created_at: str | None = None
    url: str | None = None


class Stage1Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str
    repo_path: str
    issue_number: int | None = None
    task: str | None = None

    @model_validator(mode="after")
    def validate_input(self) -> "Stage1Input":
        if bool(self.issue_number) == bool(self.task):
            raise ValueError("Exactly one of issue_number or task must be provided.")
        return self

    @property
    def input_mode(self) -> InputMode:
        return InputMode.ISSUE if self.issue_number is not None else InputMode.TASK


class TaskBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    repo: str
    repo_path: str
    input_mode: InputMode
    title: str
    summary: str
    task_text: str | None = None
    issue: IssueContext | None = None
    issue_comments: list[IssueComment] = Field(default_factory=list)
    repo_snapshot: GitSnapshot


class ResearchPacket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    likely_files: list[str] = Field(default_factory=list)
    repo_findings: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    candidate_checks: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    codex_summary: str | None = None


class PlanPacket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    implementation_steps: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    verification_plan: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class CodexWorkOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    repo: str
    repo_path: str
    objective: str
    plan_summary: str
    implementation_steps: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    verification_plan: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)


class ChangeReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    changed_files: list[str] = Field(default_factory=list)
    checks_run: list[str] = Field(default_factory=list)
    follow_up: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    codex_session_id: str | None = None


class VerificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    summary: str
    checks_run: list[str] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    diff_summary: list[str] = Field(default_factory=list)
    retry_guidance: list[str] = Field(default_factory=list)


class FinalSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ready_for_pr: bool
    overview: str
    pr_title: str
    pr_summary: str
    changed_files: list[str] = Field(default_factory=list)
    verification_notes: list[str] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)


class ApprovalState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    write_approved: bool = False
    write_approved_at: datetime | None = None
    publish_approved: bool = False
    publish_approved_at: datetime | None = None


class ArtifactIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_brief: str = "task_brief.json"
    research_packet: str = "research_packet.json"
    plan_packet: str = "plan_packet.json"
    codex_research: str = "codex_research.md"
    codex_change_report: str = "codex_change_report.json"
    verification_report: str = "verification_report.json"
    final_summary: str = "final_summary.md"


class RunLedger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    repo: str
    repo_path: str
    input_mode: InputMode
    status: Stage1Status
    approvals: ApprovalState = Field(default_factory=ApprovalState)
    attempts: dict[str, int] = Field(
        default_factory=lambda: {"research": 0, "planning": 0, "coding": 0, "verification": 0}
    )
    git_snapshot: GitSnapshot | None = None
    artifacts: ArtifactIndex = Field(default_factory=ArtifactIndex)
    model_settings: dict[str, str] = Field(default_factory=dict)
    trace_ids: dict[str, str] = Field(default_factory=dict)
    codex_session_ids: dict[str, str] = Field(default_factory=dict)
    latest_error: str | None = None

    def mark_status(self, status: Stage1Status, latest_error: str | None = None) -> None:
        self.status = status
        self.updated_at = utc_now()
        self.latest_error = latest_error

    def increment_attempt(self, step: str) -> None:
        self.attempts[step] = self.attempts.get(step, 0) + 1
        self.updated_at = utc_now()
