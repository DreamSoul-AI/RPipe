"""Single Run owner, finite lifecycle and owned-resource cleanup."""
from __future__ import annotations

import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .contracts import AdmissionError, Budget, Cancellation, RunOutcome, RunSpec, SourceMismatch, project_execution
from .artifacts import (ArtifactLayout, Ownership, environment_snapshot, identity, input_provenance,
                        read_execution, read_json, safe_error_record, write_config, write_json, validate_source, validate_train)


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
    return safe_error_record(exc)


class Cancelled(Exception):
    pass


def _admit(spec: RunSpec, retry: bool) -> None:
    if spec.result_path.exists():
        if read_json(spec.result_path).get('identity') != identity(spec):
            raise SourceMismatch('existing Run has a different source/configuration identity')
        raise AdmissionError('existing Run result is immutable; choose a new version')
    if (spec.run_dir / 'execution.json').exists():
        record, complete = read_execution(spec)
        previous = project_execution(record, {}, busy=False, complete=complete)
        if previous['status'] not in {'failed', 'cancelled'}:
            raise AdmissionError('previous execution is unresolved; retry is not safe')
        if not retry:
            raise AdmissionError('previous execution requires explicit retry=True')
    elif (spec.run_dir / 'attempts').is_dir() and any((spec.run_dir / 'attempts').iterdir()):
        raise AdmissionError('legacy attempts have no explicit active record; retry is not safe')


def execute(spec: RunSpec, payload: Any, *, budget: Budget | None = None,
            cancellation: Cancellation | None = None,
            pipeline: Callable | None = None, configure: Callable | None = None,
            retry: bool = False) -> RunOutcome:
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
    if not isinstance(retry, bool):
        raise AdmissionError('retry must be a boolean')
    # Acquire still decides competing starts; unresolved owners are never stolen.
    if not (spec.run_dir / '.execution').exists():
        _admit(spec, retry)
    validate_train(spec)
    attempt = uuid.uuid4().hex
    owner = Ownership(spec, attempt=attempt)
    owner.acquire()
    try:
        _admit(spec, retry)
    except BaseException as primary:
        try:
            owner.release()
        except BaseException as cleanup_error:
            primary.cleanup_errors = (*getattr(primary, 'cleanup_errors', ()), cleanup_error)
        raise
    ctx = RunContext(spec, cancellation, ArtifactLayout(spec))
    ctx.state['budget'] = budget
    ctx.state['attempt'] = attempt
    attempt_path = spec.run_dir / 'attempts' / attempt / 'outcome.json'
    execution_path = spec.run_dir / 'execution.json'
    record = {'schema': 1, 'identity': identity(spec), 'attempt': attempt,
              'owner_token': owner.token, 'phase': 'running', 'candidate_status': None,
              'quality': None, 'evidence_complete': False, 'scientific_result_published': False,
              'derived_status': 'not_run', 'error': None, 'cleanup_errors': [], 'cancelled': False}
    primary = None
    cleanup = []
    result = {}
    published = False
    unknown = False
    derived = 'not_run'
    try:
        record['input_provenance'] = input_provenance(spec)
        record['environment'] = environment_snapshot()
        write_json(execution_path, record)
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
        logger = ctx.state.get('logger')
        if logger is not None and not isinstance(vars(exc).get('record'), dict):
            try:
                if isinstance(exc, Cancelled):
                    logger.info('Run cancellation confirmed', event='task.cancelled')
                else:
                    logger.exception('Run execution failed', exc, event='task.failed')
            except BaseException:
                logger.diagnostics_complete = False
    finally:
        try:
            payload.close(ctx)
        except BaseException as exc:
            cleanup.append(error_record(exc))
        supplied = ctx.state.get('cleanup_error_records', [])
        if not isinstance(supplied, (list, tuple)):
            supplied = [AdmissionError('cleanup error records must be a sequence')]
        reported_count = ctx.state.get('worker_cleanup_count', 0)
        if type(reported_count) is not int or not 0 <= reported_count <= len(supplied):
            reported_count = 0
        reported_cleanup = range(len(cleanup), len(cleanup) + reported_count)
        cleanup.extend(safe_error_record(item) for item in supplied)
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
            structure = result.get('structure')
            data_snapshot = structure.get('data') if isinstance(structure, dict) else None
            observed_stats = data_snapshot.get('stats_source') if isinstance(data_snapshot, dict) else None
            observed_reference = data_snapshot.get('stats_reference') if isinstance(data_snapshot, dict) else None
            record['input_provenance'] = input_provenance(spec, observed_stats_source=observed_stats,
                                                         observed_stats_reference=observed_reference)
            result = {**result, 'id': spec.id, 'status': 'succeeded', 'quality': result.get('quality'),
                      'evidence_complete': result.get('evidence_complete', True), 'identity': identity(spec), 'attempt': attempt,
                      'input_provenance': record.get('input_provenance'),
                      'environment': ctx.state.get('worker_environment', record.get('environment'))}
            owner.verify()
            write_json(spec.result_path, result)
            published = True
            complete = result['evidence_complete']
            # Derived processing has a separate failure record and cannot rewrite the science result.
            process_hook = ctx.state.get('process_hook')
            if process_hook:
                derived = 'running'
                ctx.state['phase'] = 'process'
                if ctx.state.get('logger') is not None:
                    ctx.state['logger'].bind(phase='process')
                    ctx.state['logger'].info('Run phase started', event='task.phase_changed')
                process_hook(ctx)
                derived = 'succeeded'
        except BaseException as exc:
            primary = error_record(exc)
            logger = ctx.state.get('logger')
            if logger is not None and not isinstance(vars(exc).get('record'), dict):
                try:
                    if isinstance(exc, Cancelled):
                        logger.info('Run cancellation confirmed', event='task.cancelled')
                    else:
                        logger.exception('Run publication or derived processing failed', exc, event='task.failed')
                except BaseException:
                    logger.diagnostics_complete = False
            status = 'cancelled' if primary['type'] in {'Cancelled', 'CancelledError'} else 'failed'
            complete = False
            if published:
                derived = status
    if status == 'succeeded' and cancellation.is_set():
        status, complete = 'cancelled', False
    logger = ctx.state.get('logger')
    diagnostic_errors = []
    if logger is not None:
        for index, failure in enumerate(cleanup):
            if index not in reported_cleanup:
                try:
                    logger.exception('Run cleanup operation failed', failure,
                                     event='operation.failed', operation='cleanup')
                except BaseException:
                    logger.diagnostics_complete = False
        try:
            logger.bind(phase='closing')
            logger.info('execution candidate recorded', event='operation.completed',
                        operation='execution_candidate', status=status, outcome='unknown')
            logger.close()
        except BaseException as exc:
            diagnostic_errors.append(error_record(exc))
        complete = complete and getattr(logger, 'diagnostics_complete', True) and not diagnostic_errors
    record.update(phase='closing', candidate_status=status, quality=result.get('quality') if published else None,
                  evidence_complete=bool(complete), scientific_result_published=published,
                  derived_status=derived, error=primary, cleanup_errors=cleanup,
                  cancelled=cancellation.is_set(), budget=vars(ctx.state['budget']),
                  diagnostic_errors=diagnostic_errors, stage=ctx.state.get('phase'),
                  scientific_result='result.json' if published else None,
                  environment=ctx.state.get('worker_environment', record.get('environment')))

    def persist():
        owner.verify()
        write_json(attempt_path, record)
        write_json(execution_path, record)

    try:
        persist()
    except BaseException as exc:
        cleanup.append(error_record(exc))
        unknown = True
    if not unknown:
        try:
            owner.release()
        except BaseException as exc:
            cleanup.append(error_record(exc))
            unknown = True
    if unknown:
        record.update(candidate_status='unknown', evidence_complete=False, cleanup_errors=cleanup)
        # Only an unreleased owner can still update this Run's active record.
        try:
            holder = read_json(owner.path / 'owner.json')
            if holder.get('token') == owner.token and holder.get('attempt') == attempt:
                persist()
        except BaseException as exc:
            cleanup.append(error_record(exc))
    projected = project_execution(record, result if published else {}, busy=unknown)
    return RunOutcome(projected['status'], projected['quality'], projected['evidence_complete'],
                      result if published else {}, primary, tuple(cleanup), cancellation.is_set(),
                      attempt=attempt, scientific_status=projected['scientific_status'],
                      derived_status=projected['derived_status'])
