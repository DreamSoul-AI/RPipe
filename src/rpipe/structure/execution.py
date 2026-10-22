"""Single Run owner, finite lifecycle and owned-resource cleanup."""
from __future__ import annotations

import subprocess
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .contracts import AdmissionError, Budget, Cancellation, RunOutcome, RunSpec, SourceMismatch
from .artifacts import ArtifactLayout, Ownership, identity, read_json, write_config, write_json, validate_source, validate_train


@dataclass
class RunContext:
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


def _validate_run(payload: Any) -> None:
    for name in ('prepare', 'execute', 'collect', 'close'):
        if not callable(getattr(payload, name, None)):
            raise AdmissionError(f'payload must provide {name}(ctx)')


def error_record(exc: BaseException) -> dict[str, Any]:
    remote = getattr(exc, 'record', None)
    if isinstance(remote, dict):
        return remote
    return {'type': type(exc).__name__, 'message': str(exc),
            'traceback': ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))}


class Cancelled(Exception):
    pass


def execute(spec: RunSpec, payload: Any, *, budget: Budget | None = None,
            cancellation: Cancellation | None = None,
            pipeline: Callable | None = None, configure: Callable | None = None) -> RunOutcome:
    budget = budget or Budget()
    budget.validate()
    _validate_run(payload)
    if budget.memory_bytes is not None:
        raise AdmissionError('this backend cannot enforce a hard memory limit')
    if budget.timeout_seconds is not None:
        raise AdmissionError('in-process payload cannot enforce a hard timeout')
    validate_source(spec)
    cancellation = cancellation or Cancellation()
    if cancellation.is_set():
        return RunOutcome('cancelled', cancelled=True)
    if spec.result_path.exists():
        if read_json(spec.result_path).get('identity') != identity(spec):
            raise SourceMismatch('existing Run has a different source/configuration identity')
        raise AdmissionError('existing Run result is immutable; choose a new version')
    validate_train(spec)
    owner = Ownership(spec)
    owner.acquire()
    ctx = RunContext(spec, cancellation, ArtifactLayout(spec))
    ctx.state['budget'] = budget
    attempt = uuid.uuid4().hex
    ctx.state['attempt'] = attempt
    attempt_path = spec.run_dir / 'attempts' / attempt / 'outcome.json'
    primary = None
    cleanup = []
    result = {}
    published = False
    unknown = False
    try:
        if spec.result_path.exists():
            raise AdmissionError('Run was completed before admission')
        write_config(ctx.layout.config_path, ctx.config)
        if configure:
            configure(ctx)
        if pipeline:
            result = pipeline(payload, ctx)
        else:
            payload.prepare(ctx)
            if ctx.config != spec.to_config() or (ctx.control is not None and ctx.control.to_dict() != spec.to_config()):
                raise SourceMismatch('effective configuration changed during prepare')
            if cancellation.is_set(): raise Cancelled('cancelled after prepare')
            payload.execute(ctx)
            if ctx.config != spec.to_config() or (ctx.control is not None and ctx.control.to_dict() != spec.to_config()):
                raise SourceMismatch('effective configuration changed during execute')
            if cancellation.is_set(): raise Cancelled('cancelled after execute')
            result = payload.collect(ctx)
        if not isinstance(result, dict):
            raise AdmissionError('payload.collect must return a mapping')
        if result.get('quality') is not None and not isinstance(result['quality'], bool):
            raise AdmissionError('quality must be a boolean or None')
        if 'evidence_complete' in result and not isinstance(result['evidence_complete'], bool):
            raise AdmissionError('evidence_complete must be a boolean')
    except BaseException as exc:
        primary = error_record(exc)
    finally:
        try:
            payload.close(ctx)
        except BaseException as exc:
            cleanup.append(error_record(exc))
        cleanup.extend(ctx.state.get('cleanup_error_records', []))
        unknown = bool(ctx.state.get('unknown_resources'))
        for process in ctx.processes:
            if process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except BaseException as exc:
                    cleanup.append(error_record(exc))
                if process.poll() is None:
                    unknown = True
    status = 'unknown' if unknown else 'failed' if primary or cleanup else 'cancelled' if cancellation.is_set() else 'succeeded'
    if primary and primary['type'] in {'Cancelled', 'CancelledError'} and not unknown and not cleanup:
        status = 'cancelled'
    complete = False
    if status == 'succeeded':
        try:
            validate_source(spec)
            if ctx.config != spec.to_config() or (ctx.control is not None and ctx.control.to_dict() != spec.to_config()):
                raise SourceMismatch('effective configuration changed before result publication')
            result = {**result, 'id': spec.id, 'status': 'succeeded', 'quality': result.get('quality'),
                      'evidence_complete': result.get('evidence_complete', True), 'identity': identity(spec), 'attempt': attempt}
            write_json(spec.result_path, result)
            published = True
            complete = result['evidence_complete']
            # Derived processing has a separate failure record and cannot rewrite the science result.
            process_hook = ctx.state.get('process_hook')
            if process_hook:
                process_hook(ctx)
        except BaseException as exc:
            primary = error_record(exc)
            status = 'failed'
            complete = False
    outcome = RunOutcome(status, result.get('quality'), complete, result, primary, tuple(cleanup), cancellation.is_set())
    try:
        write_json(attempt_path, {'status': status, 'quality': outcome.quality, 'evidence_complete': complete,
                                'scientific_result_published': published, 'error': primary,
                                'cleanup_errors': cleanup, 'cancelled': outcome.cancelled, 'identity': identity(spec),
                                'budget': vars(ctx.state['budget'])})
    except BaseException as exc:
        outcome.cleanup_errors += (error_record(exc),)
        outcome.evidence_complete = False
        outcome.status = 'unknown'
        unknown = True
    if not unknown:
        try:
            owner.release()
        except BaseException as exc:
            outcome.cleanup_errors += (error_record(exc),)
            outcome.evidence_complete = False
            outcome.status = 'unknown'
    return outcome
