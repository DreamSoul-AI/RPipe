"""Execution request and observation contracts; configuration lives in control."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from threading import Event
from typing import Any

from . import control as _control


@dataclass(frozen=True)
class Budget:
    """Hard limits. An unsupported limit is rejected before execution."""

    timeout_seconds: float | None = None
    memory_bytes: int | None = None

    def validate(self) -> None:
        for name, value in vars(self).items():
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not math.isfinite(value) or value <= 0):
                raise _control.AdmissionError(f'{name} must be finite and positive')
        if self.memory_bytes is not None and not isinstance(self.memory_bytes, int):
            raise _control.AdmissionError('memory_bytes must be an integer')


@dataclass(frozen=True)
class SupervisionLimits:
    """Explicit wall deadlines and observed RSS/output stop thresholds."""

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
                raise _control.AdmissionError(f'{name} must be finite and positive')
        for name in ('rss_bytes', 'output_bytes'):
            if not isinstance(getattr(self, name), int):
                raise _control.AdmissionError(f'{name} must be an integer')


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



@dataclass
class RunOutcome:
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


def project_execution(record: dict, result: dict, *, busy: bool, complete: bool = True) -> dict:
    """Project one fixed attempt; callers decide whether its owner is still held."""
    status = record.get('candidate_status')
    valid = (type(record.get('schema')) is int and record['schema'] == 1 and record.get('phase') == 'closing'
             and isinstance(status, str) and status in {'succeeded', 'failed', 'cancelled', 'unknown'}
             and isinstance(record.get('evidence_complete'), bool)
             and (record.get('quality') is None or isinstance(record['quality'], bool))
             and isinstance(record.get('scientific_result_published'), bool)
             and isinstance(record.get('cancelled'), bool)
             and isinstance(record.get('cleanup_errors'), list)
             and (record.get('error') is None or isinstance(record['error'], dict)))
    published = record.get('scientific_result_published') is True
    if published:
        valid = valid and (result.get('status') == 'succeeded'
                           and result.get('identity') == record.get('identity')
                           and result.get('attempt') == record.get('attempt')
                           and isinstance(result.get('evidence_complete'), bool)
                           and result.get('quality') == record.get('quality'))
    elif result:
        valid = False
    if status == 'succeeded' and not published:
        valid = False
    if status == 'succeeded' and (record.get('error') or record.get('cleanup_errors')
                                  or record.get('cancelled')
                                  or record.get('derived_status') not in {'not_run', 'succeeded'}):
        valid = False
    return {**result, 'id': record.get('identity', {}).get('id'),
            'identity': record.get('identity'), 'attempt': record.get('attempt'),
            'status': status if valid and complete and not busy else 'unknown',
            'quality': record.get('quality') if valid else None,
            'evidence_complete': bool(valid and complete and not busy and status == 'succeeded'
                                      and record['evidence_complete'] and result.get('evidence_complete')),
            'scientific_status': result.get('status', 'missing'),
            'derived_status': record.get('derived_status', 'not_run'),
            'input_provenance': record.get('input_provenance'), 'environment': record.get('environment'),
            'error': record.get('error'), 'cleanup_errors': record.get('cleanup_errors', []),
            'cancelled': record.get('cancelled', False), 'busy': busy,
            **({'metadata_status': 'incomplete' if record.get('phase') == 'running' else 'invalid'}
               if not valid or not complete else {})}
