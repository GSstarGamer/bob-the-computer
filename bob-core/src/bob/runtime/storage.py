from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

from pydantic import BaseModel

from bob.runtime.models import SessionLedger

ModelT = TypeVar("ModelT", bound=BaseModel)


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def default_runs_dir() -> Path:
    return project_root() / "runs"


def generate_session_id() -> str:
    return uuid4().hex[:12]


@dataclass(frozen=True)
class SessionPaths:
    runs_dir: Path
    session_dir: Path
    ledger_path: Path


class SessionStore:
    def __init__(self, runs_dir: Path | None = None) -> None:
        self._runs_dir = (runs_dir or default_runs_dir()).resolve()

    @property
    def runs_dir(self) -> Path:
        return self._runs_dir

    def create(self, ledger: SessionLedger) -> SessionPaths:
        self._runs_dir.mkdir(parents=True, exist_ok=True)
        session_dir = self._runs_dir / ledger.session_id
        session_dir.mkdir(parents=True, exist_ok=False)
        paths = SessionPaths(self._runs_dir, session_dir, session_dir / "ledger.json")
        self.save_ledger(ledger)
        return paths

    def paths_for(self, session_id: str) -> SessionPaths:
        session_dir = self._runs_dir / session_id
        return SessionPaths(self._runs_dir, session_dir, session_dir / "ledger.json")

    def load_ledger(self, session_id: str) -> SessionLedger:
        return SessionLedger.model_validate_json(self.paths_for(session_id).ledger_path.read_text(encoding="utf-8"))

    def save_ledger(self, ledger: SessionLedger) -> None:
        paths = self.paths_for(ledger.session_id)
        paths.session_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(paths.ledger_path, ledger)

    def save_model(self, session_id: str, filename: str, model: BaseModel) -> Path:
        path = self.paths_for(session_id).session_dir / filename
        self._write_json(path, model)
        return path

    def load_model(self, session_id: str, filename: str, model_type: type[ModelT]) -> ModelT:
        path = self.paths_for(session_id).session_dir / filename
        return model_type.model_validate_json(path.read_text(encoding="utf-8"))

    def load_optional_model(self, session_id: str, filename: str, model_type: type[ModelT]) -> ModelT | None:
        path = self.paths_for(session_id).session_dir / filename
        if not path.exists():
            return None
        return model_type.model_validate_json(path.read_text(encoding="utf-8"))

    def save_text(self, session_id: str, filename: str, content: str) -> Path:
        path = self.paths_for(session_id).session_dir / filename
        path.write_text(content, encoding="utf-8")
        return path

    def save_json(self, session_id: str, filename: str, payload: dict[str, Any] | list[Any]) -> Path:
        path = self.paths_for(session_id).session_dir / filename
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    @staticmethod
    def _write_json(path: Path, model: BaseModel) -> None:
        path.write_text(model.model_dump_json(indent=2), encoding="utf-8")
