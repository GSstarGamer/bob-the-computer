from __future__ import annotations

import subprocess
from pathlib import Path

from bob.runtime.models import GitRemote, GitSnapshot

KEY_FILE_CANDIDATES = (
    "README.md",
    "pyproject.toml",
    "package.json",
    "requirements.txt",
    "Makefile",
    "Cargo.toml",
    "go.mod",
)


class RepoInspectionError(RuntimeError):
    """Raised when Bob cannot inspect the target repository."""


def _run_git(repo_path: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and completed.returncode != 0:
        raise RepoInspectionError(completed.stderr.strip() or completed.stdout.strip() or "git command failed")
    return completed.stdout.strip()


def snapshot_repo(repo_path: Path) -> GitSnapshot:
    if not repo_path.exists():
        raise RepoInspectionError(f"Repository path does not exist: {repo_path}")
    if not repo_path.is_dir():
        raise RepoInspectionError(f"Repository path is not a directory: {repo_path}")

    repo_root = Path(_run_git(repo_path, "rev-parse", "--show-toplevel"))
    branch = _run_git(repo_root, "branch", "--show-current")
    head_sha = _run_git(repo_root, "rev-parse", "HEAD")
    status_lines = [line for line in _run_git(repo_root, "status", "--short").splitlines() if line.strip()]
    remotes: list[GitRemote] = []
    seen_remote_names: set[str] = set()
    for line in _run_git(repo_root, "remote", "-v").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] not in seen_remote_names:
            remotes.append(GitRemote(name=parts[0], url=parts[1]))
            seen_remote_names.add(parts[0])
    tracked_files = [line for line in _run_git(repo_root, "ls-files").splitlines() if line.strip()]
    top_level_entries = sorted(path.name for path in repo_root.iterdir())
    return GitSnapshot(
        repo_root=str(repo_root),
        branch=branch,
        head_sha=head_sha,
        is_dirty=bool(status_lines),
        status_lines=status_lines,
        remotes=remotes,
        top_level_entries=top_level_entries,
        tracked_file_count=len(tracked_files),
        tracked_files_sample=tracked_files[:200],
    )


def build_repo_snapshot_text(snapshot: GitSnapshot) -> str:
    lines = [
        f"Repo root: {snapshot.repo_root}",
        f"Branch: {snapshot.branch or '(detached)'}",
        f"HEAD: {snapshot.head_sha}",
        f"Dirty: {'yes' if snapshot.is_dirty else 'no'}",
        "Top-level entries:",
        *[f"- {entry}" for entry in snapshot.top_level_entries],
        f"Tracked file count: {snapshot.tracked_file_count}",
        "Tracked file sample:",
        *[f"- {entry}" for entry in snapshot.tracked_files_sample[:100]],
    ]
    previews: list[str] = []
    repo_root = Path(snapshot.repo_root)
    for candidate in KEY_FILE_CANDIDATES:
        candidate_path = repo_root / candidate
        if candidate_path.exists() and candidate_path.is_file():
            previews.append(_preview_file(candidate_path))
    if previews:
        lines.append("")
        lines.append("Key file previews:")
        lines.extend(previews)
    return "\n".join(lines)


def _preview_file(path: Path, limit: int = 2000) -> str:
    content = path.read_text(encoding="utf-8", errors="replace")
    excerpt = content[:limit].strip()
    return f"\n## {path.name}\n{excerpt}\n"
