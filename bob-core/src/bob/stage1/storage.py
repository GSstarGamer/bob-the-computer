from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel

from bob.stage1.models import RunLedger


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def default_runs_dir() -> Path:
    return project_root() / "runs"


def generate_run_id() -> str:
    return uuid4().hex[:12]


@dataclass(frozen=True)
class RunPaths:
    runs_dir: Path
    run_dir: Path
    ledger_path: Path


class RunStore:
    def __init__(self, runs_dir: Path | None = None) -> None:
        self._runs_dir = (runs_dir or default_runs_dir()).resolve()

    @property
    def runs_dir(self) -> Path:
        return self._runs_dir

    def create(self, ledger: RunLedger) -> RunPaths:
        self._runs_dir.mkdir(parents=True, exist_ok=True)
        run_dir = self._runs_dir / ledger.run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        paths = RunPaths(self._runs_dir, run_dir, run_dir / "ledger.json")
        self.save_ledger(ledger)
        return paths

    def paths_for(self, run_id: str) -> RunPaths:
        run_dir = self._runs_dir / run_id
        return RunPaths(self._runs_dir, run_dir, run_dir / "ledger.json")

    def save_ledger(self, ledger: RunLedger) -> None:
        paths = self.paths_for(ledger.run_id)
        paths.run_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(paths.ledger_path, ledger)

    def load_ledger(self, run_id: str) -> RunLedger:
        return RunLedger.model_validate_json(self.paths_for(run_id).ledger_path.read_text(encoding="utf-8"))

    def save_model(self, run_id: str, filename: str, model: BaseModel) -> Path:
        path = self.paths_for(run_id).run_dir / filename
        self._write_json(path, model)
        return path

    def load_model(self, run_id: str, filename: str, model_type: type[BaseModel]) -> BaseModel:
        path = self.paths_for(run_id).run_dir / filename
        return model_type.model_validate_json(path.read_text(encoding="utf-8"))

    def save_text(self, run_id: str, filename: str, content: str) -> Path:
        path = self.paths_for(run_id).run_dir / filename
        path.write_text(content, encoding="utf-8")
        return path

    @staticmethod
    def _write_json(path: Path, model: BaseModel) -> None:
        path.write_text(model.model_dump_json(indent=2), encoding="utf-8")


def render_markdown_list(items: list[str]) -> str:
    if not items:
        return "- None"
    return "\n".join(f"- {item}" for item in items)
