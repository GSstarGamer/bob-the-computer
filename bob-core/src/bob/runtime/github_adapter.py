from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Protocol

from bob.runtime.models import IssueComment, IssueContext


class GitHubIssueReader(Protocol):
    def get_issue(self, repo: str, issue_number: int) -> IssueContext: ...

    def get_issue_comments(self, repo: str, issue_number: int) -> list[IssueComment]: ...


class GitHubAdapterError(RuntimeError):
    """Raised when GitHub issue context cannot be loaded."""


class GhCliGitHubIssueReader:
    def __init__(self, executable: str = "gh") -> None:
        self.executable = executable

    def get_issue(self, repo: str, issue_number: int) -> IssueContext:
        payload = self._run_json(
            [
                self.executable,
                "issue",
                "view",
                str(issue_number),
                "--repo",
                repo,
                "--json",
                "number,title,body,url,state,author,labels",
            ]
        )
        return IssueContext(
            number=payload["number"],
            title=payload["title"],
            body=payload.get("body") or "",
            url=payload.get("url"),
            state=payload.get("state"),
            author=(payload.get("author") or {}).get("login"),
            labels=[label["name"] for label in payload.get("labels") or [] if label.get("name")],
        )

    def get_issue_comments(self, repo: str, issue_number: int) -> list[IssueComment]:
        payload = self._run_json(
            [self.executable, "api", f"repos/{repo}/issues/{issue_number}/comments", "--paginate"]
        )
        return [
            IssueComment(
                author=(comment.get("user") or {}).get("login"),
                body=comment.get("body") or "",
                created_at=comment.get("created_at"),
                url=comment.get("html_url"),
            )
            for comment in payload
        ]

    def _run_json(self, command: list[str]) -> dict | list[dict]:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            cwd=Path.cwd(),
        )
        if completed.returncode != 0:
            raise GitHubAdapterError(completed.stderr.strip() or completed.stdout.strip() or "gh command failed")
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise GitHubAdapterError(f"Failed to parse GitHub JSON output: {exc}") from exc
