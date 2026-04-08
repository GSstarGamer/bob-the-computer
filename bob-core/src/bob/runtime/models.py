from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def clamp_score(value: float) -> float:
    return max(0.0, min(1.0, value))


class SessionStatus(str, Enum):
    INTAKE = "INTAKE"
    SCORING = "SCORING"
    PLANNING = "PLANNING"
    READY_FOR_EXECUTION = "READY_FOR_EXECUTION"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


class InputMode(str, Enum):
    TASK = "task"
    ISSUE = "issue"
    CANDIDATE_FILE = "candidate_file"


class CandidateSourceType(str, Enum):
    TASK_INPUT = "task_input"
    GITHUB_ISSUE = "github_issue"
    CANDIDATE_FILE = "candidate_file"


class LLMProvider(str, Enum):
    OPENAI = "openai"
    OPENAI_COMPATIBLE = "openai_compatible"


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


class BobInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str
    repo_path: str
    issue_number: int | None = None
    task: str | None = None
    candidate_file: str | None = None

    @model_validator(mode="after")
    def validate_input(self) -> "BobInput":
        provided = [bool(self.issue_number), bool(self.task), bool(self.candidate_file)]
        if sum(provided) != 1:
            raise ValueError("Exactly one of issue_number, task, or candidate_file must be provided.")
        return self

    @property
    def input_mode(self) -> InputMode:
        if self.issue_number is not None:
            return InputMode.ISSUE
        if self.candidate_file:
            return InputMode.CANDIDATE_FILE
        return InputMode.TASK


class CandidateTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    source_type: CandidateSourceType
    source_id: str
    title: str
    summary: str
    details: str
    repo_path: str
    repo_root: str | None = None
    issue: IssueContext | None = None
    issue_comments: list[IssueComment] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)


class TaskEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: CandidateTask
    usefulness: float
    simplicity: float
    feasibility: float
    implementation_risk: float
    value: float
    evaluator_summary: str
    blockers: list[str] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)
    is_viable: bool = False
    weighted_total_score: float = 0.0
    normalization_warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_scores(self) -> "TaskEvaluation":
        self.usefulness = clamp_score(self.usefulness)
        self.simplicity = clamp_score(self.simplicity)
        self.feasibility = clamp_score(self.feasibility)
        self.implementation_risk = clamp_score(self.implementation_risk)
        self.value = clamp_score(self.value)
        self.weighted_total_score = clamp_score(self.weighted_total_score)
        return self


class SelectionWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    usefulness: float = 0.30
    feasibility: float = 0.25
    simplicity: float = 0.15
    value: float = 0.20
    inverse_risk: float = 0.10


class SelectionThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_usefulness: float = 0.45
    minimum_feasibility: float = 0.45
    maximum_implementation_risk: float = 0.70
    minimum_weighted_total: float = 0.55


class SelectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_candidate_id: str | None = None
    selected_task: CandidateTask | None = None
    ranked_candidate_ids: list[str] = Field(default_factory=list)
    rejected_candidate_ids: list[str] = Field(default_factory=list)
    selection_reason: str
    weights: SelectionWeights = Field(default_factory=SelectionWeights)
    thresholds: SelectionThresholds = Field(default_factory=SelectionThresholds)


class ImplementationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    source_id: str
    source_type: CandidateSourceType
    title: str
    short_summary: str
    why_it_is_useful: str
    scope: str
    difficulty: str
    estimated_implementation_size: str
    dependencies: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    edge_cases: list[str] = Field(default_factory=list)
    implementation_steps: list[str] = Field(default_factory=list)
    recommended_files_or_modules: list[str] = Field(default_factory=list)
    confidence: float
    priority_score: float

    @model_validator(mode="after")
    def validate_scores(self) -> "ImplementationPlan":
        self.confidence = clamp_score(self.confidence)
        self.priority_score = clamp_score(self.priority_score)
        return self


class PlannerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: ImplementationPlan
    model: str
    response_id: str | None = None
    prompt_version: str
    normalization_warnings: list[str] = Field(default_factory=list)
    raw_output_text: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class CandidateTaskCollection(RootModel[list[CandidateTask]]):
    pass


class TaskEvaluationCollection(RootModel[list[TaskEvaluation]]):
    pass


class ArtifactIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_tasks: str = "candidate_tasks.json"
    task_evaluations: str = "task_evaluations.json"
    selection_result: str = "selection_result.json"
    planner_prompt: str = "planner_prompt.md"
    planner_response: str = "planner_response.json"
    planner_result: str = "planner_result.json"


class SessionLedger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    repo: str
    repo_path: str
    input_mode: InputMode
    status: SessionStatus
    git_snapshot: GitSnapshot | None = None
    artifacts: ArtifactIndex = Field(default_factory=ArtifactIndex)
    model_settings: dict[str, str] = Field(default_factory=dict)
    response_ids: dict[str, str] = Field(default_factory=dict)
    latest_error: str | None = None

    def mark_status(self, status: SessionStatus, latest_error: str | None = None) -> None:
        self.status = status
        self.updated_at = utc_now()
        self.latest_error = latest_error


class SessionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    status: SessionStatus
    session_dir: str
    selected_task: CandidateTask | None = None
    evaluations: list[TaskEvaluation] = Field(default_factory=list)
    selection_result: SelectionResult | None = None
    planner_result: PlannerResult | None = None
    model: str | None = None


class LLMEndpointConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: LLMProvider
    model: str
    base_url: str | None = None
    api_key: str | None = None
    api_key_env: str | None = None
    timeout_seconds: int
    max_retries: int

    @model_validator(mode="after")
    def validate_endpoint(self) -> "LLMEndpointConfig":
        if self.provider == LLMProvider.OPENAI_COMPATIBLE and not self.base_url:
            raise ValueError("base_url is required when provider is openai_compatible.")
        if self.provider == LLMProvider.OPENAI and not self.api_key:
            missing = self.api_key_env or "OPENAI_API_KEY"
            raise ValueError(f"Missing API key for LLM endpoint. Set {missing}.")
        return self
