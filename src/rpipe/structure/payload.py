"""Invocation context and finite payload contract, without backend selection."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import subprocess
from typing import Any

from .contracts import AdmissionError, Cancellation, RunSpec


@dataclass
class PayloadContext:
    spec: RunSpec
    cancellation: Cancellation
    layout: Any
    state: dict[str, Any] = field(default_factory=dict)
    control: Any = None
    processes: list[subprocess.Popen] = field(default_factory=list)

    def __post_init__(self):
        self.config = self.spec.to_config()

    @property
    def study_dir(self) -> Path:
        return self.spec.source_dir

    @property
    def scope(self) -> str:
        return 'run'

    def register_process(self, process: subprocess.Popen) -> None:
        if not isinstance(process, subprocess.Popen):
            raise AdmissionError('only a process handle created by this execution may be registered')
        self.processes.append(process)


def validate_payload(payload: Any) -> None:
    for name in ('prepare', 'execute', 'collect', 'close'):
        if not callable(getattr(payload, name, None)):
            raise AdmissionError(f'payload must provide {name}(ctx)')
