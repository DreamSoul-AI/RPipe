"""In-memory execution requests and contexts; persistent records belong to artifact."""
from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any, TypeAlias

from rpipe.structure.control import AdmissionError, RunSpec, SourceMismatch

ResultDraft: TypeAlias = dict[str, Any]


@dataclass(frozen=True)
class Budget:
    timeout_seconds: float | None = None
    memory_bytes: int | None = None

    def validate(self) -> None:
        for name, value in vars(self).items():
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not math.isfinite(value) or value <= 0):
                raise AdmissionError(f'{name} must be finite and positive')
        if self.memory_bytes is not None and not isinstance(self.memory_bytes, int):
            raise AdmissionError('memory_bytes must be an integer')


@dataclass(frozen=True)
class SupervisionLimits:
    run_seconds: float
    batch_seconds: float
    rss_bytes: int
    output_bytes: int
    poll_seconds: float = 0.1
    cleanup_seconds: float = 2.0

    def validate(self) -> None:
        for name, value in vars(self).items():
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value <= 0):
                raise AdmissionError(f'{name} must be finite and positive')
        for name in ('rss_bytes', 'output_bytes'):
            if not isinstance(getattr(self, name), int):
                raise AdmissionError(f'{name} must be an integer')


class Cancellation:
    def __init__(self) -> None:
        self._event = Event()

    def request(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    @property
    def requested(self) -> bool:
        return self.is_set()


class Cancelled(Exception):
    pass


@dataclass
class RunReport:
    status: str
    quality: bool | None = None
    evidence_complete: bool = False
    result: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    cleanup_errors: tuple[dict[str, Any], ...] = ()
    cancelled: bool = False
    attempt: str | None = None
    scientific_status: str = 'missing'
    derived_status: str = 'not_run'


def _validate_config(ctx, spec: RunSpec | None, when: str) -> None:
    expected = (spec or ctx.spec).to_config()
    try:
        encoded = json.dumps(expected, sort_keys=True, allow_nan=False)
        unchanged = (json.dumps(ctx.config, sort_keys=True, allow_nan=False) == encoded
                     and (ctx.control is None
                          or json.dumps(ctx.control.to_dict(), sort_keys=True, allow_nan=False) == encoded))
    except (TypeError, ValueError, OverflowError):
        unchanged = False
    if not unchanged:
        raise SourceMismatch(f'effective configuration changed {when}')


@dataclass
class RunContext:
    spec: RunSpec
    cancellation: Cancellation
    layout: Any
    owner: Any = None
    state: dict[str, Any] = field(default_factory=dict)
    control: Any = None
    config: dict[str, Any] = field(init=False)

    def __post_init__(self) -> None:
        self.config = self.spec.to_config()

    @property
    def study_dir(self) -> Path:
        return self.spec.source_dir

    @property
    def scope(self) -> str:
        return 'run'

    def validate_config(self, spec: RunSpec | None = None, when: str = 'after admission') -> None:
        _validate_config(self, spec, when)


@dataclass
class ScienceContext:
    spec: RunSpec
    cancellation: Cancellation
    layout: Any
    state: dict[str, Any] = field(default_factory=dict)
    control: Any = None
    resources: list[Any] = field(default_factory=list)
    processes: list[subprocess.Popen] = field(default_factory=list)
    config: dict[str, Any] = field(init=False)

    def __post_init__(self) -> None:
        self.config = self.spec.to_config()

    @property
    def study_dir(self) -> Path:
        return self.spec.source_dir

    @property
    def scope(self) -> str:
        return 'run'

    def validate_config(self, spec: RunSpec | None = None, when: str = 'after admission') -> None:
        _validate_config(self, spec, when)

    def register_resource(self, resource: Any) -> Any:
        if (callable(getattr(resource, 'close', None))
                and not any(item is resource for item in self.resources)):
            self.resources.append(resource)
        return resource

    def register_process(self, process: subprocess.Popen) -> None:
        if not isinstance(process, subprocess.Popen):
            raise AdmissionError('only an owned process handle may be registered')
        if not any(item is process for item in self.processes):
            self.processes.append(process)


@dataclass
class StudyProcessContext:
    study_dir: Path
    layout: Any
    specs: tuple[RunSpec, ...] = ()
    records: tuple[dict[str, Any], ...] = ()
    target: Path | None = None
    scope: str = 'study'
    state: dict[str, Any] = field(default_factory=dict)


def validate_draft(value: Any) -> ResultDraft:
    if not isinstance(value, dict) or not isinstance(value.get('metrics'), dict):
        raise AdmissionError('ResultDraft must contain a metrics mapping')
    if value.get('quality') is not None and type(value['quality']) is not bool:
        raise AdmissionError('ResultDraft quality must be bool or null')
    if 'evidence_complete' in value and type(value['evidence_complete']) is not bool:
        raise AdmissionError('ResultDraft evidence_complete must be bool')
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as error:
        raise AdmissionError('ResultDraft must contain finite JSON values') from error

    def keys(item):
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise AdmissionError('ResultDraft object keys must be strings')
            for child in item.values():
                keys(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                keys(child)

    keys(value)
    return value
