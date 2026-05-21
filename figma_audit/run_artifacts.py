from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from figma_audit.utils.io import save_json


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StageRecord:
    name: str
    status: str = "pending"
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float | None = None
    output_files: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    _started_perf: float | None = field(default=None, repr=False)

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "output_files": self.output_files,
            "details": self.details,
            "error": self.error,
        }


class RunArtifacts:
    """Persist client-visible run status after every pipeline stage."""

    def __init__(
        self,
        *,
        source_url: str,
        status_path: Path,
        final_result_path: Path,
    ) -> None:
        self.source_url = source_url
        self.status_path = status_path
        self.final_result_path = final_result_path
        self.started_at = utc_now_iso()
        self.status = "running"
        self.error: str | None = None
        self.stages: dict[str, StageRecord] = {}

    def start_stage(self, name: str, *, details: dict[str, Any] | None = None) -> None:
        record = self.stages.get(name, StageRecord(name=name))
        record.status = "running"
        record.started_at = utc_now_iso()
        record.completed_at = None
        record.duration_seconds = None
        record.error = None
        record.details = details or {}
        record._started_perf = perf_counter()
        self.stages[name] = record
        self.write_status()

    def complete_stage(
        self,
        name: str,
        *,
        output_files: list[Path] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        record = self.stages[name]
        record.status = "completed"
        record.completed_at = utc_now_iso()
        if record._started_perf is not None:
            record.duration_seconds = round(perf_counter() - record._started_perf, 3)
        if output_files:
            record.output_files = [str(path) for path in output_files]
        if details:
            record.details.update(details)
        self.write_status()

    def fail_stage(self, name: str, error: str) -> None:
        record = self.stages.get(name, StageRecord(name=name))
        record.status = "failed"
        record.completed_at = utc_now_iso()
        record.error = error
        if record._started_perf is not None:
            record.duration_seconds = round(perf_counter() - record._started_perf, 3)
        self.stages[name] = record
        self.status = "failed"
        self.error = error
        self.write_status()

    def complete_run(self, *, details: dict[str, Any] | None = None) -> None:
        self.status = "completed"
        self.write_status(extra=details or {})

    def write_status(self, *, extra: dict[str, Any] | None = None) -> None:
        payload = {
            "status": self.status,
            "source_url": self.source_url,
            "started_at": self.started_at,
            "updated_at": utc_now_iso(),
            "error": self.error,
            "final_result_path": str(self.final_result_path),
            "stages": [record.public_dict() for record in self.stages.values()],
        }
        if extra:
            payload.update(extra)
        save_json(self.status_path, payload)

