from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from bob.stage1.models import ChangeReport, CodexWorkOrder, ResearchPacket, TaskBrief


class CodexClient(Protocol):
    def run_research(self, task_brief: TaskBrief, repo_snapshot_text: str) -> tuple[ResearchPacket, str, str | None]:
        ...

    def run_write(self, work_order: CodexWorkOrder) -> ChangeReport: ...


class CodexAdapterError(RuntimeError):
    """Raised when Bob cannot delegate work to Codex."""


class CodexCliClient:
    def __init__(
        self,
        executable: str = "codex",
        provider: str = "openai",
        research_model: str = "gpt-5.4-mini",
        write_model: str = "gpt-5.4-mini",
        research_timeout_seconds: int = 300,
        write_timeout_seconds: int = 1800,
    ) -> None:
        self.executable = executable
        self.provider = provider
        self.research_model = research_model
        self.write_model = write_model
        self.research_timeout_seconds = research_timeout_seconds
        self.write_timeout_seconds = write_timeout_seconds

    def run_research(self, task_brief: TaskBrief, repo_snapshot_text: str) -> tuple[ResearchPacket, str, str | None]:
        schema = json.dumps(ResearchPacket.model_json_schema(), indent=2)
        prompt = "\n".join(
            [
                "You are Codex helping Bob Stage 1 research a single implementation task.",
                "Use only the task brief and repo snapshot below.",
                "Do not call shell tools, do not edit files, and do not assume hidden repository context.",
                "Return only valid JSON matching this schema:",
                schema,
                "",
                "Task brief:",
                task_brief.model_dump_json(indent=2),
                "",
                "Repo snapshot:",
                repo_snapshot_text,
            ]
        )
        final_text, session_id = self._invoke(
            prompt=prompt,
            model=self.research_model,
            cwd=Path(task_brief.repo_path),
            timeout_seconds=self.research_timeout_seconds,
            dangerous=False,
        )
        packet = self._parse_output(final_text, ResearchPacket)
        return packet, self._render_research_markdown(packet), session_id

    def run_write(self, work_order: CodexWorkOrder) -> ChangeReport:
        schema = json.dumps(ChangeReport.model_json_schema(), indent=2)
        prompt = "\n".join(
            [
                "You are Codex implementing a bounded Stage 1 Bob work order.",
                "Work only inside the current repository.",
                "You may inspect files, edit code, and run local verification commands when helpful.",
                "Forbidden actions:",
                *[f"- {item}" for item in work_order.forbidden_actions],
                "",
                "Return only valid JSON matching this schema:",
                schema,
                "",
                "Work order:",
                work_order.model_dump_json(indent=2),
            ]
        )
        final_text, session_id = self._invoke(
            prompt=prompt,
            model=self.write_model,
            cwd=Path(work_order.repo_path),
            timeout_seconds=self.write_timeout_seconds,
            dangerous=True,
        )
        report = self._parse_output(final_text, ChangeReport)
        if not report.codex_session_id and session_id:
            report.codex_session_id = session_id
        return report

    def _invoke(
        self,
        *,
        prompt: str,
        model: str,
        cwd: Path,
        timeout_seconds: int,
        dangerous: bool,
    ) -> tuple[str, str | None]:
        command = [
            self.executable,
            "-q",
            "--provider",
            self.provider,
            "--model",
            model,
        ]
        if dangerous:
            command.append("--dangerously-auto-approve-everything")
        command.append(prompt)
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            env=_sanitized_env(),
        )
        if completed.returncode != 0:
            raise CodexAdapterError(completed.stderr.strip() or completed.stdout.strip() or "codex failed")
        return _extract_assistant_text(completed.stdout)

    def _parse_output(self, output_text: str, model_type: type[BaseModel]) -> BaseModel:
        payload = _extract_json(output_text)
        try:
            return model_type.model_validate(payload)
        except ValidationError as exc:
            raise CodexAdapterError(f"Codex returned invalid {model_type.__name__}: {exc}") from exc

    @staticmethod
    def _render_research_markdown(packet: ResearchPacket) -> str:
        sections = [
            "# Codex Research",
            "",
            packet.summary,
            "",
            "## Likely Files",
            *([f"- {item}" for item in packet.likely_files] or ["- None"]),
            "",
            "## Repo Findings",
            *([f"- {item}" for item in packet.repo_findings] or ["- None"]),
            "",
            "## Constraints",
            *([f"- {item}" for item in packet.constraints] or ["- None"]),
            "",
            "## Candidate Checks",
            *([f"- {item}" for item in packet.candidate_checks] or ["- None"]),
            "",
            "## Risks",
            *([f"- {item}" for item in packet.risks] or ["- None"]),
        ]
        return "\n".join(sections)


def _extract_assistant_text(stdout: str) -> tuple[str, str | None]:
    session_id = None
    assistant_text = None
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "message" and event.get("role") == "assistant":
            session_id = event.get("id") or session_id
            chunks = []
            for item in event.get("content") or []:
                if item.get("type") == "output_text":
                    chunks.append(item.get("text", ""))
            assistant_text = "\n".join(chunk for chunk in chunks if chunk).strip()
    if not assistant_text:
        raise CodexAdapterError("Codex did not return a final assistant message.")
    return assistant_text, session_id


def _extract_json(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise CodexAdapterError("Codex response did not contain valid JSON.")
        return json.loads(stripped[start : end + 1])


def _sanitized_env() -> dict[str, str]:
    allowed_keys = {
        "APPDATA",
        "CODEX_HOME",
        "COMSPEC",
        "HOME",
        "LOCALAPPDATA",
        "OPENAI_API_KEY",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
    }
    return {key: value for key, value in os.environ.items() if key in allowed_keys}
